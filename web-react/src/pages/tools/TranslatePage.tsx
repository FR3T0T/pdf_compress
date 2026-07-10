import { useEffect, useMemo, useRef, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { Checkbox, Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import { workspaceOutputPath } from '../../workspace/workspaceOutputPath';
import type { PickedFile } from '../../types/bridge';

interface Language {
  code: string;
  name: string;
  native?: string;
  translateTo?: boolean;
  translateFrom?: boolean;
}
interface RuntimeStatus {
  needed: boolean;
  installed: boolean;
  downloadSizeMB: number;
  dir: string;
}
interface TranslationStatus {
  argosAvailable: boolean;
  ocrAvailable: boolean;
  argosPairs: string[];
  languages: Language[];
  // Frozen builds provision the ML stack on demand — see translate_runtime.py.
  runtime?: RuntimeStatus;
}
interface SetupResult {
  runtimeInstalled?: boolean;
  installed?: number;
  skipped?: number;
  requested?: number;
  status?: TranslationStatus;
}
interface ImageTranslateResult {
  sourceText?: string;
  translatedText?: string;
  source?: string;
  target?: string;
  ocrLang?: string;
  note?: string;
}
interface PdfTranslateResult {
  output: string;
  outputSize: number;
  pages: number;
  source: string;
  target: string;
  note?: string;
}
type TranslateResult = PdfTranslateResult | ImageTranslateResult;
type TranslateMode = 'image' | 'pdf';

type OutputFormat = 'pdf' | 'docx' | 'txt';

const FORMAT_OPTIONS: Record<OutputFormat, { label: string; filter: string; ext: string }> = {
  pdf: { label: 'PDF (keeps original images)', filter: 'PDF Document (*.pdf)', ext: '.pdf' },
  docx: { label: 'Word (.docx)', filter: 'Word Document (*.docx)', ext: '.docx' },
  txt: { label: 'Plain text (.txt)', filter: 'Text (*.txt)', ext: '.txt' },
};

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'tiff', 'tif', 'bmp', 'gif', 'webp'];
function ext(path: string) {
  const m = /\.([a-z0-9]+)$/i.exec(path || '');
  return m ? m[1].toLowerCase() : '';
}
function isImage(path: string) {
  return IMAGE_EXT.includes(ext(path));
}
function isPdf(path: string) {
  return ext(path) === 'pdf';
}

// Default the "To" language to the first INSTALLED non-English pack:
// with "From" defaulting to auto-detect (usually English content), the
// old To=English default produced an English→English no-op that read as
// broken. Falls back to 'en' only when no packs are installed yet — the
// setup flow is showing in that state anyway.
function defaultTarget(langs: Language[]): string {
  return langs.find((l) => l.code !== 'en' && l.translateTo)?.code ?? 'en';
}

/**
 * React port of web/js/pages/translate.js. Both flows are now async,
 * off the UI thread, through the same useOperation('translate') flow:
 *   - Image: BridgeAPI.startTranslateImage({ path, source, target,
 *     protectTerms }) -- was a synchronous translateImage() call, which
 *     froze the window ("not responding") on Argos's slow first-use
 *     model load since it ran on the UI thread. ui/bridge.py now runs it
 *     via _run_in_thread like every other tool, same as the PDF flow
 *     below already did.
 *   - PDF: BridgeAPI.startTranslatePdf({ inputPath, outputPath, source,
 *     target, protectTerms }) — camelCase, matches ui/bridge.py directly.
 * Both share one useOperation('translate') instance (same toolKey, so
 * progress/done routing works for either) — `lastMode` tracks which flow
 * is in flight so the result/progress UI knows how to interpret
 * op.result.results, since the two flows return different shapes.
 * translate_pdf's result ({output, outputSize, pages, source, target,
 * files, output_dir}, verified in pdf_translate.py) is one of the pages
 * vanilla already reads correctly via data.results.
 */
export function TranslatePage() {
  const toast = useToast();
  const [status, setStatus] = useState<TranslationStatus | null>(null);
  const [source, setSource] = useState('auto');
  const [target, setTarget] = useState('en');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>('pdf');
  const [lastMode, setLastMode] = useState<TranslateMode | null>(null);
  // Argos loads the target language model into memory on its first use in
  // the process — that first call has a real, noticeable delay beyond
  // normal translation time. Tracked client-side (no backend signal for
  // "model is loading" vs. "translating") so the loading copy can say
  // something more honest than a generic spinner for that first run.
  const [modelWarmedUp, setModelWarmedUp] = useState(false);
  // Names/places the built-in heuristics in pdf_translate.py's
  // translate_line() wouldn't otherwise catch (see FIX B there) — kept
  // untranslated on top of emails/URLs/"City, ST"/phone numbers/acronyms.
  const [protectTermsInput, setProtectTermsInput] = useState('');
  const protectTerms = useMemo(
    () =>
      protectTermsInput
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean),
    [protectTermsInput]
  );
  const op = useOperation<TranslateResult>('translate');
  // Mount-time provisioning check, off the UI thread: translation_status()'s
  // first call in the process imports argostranslate (pulls in ctranslate2
  // and friends), which can take several seconds cold. Running it through
  // the synchronous getTranslationStatus() slot froze the whole window on
  // Translate-page mount, before any translation action -- this async
  // counterpart (startGetTranslationStatus, toolKey "translationStatus")
  // keeps that cost off the UI thread, same pattern as the translate
  // actions themselves.
  const statusOp = useOperation<TranslationStatus>('translationStatus');
  // One-time translation setup (startSetupTranslation): frozen builds
  // download the pinned ML runtime first, then the chosen Argos language
  // packs; source checkouts skip straight to the packs. The worker's done
  // payload carries a fresh translation_status(), so no second status
  // round-trip is needed after setup.
  const setupOp = useOperation<SetupResult>('translateSetup');
  const [setupCodes, setSetupCodes] = useState<string[]>([]);
  const [setupOpen, setSetupOpen] = useState(false);

  // -- Workspace (persistent working document) -----------------------------
  // See WatermarkPage.tsx for the reference pattern this mirrors. A
  // workspace document is always a PDF, so only the PDF flow (never the
  // image auto-translate flow) applies. The output-format choice decides
  // whether the run advances the workspace pointer (PDF output) or is a
  // terminal side-output (docx/txt) — see workspacePdfMode below.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    statusOp.run(() => bridgeApi.startGetTranslationStatus({ toolKey: 'translationStatus' }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (statusOp.status === 'done' && statusOp.result?.results) {
      const res = statusOp.result.results;
      setStatus(res);
      setTarget(defaultTarget(res.languages ?? []));
    } else if (statusOp.status === 'error') {
      toast.error(statusOp.error || 'Could not check translation setup.');
    }
  }, [statusOp.status, statusOp.result, statusOp.error, toast]);

  useEffect(() => {
    if (setupOp.status === 'done' && setupOp.result?.results) {
      const res = setupOp.result.results;
      if (res.status) {
        setStatus(res.status);
        // If the target still sits on the degenerate 'en' default, move
        // it onto one of the packs that setup just installed; never stomp
        // a language the user already picked deliberately.
        const langs = res.status.languages ?? [];
        setTarget((cur) => (cur === 'en' ? defaultTarget(langs) : cur));
      }
      const n = res.installed ?? 0;
      toast.success(
        res.runtimeInstalled
          ? `Translation engine installed · ${n} language pack(s) added.`
          : `${n} language pack(s) added.`
      );
      if ((res.skipped ?? 0) > 0) toast.info(`${res.skipped} pack(s) could not be installed.`);
      setSetupCodes([]);
      setSetupOpen(false);
    } else if (setupOp.status === 'error') {
      toast.error(setupOp.error || 'Translation setup failed.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setupOp.status]);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      setModelWarmedUp(true);
      if (lastMode === 'pdf') {
        const { pages, note, output } = op.result.results as PdfTranslateResult;
        if (workspaceRunRef.current) {
          workspaceRunRef.current = false;
          // pdf_translate.py can redirect certain scripts (ar/hi/bn) from
          // PDF to .docx output regardless of the requested format — only
          // advance the workspace pointer if the actual output is a PDF,
          // never point the working document at a non-PDF file.
          if (output && output.toLowerCase().endsWith('.pdf')) {
            workspace.applyResult(output, `Translate (${source} → ${target})`);
            toast.success(`Translated ${pages} page${pages === 1 ? '' : 's'} — working document updated.`);
          } else {
            toast.success(`Translated ${pages} page${pages === 1 ? '' : 's'}.`);
            toast.info('Output was not a PDF, so the working document was left unchanged.');
          }
          if (note) toast.info(note);
          return;
        }
        toast.success(`Translated ${pages} page${pages === 1 ? '' : 's'}.`);
        // e.g. ar/hi/bn redirected from PDF to .docx — see pdf_translate.py.
        if (note) toast.info(note);
      } else if (lastMode === 'image') {
        const { note } = op.result.results as ImageTranslateResult;
        if (note) toast.info(note);
      }
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'Translation failed.');
      setModelWarmedUp(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, lastMode, toast]);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;
  const workspacePdfMode = !!workspace.path && outputFormat === 'pdf';

  const runImage = (path: string, src: string, tgt: string, terms: string[]) => {
    setLastMode('image');
    op.run(() =>
      bridgeApi.startTranslateImage({
        toolKey: 'translate',
        path,
        source: src,
        target: tgt,
        protectTerms: terms,
      })
    );
  };

  useEffect(() => {
    if (!file) {
      setOutputPath(null);
      op.reset();
      return;
    }
    if (isImage(file.path)) {
      runImage(file.path, source, target, protectTerms);
    } else if (isPdf(file.path)) {
      op.reset();
    } else {
      toast.warning('Unsupported file type.');
    }
    // Deliberately keyed on `file` only — re-runs when a new file is
    // picked, not on every source/target/protectTerms change (that's
    // onSourceChange/onTargetChange below, matching vanilla's separate
    // _onLangChange path; protectTerms edits apply on the next run).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);

  // Re-run image translation when languages change, matching vanilla's
  // _onLangChange behavior.
  const onSourceChange = (v: string) => {
    setSource(v);
    if (file && isImage(file.path) && op.status !== 'running') runImage(file.path, v, target, protectTerms);
  };
  const onTargetChange = (v: string) => {
    setTarget(v);
    if (file && isImage(file.path) && op.status !== 'running') runImage(file.path, source, v, protectTerms);
  };

  const pickOutput = async () => {
    const base = effectivePath ? bridgeApi.basename(effectivePath).replace(/\.[^.]+$/, '') : 'document';
    const { filter, ext } = FORMAT_OPTIONS[outputFormat];
    const path = await bridgeApi.saveFile(filter, `${base}_translated${ext}`);
    if (path) setOutputPath(path);
  };

  const onFormatChange = (v: string) => {
    setOutputFormat(v as OutputFormat);
    // The backend picks output shape from outputPath's extension (see
    // translate_pdf) — clear any already-chosen path so a stale extension
    // from a previous format choice can't silently mismatch the new one.
    setOutputPath(null);
  };

  const runPdf = () => {
    if (!effectivePath) {
      toast.warning('Add a file first.');
      return;
    }
    setLastMode('pdf');

    if (workspacePdfMode) {
      const wsPath = effectivePath;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startTranslatePdf({
          toolKey: 'translate',
          inputPath: wsPath,
          outputPath: workspaceOutputPath(wsDir, wsPath, opIndex),
          source,
          target,
          protectTerms,
        });
      });
      return;
    }

    if (!outputPath) {
      toast.warning('Choose an output location.');
      return;
    }
    op.run(() =>
      bridgeApi.startTranslatePdf({
        toolKey: 'translate',
        inputPath: effectivePath,
        outputPath,
        source,
        target,
        protectTerms,
      })
    );
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success('Copied.');
    } catch {
      toast.info('Select and copy manually.');
    }
  };

  const running = op.status === 'running';
  const ready = !!status?.argosAvailable && (status?.argosPairs.length ?? 0) > 0;
  const runtime = status?.runtime;
  const needsEngine = !!runtime?.needed && !runtime.installed;
  const setupRunning = setupOp.status === 'running';
  const missingLangs = (status?.languages ?? []).filter(
    (l) => l.code !== 'en' && !(l.translateTo && l.translateFrom)
  );
  const toggleSetupCode = (code: string, on: boolean) =>
    setSetupCodes((cur) => (on ? [...cur, code] : cur.filter((c) => c !== code)));
  const runSetup = () =>
    setupOp.run(() => bridgeApi.startSetupTranslation({ toolKey: 'translateSetup', codes: setupCodes }));
  const r = op.status === 'done' ? op.result?.results : null;
  const pdfResult = r && lastMode === 'pdf' ? (r as PdfTranslateResult) : null;
  const imageResult = r && lastMode === 'image' ? (r as ImageTranslateResult) : null;
  const busyLabel = modelWarmedUp ? 'Translating…' : 'Loading language model…';

  return (
    <div className="console">
      <PageHeader title="Translate" subtitle="Offline translation of documents and image text" backButton={false} />

      {!status ? (
        <Card>
          <div
            style={{
              textAlign: 'center',
              color: 'var(--text-1)',
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
              padding: '4px 0',
            }}
          >
            <span className="spinner" style={{ width: 18, height: 18, borderWidth: 3 }} />
            Checking translation setup…
          </div>
        </Card>
      ) : (
        <>
          <div
            className="panel"
            style={{
              borderLeftWidth: 3,
              borderLeftStyle: 'solid',
              borderLeftColor: ready ? 'var(--sev-info)' : 'var(--sev-medium)',
              fontSize: 'var(--font-size-sm)',
              color: 'var(--text-2)',
              marginBottom: 'var(--space-3)',
            }}
          >
            {ready ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
                <span style={{ flex: 1 }}>
                  Offline translation ready · {status.languages.filter((l) => l.translateTo).length} language(s)
                  installed
                  {status.ocrAvailable ? ' · OCR available' : ' · OCR not installed'}
                </span>
                {missingLangs.length > 0 && !setupOpen && (
                  <button className="btn-ghost" onClick={() => setSetupOpen(true)} disabled={setupRunning}>
                    Add languages…
                  </button>
                )}
              </div>
            ) : (
              <>
                Translation isn't set up on this machine yet — pick the languages you need below. It's a
                one-time download; afterwards translation runs fully offline.
              </>
            )}
          </div>

          {(!ready || setupOpen) && (
            <Card style={{ marginBottom: 'var(--space-3)' }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', marginBottom: 6 }}>
                Set up offline translation
              </div>
              <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
                Each language installs the English ↔ language model pair (other combinations pivot through
                English automatically).
                {needsEngine
                  ? ` The first setup also downloads the translation engine (~${runtime?.downloadSizeMB ?? 0} MB).`
                  : ''}
              </div>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                  gap: 2,
                  marginBottom: 'var(--space-3)',
                }}
              >
                {(status.languages ?? [])
                  .filter((l) => l.code !== 'en')
                  .map((l) =>
                    l.translateTo && l.translateFrom ? (
                      <div
                        key={l.code}
                        style={{ padding: '6px 0', fontSize: 'var(--font-size-sm)', color: 'var(--text-3)' }}
                      >
                        ✓ {l.name} — installed
                      </div>
                    ) : (
                      <Checkbox
                        key={l.code}
                        checked={setupCodes.includes(l.code)}
                        onChange={(on) => toggleSetupCode(l.code, on)}
                        label={`${l.name}${l.native && l.native !== l.name ? ` (${l.native})` : ''}`}
                      />
                    )
                  )}
              </div>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <button
                  className="btn-primary"
                  onClick={runSetup}
                  disabled={setupRunning || setupCodes.length === 0}
                >
                  {setupRunning ? 'Setting up…' : 'Download & set up'}
                </button>
                {ready && setupOpen && (
                  <button className="btn-ghost" onClick={() => setSetupOpen(false)} disabled={setupRunning}>
                    Close
                  </button>
                )}
              </div>
            </Card>
          )}

          {setupRunning && (
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <ProgressPanel
                pct={setupOp.progress?.pct ?? 0}
                current={setupOp.progress?.current}
                total={setupOp.progress?.total}
                filename={setupOp.progress?.filename}
                onCancel={setupOp.cancel}
              />
            </div>
          )}

      <Card>
        <div style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>From</div>
            <Select
              value={source}
              onChange={onSourceChange}
              disabled={running}
              options={[
                { value: 'auto', label: 'Auto-detect' },
                ...(status?.languages ?? []).map((l) => ({
                  value: l.code,
                  label: `${l.name}${l.translateFrom === false ? ' — not installed' : ''}`,
                })),
              ]}
            />
          </div>
          <div style={{ paddingBottom: 10, color: 'var(--text-3)' }}>→</div>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>To</div>
            <Select
              value={target}
              onChange={onTargetChange}
              disabled={running}
              options={(status?.languages ?? []).map((l) => ({
                value: l.code,
                label: `${l.name}${l.translateTo === false ? ' — not installed' : ''}`,
              }))}
            />
          </div>
        </div>
      </Card>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>
            Keep these words untranslated
          </div>
          <TextInput
            value={protectTermsInput}
            onChange={setProtectTermsInput}
            disabled={running}
            placeholder="Comma-separated names or places, e.g. Aria Nakamura, Zephyr"
          />
          <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
            Emails, URLs, phone numbers, "City, ST", and acronyms are already protected automatically — add names or
            places the translator gets wrong.
          </div>
        </Card>
      </div>

      {workspace.path ? (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
              Operating on the workspace document ({workspace.originalName}) — see the bar above to
              Preview, Export, or Clear it.
            </div>
          </Card>
        </div>
      ) : (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <DropZone
            files={files}
            onFilesChanged={setFiles}
            multiple={false}
            title="Drop a PDF or an image"
            subtitle="PDF, PNG, JPG, TIFF…"
            accept="PDF & Images (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp *.gif)"
            disabled={running}
          />
        </div>
      )}

      {(workspace.path || (file && isPdf(file.path))) && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>Output format:</span>
              <div style={{ width: 220 }}>
                <Select
                  value={outputFormat}
                  onChange={onFormatChange}
                  disabled={running}
                  options={(Object.keys(FORMAT_OPTIONS) as OutputFormat[]).map((key) => ({
                    value: key,
                    label: FORMAT_OPTIONS[key].label,
                  }))}
                />
              </div>
            </div>
            {workspacePdfMode ? (
              <div style={{ marginTop: 'var(--space-3)', color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
                PDF output updates the workspace working document directly — no output location needed.
              </div>
            ) : (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-3)',
                  flexWrap: 'wrap',
                  marginTop: 'var(--space-3)',
                }}
              >
                <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>Save translation as:</span>
                <span className="mono" style={{ flex: 1, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
                  {outputPath ? bridgeApi.basename(outputPath) : 'No output file selected'}
                </span>
                <button onClick={pickOutput} disabled={running} className="btn-ghost">
                  Output…
                </button>
              </div>
            )}
            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                marginTop: 'var(--space-3)',
              }}
            >
              <button onClick={runPdf} disabled={running || (!workspacePdfMode && !outputPath)} className="btn-primary">
                {running && lastMode === 'pdf' ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                    <span className="spinner" />
                    {busyLabel}
                  </span>
                ) : (
                  'Translate'
                )}
              </button>
            </div>
            <div style={{ marginTop: 8, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {outputFormat === 'pdf'
                ? 'PDF output keeps the original images and approximate page layout; very dense text blocks may shrink to fit. Arabic, Hindi, and Bengali targets are saved as Word instead — PDF text placement can’t shape those scripts correctly.'
                : 'Text/Word output renders every script correctly using your system fonts.'}
            </div>
          </Card>
        </div>
      )}

      {running && lastMode === 'pdf' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            current={op.progress?.current}
            total={op.progress?.total}
            filename={op.progress?.filename ?? busyLabel}
            onCancel={() => {
              bridgeApi.cancel('translate');
              op.reset();
              toast.info('Translation cancelled.');
            }}
          />
        </div>
      )}

      {running && lastMode === 'image' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card
            style={{
              borderLeftWidth: 3,
              borderLeftStyle: 'solid',
              borderLeftColor: 'var(--sev-info)',
            }}
          >
            <div
              style={{
                textAlign: 'center',
                color: 'var(--text-1)',
                fontWeight: 600,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 10,
                padding: '4px 0',
              }}
            >
              <span className="spinner" style={{ width: 18, height: 18, borderWidth: 3 }} />
              {modelWarmedUp ? 'Reading & translating…' : 'Loading language model…'}
            </div>
          </Card>
        </div>
      )}

      {imageResult && !running && (
        <div style={{ display: 'flex', gap: 'var(--space-4)', marginTop: 'var(--space-4)', flexWrap: 'wrap' }}>
          <TextPanel label={`Detected text (${imageResult.source ?? '?'})`} text={imageResult.sourceText ?? ''} onCopy={copy} />
          <TextPanel label={`Translation (${imageResult.target ?? '?'})`} text={imageResult.translatedText ?? ''} onCopy={copy} />
        </div>
      )}

      {pdfResult && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span style={{ flex: 1, fontSize: 'var(--font-size-sm)' }}>
                Saved <strong className="mono">{bridgeApi.basename(pdfResult.output)}</strong> ({pdfResult.source} →{' '}
                {pdfResult.target})
              </span>
              <button onClick={() => bridgeApi.openFilePath(pdfResult.output)} className="btn-ghost">
                Open
              </button>
            </div>
          </Card>
        </div>
      )}
        </>
      )}
    </div>
  );
}

function TextPanel({ label, text, onCopy }: { label: string; text: string; onCopy: (t: string) => void }) {
  return (
    <div style={{ flex: 1, minWidth: 240 }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>{label}</span>
        <button onClick={() => onCopy(text)} className="btn-ghost" style={{ marginLeft: 'auto' }}>
          Copy
        </button>
      </div>
      <div
        className="panel"
        style={{ whiteSpace: 'pre-wrap', maxHeight: 320, overflow: 'auto', fontSize: 'var(--font-size-sm)' }}
      >
        {text}
      </div>
    </div>
  );
}
