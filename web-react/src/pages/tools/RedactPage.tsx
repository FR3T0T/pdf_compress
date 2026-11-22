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

interface VerificationCheck {
  id: string;
  description: string;
  passed: boolean;
  evidence: string[];
  pages: number[];
  isDocumentLevel: boolean;
}

interface VerificationReport {
  verified: boolean;
  checks: VerificationCheck[];
  flattenTargetPages: number[] | null;
}

/** A finished run in plain words, made by the Python side
 *  (pdf_ops.redaction_summary, #119) so the wording is in one place and
 *  tested. This page shows the lines it is given. */
interface RedactSummary {
  headline: string;
  checked: string;
  removed: string[];
  notes: string[];
}

interface RedactResult {
  input_path: string;
  output_path: string;
  redaction_count: number;
  pages_affected: number;
  // #99: per-surface removal breakdown, the proof-of-removal report, and any
  // pages the flatten fallback rasterized on this run.
  surface_counts?: Record<string, number>;
  verification?: VerificationReport;
  flattened_pages?: number[];
  // #119: the plain facts of the run and their plain-words summary.
  flattened_areas?: Record<string, number>;
  notes?: unknown[];
  summary?: RedactSummary;
}

/** A refused run in plain words (pdf_verify.redaction_refusal_detail). */
interface RedactRefusal {
  headline: string;
  found: string[];
  nextStep: string;
}

/** Extra top-level fields the bridge attaches to a FAILED redaction payload.
 *  When verify_redaction refused the output (#99, RedactionVerificationError):
 *  the report, the flatten-eligible pages (null when flatten can't help),
 *  whether the residual was document-level, and the refusal in plain words.
 *  When the file cannot be redacted at all (RedactionNotPossibleError, #119):
 *  notPossible, with the reason in the payload's message. */
interface RedactFailure {
  verification?: VerificationReport;
  flattenablePages?: number[] | null;
  documentLevel?: boolean;
  refusal?: RedactRefusal | null;
  notPossible?: boolean;
}

