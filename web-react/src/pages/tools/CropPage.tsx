import { useEffect, useRef, useState } from 'react';
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
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import { workspaceOutputPath } from '../../workspace/workspaceOutputPath';
import type { PickedFile } from '../../types/bridge';

/**
 * React port of web/js/pages/crop.js. Bridge call preserved exactly:
 * BridgeAPI.startCrop({ file, output_path, margins: {left,right,top,bottom},
 * unit }). Verified against ui/bridge.py: no progress callback (single-
 * shot), result is { outputPath }.
 */
export function CropPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [unit, setUnit] = useState('mm');
  const [left, setLeft] = useState('0');
  const [right, setRight] = useState('0');
  const [top, setTop] = useState('0');
  const [bottom, setBottom] = useState('0');
  const op = useOperation<{ outputPath: string }>('crop');

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
          workspace.applyResult(outPath, 'Crop');
          toast.success('Cropped the working document.');
        } else {
          toast.error('Crop failed — working document unchanged.');
        }
        return;
      }
      toast.success('Crop complete.');
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'An error occurred during cropping.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = (workspace.path ? true : !!file && !!outputPath) && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_cropped.pdf') : 'cropped.pdf';
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (path) setOutputPath(path);
  };

  const margins = {
    left: parseFloat(left) || 0,
    right: parseFloat(right) || 0,
    top: parseFloat(top) || 0,
    bottom: parseFloat(bottom) || 0,
  };

  const run = () => {
    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startCrop({
          file: wsPath,
          output_path: workspaceOutputPath(wsDir, wsPath, opIndex),
          margins,
          unit,
        });
      });
      return;
    }

    if (!file) {
      toast.warning('Please add a PDF file.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output location.');
      return;
    }
    op.run(() =>
      bridgeApi.startCrop({
        file: file.path,
        output_path: outputPath,
        margins,
        unit,
      })
    );
  };

  const results = op.status === 'done' && op.result && !workspace.path
    ? {
        files: [{ name: bridgeApi.basename(op.result.results?.outputPath ?? ''), status: 'done' as const }],
        outputDir: op.result.results?.outputPath ? bridgeApi.dirname(op.result.results.outputPath) : undefined,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Crop" subtitle="Crop page margins from a PDF" backButton={false} />

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
          title="Drop PDF file here"
          subtitle="or click to browse"
          disabled={op.status === 'running'}
        />
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
            Margins to crop
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 'var(--space-3)' }}>
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-2)' }}>Unit:</span>
            <div style={{ width: 160 }}>
              <Select
                value={unit}
                onChange={setUnit}
                options={[
                  { value: 'mm', label: 'Millimeters (mm)' },
                  { value: 'pt', label: 'Points (pt)' },
                  { value: 'inch', label: 'Inches (in)' },
                ]}
              />
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-3)' }}>
            <MarginField label="Left" value={left} onChange={setLeft} />
            <MarginField label="Right" value={right} onChange={setRight} />
            <MarginField label="Top" value={top} onChange={setTop} />
            <MarginField label="Bottom" value={bottom} onChange={setBottom} />
          </div>
        </Card>
      </div>

      {!workspace.path && (
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
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Cropping…' : 'Crop'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('crop');
              op.reset();
              toast.info('Crop cancelled.');
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

function MarginField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
      <span style={{ width: 56, flexShrink: 0, fontSize: 'var(--font-size-sm)', color: 'var(--text-2)' }}>{label}:</span>
      <TextInput type="number" value={value} onChange={onChange} />
    </div>
  );
}
