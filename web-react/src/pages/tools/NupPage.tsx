import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Select } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

/**
 * React port of web/js/pages/nup.js. Bridge call preserved exactly:
 * BridgeAPI.startNup({ file, output_path, pages_per_sheet, page_size,
 * orientation }). Verified against ui/bridge.py: no progress callback,
 * result is { outputPath }.
 */
export function NupPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [pagesPerSheet, setPagesPerSheet] = useState('4');
  const [pageSize, setPageSize] = useState('A4');
  const [orientation, setOrientation] = useState('landscape');
  const op = useOperation<{ outputPath: string }>('nup');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('N-up layout created successfully.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during N-up layout.');
    }
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = !!file && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_nup.pdf') : 'nup.pdf';
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (!file) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output location.');
      return;
    }
    op.run(() =>
      bridgeApi.startNup({
        file: file.path,
        output_path: outputPath,
        pages_per_sheet: parseInt(pagesPerSheet, 10),
        page_size: pageSize,
        orientation,
      })
    );
  };

  const results = op.status === 'done' && op.result
    ? {
        files: [{ name: bridgeApi.basename(op.result.results?.outputPath ?? ''), status: 'done' as const }],
        outputDir: op.result.results?.outputPath ? bridgeApi.dirname(op.result.results.outputPath) : undefined,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="N-Up Layout" subtitle="Place multiple pages per sheet" backButton={false} />

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
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)' }}>
            <Field label="Pages per sheet">
              <Select
                value={pagesPerSheet}
                onChange={setPagesPerSheet}
                options={[2, 4, 6, 9, 16].map((n) => ({ value: String(n), label: `${n} pages` }))}
              />
            </Field>
            <Field label="Page size">
              <Select
                value={pageSize}
                onChange={setPageSize}
                options={['A4', 'Letter', 'A3'].map((s) => ({ value: s, label: s }))}
              />
            </Field>
            <Field label="Orientation">
              <Select
                value={orientation}
                onChange={setOrientation}
                options={[
                  { value: 'portrait', label: 'Portrait' },
                  { value: 'landscape', label: 'Landscape' },
                ]}
              />
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
          {op.status === 'running' ? 'Creating…' : 'Create'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('nup');
              op.reset();
              toast.info('N-up cancelled.');
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