/** "page 2", "pages 1, 3 and 5", or "these 12 pages" for a long list. */
function pagesLabel(pages: number[]): string {
  if (pages.length === 1) return `page ${pages[0]}`;
  if (pages.length <= 5) return `pages ${pages.slice(0, -1).join(', ')} and ${pages[pages.length - 1]}`;
  return `these ${pages.length.toLocaleString('en-US')} pages`;
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
  // The frontend-computed, known-good output path for the in-flight workspace
  // run (inside the workspace dir, via workspaceOutputPath). The done handler
  // advances the workspace with THIS, never the path echoed back in the
  // backend result — never trust a round-tripped path (FE-03).
  const workspaceOutPathRef = useRef<string | null>(null);

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
        toast.error(res.error || 'The pages could not be shown for drawing boxes.');
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
      const { redaction_count, pages_affected, summary } = op.result.results;
      // #99: the operation only reaches 'done' when verify_redaction proved
      // the output clean, so success always means checked. The words come
      // from the Python side (#119); the fallback is for an older bridge.
      const msg =
        summary?.headline ??
        `Redacted ${redaction_count.toLocaleString('en-US')} places on ${pages_affected.toLocaleString('en-US')} pages.`;
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        // Advance the workspace with the path THIS page computed, not the one
        // the backend echoed back in the result (FE-03). Counts for the toast
        // still come from the backend result.
        const trustedOut = workspaceOutPathRef.current;
        workspaceOutPathRef.current = null;
        if (trustedOut) {
          workspace.applyResult(trustedOut, `Redact (${mode})`);
          toast.success(msg);
        } else {
          toast.error('The redaction worked, but the working document could not be updated.');
        }
        return;
      }
      toast.success(msg);
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      workspaceOutPathRef.current = null;
      // A refusal is not a crash: the panel below says what happened.
      const f = (op.result as unknown as RedactFailure) ?? null;
      if (f?.verification || f?.refusal) toast.warning('Redaction refused. Nothing was saved.');
      else if (f?.notPossible) toast.warning("Redaction can't be done. Nothing was saved.");
      else toast.error(op.error || 'Something went wrong while redacting. Nothing was saved.');
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
      toast.warning('Please type at least one word to search for.');
      return;
    }
    if (mode === 'boxes' && boxes.length === 0) {
      toast.warning('Please draw at least one box.');
      return;
    }
    if (!workspace.path && !outputPath) {
      toast.warning('Please choose where to save the new file.');
      return;
    }
    setConfirming(true);
  };

  // Builds the startRedact params for the current mode, optionally adding
  // the D1 flatten retry pages (#99).
  const buildParams = (path: string, out: string, flattenPages?: number[]) => ({
    ...(mode === 'terms'
      ? { file: path, output_path: out, search_terms: searchTerms, case_sensitive: caseSensitive }
      : { file: path, output_path: out, rects: boxesToRects(boxes) }),
    ...(flattenPages && flattenPages.length ? { flatten_pages: flattenPages } : {}),
  });

  // Single dispatch path used by both the confirm step and the flatten retry.
  const startRun = (flattenPages?: number[]) => {
    if (!effectivePath) return;

    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        const outPath = workspaceOutputPath(wsDir, wsPath, opIndex);
        // Stash the known-good path for the done handler (FE-03).
        workspaceOutPathRef.current = outPath;
        bridgeApi.startRedact(buildParams(wsPath, outPath, flattenPages));
      });
      return;
    }

    if (!outputPath) return;
    op.run(() => bridgeApi.startRedact(buildParams(effectivePath, outputPath, flattenPages)));
  };

  const confirmRedact = () => {
    setConfirming(false);
    startRun();
  };

  // The fail-closed failure detail (#99): present only when verify_redaction
  // refused the output. op.result carries the structured extras even on error.
  const failure =
    op.status === 'error' ? ((op.result as unknown as RedactFailure) ?? null) : null;
  const flattenablePages =
    failure?.flattenablePages && failure.flattenablePages.length
      ? failure.flattenablePages
      : null;

  const addBox = (box: Omit<RedactBox, 'id'>) => {
    setBoxes((prev) => [...prev, { ...box, id: crypto.randomUUID() }]);
  };
  const removeBox = (id: string) => setBoxes((prev) => prev.filter((b) => b.id !== id));
  const clearPageBoxes = (page: number) => setBoxes((prev) => prev.filter((b) => b.page !== page));

  const done = op.status === 'done' ? (op.result?.results ?? null) : null;
  const r = done && !workspace.path ? done : null;
  const results = r
    ? {
        files: [{ name: bridgeApi.basename(r.output_path), status: 'done' as const }],
        outputDir: bridgeApi.dirname(r.output_path),
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Redact" subtitle="Remove words and areas from a PDF for good" backButton={false} />

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
          Redaction can't be undone. What you redact is gone for good.
        </span>
      </div>

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Working on the document in your workspace ({workspace.originalName}). Use the bar
            above to preview, export or clear it.
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
              Words to search for (one per line)
            </div>
            <textarea
              value={terms}
              onChange={(e) => setTerms(e.target.value)}
              rows={6}
              placeholder={'Type what to remove, one per line\nfor example:\nJohn Doe\n555-0123\nSSN: 123-45-6789'}
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
              <Checkbox checked={caseSensitive} onChange={setCaseSensitive} label="Match upper and lower case exactly" />
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
                Drag over a page below to mark an area to remove.
              </span>
            </div>
          </Card>

          <div style={{ marginTop: 'var(--space-3)', maxHeight: 640, overflowY: 'auto' }}>
            {!effectivePath && (
              <Card>
                <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>
                  Add a PDF file above to see its pages here.
                </span>
              </Card>
            )}
            {effectivePath && pagesLoading && (
              <Card>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-2)' }}>
                  <span className="spinner" /> Loading pages…
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
                {outputPath ? bridgeApi.basename(outputPath) : 'No place to save chosen yet'}
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
              <strong style={{ fontSize: 'var(--font-size-sm)' }}>This can't be undone.</strong>
              <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginTop: 4 }}>
                {mode === 'terms'
                  ? `Every place ${searchTerms.length === 1 ? 'this word appears' : `these ${searchTerms.length} words appear`} will be removed for good.`
                  : `Everything in ${boxes.length === 1 ? 'this area' : `these ${boxes.length} areas`} will be removed for good.`}{' '}
                Do you want to go on?
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 'var(--space-3)' }}>
                <button onClick={() => setConfirming(false)} className="btn-ghost">
                  Cancel
                </button>
                <button onClick={confirmRedact} className="btn-primary">
                  Yes, redact
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
              toast.info('Redaction stopped. Nothing was saved.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} />
        </div>
      )}

      {done?.summary && <RedactSummaryPanel summary={done.summary} />}

      {failure?.verification && (
        <VerificationFailurePanel
          failure={failure}
          flattenablePages={flattenablePages}
          onFlatten={() => flattenablePages && startRun(flattenablePages)}
          busy={op.status === 'running'}
        />
      )}

      {failure?.notPossible && <NotPossiblePanel message={op.error} />}
    </div>
  );
}

/** Lines of text that may be long (file names, page lists): they wrap
 *  anywhere rather than push the panel wider. */
const wrapAnywhere = { overflowWrap: 'anywhere' as const, wordBreak: 'break-word' as const };

/** A finished run in plain words (#119): the headline, that the new file
 *  was checked, what else was removed and what to know. Every line comes
 *  from the Python side (pdf_ops.redaction_summary); a count can be 1 or
 *  32,768, and the lines simply wrap. Shown for a workspace run too. */
