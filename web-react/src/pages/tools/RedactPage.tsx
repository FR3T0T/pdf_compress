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
  // Pixel coordinates in the rendered page image (natural size, top-left
  // origin) — NOT yet PDF points. Converted at submit time (see
  // boxesToRects) since that's where the page height needed for the Y flip
  // is available for every box's own page.
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

type RedactMode = 'terms' | 'boxes';

/**
 * Converts drawn boxes (image pixel space, top-left origin) to PDF-point
 * rects (bottom-left origin) for startRedact's rects param.
 *
 * pdf_points_per_pixel = 72 / dpi -- uniform across every page because
 * getPageImages (ui/bridge.py) renders every page at the same fixed DPI,
 * regardless of that page's own point dimensions (verified directly: a
 * 612x792pt page and a 300x400pt page rendered at the same DPI produce
 * pixel dimensions that both satisfy px = pts * dpi/72 exactly).
 *
 * Y-axis: image origin is top-left, PDF origin is bottom-left, so
 * y_pdf = page_height_pts - (y_px * scale). Rounds outward (floor the
 * low edge, ceil the high edge) plus a 1pt margin, so a box can only ever
 * grow from rounding/imprecision, never shrink and leak content at the edge.
 */
function boxesToRects(boxes: RedactBox[], pages: PageImage[], dpi: number) {
  const scale = 72 / dpi;
  const MARGIN_PT = 1;
  const heightPxByPage = new Map(pages.map((p) => [p.index, p.height]));

  return boxes.map((b) => {
    const heightPx = heightPxByPage.get(b.page) ?? 0;
    const heightPts = heightPx * scale;
    const left = Math.min(b.x0, b.x1);
    const right = Math.max(b.x0, b.x1);
    const top = Math.min(b.y0, b.y1);
    const bottom = Math.max(b.y0, b.y1);

    return {
      page: b.page,
      x0: Math.floor(left * scale) - MARGIN_PT,
      x1: Math.ceil(right * scale) + MARGIN_PT,
      y0: Math.floor(heightPts - bottom * scale) - MARGIN_PT,
      y1: Math.ceil(heightPts - top * scale) + MARGIN_PT,
    };
  });
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

  usePageBusy(op.status === 'running');

  const file = files[0] ?? null;

  useEffect(() => {
    if (!file) {
      setPageImages([]);
      setBoxes([]);
      setPagesForPath(null);
      return;
    }
    if (mode !== 'boxes' || pagesForPath === file.path) return;

    setPagesLoading(true);
    bridgeApi.getPageImages(file.path).then((res) => {
      setPagesLoading(false);
      if (!res.success || !res.pages) {
        toast.error(res.error || 'Could not render pages for box drawing.');
        return;
      }
      setPageImages(res.pages);
      setPageDpi(res.dpi ?? 150);
      setPagesForPath(file.path);
      setBoxes([]);
    });
  }, [mode, file, pagesForPath, toast]);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const { redaction_count, pages_affected } = op.result.results;
      toast.success(`Redaction complete: ${redaction_count} matches redacted across ${pages_affected} page(s).`);
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during redaction.');
    }
  }, [op.status, op.result, op.error, toast]);

  const searchTerms = terms
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
  const pagesWithBoxes = new Set(boxes.map((b) => b.page)).size;

  const canRun =
    !!file &&
    !!outputPath &&
    op.status !== 'running' &&
    (mode === 'terms' ? searchTerms.length > 0 : boxes.length > 0);

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_redacted.pdf') : 'redacted.pdf';
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (path) setOutputPath(path);
  };

  const requestRedact = () => {
    if (!file) {
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
    if (!outputPath) {
      toast.warning('Please choose an output location.');
      return;
    }
    setConfirming(true);
  };

  const confirmRedact = () => {
    setConfirming(false);
    if (!file || !outputPath) return;
    op.run(() =>
      bridgeApi.startRedact(
        mode === 'terms'
          ? {
              file: file.path,
              output_path: outputPath,
              search_terms: searchTerms,
              case_sensitive: caseSensitive,
            }
          : {
              file: file.path,
              output_path: outputPath,
              rects: boxesToRects(boxes, pageImages, pageDpi),
            }
      )
    );
  };

  const addBox = (box: Omit<RedactBox, 'id'>) => {
    setBoxes((prev) => [...prev, { ...box, id: crypto.randomUUID() }]);
  };
  const removeBox = (id: string) => setBoxes((prev) => prev.filter((b) => b.id !== id));
  const clearPageBoxes = (page: number) => setBoxes((prev) => prev.filter((b) => b.page !== page));

  const r = op.status === 'done' ? op.result?.results : null;
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

      <DropZone
        files={files}
        onFilesChanged={setFiles}
        multiple={false}
        title="Drop PDF file here"
        subtitle="or click to browse"
        disabled={op.status === 'running'}
      />

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
            {!file && (
              <Card>
                <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>
                  Add a PDF file above to render its pages here.
                </span>
              </Card>
            )}
            {file && pagesLoading && (
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
  boxes,
  onAddBox,
  onRemoveBox,
  onClearPage,
  disabled,
}: {
  page: PageImage;
  boxes: RedactBox[];
  onAddBox: (box: Omit<RedactBox, 'id'>) => void;
  onRemoveBox: (id: string) => void;
  onClearPage: () => void;
  disabled: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

  const posFromEvent = (e: ReactMouseEvent) => {
    const rect = containerRef.current!.getBoundingClientRect();
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
      const x0 = Math.min(draft.x0, draft.x1);
      const x1 = Math.max(draft.x0, draft.x1);
      const y0 = Math.min(draft.y0, draft.y1);
      const y1 = Math.max(draft.y0, draft.y1);
      // Ignore accidental clicks/tiny drags (a few px of jitter on a click).
      if (x1 - x0 > 3 && y1 - y0 > 3) {
        onAddBox({ page: page.index, x0, y0, x1, y1 });
      }
    }
    dragStart.current = null;
    setDraft(null);
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
          width: page.width,
          height: page.height,
          cursor: disabled ? 'default' : 'crosshair',
          userSelect: 'none',
          border: '1px solid var(--border)',
        }}
      >
        <img
          src={page.dataUrl}
          width={page.width}
          height={page.height}
          draggable={false}
          alt={`Page ${page.index + 1}`}
          style={{ display: 'block', pointerEvents: 'none' }}
        />
        {boxes.map((b) => (
          <div
            key={b.id}
            style={{
              position: 'absolute',
              left: Math.min(b.x0, b.x1),
              top: Math.min(b.y0, b.y1),
              width: Math.abs(b.x1 - b.x0),
              height: Math.abs(b.y1 - b.y0),
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
        ))}
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
