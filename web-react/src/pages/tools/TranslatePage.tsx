import { useEffect, useMemo, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface Language {
  code: string;
  name: string;
  native?: string;
  translateTo?: boolean;
  translateFrom?: boolean;
}
interface TranslationStatus {
  argosAvailable: boolean;
  ocrAvailable: boolean;
  argosPairs: string[];
  languages: Language[];
}
interface ImageResult {
  success: boolean;
  sourceText?: string;
  translatedText?: string;
  source?: string;
  target?: string;
  note?: string;
  error?: string;
}
interface PdfTranslateResult {
  output: string;
  outputSize: number;
  pages: number;
  source: string;
  target: string;
  note?: string;
}

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

/**
 * React port of web/js/pages/translate.js. Two bridge flows, both
 * preserved exactly:
 *   - Image (sync): BridgeAPI.translateImage(path, source, target).
 *   - PDF (async): BridgeAPI.startTranslatePdf({ inputPath, outputPath,
 *     source, target }) — camelCase, matches ui/bridge.py directly.
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
  const [imageResult, setImageResult] = useState<ImageResult | null>(null);
  const [imageBusy, setImageBusy] = useState(false);
  const [outputFormat, setOutputFormat] = useState<OutputFormat>('pdf');
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
  const op = useOperation<PdfTranslateResult>('translate');

  usePageBusy(op.status === 'running' || imageBusy);

  useEffect(() => {
    bridgeApi.getTranslationStatus().then((res) => {
      setStatus(res as unknown as TranslationStatus);
      const en = (res as unknown as TranslationStatus).languages?.find((l) => l.code === 'en');
      if (en) setTarget('en');
    });
  }, []);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const { pages, note } = op.result.results;
      toast.success(`Translated ${pages} page${pages === 1 ? '' : 's'}.`);
      // e.g. ar/hi/bn redirected from PDF to .docx — see pdf_translate.py.
      if (note) toast.info(note);
      setModelWarmedUp(true);
    } else if (op.status === 'error') {
      toast.error(op.error || 'Translation failed.');
      setModelWarmedUp(true);
    }
  }, [op.status, op.result, op.error, toast]);

  const file = files[0] ?? null;

  const runImage = async (path: string, src: string, tgt: string, terms: string[]) => {
    setImageBusy(true);
    setImageResult(null);
    try {
      const res = (await bridgeApi.translateImage(path, src, tgt, terms)) as unknown as ImageResult;
      if (!res.success) {
        toast.error(res.error || 'Translation failed.');
      } else {
        if (res.note) toast.info(res.note);
        setImageResult(res);
      }
    } catch {
      toast.error('Could not translate the image.');
    } finally {
      setImageBusy(false);
      setModelWarmedUp(true);
    }
  };

  useEffect(() => {
    if (!file) {
      setOutputPath(null);
      setImageResult(null);
      return;
    }
    if (isImage(file.path)) {
      void runImage(file.path, source, target, protectTerms);
    } else if (isPdf(file.path)) {
      setImageResult(null);
    } else {
      toast.warning('Unsupported file type.');
    }
    // Deliberately keyed on `file` only — re-runs when a new file is
    // picked, not on every source/target/protectTerms change (that's
    // onSourceChange/onTargetChange below, matching vanilla's separate
    // _onLangChange path; protectTerms edits apply on the next run).
  }, [file]);

  // Re-run image translation when languages change, matching vanilla's
  // _onLangChange behavior.
  const onSourceChange = (v: string) => {
    setSource(v);
    if (file && isImage(file.path) && !imageBusy) void runImage(file.path, v, target, protectTerms);
  };
  const onTargetChange = (v: string) => {
    setTarget(v);
    if (file && isImage(file.path) && !imageBusy) void runImage(file.path, source, v, protectTerms);
  };

  const pickOutput = async () => {
    const base = file ? bridgeApi.basename(file.path).replace(/\.[^.]+$/, '') : 'document';
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
    if (!file) {
      toast.warning('Add a file first.');
      return;
    }
    if (!outputPath) {
      toast.warning('Choose an output location.');
      return;
    }
    op.run(() =>
      bridgeApi.startTranslatePdf({
        toolKey: 'translate',
        inputPath: file.path,
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

  const ready = !!status?.argosAvailable && (status?.argosPairs.length ?? 0) > 0;
  const r = op.status === 'done' ? op.result?.results : null;

  return (
    <div className="console">
      <PageHeader title="Translate" subtitle="Offline translation of documents and image text" backButton={false} />

      {status && (
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
            <>
              Offline translation ready · {status.languages.filter((l) => l.translateTo).length} language(s) installed
              {status.ocrAvailable ? ' · OCR available' : ' · OCR not installed'}
            </>
          ) : (
            <>
              Translation models are not installed yet. Run{' '}
              <code className="mono">python setup_translation.py --install all</code> once (the only online step),
              then translation works fully offline.
            </>
          )}
        </div>
      )}

      <Card>
        <div style={{ display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>From</div>
            <Select
              value={source}
              onChange={onSourceChange}
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
            placeholder="Comma-separated names or places, e.g. Aria Nakamura, Zephyr"
          />
          <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
            Emails, URLs, phone numbers, "City, ST", and acronyms are already protected automatically — add names or
            places the translator gets wrong.
          </div>
        </Card>
      </div>

      <div style={{ marginTop: 'var(--space-4)' }}>
        <DropZone
          files={files}
          onFilesChanged={setFiles}
          multiple={false}
          title="Drop a PDF or an image"
          subtitle="PDF, PNG, JPG, TIFF…"
          accept="PDF & Images (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp *.gif)"
          disabled={op.status === 'running' || imageBusy}
        />
      </div>

      {file && isPdf(file.path) && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)', flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>Output format:</span>
              <div style={{ width: 220 }}>
                <Select
                  value={outputFormat}
                  onChange={onFormatChange}
                  options={(Object.keys(FORMAT_OPTIONS) as OutputFormat[]).map((key) => ({
                    value: key,
                    label: FORMAT_OPTIONS[key].label,
                  }))}
                />
              </div>
            </div>
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
              <button onClick={pickOutput} disabled={op.status === 'running'} className="btn-ghost">
                Output…
              </button>
              <button onClick={runPdf} disabled={op.status === 'running' || !outputPath} className="btn-primary">
                {op.status === 'running' ? (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                    <span className="spinner" />
                    {modelWarmedUp ? 'Translating…' : 'Loading language model…'}
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

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            current={op.progress?.current}
            total={op.progress?.total}
            filename={op.progress?.filename ?? 'Translating…'}
            onCancel={() => {
              bridgeApi.cancel('translate');
              op.reset();
              toast.info('Translation cancelled.');
            }}
          />
        </div>
      )}

      {imageBusy && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div
              style={{
                textAlign: 'center',
                color: 'var(--text-2)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
              }}
            >
              <span className="spinner" />
              {modelWarmedUp ? 'Reading & translating…' : 'Loading language model…'}
            </div>
          </Card>
        </div>
      )}

      {imageResult && !imageBusy && (
        <div style={{ display: 'flex', gap: 'var(--space-4)', marginTop: 'var(--space-4)', flexWrap: 'wrap' }}>
          <TextPanel label={`Detected text (${imageResult.source ?? '?'})`} text={imageResult.sourceText ?? ''} onCopy={copy} />
          <TextPanel label={`Translation (${imageResult.target ?? '?'})`} text={imageResult.translatedText ?? ''} onCopy={copy} />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span style={{ flex: 1, fontSize: 'var(--font-size-sm)' }}>
                Saved <strong className="mono">{bridgeApi.basename(r.output)}</strong> ({r.source} → {r.target})
              </span>
              <button onClick={() => bridgeApi.openFilePath(r.output)} className="btn-ghost">
                Open
              </button>
            </div>
          </Card>
        </div>
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