function RedactSummaryPanel({ summary }: { summary: RedactSummary }) {
  const listStyle = {
    margin: '6px 0 0',
    paddingLeft: 18,
    color: 'var(--text-2)',
    fontSize: 'var(--font-size-sm)',
    ...wrapAnywhere,
  };
  return (
    <div style={{ marginTop: 'var(--space-3)' }}>
      <Card>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, color: 'var(--sev-info)', fontWeight: 700 }}>
          <span aria-hidden>✓</span>
          <span style={{ fontSize: 'var(--font-size-sm)', ...wrapAnywhere }}>{summary.headline}</span>
        </div>
        <div style={{ marginTop: 4, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>{summary.checked}</div>
        {summary.removed.length > 0 && (
          <div style={{ marginTop: 'var(--space-3)' }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>Also removed</div>
            <ul style={listStyle}>
              {summary.removed.map((line, i) => (
                <li key={i} style={{ marginBottom: 2 }}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}
        {summary.notes.length > 0 && (
          <div style={{ marginTop: 'var(--space-3)' }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>Good to know</div>
            <ul style={listStyle}>
              {summary.notes.map((line, i) => (
                <li key={i} style={{ marginBottom: 4 }}>
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </div>
  );
}

/** Redaction cannot be done at all (#119): a form stored only as XFA, or a
 *  PDF library that removes other text when it redacts. A plain refusal with
 *  the reason the Python side gives, not an error. The heading fits both:
 *  in the second case the file is not the problem. */
function NotPossiblePanel({ message }: { message: string | null }) {
  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <Card>
        <div style={{ borderLeft: '3px solid var(--sev-medium)', paddingLeft: 'var(--space-3)' }}>
          <strong style={{ fontSize: 'var(--font-size-sm)' }}>Redaction can't be done.</strong>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginTop: 4, ...wrapAnywhere }}>
            {message || 'Nothing was saved.'}
          </div>
        </div>
      </Card>
    </div>
  );
}

/** Fail-closed redaction result (#99): verify_redaction refused the
 *  candidate before it reached the destination, so nothing was written
 *  there and whatever was at that path is untouched. Says, in the plain
 *  words the Python side made from the failed checks (#119,
 *  pdf_verify.redaction_refusal_detail), what was found and where, and what
 *  can be done — with no promise. Only when every residual is on a page is
 *  the D1 offer to turn those pages into pictures made. The checks' own
 *  lines are kept under "Technical details", for a support question. */
function VerificationFailurePanel({
  failure,
  flattenablePages,
  onFlatten,
  busy,
}: {
  failure: RedactFailure;
  flattenablePages: number[] | null;
  onFlatten: () => void;
  busy: boolean;
}) {
  const failed = (failure.verification?.checks ?? []).filter((c) => !c.passed);
  const refusal: RedactRefusal = failure.refusal ?? {
    headline: 'Redaction refused: what you asked to remove could not be shown to be gone. Nothing was saved.',
    found: [],
    nextStep: '',
  };
  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <Card>
        <div style={{ borderLeft: '3px solid var(--sev-high)', paddingLeft: 'var(--space-3)' }}>
          <strong style={{ fontSize: 'var(--font-size-sm)', color: 'var(--sev-high)', ...wrapAnywhere }}>
            {refusal.headline}
          </strong>

          {refusal.found.length > 0 && (
            <ul
              style={{
                margin: '8px 0 0',
                paddingLeft: 18,
                color: 'var(--text-2)',
                fontSize: 'var(--font-size-sm)',
                ...wrapAnywhere,
              }}
            >
              {refusal.found.map((line, i) => (
                <li key={i} style={{ marginBottom: 2 }}>
                  {line}
                </li>
              ))}
            </ul>
          )}

          {refusal.nextStep && (
            <div style={{ marginTop: 'var(--space-3)', color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
              {refusal.nextStep}
            </div>
          )}

          {flattenablePages && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <button onClick={onFlatten} disabled={busy} className="btn-primary">
                {busy
                  ? 'Working…'
                  : `Turn ${pagesLabel(flattenablePages)} into ${flattenablePages.length === 1 ? 'a picture' : 'pictures'}`}
              </button>
            </div>
          )}

          {failed.length > 0 && (
            <details style={{ marginTop: 'var(--space-3)' }}>
              <summary style={{ cursor: 'pointer', color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
                Technical details
              </summary>
              <ul
                style={{
                  margin: '6px 0 0',
                  paddingLeft: 18,
                  color: 'var(--text-3)',
                  fontSize: 'var(--font-size-xs)',
                  ...wrapAnywhere,
                }}
              >
                {failed.flatMap((c) =>
                  (c.evidence.length ? c.evidence : [c.description]).map((e, i) => (
                    <li key={`${c.id}-${i}`} style={{ marginBottom: 2 }}>
                      {e}
                    </li>
                  ))
                )}
              </ul>
            </details>
          )}
        </div>
      </Card>
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
