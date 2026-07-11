import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
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

interface FileResult {
  file: string;
  status: 'ok' | 'error';
  details?: string;
  outputPath?: string;
}
interface UnlockResult {
  files: FileResult[];
  elapsed: number;
  output_dir: string;
}
interface EpdfInfo {
  name: string;
  cipher?: string;
  kdf?: string;
}

/**
 * React port of web/js/pages/unlock.js. Bridge call preserved exactly:
 * BridgeAPI.startUnlock({ files, password, output_dir, naming }).
 * Verified against ui/bridge.py: result shape
 * { files: [{file,status,details,outputPath}], elapsed, output_dir }
 * matches vanilla's own reading exactly. .epdf detection via
 * BridgeAPI.checkEpdf(path), same as vanilla.
 */
export function UnlockPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [password, setPassword] = useState('');
  const [outputDir, setOutputDir] = useState('');
  const [naming, setNaming] = useState('{name}_unlocked');
  // Explicit single-file output path chosen up-front via "Choose location…".
  // Empty ⇒ default to the same folder as the input file.
  const [outputPath, setOutputPath] = useState('');
  const [epdfFiles, setEpdfFiles] = useState<EpdfInfo[]>([]);
  const op = useOperation<UnlockResult>('unlock');

  // -- Workspace (persistent working document) -----------------------------
  // Terminal tool: Unlock reads the workspace document as input when loaded,
  // but decryption is kept out of the running-result chain for symmetry with
  // Protect (crypto tools don't feed back into the workspace) — the unlocked
  // output is a normal side download to a real folder and the workspace
  // pointer is never advanced (see the Step B spec's terminal-tool list).
  const workspace = useWorkspace();

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const res = op.result.results;
      const nOk = res.files.filter((f) => f.status === 'ok').length;
      // Don't auto-open anything on completion — the results panel below shows
      // the output path with "Show in folder" / "Open folder" for opening it.
      if (nOk > 0) toast.success(`${nOk} file${nOk === 1 ? '' : 's'} unlocked successfully!`);
      res.files
        .filter((f) => f.status === 'error')
        .forEach((f) => toast.error(`${f.file}: ${f.details}`));
    } else if (op.status === 'error') {
      toast.error(op.error || 'Unlock failed.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, toast]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const epdf: EpdfInfo[] = [];
      for (const f of files) {
        if (f.path.toLowerCase().endsWith('.epdf')) {
          try {
            const info = await bridgeApi.checkEpdf(f.path);
            if (info.isEpdf) epdf.push({ name: f.name, cipher: info.cipher, kdf: info.kdf });
          } catch {
            // ignore — matches vanilla's console-only error handling
          }
        }
      }
      if (!cancelled) setEpdfFiles(epdf);
    })();
    return () => {
      cancelled = true;
    };
  }, [files]);

  const canRun = (workspace.path ? true : files.length > 0) && password.length > 0 && op.status !== 'running';

  // The single input this run will operate on, and the destination used if the
  // user doesn't pick one: the same folder as that file, "<name>_unlocked.pdf".
  const singleSrc = workspace.path ?? (files.length === 1 ? files[0].path : null);
  const defaultOutName = singleSrc
    ? `${bridgeApi.basename(singleSrc).replace(/\.(pdf|epdf)$/i, '')}_unlocked.pdf`
    : '';
  const defaultOutPath = singleSrc ? `${bridgeApi.dirname(singleSrc)}\\${defaultOutName}` : '';

  // A picked output path is only meaningful for the current input, so drop it
  // whenever the input file changes.
  useEffect(() => {
    setOutputPath('');
  }, [workspace.path, files]);

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) setOutputDir(dir);
  };

  // Let the user choose the exact output file up-front (before Unlock). If they
  // don't, `run` falls back to defaultOutPath (same folder as the input).
  const pickOutputFile = async () => {
    if (!singleSrc) return;
    const dest = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultOutName);
    if (dest) setOutputPath(dest);
  };

  const run = () => {
    if (!password) {
      toast.warning('Please enter the password.');
      return;
    }

    const inputPaths = workspace.path ? [workspace.path] : files.map((f) => f.path);
    if (inputPaths.length === 0) {
      toast.warning('Please add at least one file.');
      return;
    }

    // Single input: write to the location the user picked via "Choose
    // location…", or — if they didn't — default to the same folder as the
    // input file (passed explicitly so a workspace temp-file input doesn't
    // land its output in the hidden temp dir).
    if (inputPaths.length === 1) {
      const src = inputPaths[0];
      op.run(() =>
        bridgeApi.startUnlock(
          outputPath
            ? { files: [src], password, outputPath }
            : { files: [src], password, output_dir: bridgeApi.dirname(src), naming: naming.trim() || '{name}_unlocked' }
        )
      );
      return;
    }

    // Multiple files → output folder + naming template, as before.
    op.run(() =>
      bridgeApi.startUnlock({
        files: inputPaths,
        password,
        output_dir: outputDir,
        naming: naming.trim() || '{name}_unlocked',
      })
    );
  };

  const r = op.status === 'done' ? op.result?.results : null;
  const results = r
    ? {
        files: r.files.map((fr) => ({
          name: fr.file,
          status: fr.status === 'ok' ? ('done' as const) : ('error' as const),
          error: fr.status === 'error' ? fr.details : undefined,
          outputPath: fr.status === 'ok' ? fr.outputPath : undefined,
        })),
        totalTime: r.elapsed,
        outputDir: r.output_dir,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Unlock PDF" subtitle="Remove password protection from PDF or EPDF files" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — this reads from it without
            changing it; the unlocked copy is saved next to it by default, or pick a location in the
            Output section below. See the bar above to Preview, Export, or Clear the working document.
          </div>
        </Card>
      ) : (
        <>
          <DropZone
            files={files}
            onFilesChanged={setFiles}
            multiple
            compact={files.length > 0}
            title="Drop protected PDF or EPDF files here"
            subtitle="or click to browse"
            accept="PDF & EPDF files (*.pdf *.epdf)"
            disabled={op.status === 'running'}
          />
          <div style={{ marginTop: 8 }}>
            <FileList files={files} onRemove={(i) => setFiles((fs) => fs.filter((_, idx) => idx !== i))} />
          </div>
        </>
      )}

      {epdfFiles.length > 0 && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ borderLeft: '3px solid var(--sev-info)', paddingLeft: 'var(--space-3)' }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', color: 'var(--sev-info-text)', marginBottom: 6 }}>
                Enhanced encryption detected ({epdfFiles.length} .epdf file{epdfFiles.length === 1 ? '' : 's'})
              </div>
              {epdfFiles.slice(0, 5).map((ef) => (
                <div key={ef.name} className="mono" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)', marginTop: 2 }}>
                  {ef.name} — {ef.cipher} + {ef.kdf}
                </div>
              ))}
              {epdfFiles.length > 5 && (
                <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)', marginTop: 2 }}>
                  …and {epdfFiles.length - 5} more
                </div>
              )}
            </div>
          </Card>
        </div>
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Password</div>
          <TextInput type="password" value={password} onChange={setPassword} placeholder="Enter the file password" />
          <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
            Enter the password used to protect the file(s). For batch operations, all files must share the same
            password.
          </div>
        </Card>
      </div>

      {/* Single input: destination defaults to the same folder as the file;
          "Choose location…" lets the user override it before unlocking. */}
      {singleSrc && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Output file</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span
                className="mono"
                style={{
                  flex: 1,
                  minWidth: 0,
                  color: 'var(--text-2)',
                  fontSize: 'var(--font-size-sm)',
                  padding: '7px 10px',
                  background: 'var(--panel-bg-elevated)',
                  border: '1px solid var(--border-strong)',
                  borderRadius: 'var(--radius-panel-sm)',
                  wordBreak: 'break-all',
                }}
              >
                {outputPath || defaultOutPath}
              </span>
              <button onClick={pickOutputFile} disabled={op.status === 'running'} className="btn-ghost" style={{ flexShrink: 0 }}>
                Choose location…
              </button>
            </div>
            <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span>
                {outputPath
                  ? 'Custom output location.'
                  : 'Defaults to the same folder as the input file — choose a location to change it.'}
              </span>
              {outputPath && (
                <button
                  onClick={() => setOutputPath('')}
                  disabled={op.status === 'running'}
                  className="btn-ghost"
                  style={{ padding: '2px 6px', fontSize: 'var(--font-size-xs)', flexShrink: 0 }}
                >
                  Reset
                </button>
              )}
            </div>
          </Card>
        </div>
      )}

      {/* Multiple dropped files use an output folder + naming template. */}
      {!workspace.path && files.length > 1 && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Output folder (batch)</div>
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
                {outputDir || 'Same folder as input'}
              </span>
              <button onClick={pickOutputDir} disabled={op.status === 'running'} className="btn-ghost">
                Browse
              </button>
            </div>
            <div style={{ marginTop: 'var(--space-3)' }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Naming template</div>
              <TextInput value={naming} onChange={setNaming} placeholder="{name}_unlocked" />
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Unlocking…' : 'Unlock'}
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
              bridgeApi.cancel('unlock');
              op.reset();
              toast.info('Unlock cancelled.');
            }}
          />
        </div>
      )}

      {results && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ResultsPanel results={results} onRevealFile={(path) => bridgeApi.revealFile(path)} />
        </div>
      )}
    </div>
  );
}
