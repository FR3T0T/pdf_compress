import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { FileList } from '../../components/shared/FileList';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Checkbox, Select, TextInput } from '../../components/shared/formControls';
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
interface ProtectResult {
  files: FileResult[];
  elapsed: number;
  output_dir: string;
}

const CIPHERS = [
  { value: 'chacha20-poly1305', label: 'ChaCha20-Poly1305 (256-bit, AEAD)' },
  { value: 'aes-256-gcm', label: 'AES-256-GCM (AEAD)' },
  { value: 'camellia-256-cbc', label: 'Camellia-256-CBC + HMAC (encrypt-then-MAC)' },
];
const KDFS = [
  { value: 'argon2id', label: 'Argon2id (recommended — resists side-channel + GPU)' },
  { value: 'argon2d', label: 'Argon2d (faster — resists GPU attacks)' },
];

function passwordStrength(pw: string): { label: string; color: string; textColor: string; width: string } {
  if (!pw) return { label: '', color: '', textColor: '', width: '0%' };
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++;
  if (/[0-9]/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  // color is the progress-bar FILL (needs to read well as a saturated
  // block); textColor is the label text next to it (needs its own AA
  // contrast against the page background) — these diverge in light mode,
  // where the bright fill shades aren't dark enough to use as text.
  if (score <= 1) return { label: 'Weak', color: 'var(--sev-high)', textColor: 'var(--sev-high-text)', width: '25%' };
  if (score <= 2) return { label: 'Fair', color: 'var(--sev-medium)', textColor: 'var(--sev-medium-text)', width: '50%' };
  if (score <= 3) return { label: 'Moderate', color: 'var(--sev-medium)', textColor: 'var(--sev-medium-text)', width: '66%' };
  return { label: 'Strong', color: 'var(--sev-info)', textColor: 'var(--sev-info-text)', width: '100%' };
}

/**
 * React port of web/js/pages/protect.js. Bridge call preserved exactly:
 * BridgeAPI.startProtect({ files, user_password, owner_password, mode,
 * output_dir, naming, ...mode-specific }). Verified against
 * ui/bridge.py's startProtect — result shape
 * { files: [{file,status,details,outputPath}], elapsed, output_dir }
 * matches vanilla's own reading exactly (no bug here, unlike several
 * other pages). Settings persistence (protect/mode, protect/cipher,
 * protect/kdf, protect/naming) preserved.
 */
export function ProtectPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [mode, setMode] = useState<'standard' | 'enhanced'>('standard');
  const [encryption, setEncryption] = useState('AES-256');
  const [permPrint, setPermPrint] = useState(true);
  const [permModify, setPermModify] = useState(false);
  const [permCopy, setPermCopy] = useState(false);
  const [permAnnotate, setPermAnnotate] = useState(false);
  const [cipher, setCipher] = useState('chacha20-poly1305');
  const [kdf, setKdf] = useState('argon2id');
  const [userPassword, setUserPassword] = useState('');
  const [ownerPassword, setOwnerPassword] = useState('');
  const [outputDir, setOutputDir] = useState('');
  const [naming, setNaming] = useState('{name}_protected');
  // Explicit single-file output path chosen up-front via "Choose location…".
  // Empty ⇒ default to the same folder as the input file.
  const [outputPath, setOutputPath] = useState('');
  const op = useOperation<ProtectResult>('protect');

  // -- Workspace (persistent working document) -----------------------------
  // Terminal tool: Protect reads the workspace document as input when loaded,
  // but encryption produces a terminal artifact (an .epdf, or a password-
  // locked PDF) that no downstream workspace tool can preview, scan, or
  // transform — so the output is a normal side download to a real folder and
  // the workspace pointer is never advanced (see the Step B spec's
  // terminal-tool list).
  const workspace = useWorkspace();

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    (async () => {
      const m = await bridgeApi.loadSetting('protect/mode');
      if (m === 'standard' || m === 'enhanced') setMode(m);
      const c = await bridgeApi.loadSetting('protect/cipher');
      if (c) setCipher(c);
      const k = await bridgeApi.loadSetting('protect/kdf');
      if (k) setKdf(k);
      const n = await bridgeApi.loadSetting('protect/naming');
      if (n) setNaming(n);
    })();
  }, []);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const res = op.result.results;
      const nOk = res.files.filter((f) => f.status === 'ok').length;
      const nErr = res.files.length - nOk;
      // Don't auto-open anything on completion — the results panel below shows
      // the output path with "Show in folder" / "Open folder" for opening it.
      if (nOk > 0) toast.success(`${nOk} file${nOk === 1 ? '' : 's'} protected successfully!`);
      if (nErr > 0 && nOk === 0) toast.error('Protection failed for all files.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'Protection failed.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, toast]);

  const canRun = (workspace.path ? true : files.length > 0) && userPassword.length > 0 && op.status !== 'running';

  // The single input this run will operate on (the workspace doc, or the one
  // dropped file), and the destination that will be used if the user doesn't
  // pick one: the same folder as that file, named "<name>_protected.<ext>".
  const singleSrc = workspace.path ?? (files.length === 1 ? files[0].path : null);
  const outExt = mode === 'enhanced' ? '.epdf' : '.pdf';
  const defaultOutName = singleSrc
    ? `${bridgeApi.basename(singleSrc).replace(/\.(pdf|epdf)$/i, '')}_protected${outExt}`
    : '';
  const defaultOutPath = singleSrc ? `${bridgeApi.dirname(singleSrc)}\\${defaultOutName}` : '';

  // A picked output path is only meaningful for the current input/format, so
  // drop it whenever the input file or the encryption format changes.
  useEffect(() => {
    setOutputPath('');
  }, [workspace.path, files, mode]);

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) setOutputDir(dir);
  };

  // Let the user choose the exact output file up-front (before Protect). If
  // they don't, `run` falls back to defaultOutPath (same folder as the input).
  const pickOutputFile = async () => {
    if (!singleSrc) return;
    const filter = mode === 'enhanced' ? 'Encrypted PDF (*.epdf)' : 'PDF Files (*.pdf)';
    const dest = await bridgeApi.saveFile(filter, defaultOutName);
    if (dest) setOutputPath(dest);
  };

  const buildParams = (filePaths: string[], outDir: string, namingTemplate: string): Record<string, unknown> => {
    const params: Record<string, unknown> = {
      files: filePaths,
      user_password: userPassword,
      owner_password: ownerPassword,
      mode,
      output_dir: outDir,
      naming: namingTemplate,
    };
    if (mode === 'standard') {
      params.encryption = encryption;
      const permissions: string[] = [];
      if (permPrint) permissions.push('print');
      if (permModify) permissions.push('modify');
      if (permCopy) permissions.push('copy');
      if (permAnnotate) permissions.push('annotate');
      params.permissions = permissions;
    } else {
      params.cipher = cipher;
      params.kdf = kdf;
    }
    return params;
  };

  const run = () => {
    if (!userPassword) {
      toast.warning('Please enter a password.');
      return;
    }

    const inputPaths = workspace.path ? [workspace.path] : files.map((f) => f.path);
    if (inputPaths.length === 0) {
      toast.warning('Please add at least one PDF file.');
      return;
    }

    const trimmedNaming = naming.trim() || '{name}_protected';
    bridgeApi.saveSetting('protect/mode', mode);
    bridgeApi.saveSetting('protect/naming', trimmedNaming);
    if (mode === 'enhanced') {
      bridgeApi.saveSetting('protect/cipher', cipher);
      bridgeApi.saveSetting('protect/kdf', kdf);
    }

    // Single input (the workspace document, or one dropped file): write to the
    // location the user picked via "Choose location…", or — if they didn't —
    // default to the same folder as the input file. Passing output_dir
    // explicitly (rather than leaving it empty) keeps a workspace temp-file
    // input from landing its output in the hidden temp dir.
    if (inputPaths.length === 1) {
      const src = inputPaths[0];
      const params = outputPath
        ? { ...buildParams([src], '', trimmedNaming), outputPath }
        : buildParams([src], bridgeApi.dirname(src), trimmedNaming);
      op.run(() => bridgeApi.startProtect(params));
      return;
    }

    // Multiple files → output folder + naming template.
    op.run(() => bridgeApi.startProtect(buildParams(inputPaths, outputDir, trimmedNaming)));
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

  const userStr = passwordStrength(userPassword);
  const ownerStr = passwordStrength(ownerPassword);

  return (
    <div className="console">
      <PageHeader title="Protect PDF" subtitle="Add password protection with standard or enhanced encryption" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — this reads from it without
            changing it; the protected copy is saved next to it by default, or pick a location in the
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
            title="Drop PDF files here"
            subtitle="or click to browse"
            disabled={op.status === 'running'}
          />
          <div style={{ marginTop: 8 }}>
            <FileList files={files} onRemove={(i) => setFiles((fs) => fs.filter((_, idx) => idx !== i))} />
          </div>
        </>
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <SectionLabel>Encryption mode</SectionLabel>
          <Select
            value={mode}
            onChange={(v) => setMode(v as 'standard' | 'enhanced')}
            options={[
              { value: 'standard', label: 'Standard PDF (AES) — opens in any PDF reader' },
              { value: 'enhanced', label: 'Enhanced .epdf (advanced ciphers) — this toolkit only' },
            ]}
          />

          {mode === 'standard' ? (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <Field label="Encryption">
                <Select
                  value={encryption}
                  onChange={setEncryption}
                  options={[
                    { value: 'AES-256', label: 'AES-256 (recommended)' },
                    { value: 'AES-128', label: 'AES-128' },
                  ]}
                />
              </Field>
              <div style={{ marginTop: 'var(--space-3)' }}>
                <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>Permissions</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                  <Checkbox checked={permPrint} onChange={setPermPrint} label="Allow printing" />
                  <Checkbox checked={permModify} onChange={setPermModify} label="Allow modifying" />
                  <Checkbox checked={permCopy} onChange={setPermCopy} label="Allow copying" />
                  <Checkbox checked={permAnnotate} onChange={setPermAnnotate} label="Allow annotating" />
                </div>
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <Field label="Cipher">
                <Select value={cipher} onChange={setCipher} options={CIPHERS} />
              </Field>
              <div style={{ marginTop: 'var(--space-3)' }}>
                <Field label="Key Derivation">
                  <Select value={kdf} onChange={setKdf} options={KDFS} />
                </Field>
              </div>
              <div
                style={{
                  marginTop: 'var(--space-3)',
                  padding: 'var(--space-3)',
                  background: 'var(--panel-bg-elevated)',
                  borderRadius: 'var(--radius-panel-sm)',
                  fontSize: 'var(--font-size-sm)',
                  color: 'var(--text-3)',
                  lineHeight: 1.5,
                }}
              >
                Enhanced encryption creates .epdf files that can only be opened with this toolkit. Uses
                military-grade cryptography with memory-hard key derivation for maximum security.
              </div>
            </div>
          )}
        </Card>
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <SectionLabel>Passwords</SectionLabel>
          <Field label={mode === 'standard' ? 'User password (required to open)' : 'Encryption password'}>
            <TextInput type="password" value={userPassword} onChange={setUserPassword} placeholder="Enter password" />
            <StrengthBar strength={userStr} />
          </Field>

          {mode === 'standard' && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <Field label="Owner password (required to change permissions)">
                <TextInput type="password" value={ownerPassword} onChange={setOwnerPassword} placeholder="Enter password" />
                <StrengthBar strength={ownerStr} />
              </Field>
            </div>
          )}
        </Card>
      </div>

      {/* Single input: destination defaults to the same folder as the file;
          "Choose location…" lets the user override it before protecting. */}
      {singleSrc && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <SectionLabel>Output</SectionLabel>
            <Field label="Output file">
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
            </Field>
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
            <SectionLabel>Output (batch)</SectionLabel>
            <Field label="Output folder">
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
            </Field>
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Field label="Naming template" help="Variables: {name}, {cipher}, {mode}">
                <TextInput value={naming} onChange={setNaming} placeholder="{name}_protected" />
              </Field>
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Protecting…' : 'Protect'}
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
              bridgeApi.cancel('protect');
              op.reset();
              toast.info('Protection cancelled.');
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

function SectionLabel({ children }: { children: string }) {
  return (
    <div
      style={{
        fontSize: 'var(--font-size-xs)',
        fontWeight: 700,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        color: 'var(--text-3)',
        marginBottom: 'var(--space-3)',
      }}
    >
      {children}
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

function StrengthBar({ strength }: { strength: { label: string; color: string; textColor: string; width: string } }) {
  if (!strength.label) return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
      <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'var(--border)', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: strength.width, background: strength.color, transition: 'width 150ms' }} />
      </div>
      <span style={{ fontSize: 'var(--font-size-xs)', color: strength.textColor, minWidth: 60 }}>{strength.label}</span>
    </div>
  );
}
