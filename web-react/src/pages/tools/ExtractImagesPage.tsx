import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface ExtractImagesResult {
  input_path: string;
  output_dir: string;
  image_paths: string[];
  image_count: number;
}

/**
 * React port of web/js/pages/extract-images.js. Bridge call preserved
 * exactly: BridgeAPI.startExtractImages({ file, output_dir, format,
 * min_size }). Result shape verified against pdf_ops.py's
 * ExtractImagesResult dataclass. Unlike Repair/Flatten/ExtractText, the
 * Python side DOES call a real progress callback per page (see
 * ui/bridge.py's startExtractImages), so ProgressPanel shows real percent.
 */
export function ExtractImagesPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputDir, setOutputDir] = useState<string | null>(null);
  const [format, setFormat] = useState('png');
  const [minSize, setMinSize] = useState('0');
  const op = useOperation<ExtractImagesResult>('extract_images');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      toast.success(`Extracted ${op.result.results.image_count} image(s) from PDF.`);
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during image extraction.');
    }
  }, [op.status, op.result, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = !!file && !!outputDir && op.status !== 'running';

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) setOutputDir(dir);
  };

  const run = () => {
    if (!file) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputDir) {
      toast.warning('Please choose an output folder.');
      return;
    }
    op.run(() =>
      bridgeApi.startExtractImages({
        file: file.path,
        output_dir: outputDir,
        format,
        min_size: parseInt(minSize, 10) || 0,
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
      <PageHeader title="Extract Images" subtitle="Extract all images from a PDF file" backButton={false} />

      <DropZone
        files={files}
        onFilesChanged={setFiles}
        multiple={false}
        title="Drop PDF file here"
        subtitle="or click to browse"
        disabled={op.status === 'running'}
      />

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
            <div>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Output format</div>
              <Select
                value={format}
                onChange={setFormat}
                options={[
                  { value: 'png', label: 'PNG' },
                  { value: 'jpeg', label: 'JPEG' },
                ]}
              />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>
                Minimum size (pixels)
              </div>
              <TextInput type="number" value={minSize} onChange={setMinSize} placeholder="0 (no filter)" />
              <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
                Skip images smaller than this width or height
              </div>
            </div>
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
          {op.status === 'running' ? 'Extracting…' : 'Extract'}
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
              bridgeApi.cancel('extract_images');
              op.reset();
              toast.info('Image extraction cancelled.');
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
