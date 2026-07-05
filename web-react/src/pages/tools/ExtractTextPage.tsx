import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface ExtractTextResult {
  input_path: string;
  output_path: string;
  page_count: number;
  char_count: number;
}

/**
 * React port of web/js/pages/extract-text.js. Bridge call preserved
 * exactly: BridgeAPI.startExtractText({ file, output_path, page_range }).
 * Result shape verified against pdf_ops.py's ExtractTextResult dataclass
 * (input_path, output_path, page_count, char_count) — the vanilla page
 * reads data.page_count/data.char_count at the top level, which doesn't
 * match the real toolKey/success/message/results payload; this reads
 * them from op.result.results, where they actually are.
 */
export function ExtractTextPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [pageRange, setPageRange] = useState('');
  const op = useOperation<ExtractTextResult>('extract_text');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const { page_count, char_count } = op.result.results;
      toast.success(`Text extracted: ${char_count} characters from ${page_count} page(s).`);
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during text extraction.');
    }
  }, [op.status, op.result, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = !!file && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '.txt') : 'extracted.txt';
    const path = await bridgeApi.saveFile('Text Files (*.txt)', defaultName);
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (!file) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output file.');
      return;
    }
    op.run(() =>
      bridgeApi.startExtractText({
        file: file.path,
        output_path: outputPath,
        page_range: pageRange.trim() || null,
      })
    );
  };

  const r = op.status === 'done' ? op.result?.results : null;

  return (
    <div className="console">
      <PageHeader title="Extract Text" subtitle="Extract text content from a PDF" backButton={false} />

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
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>
            Page range (optional)
          </div>
          <TextInput value={pageRange} onChange={setPageRange} placeholder="e.g. 1-5, 8, 10-12 (blank = all pages)" />
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
          {op.status === 'running' ? 'Extracting…' : 'Extract'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('extract_text');
              op.reset();
              toast.info('Text extraction cancelled.');
            }}
          />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', gap: 'var(--space-5)' }}>
              <Stat label="Pages" value={r.page_count} />
              <Stat label="Characters" value={r.char_count} />
            </div>
            <div style={{ marginTop: 'var(--space-3)', textAlign: 'right' }}>
              <button onClick={() => bridgeApi.openFilePath(r.output_path)} className="btn-primary">
                Open output file
              </button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>{label}</div>
      <div className="mono" style={{ fontSize: 'var(--font-size-lg)', fontWeight: 700 }}>
        {value}
      </div>
    </div>
  );
}
