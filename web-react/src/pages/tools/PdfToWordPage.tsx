import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile } from '../../types/bridge';

interface PdfToWordResult {
  input_path: string;
  output_path: string;
  page_count: number;
}

/**
 * React port of web/js/pages/pdf-to-word.js. Bridge call preserved
 * exactly: BridgeAPI.startPdfToWord({ file, output_path }). Result shape
 * verified against pdf_ops.py's PdfToWordResult dataclass. Has a real
 * progress callback (per-page).
 */
export function PdfToWordPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const op = useOperation<PdfToWordResult>('pdf_to_word');

  // -- Workspace (persistent working document) -----------------------------
  // Terminal tool: reads the workspace document as input when loaded, but
  // its output is a normal side download — the workspace pointer is never
  // advanced (see the Step B spec's terminal-tool list).
  const workspace = useWorkspace();

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('PDF converted to Word successfully.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during PDF to Word conversion.');
    }
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;
  const canRun = !!effectivePath && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '.docx') : 'converted.docx';
    const path = await bridgeApi.saveFile('Word Documents (*.docx)', defaultName);
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (!effectivePath) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output file.');
      return;
    }
    op.run(() => bridgeApi.startPdfToWord({ file: effectivePath, output_path: outputPath }));
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
      <PageHeader title="PDF to Word" subtitle="Convert a PDF file to a Word document" backButton={false} />

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
              bridgeApi.cancel('pdf_to_word');
              op.reset();
              toast.info('Conversion cancelled.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} />
          {r && (
            <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {r.page_count} pages converted
            </div>
          )}
        </div>
      )}
    </div>
  );
}
