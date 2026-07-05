import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
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

/**
 * React port of web/js/pages/page-numbers.js. Bridge call: BridgeAPI.
 * startPageNumbers({ file, output_path, position, format, start_number,
 * font_size }).
 *
 * Bug fix vs. vanilla: page-numbers.js sends the format string under the
 * key "fmt", but ui/bridge.py's startPageNumbers reads p.get("format", ...)
 * — no snake/camel normalization maps "fmt" to "format", so the vanilla
 * page's format field is silently ignored and the default "{page}" is
 * always used. This sends "format" so the field actually works.
 */
export function PageNumbersPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [position, setPosition] = useState('bottom-center');
  const [format, setFormat] = useState('{page}');
  const [startNumber, setStartNumber] = useState('1');
  const [fontSize, setFontSize] = useState('10');
  const op = useOperation<{ outputPath: string }>('page_numbers');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('Page numbers added successfully.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred while adding page numbers.');
    }
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = !!file && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_numbered.pdf') : 'numbered.pdf';
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
      bridgeApi.startPageNumbers({
        file: file.path,
        output_path: outputPath,
        position,
        format: format.trim() || '{page}',
        start_number: parseInt(startNumber, 10) || 1,
        font_size: parseInt(fontSize, 10) || 10,
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
      <PageHeader title="Page Numbers" subtitle="Add page numbers to your PDF" backButton={false} />

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
            <Field label="Position">
              <Select
                value={position}
                onChange={setPosition}
                options={[
                  { value: 'bottom-center', label: 'Bottom Center' },
                  { value: 'bottom-left', label: 'Bottom Left' },
                  { value: 'bottom-right', label: 'Bottom Right' },
                  { value: 'top-center', label: 'Top Center' },
                  { value: 'top-left', label: 'Top Left' },
                  { value: 'top-right', label: 'Top Right' },
                ]}
              />
            </Field>
            <Field label="Number format" help="Use {page} for current page, {total} for total pages">
              <TextInput value={format} onChange={setFormat} placeholder="{page} / {total}" />
            </Field>
            <Field label="Start number">
              <TextInput type="number" value={startNumber} onChange={setStartNumber} />
            </Field>
            <Field label="Font size">
              <TextInput type="number" value={fontSize} onChange={setFontSize} />
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
          {op.status === 'running' ? 'Applying…' : 'Apply'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('page_numbers');
              op.reset();
              toast.info('Page numbering cancelled.');
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

function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>{label}</div>
      {children}
      {help && <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>{help}</div>}
    </div>
  );
}
