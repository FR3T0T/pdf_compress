import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { FileList } from '../../components/shared/FileList';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface ImagesToPdfResult {
  output_path: string;
  image_count: number;
  page_count: number;
}

/**
 * React port of web/js/pages/images-to-pdf.js. Bridge call preserved
 * exactly: BridgeAPI.startImagesToPdf({ imagePaths, outputPath, pageSize,
 * marginMm }) — note these are already camelCase in the vanilla page
 * (matches ui/bridge.py's startImagesToPdf directly, no normalization
 * shorthand needed). Result shape verified against pdf_ops.py's
 * ImagesToPdfResult dataclass.
 */
export function ImagesToPdfPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState('auto');
  const [margin, setMargin] = useState('10');
  const op = useOperation<ImagesToPdfResult>('images_to_pdf');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('PDF created from images successfully.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred while creating PDF from images.');
    }
  }, [op.status, op.error, toast]);

  const canRun = files.length > 0 && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', 'images.pdf');
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (files.length === 0) {
      toast.warning('Please add at least one image file.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output file.');
      return;
    }
    op.run(() =>
      bridgeApi.startImagesToPdf({
        imagePaths: files.map((f) => f.path),
        outputPath,
        pageSize,
        marginMm: parseInt(margin, 10) || 10,
      })
    );
  };

  const r = op.status === 'done' ? op.result?.results : null;
  const results = r
    ? {
        files: [{ name: bridgeApi.basename(r.output_path), status: 'done' as const }],
        outputDir: bridgeApi.dirname(r.output_path),
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Images to PDF" subtitle="Convert image files into a PDF document" backButton={false} />

      <DropZone
        files={files}
        onFilesChanged={setFiles}
        multiple
        compact={files.length > 0}
        title="Drop image files here"
        subtitle="or click to browse"
        accept="Images (*.png *.jpg *.jpeg *.tiff *.bmp *.gif)"
        disabled={op.status === 'running'}
      />

      <div style={{ marginTop: 8 }}>
        <FileList
          files={files}
          onRemove={(i) => setFiles((fs) => fs.filter((_, idx) => idx !== i))}
          onReorder={(from, to) =>
            setFiles((fs) => {
              const next = [...fs];
              const [moved] = next.splice(from, 1);
              next.splice(to, 0, moved);
              return next;
            })
          }
          emptyMessage="No images added yet."
        />
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
            <Field label="Page size">
              <Select
                value={pageSize}
                onChange={setPageSize}
                options={[
                  { value: 'auto', label: 'Auto (fit to image)' },
                  { value: 'A4', label: 'A4' },
                  { value: 'Letter', label: 'Letter' },
                ]}
              />
            </Field>
            <Field label="Margin (mm)">
              <TextInput type="number" value={margin} onChange={setMargin} />
            </Field>
          </div>
        </Card>
      </div>

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

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Creating…' : 'Create PDF'}
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
              bridgeApi.cancel('images_to_pdf');
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
