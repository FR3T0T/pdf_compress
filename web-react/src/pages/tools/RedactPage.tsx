import { useEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Checkbox } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import { workspaceOutputPath } from '../../workspace/workspaceOutputPath';
import type { PickedFile } from '../../types/bridge';

interface RedactResult {
  input_path: string;
  output_path: string;
  redaction_count: number;
  pages_affected: number;
}

interface PageImage {
  index: number;
  dataUrl: string;
  width: number;
  height: number;
}

interface RedactBox {
  id: string;
  page: number;
  // PDF point coordinates, top-left origin -- same space as the page
  // pixmap PageCanvas draws over and as pdf_ops.py's redact_pdf expects for
  // its `rects` param directly, no axis flip anywhere in the pipeline (see
  // boxesToRects). Converted from displayed pixels to points at DRAW time
  // (see PageCanvas.finishDrag) using the image element's actual on-screen
  // rendered size at that moment -- not its natural pixel size and not the
  // render DPI -- so the box is correct regardless of what the display
  // size does afterward (CSS scaling, window resize, HiDPI, browser zoom,
  // etc.). Storing already-converted points also means resubmission
  // doesn't depend on the display still being at the size it was drawn at.
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

type RedactMode = 'terms' | 'boxes';

/** PDF point dimensions of a rendered page image -- derived from its
 *  natural pixel size and the DPI it was rendered at (uniform across every
 *  page: getPageImages renders every page at the same fixed DPI regardless
 *  of that page's own point dimensions, verified directly). This is only
 *  used to know each page's size in points; it is NOT used to scale box
 *  coordinates -- those are scaled from displayed px directly (see
 *  PageCanvas), never from DPI. */
function pageSizePts(page: PageImage, dpi: number) {
  const scale = 72 / dpi;
  return { wPts: page.width * scale, hPts: page.height * scale };
}

/**
 * Converts drawn boxes (already in PDF points) to the rects startRedact's
 * `rects` param expects -- just ordering + outward rounding, no axis flip.
 *
 * pdf_ops.py's redact_pdf builds `fitz.Rect(x0, y0, x1, y1)` directly from
 * these numbers and hands it straight to page.add_redact_annot(). PyMuPDF's
 * page-rect coordinate space (like get_text()/search_for(), and like the
 * page pixmap PageCanvas draws over) is top-left-origin -- confirmed
 * directly: a word inserted at y=140 is reported by get_text('words') with
 * y0=124.95/y1=144.19, matching insert_text's own top-left-origin y, not a
 * bottom-left one. So a box already in that same top-left-origin point
 * space (see PageCanvas) needs no flip -- flipping it would target the
 * mirrored position on the page instead of the one actually drawn over.
 *
 * Rounds outward (floor the low edge, ceil the high edge) plus a 1pt
 * margin, so a box can only ever grow from rounding/imprecision, never
 * shrink and leak content at the edge.
 */
function boxesToRects(boxes: RedactBox[]) {
  const MARGIN_PT = 1;

  return boxes.map((b) => ({
    page: b.page,
    x0: Math.floor(Math.min(b.x0, b.x1)) - MARGIN_PT,
    x1: Math.ceil(Math.max(b.x0, b.x1)) + MARGIN_PT,
    y0: Math.floor(Math.min(b.y0, b.y1)) - MARGIN_PT,
    y1: Math.ceil(Math.max(b.y0, b.y1)) + MARGIN_PT,
  }));
}

/**
 * React port of web/js/pages/redact.js, extended with a second mode this
 * page didn't have: user-drawn coordinate boxes, sent as `rects` to the
 * same startRedact slot search_terms mode already used (ui/bridge.py's
 * redact_pdf takes both, added for this feature — see PART 2 spec).
 *
 * Bridge calls: BridgeAPI.startRedact({ file, output_path, search_terms,
 * case_sensitive }) for terms mode, or ({ file, output_path, rects }) for
 * boxes mode. Result shape verified against pdf_ops.py's RedactResult
 * dataclass (input_path, output_path, redaction_count, pages_affected) —
 * vanilla reads these at the top level of the done payload
 * (data.redaction_count), but the real shape nests them under results,
 * matching the systemic toolKey/pct bug found across the older pages.
 * Read from op.result.results here instead.
 *
 * Page rendering for the box canvas uses BridgeAPI.getPageImages(path) —
 * a new bridge slot (ui/bridge.py), since the only existing per-page
 * render capability (getThumbnail) is hardcoded to page 1 at thumbnail
 * size. Reuses the exact same PyMuPDF page.get_pixmap() call, just
 * generalized to every page at a size suited to precise box placement.
 *
 * The vanilla page shows a confirmation modal before running (redaction
 * is irreversible) — ported as an inline confirm step rather than a
 * modal dialog component (none built yet in the shared set).
 */
export function RedactPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [mode, setMode] = useState<RedactMode>('terms');

