import { useEffect, useRef, useState } from 'react';
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

/**
 * React port of web/js/pages/flatten.js. Bridge call preserved exactly:
 * BridgeAPI.startFlatten({ file, output_path, annotations, forms }).
 * No progress callback on the Python side (single-shot) — same
 * indeterminate-progress note as RepairPage.
 */
export function FlattenPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState(true);
  const [forms, setForms] = useState(true);
  const op = useOperation<{ outputPath: string }>('flatten');

  // -- Workspace (persistent working document) -----------------------------
  // See WatermarkPage.tsx for the reference pattern this mirrors.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    if (op.status === 'done') {
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        const outPath = op.result?.results?.outputPath;
        if (outPath) {
          workspace.applyResult(outPath, 'Flatten');
          toast.success('Flattened the working document.');
        } else {
          toast.error('Flatten failed — working document unchanged.');
        }
        return;
      }
      toast.success('PDF flattened successfully!');
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'Flatten failed.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = (workspace.path ? true : !!file && !!outputPath) && (annotations || forms) && op.status !== 'running';

  const pickOutput = async () => {
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', 'flattened.pdf');
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (!annotations && !forms) {
      toast.warning('Please select at least one flatten option.');
      return;
    }

    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startFlatten({
          file: wsPath,
          output_path: workspaceOutputPath(wsDir, wsPath, opIndex),
          annotations,
          forms,
        });
      });
      return;
    }

    if (!file) {
      toast.warning('Please add a PDF file first.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output file path.');
      return;
    }
    op.run(() => bridgeApi.startFlatten({ file: file.path, output_path: outputPath, annotations, forms }));
  };

  const results = op.status === 'done' && op.result && !workspace.path
    ? {
        files: [{ name: bridgeApi.basename(op.result.results?.outputPath ?? ''), status: 'done' as const }],
        outputDir: op.result.results?.outputPath ? bridgeApi.dirname(op.result.results.outputPath) : undefined,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Flatten PDF" subtitle="Remove interactive annotations and form fields" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — see the bar above to
            Preview, Export, or Clear it.
          </div>
        </Card>
      ) : (
        <DropZone
          files={files}
          onFilesChanged={setFiles}
          multiple={false}
          title="Drop a PDF file here"
          subtitle="or click to browse"
          disabled={op.status === 'running'}
        />
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>Flatten options</div>
          <Checkbox
            checked={annotations}
            onChange={setAnnotations}
            label={
              <div>
                Flatten annotations
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 2 }}>
                  Merge comments, highlights, stamps, and other annotations into the page content.
                </div>
              </div>
            }
          />
          <Checkbox
            checked={forms}
            onChange={setForms}
            label={
              <div>
                Flatten form fields
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 2 }}>
                  Convert interactive form fields into static text on the page.
                </div>
              </div>
            }
          />
        </Card>
      </div>

      {!workspace.path && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span className="mono" style={{ flex: 1, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
                {outputPath ? bridgeApi.basename(outputPath) : 'Choose output file path…'}
              </span>
              <button onClick={pickOutput} disabled={op.status === 'running'} className="btn-ghost">
                Browse
              </button>
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Flattening…' : 'Flatten'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('flatten');
              op.reset();
              toast.info('Flatten cancelled.');
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
