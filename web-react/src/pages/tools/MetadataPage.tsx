import { useEffect, useRef, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import { workspaceOutputPath } from '../../workspace/workspaceOutputPath';
import type { PickedFile } from '../../types/bridge';

const FIELDS: Array<{ key: 'title' | 'author' | 'subject' | 'keywords' | 'creator' | 'producer'; label: string }> = [
  { key: 'title', label: 'Title' },
  { key: 'author', label: 'Author' },
  { key: 'subject', label: 'Subject' },
  { key: 'keywords', label: 'Keywords' },
  { key: 'creator', label: 'Creator' },
  { key: 'producer', label: 'Producer' },
];

/**
 * React port of web/js/pages/metadata.js. SYNC: BridgeAPI.getMetadata(path)
 * on file select. ASYNC: BridgeAPI.startWriteMetadata({ inputPath,
 * outputPath, fields }) — note these ARE camelCase in the vanilla page,
 * matching ui/bridge.py's startWriteMetadata directly. Result is
 * { outputPath } (no progress callback, single-shot).
 */
export function MetadataPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({
    title: '',
    author: '',
    subject: '',
    keywords: '',
    creator: '',
    producer: '',
  });
  const op = useOperation<{ outputPath: string }>('metadata');

  // -- Workspace (persistent working document) -----------------------------
  // See WatermarkPage.tsx for the reference pattern this mirrors. Metadata
  // is (re)loaded from whichever file is in play, local or workspace.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;

  useEffect(() => {
    if (!effectivePath) return;
    bridgeApi.getMetadata(effectivePath).then((meta) => {
      setFields((prev) => {
        const next = { ...prev };
        for (const f of FIELDS) {
          const v = meta[f.key];
          if (v != null) next[f.key] = String(v);
        }
        return next;
      });
      toast.info('Metadata loaded from file.');
    });
    // Deliberately keyed on `effectivePath` only — toast is stable (see
    // Toast.tsx) so including it wouldn't change behavior, and
    // fields/setFields shouldn't retrigger a reload of the just-loaded
    // metadata.
  }, [effectivePath, toast]);

  useEffect(() => {
    if (op.status === 'done') {
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        const outPath = op.result?.results?.outputPath;
        if (outPath) {
          workspace.applyResult(outPath, 'Edit metadata');
          toast.success('Saved metadata to the working document.');
        } else {
          toast.error('Metadata save failed — working document unchanged.');
        }
        return;
      }
      toast.success('Metadata saved successfully.');
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'An error occurred while saving metadata.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.error, toast]);

  const canRun = !!effectivePath && (workspace.path ? true : !!outputPath) && op.status !== 'running';

  const pickOutput = async () => {
    const defaultName = file ? bridgeApi.basename(file.path).replace(/\.pdf$/i, '_metadata.pdf') : 'metadata.pdf';
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startWriteMetadata({
          inputPath: wsPath,
          outputPath: workspaceOutputPath(wsDir, wsPath, opIndex),
          fields,
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
    op.run(() => bridgeApi.startWriteMetadata({ inputPath: file.path, outputPath, fields }));
  };

  const r = op.status === 'done' && !workspace.path ? op.result?.results : null;

  return (
    <div className="console">
      <PageHeader title="Metadata" subtitle="View and edit PDF metadata" backButton={false} />

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

      {effectivePath && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', marginBottom: 'var(--space-4)' }}>
              Document metadata
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
              {FIELDS.map((f) => (
                <div key={f.key}>
                  <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>{f.label}</div>
                  <TextInput
                    value={fields[f.key]}
                    onChange={(v) => setFields((prev) => ({ ...prev, [f.key]: v }))}
                    placeholder={f.label}
                  />
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

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
          {op.status === 'running' ? 'Saving…' : 'Save'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('metadata');
              op.reset();
              toast.info('Metadata save cancelled.');
            }}
          />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span className="mono" style={{ flex: 1, fontSize: 'var(--font-size-sm)' }}>
                {bridgeApi.basename(r.outputPath)}
              </span>
              <button onClick={() => bridgeApi.openFolderPath(bridgeApi.dirname(r.outputPath))} className="btn-ghost">
                Open folder
              </button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
