import { useEffect, useRef, useState } from 'react';
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
import { workspaceOutputPath } from '../../workspace/workspaceOutputPath';
import type { PickedFile } from '../../types/bridge';

/**
 * React port of web/js/pages/repair.js. Bridge call preserved exactly:
 * BridgeAPI.startRepair({ file, output_path }).
 *
 * repair.js's own progress/done handlers check `data.tool`/`data.percent`/
 * `data.files` — fields the real payload never sends (verified against
 * ui/bridge.py: the actual shape is toolKey/pct/results, and startRepair's
 * result is just { outputPath }). Those vanilla handlers silently never
 * fire. This page uses useOperation(), built against the verified real
 * payload shape, so it isn't affected by that bug — Python-side isn't
 * changed, this correctly consumes what it has always actually sent.
 * Also: startRepair has no progress callback on the Python side (single-
 * shot), so ProgressPanel shows an indeterminate "Working…" state rather
 * than a real percentage.
 */
export function RepairPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const op = useOperation<{ outputPath: string }>('repair');

  // -- Workspace (persistent working document) -----------------------------
  // See WatermarkPage.tsx for the reference pattern this mirrors.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  // useOperation sets status 'done' only on success and 'error' otherwise
  // (see useOperation.ts) — handle both rather than branching on
  // op.result.success within a single status check.
  useEffect(() => {
    if (op.status === 'done') {
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        const outPath = op.result?.results?.outputPath;
        if (outPath) {
          workspace.applyResult(outPath, 'Repair');
          toast.success('Repaired the working document.');
        } else {
          toast.error('Repair failed — working document unchanged.');
        }
        return;
      }
      toast.success('PDF repaired successfully!');
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'The file could not be repaired. It may be too severely damaged.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = (workspace.path ? true : !!file && !!outputPath) && op.status !== 'running';

  const pickOutput = async () => {
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', 'repaired.pdf');
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startRepair({ file: wsPath, output_path: workspaceOutputPath(wsDir, wsPath, opIndex) });
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
    op.run(() => bridgeApi.startRepair({ file: file.path, output_path: outputPath }));
  };

  const results = op.status === 'done' && op.result && !workspace.path
    ? {
        files: [{ name: bridgeApi.basename(op.result.results?.outputPath ?? ''), status: 'done' as const }],
        outputDir: op.result.results?.outputPath ? bridgeApi.dirname(op.result.results.outputPath) : undefined,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Repair PDF" subtitle="Attempt to repair a corrupted or damaged PDF file" backButton={false} />

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
            <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              The repaired PDF will be saved to a new file, leaving the original intact.
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Repairing…' : 'Repair'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('repair');
              op.reset();
              toast.info('Repair cancelled.');
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
