import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Select, TextInput, Slider } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile } from '../../types/bridge';

interface PdfToImagesResult {
  input_path: string;
  output_dir: string;
  image_paths: string[];
  page_count: number;
  format: string;
}

/**
 * React port of web/js/pages/pdf-to-images.js. Bridge call preserved
 * exactly: BridgeAPI.startPdfToImages({ file, output_dir, format, dpi,
 * quality, page_range }). Result shape verified against pdf_ops.py's
 * PdfToImagesResult dataclass. Has a real progress callback (per-page).
 */
export function PdfToImagesPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputDir, setOutputDir] = useState<string | null>(null);
  const [format, setFormat] = useState('png');
  const [dpi, setDpi] = useState('150');
  const [quality, setQuality] = useState(85);
  const [pageRange, setPageRange] = useState('');
  const op = useOperation<PdfToImagesResult>('pdf_to_images');

  // -- Workspace (persistent working document) -----------------------------
  // Terminal tool: reads the workspace document as input when loaded, but
  // its output is a normal side download — the workspace pointer is never
  // advanced (see the Step B spec's terminal-tool list).
  const workspace = useWorkspace();

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('PDF converted to images successfully.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during PDF to image conversion.');
    }
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;
  const canRun = !!effectivePath && !!outputDir && op.status !== 'running';

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) setOutputDir(dir);
  };

  const run = () => {
    if (!effectivePath) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputDir) {
      toast.warning('Please choose an output folder.');
      return;
    }
    op.run(() =>
      bridgeApi.startPdfToImages({
        file: effectivePath,
        output_dir: outputDir,
        format,
        dpi: parseInt(dpi, 10) || 150,
        quality,
        page_range: pageRange.trim() || null,
      })
    );
  };

  const r = op.status === 'done' ? op.result?.results : null;
  const results = r
    ? {
        files: r.image_paths.map((p) => ({ name: bridgeApi.basename(p), status: 'done' as const })),
        outputDir: r.output_dir,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="PDF to Images" subtitle="Convert PDF pages to image files" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — this reads from it without
            changing it; see the bar above to Preview, Export, or Clear it.
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

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)' }}>
            <Field label="Output format">
              <Select
                value={format}
                onChange={setFormat}
                options={[
                  { value: 'png', label: 'PNG' },
                  { value: 'jpeg', label: 'JPEG' },
                ]}
              />
            </Field>
            <Field label="DPI">
              <TextInput type="number" value={dpi} onChange={setDpi} />
            </Field>
            {format === 'jpeg' && (
              <Field label="JPEG Quality">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Slider value={quality} min={1} max={100} onChange={setQuality} />
                  <span className="mono" style={{ width: 32, textAlign: 'right', fontSize: 'var(--font-size-sm)' }}>
                    {quality}
                  </span>
                </div>
              </Field>
            )}
          </div>
          <div style={{ marginTop: 'var(--space-4)' }}>
            <Field label="Page range (optional)">
              <TextInput value={pageRange} onChange={setPageRange} placeholder="e.g. 1-5, 8, 10-12 (blank = all pages)" />
            </Field>
          </div>
        </Card>
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <span className="mono" style={{ flex: 1, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
              {outputDir ?? 'No folder selected'}
            </span>
            <button onClick={pickOutputDir} disabled={op.status === 'running'} className="btn-ghost">
              Browse…
            </button>
          </div>
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Converting…' : 'Convert'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            current={op.progress?.current}
            total={op.progress?.total}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('pdf_to_images');
              op.reset();
              toast.info('Conversion cancelled.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} />
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  );
}