  // -- Search-terms mode ---------------------------------------------------
  const [terms, setTerms] = useState('');
  const [caseSensitive, setCaseSensitive] = useState(false);

  // -- Draw-boxes mode -------------------------------------------------------
  const [pageImages, setPageImages] = useState<PageImage[]>([]);
  const [pageDpi, setPageDpi] = useState(150);
  const [pagesLoading, setPagesLoading] = useState(false);
  const [pagesForPath, setPagesForPath] = useState<string | null>(null);
  const [boxes, setBoxes] = useState<RedactBox[]>([]);

  const [confirming, setConfirming] = useState(false);
  const op = useOperation<RedactResult>('redact');

  // -- Workspace (persistent working document) -----------------------------
  // See WatermarkPage.tsx for the reference pattern this mirrors. Redact's
  // box-drawing mode renders whatever `effectivePath` points at, so it
  // works against the workspace document exactly like search-terms mode.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;

  useEffect(() => {
    if (!effectivePath) {
      setPageImages([]);
      setBoxes([]);
      setPagesForPath(null);
      return;
    }
    if (mode !== 'boxes' || pagesForPath === effectivePath) return;

    setPagesLoading(true);
    bridgeApi.getPageImages(effectivePath).then((res) => {
      setPagesLoading(false);
      if (!res.success || !res.pages) {
        toast.error(res.error || 'Could not render pages for box drawing.');
        return;
      }
      setPageImages(res.pages);
      setPageDpi(res.dpi ?? 150);
      setPagesForPath(effectivePath);
      setBoxes([]);
    });
  }, [mode, effectivePath, pagesForPath, toast]);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const { output_path, redaction_count, pages_affected } = op.result.results;
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        workspace.applyResult(output_path, `Redact (${mode})`);
        toast.success(`Redaction complete: ${redaction_count} matches redacted across ${pages_affected} page(s).`);
        return;
      }
      toast.success(`Redaction complete: ${redaction_count} matches redacted across ${pages_affected} page(s).`);
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'An error occurred during redaction.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, toast]);

  const searchTerms = terms
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  const pagesWithBoxes = new Set(boxes.map((b) => b.page)).size;

  const canRun =
    !!effectivePath &&
    (workspace.path ? true : !!outputPath) &&
    op.status !== 'running' &&
    (mode === 'terms' ? searchTerms.length > 0 : boxes.length > 0);

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_redacted.pdf') : 'redacted.pdf';
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (path) setOutputPath(path);
  };

  const requestRedact = () => {
    if (!effectivePath) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (mode === 'terms' && searchTerms.length === 0) {
      toast.warning('Please enter at least one search term.');
      return;
    }
    if (mode === 'boxes' && boxes.length === 0) {
      toast.warning('Please draw at least one box.');
      return;
    }
    if (!workspace.path && !outputPath) {
      toast.warning('Please choose an output location.');
      return;
    }
    setConfirming(true);
  };

  const confirmRedact = () => {
    setConfirming(false);
    if (!effectivePath) return;

    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        const outPath = workspaceOutputPath(wsDir, wsPath, opIndex);
        bridgeApi.startRedact(
          mode === 'terms'
            ? { file: wsPath, output_path: outPath, search_terms: searchTerms, case_sensitive: caseSensitive }
            : { file: wsPath, output_path: outPath, rects: boxesToRects(boxes) }
        );
      });
      return;
    }

    if (!outputPath) return;
    op.run(() =>
      bridgeApi.startRedact(
        mode === 'terms'
          ? {
              file: effectivePath,
              output_path: outputPath,
              search_terms: searchTerms,
              case_sensitive: caseSensitive,
            }
          : {
              file: effectivePath,
              output_path: outputPath,
              rects: boxesToRects(boxes),
            }
      )
    );
  };

  const addBox = (box: Omit<RedactBox, 'id'>) => {
    setBoxes((prev) => [...prev, { ...box, id: crypto.randomUUID() }]);
  };
  const removeBox = (id: string) => setBoxes((prev) => prev.filter((b) => b.id !== id));
  const clearPageBoxes = (page: number) => setBoxes((prev) => prev.filter((b) => b.page !== page));

  const r = op.status === 'done' && !workspace.path ? op.result?.results : null;
  const results = r
    ? {
        files: [{ name: bridgeApi.basename(r.output_path), status: 'done' as const }],
        outputDir: bridgeApi.dirname(r.output_path),
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Redact" subtitle="Permanently remove sensitive text from PDF" backButton={false} />

      <div
        className="panel"
        style={{
          borderLeftWidth: 3,
          borderLeftStyle: 'solid',
          borderLeftColor: 'var(--sev-medium)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          marginBottom: 'var(--space-3)',
        }}
      >
        <span style={{ fontSize: 20 }}>⚠️</span>
        <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>
          Redaction is irreversible. Redacted content cannot be recovered.
        </span>
      </div>

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — see the bar above to
            Preview, Export, or Clear it.
          </div>
        </Card>
      ) : (
        <DropZone
          files={files}
          onFilesChanged={setFiles}
          multiple={false}
          title="Drop PDF file here"
          subtitle="or click to browse"
          disabled={op.status === 'running'}
        />
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 'var(--space-3)' }}>
        <ModeButton active={mode === 'terms'} onClick={() => setMode('terms')} disabled={op.status === 'running'}>
          Search terms
        </ModeButton>
        <ModeButton active={mode === 'boxes'} onClick={() => setMode('boxes')} disabled={op.status === 'running'}>
          Draw boxes
        </ModeButton>
      </div>

      {mode === 'terms' ? (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>
              Search terms (one per line)
            </div>
            <textarea
              value={terms}
              onChange={(e) => setTerms(e.target.value)}
              rows={6}
              placeholder={'Enter text to redact, one term per line\ne.g.\nJohn Doe\n555-0123\nSSN: 123-45-6789'}
              className="mono"
              style={{
                width: '100%',
                resize: 'vertical',
                background: 'var(--panel-bg-elevated)',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-panel-sm)',
                color: 'var(--text-1)',
                fontSize: 'var(--font-size-sm)',
                padding: 'var(--space-3)',
              }}
            />
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Checkbox checked={caseSensitive} onChange={setCaseSensitive} label="Case sensitive" />
            </div>
          </Card>
        </div>
      ) : (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>
                {boxes.length} box{boxes.length === 1 ? '' : 'es'} drawn
                {pagesWithBoxes > 0 ? ` across ${pagesWithBoxes} page${pagesWithBoxes === 1 ? '' : 's'}` : ''}
              </span>
              <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
                Click and drag over a page below to mark an area for redaction.
              </span>
            </div>
          </Card>

          <div style={{ marginTop: 'var(--space-3)', maxHeight: 640, overflowY: 'auto' }}>
            {!effectivePath && (
              <Card>
                <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>
                  Add a PDF file above to render its pages here.
                </span>
              </Card>
            )}
            {effectivePath && pagesLoading && (
              <Card>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-2)' }}>
                  <span className="spinner" /> Rendering pages…
                </div>
              </Card>
            )}
            {pageImages.map((page) => (
              <PageCanvas
                key={page.index}
                page={page}
                dpi={pageDpi}
                boxes={boxes.filter((b) => b.page === page.index)}
                onAddBox={addBox}
                onRemoveBox={removeBox}
                onClearPage={() => clearPageBoxes(page.index)}
                disabled={op.status === 'running'}
              />
            ))}
          </div>
        </div>
      )}

      {!workspace.path && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span className="mono" style={{ flex: 1, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
                {outputPath ? bridgeApi.basename(outputPath) : 'No output file selected'}
              </span>
              <button onClick={pickOutput} disabled={op.status === 'running'} className="btn-ghost">
                Browse…
              </button>
            </div>
          </Card>
        </div>
      )}

      {confirming ? (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ borderLeft: '2px solid var(--sev-high)', paddingLeft: 'var(--space-3)' }}>
              <strong style={{ fontSize: 'var(--font-size-sm)' }}>This action is irreversible.</strong>
              <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginTop: 4 }}>
                {mode === 'terms'
                  ? `${searchTerms.length} search term(s) will be permanently redacted from the document.`
                  : `${boxes.length} area(s) across ${pagesWithBoxes} page(s) will be permanently removed.`}{' '}
                Are you sure you want to continue?
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 'var(--space-3)' }}>
                <button onClick={() => setConfirming(false)} className="btn-ghost">
                  Cancel
                </button>
                <button onClick={confirmRedact} className="btn-primary">
                  Redact permanently
                </button>
              </div>
            </div>
          </Card>
        </div>
      ) : (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
          <button onClick={requestRedact} disabled={!canRun} className="btn-primary">
            {op.status === 'running' ? 'Redacting…' : 'Redact'}
          </button>
        </div>
      )}

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('redact');
              op.reset();
              toast.info('Redaction cancelled.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} />
          {r && (
            <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {r.redaction_count} matches redacted across {r.pages_affected} page(s)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  disabled,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled: boolean;
  children: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={active ? 'btn-primary' : 'btn-ghost'}
      style={{ flex: 1 }}
    >
      {children}
    </button>
  );
}

function PageCanvas({
  page,
  dpi,
  boxes,
  onAddBox,
  onRemoveBox,
  onClearPage,
  disabled,
}: {
  page: PageImage;
  dpi: number;
  boxes: RedactBox[];
  onAddBox: (box: Omit<RedactBox, 'id'>) => void;
  onRemoveBox: (id: string) => void;
  onClearPage: () => void;
  disabled: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  // The image's actual ON-SCREEN rendered size (not its natural pixel
  // size) -- re-measured whenever the element's box changes so drawn
  // boxes keep lining up with the image regardless of what the display
  // size does (CSS scaling, window/panel resize, zoom, HiDPI, etc.).
  const [dispSize, setDispSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    const el = imgRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      setDispSize({ w: rect.width, h: rect.height });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [page.dataUrl]);

  const { wPts, hPts } = pageSizePts(page, dpi);

  // Position relative to the image element's own top-left, in DISPLAYED
  // pixels -- not the container's box, so any border/padding on the
  // container can never introduce an offset.
  const posFromEvent = (e: ReactMouseEvent) => {
    const rect = imgRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const onMouseDown = (e: ReactMouseEvent) => {
    if (disabled) return;
    const pos = posFromEvent(e);
    dragStart.current = pos;
    setDraft({ x0: pos.x, y0: pos.y, x1: pos.x, y1: pos.y });
  };
  const onMouseMove = (e: ReactMouseEvent) => {
    if (!dragStart.current) return;
    const pos = posFromEvent(e);
    setDraft({ x0: dragStart.current.x, y0: dragStart.current.y, x1: pos.x, y1: pos.y });
  };
  const finishDrag = () => {
    if (dragStart.current && draft) {
      const rect = imgRef.current!.getBoundingClientRect();
      const dispW = rect.width;
      const dispH = rect.height;
      const x0Disp = Math.min(draft.x0, draft.x1);
      const x1Disp = Math.max(draft.x0, draft.x1);
      const y0Disp = Math.min(draft.y0, draft.y1);
      const y1Disp = Math.max(draft.y0, draft.y1);
      // Ignore accidental clicks/tiny drags (a few px of jitter on a click).
      if (x1Disp - x0Disp > 3 && y1Disp - y0Disp > 3 && dispW > 0 && dispH > 0) {
        // Map DISPLAYED px straight to PDF points using the ratio of the
        // page's point size to the image's actual on-screen size -- NOT
        // natural pixel size, NOT render DPI. This is the fix: boxes used
        // to be captured in natural-pixel space and scaled only by
        // render DPI, which silently assumed displayed size == natural
        // size. When the browser renders the image at any other size,
        // that assumption is wrong and the box lands offset/undersized.
        const scaleX = wPts / dispW;
        const scaleY = hPts / dispH;
        onAddBox({
          page: page.index,
          x0: x0Disp * scaleX,
          y0: y0Disp * scaleY,
          x1: x1Disp * scaleX,
          y1: y1Disp * scaleY,
        });
      }
    }
    dragStart.current = null;
    setDraft(null);
  };

  // Project a box (stored in PDF points) back to the CURRENT displayed
  // px, for rendering the overlay -- re-derived on every render from
  // `dispSize`, so already-drawn boxes stay visually aligned even if the
  // image's displayed size changes later.
  const toDisplayRect = (b: { x0: number; y0: number; x1: number; y1: number }) => {
    if (!dispSize || dispSize.w === 0 || dispSize.h === 0) return null;
    const scaleX = dispSize.w / wPts;
    const scaleY = dispSize.h / hPts;
    return {
      left: Math.min(b.x0, b.x1) * scaleX,
      top: Math.min(b.y0, b.y1) * scaleY,
      width: Math.abs(b.x1 - b.x0) * scaleX,
      height: Math.abs(b.y1 - b.y0) * scaleY,
    };
  };

  return (
    <div style={{ marginBottom: 'var(--space-4)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 4,
          fontSize: 'var(--font-size-xs)',
          color: 'var(--text-3)',
        }}
      >
        <span>Page {page.index + 1}</span>
        {boxes.length > 0 && (
          <button onClick={onClearPage} className="btn-ghost" style={{ padding: '2px 8px', fontSize: 'var(--font-size-xs)' }}>
            Clear this page ({boxes.length})
          </button>
        )}
      </div>
      <div
        ref={containerRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={finishDrag}
        onMouseLeave={finishDrag}
        style={{
          position: 'relative',
          display: 'inline-block',
          cursor: disabled ? 'default' : 'crosshair',
          userSelect: 'none',
          // outline (not border): drawn on top without affecting the box
          // model, so this container's content box -- where the image
          // and overlay boxes are positioned -- can never be offset from
          // the image element's own top-left by a border/padding inset.
          outline: '1px solid var(--border)',
          outlineOffset: -1,
        }}
      >
        <img
          ref={imgRef}
          src={page.dataUrl}
          width={page.width}
          height={page.height}
          draggable={false}
          alt={`Page ${page.index + 1}`}
          style={{ display: 'block', pointerEvents: 'none', maxWidth: '100%', height: 'auto' }}
        />
        {boxes.map((b) => {
          const r = toDisplayRect(b);
          if (!r) return null;
          return (
            <div
              key={b.id}
              style={{
                position: 'absolute',
                left: r.left,
                top: r.top,
                width: r.width,
                height: r.height,
                background: 'rgba(216, 90, 48, 0.35)',
                border: '1.5px solid var(--sev-high)',
                pointerEvents: 'none',
              }}
            >
              <button
                onClick={() => onRemoveBox(b.id)}
                onMouseDown={(e) => e.stopPropagation()}
                title="Remove box"
                style={{
                  position: 'absolute',
                  top: -10,
                  right: -10,
                  width: 20,
                  height: 20,
                  borderRadius: '50%',
                  border: 'none',
                  background: 'var(--sev-high)',
                  color: '#fff',
                  fontSize: 12,
                  lineHeight: '20px',
                  textAlign: 'center',
                  cursor: 'pointer',
                  padding: 0,
                  pointerEvents: 'auto',
                }}
              >
                ×
              </button>
            </div>
          );
        })}
        {draft && (
          <div
            style={{
              position: 'absolute',
              left: Math.min(draft.x0, draft.x1),
              top: Math.min(draft.y0, draft.y1),
              width: Math.abs(draft.x1 - draft.x0),
              height: Math.abs(draft.y1 - draft.y0),
              background: 'rgba(29, 158, 117, 0.25)',
              border: '1.5px dashed var(--sev-info)',
              pointerEvents: 'none',
            }}
          />
        )}
      </div>
    </div>
  );
}
