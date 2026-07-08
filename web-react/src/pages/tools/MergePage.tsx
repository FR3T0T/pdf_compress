import { useEffect, useRef, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import type { DropZoneHandle } from '../../components/shared/DropZone';
import { useHotkeys } from '../../bridge/useHotkeys';
import { FileList } from '../../components/shared/FileList';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile } from '../../types/bridge';

interface MergeResult {
  output_path: string;
  input_paths: string[];
  total_pages: number;
}

/**
 * React port of web/js/pages/merge.js. Bridge call preserved exactly:
 * BridgeAPI.startMerge({ files, output_path }). This is one of the
 * "premium" pages that already used the correct toolKey/pct/results
 * fields — but its own result-reading (res.files/res.elapsed/
 * res.output_dir) still doesn't match the real MergeResult dataclass
 * ({output_path, input_paths, total_pages}), verified against
 * pdf_ops.py. Settings persistence (merge/outputDir, merge/outputName)
 * preserved via the same bridge keys. Keyboard shortcuts (Ctrl+O,
 * Ctrl+Enter, Esc) restored via the shared useHotkeys hook.
 */
export function MergePage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputDir, setOutputDir] = useState('');
  const [outputName, setOutputName] = useState('merged');
  const op = useOperation<MergeResult>('merge');
  // Paths we've already kicked off analyzeFile() for, so a fetch that fails
  // or returns no count isn't retried on every render. Pruned to the current
  // file set below, so removing then re-adding a file re-analyzes it.
  const analyzedPaths = useRef<Set<string>>(new Set());
  const dropRef = useRef<DropZoneHandle>(null);

  // -- Workspace (persistent working document) -----------------------------
  // Multi-input tool: the workspace document (if any) can be added as ONE
  // of the files to merge, alongside the normal multi-file flow — it's
  // never required and the workspace pointer is never advanced (merge's
  // output is a brand-new file, not a transform of a single document).
  const workspace = useWorkspace();
  const workspaceIncluded = !!workspace.path && files.some((f) => f.path === workspace.path);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && workspaceIncluded);

  const addWorkspaceFile = () => {
    if (!workspace.path || workspaceIncluded) return;
    setFiles((fs) => [...fs, { path: workspace.path!, name: workspace.originalName || bridgeApi.basename(workspace.path!) }]);
  };

  useEffect(() => {
    bridgeApi.loadSetting('merge/outputDir').then((v) => v && setOutputDir(v));
    bridgeApi.loadSetting('merge/outputName').then((v) => v && setOutputName(v));
  }, []);

  // Fetch each file's page count (and true size) as it's added, mirroring the
  // vanilla merge page's per-file info. Additive: FileList shows `pages` when
  // present. Field fallback matches vanilla (info.pages || info.page_count).
  useEffect(() => {
    const currentPaths = new Set(files.map((f) => f.path));
    for (const p of analyzedPaths.current) {
      if (!currentPaths.has(p)) analyzedPaths.current.delete(p);
    }

    const pending = files.filter((f) => f.pages === undefined && !analyzedPaths.current.has(f.path));
    if (pending.length === 0) return;

    let cancelled = false;
    for (const f of pending) {
      analyzedPaths.current.add(f.path);
      bridgeApi
        .analyzeFile(f.path)
        .then((info) => {
          if (cancelled || !info) return;
          const pages = (info.pages ?? info.page_count) as number | undefined;
          const size = (info.size ?? info.file_size) as number | undefined;
          if (pages === undefined && size === undefined) return;
          setFiles((fs) =>
            fs.map((x) =>
              x.path === f.path ? { ...x, pages: pages ?? x.pages, size: size ?? x.size } : x
            )
          );
        })
        .catch(() => {
          /* leave counts unset — the list still renders name/size */
        });
    }
    return () => {
      cancelled = true;
    };
  }, [files]);

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('PDF files merged successfully!');
    } else if (op.status === 'error') {
      toast.error(op.error || 'Merge failed.');
    }
  }, [op.status, op.error, toast]);

  const canRun = files.length >= 2 && op.status !== 'running';

  useHotkeys({
    onAddFiles: () => dropRef.current?.open(),
    onRun: () => canRun && run(),
    onClear: op.status === 'running' ? undefined : () => setFiles([]),
  });

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) {
      setOutputDir(dir);
      bridgeApi.saveSetting('merge/outputDir', dir);
    }
  };

  const run = () => {
    if (files.length < 2) {
      toast.warning('Please add at least 2 PDF files to merge.');
      return;
    }
    let name = outputName.trim() || 'merged';
    if (!/\.pdf$/i.test(name)) name += '.pdf';
    const dir = outputDir || bridgeApi.dirname(files[0].path);
    const outputPath = `${dir}\\${name}`;

    bridgeApi.saveSetting('merge/outputDir', outputDir);
    bridgeApi.saveSetting('merge/outputName', outputName);

    op.run(() => bridgeApi.startMerge({ files: files.map((f) => f.path), output_path: outputPath }));
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
      <PageHeader title="Merge PDFs" subtitle="Combine multiple PDF files into one document" backButton={false} />

      <DropZone
        ref={dropRef}
        files={files}
        onFilesChanged={setFiles}
        multiple
        compact={files.length > 0}
        title="Drop PDF files here"
        subtitle="or click to browse — add as many as you need"
        disabled={op.status === 'running'}
      />

      {workspace.path && !workspaceIncluded && (
        <div style={{ marginTop: 8 }}>
          <button onClick={addWorkspaceFile} disabled={op.status === 'running'} className="btn-ghost">
            Add workspace document ({workspace.originalName})
          </button>
        </div>
      )}

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
          emptyMessage="No PDF files added yet. Drop files above or click to browse."
        />
      </div>

      {files.length > 0 && (
        <div style={{ marginTop: 8, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
          {files.length} file{files.length === 1 ? '' : 's'} selected
        </div>
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
            Output
          </div>
          <div style={{ marginBottom: 'var(--space-3)' }}>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-2)', marginBottom: 6 }}>
              Output folder
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span
                className="mono"
                style={{
                  flex: 1,
                  color: 'var(--text-2)',
                  fontSize: 'var(--font-size-sm)',
                  padding: '7px 10px',
                  background: 'var(--panel-bg-elevated)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 'var(--radius-panel-sm)',
                }}
              >
                {outputDir || 'Same as first source file'}
              </span>
              <button onClick={pickOutputDir} disabled={op.status === 'running'} className="btn-ghost">
                Browse
              </button>
            </div>
          </div>
          <div>
            <div style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-2)', marginBottom: 6 }}>
              Output filename
            </div>
            <TextInput value={outputName} onChange={setOutputName} placeholder="merged" />
            <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
              The .pdf extension will be added automatically.
            </div>
          </div>
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Merging…' : 'Merge'}
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
              bridgeApi.cancel('merge');
              op.reset();
              toast.info('Merge cancelled.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} />
          {r && (
            <div style={{ marginTop: 6, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {r.total_pages} total pages from {r.input_paths.length} files
            </div>
          )}
        </div>
      )}
    </div>
  );
}
