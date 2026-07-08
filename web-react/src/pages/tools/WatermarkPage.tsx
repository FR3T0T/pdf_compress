import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import type { DropZoneHandle } from '../../components/shared/DropZone';
import { FileList } from '../../components/shared/FileList';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Select, Slider, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useHotkeys } from '../../bridge/useHotkeys';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile } from '../../types/bridge';

interface WatermarkFileResult {
  file: string;
  status: 'ok' | 'error';
  details?: string;
  outputPath?: string;
}

interface WatermarkResult {
  files: WatermarkFileResult[];
  elapsed: number;
  output_dir: string;
}

const PRESETS = [
  { key: 'custom', label: 'Custom', text: '', opacity: 30, rotation: -45, fontSize: 48, color: '#808080', position: 'center' },
  { key: 'confidential', label: 'CONFIDENTIAL', text: 'CONFIDENTIAL', opacity: 20, rotation: -45, fontSize: 60, color: '#DC2626', position: 'center' },
  { key: 'draft', label: 'DRAFT', text: 'DRAFT', opacity: 25, rotation: -45, fontSize: 72, color: '#2563EB', position: 'center' },
  { key: 'donotcopy', label: 'DO NOT COPY', text: 'DO NOT COPY', opacity: 20, rotation: -45, fontSize: 54, color: '#DC2626', position: 'center' },
  { key: 'sample', label: 'SAMPLE', text: 'SAMPLE', opacity: 25, rotation: -45, fontSize: 64, color: '#7C3AED', position: 'center' },
  { key: 'approved', label: 'APPROVED', text: 'APPROVED', opacity: 20, rotation: 0, fontSize: 48, color: '#059669', position: 'center' },
  { key: 'void', label: 'VOID', text: 'VOID', opacity: 30, rotation: -45, fontSize: 80, color: '#DC2626', position: 'center' },
];

const POSITIONS = [
  { value: 'center', label: 'Center' },
  { value: 'top-left', label: 'Top Left' },
  { value: 'top-center', label: 'Top Center' },
  { value: 'top-right', label: 'Top Right' },
  { value: 'bottom-left', label: 'Bottom Left' },
  { value: 'bottom-center', label: 'Bottom Center' },
  { value: 'bottom-right', label: 'Bottom Right' },
];

const MODES = [
  { value: 'single', label: 'Single (centered)' },
  { value: 'tiled', label: 'Tiled (repeated across page)' },
];

const SETTINGS_KEYS = {
  naming: 'watermark/naming',
  outputDir: 'watermark/outputDir',
  preset: 'watermark/preset',
  opacity: 'watermark/opacity',
  rotation: 'watermark/rotation',
  fontSize: 'watermark/fontSize',
  color: 'watermark/color',
  position: 'watermark/position',
  mode: 'watermark/mode',
};

/**
 * React port of web/js/pages/watermark.js. Bridge call preserved exactly:
 * BridgeAPI.startWatermark({ files, text, opacity, rotation, font_size,
 * color, position, mode, page_range, output_dir, naming }).
 *
 * `mode`: "single" (one centered instance, the original/vanilla behavior)
 * or "tiled" (repeated in a staggered diagonal grid across the whole
 * page, at the same `rotation` angle, so it can't be cropped out by
 * removing one area — see pdf_ops.py's add_watermark/_tiled_watermark_body).
 * `position` only applies in single mode.
 *
 * Bug fix vs. vanilla: the opacity slider is 1-100 (a percentage) but
 * ui/bridge.py's startWatermark reads it directly as the PDF's /ca /CA
 * graphics-state alpha, which the PDF spec defines as 0.0-1.0 — verified
 * in pdf_ops.py's add_watermark. Sending 30 instead of 0.3 means the
 * vanilla page's watermark is always fully opaque regardless of the
 * slider. This divides by 100 before sending.
 *
 * Result shape verified against ui/bridge.py's startWatermark _work():
 * { files: [{file, status, details, outputPath}], elapsed, output_dir } —
 * unlike most other "premium" pages, watermark.js's own result reading
 * already matches this exactly, so no shape bug here.
 *
 * Settings persistence (9 watermark/* keys) and preset switching
 * preserved; keyboard shortcuts intentionally not carried over.
 *
 * Workspace integration: loading a document into the workspace is now a
 * global action (the WorkspaceBar in AppShell, above every tool page), not
 * a per-page toggle. When a workspace document is loaded, this page skips
 * its own drop zone and Apply Watermark runs against workspace.path
 * directly, advancing the workspace pointer to a new temp file on success
 * (running-result model — see WorkspaceContext.tsx). With no workspace
 * document loaded, this page is byte-for-byte the original standalone
 * behavior below.
 */
export function WatermarkPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [preset, setPreset] = useState('custom');
  const [text, setText] = useState('');
  const [opacityPct, setOpacityPct] = useState(30);
  const [rotation, setRotation] = useState('-45');
  const [fontSize, setFontSize] = useState('48');
  const [color, setColor] = useState('#808080');
  const [position, setPosition] = useState('center');
  const [mode, setMode] = useState('single');
  const [pageRange, setPageRange] = useState('');
  const [outputDir, setOutputDir] = useState('');
  const [naming, setNaming] = useState('{name}_watermarked');
  const op = useOperation<WatermarkResult>('watermark');
  const dropRef = useRef<DropZoneHandle>(null);

  // -- Workspace (persistent working document) -----------------------------
  // See WorkspaceContext.tsx / WorkspaceBar.tsx. Loading a document into the
  // workspace happens globally via the bar; this page only reads
  // workspace.path/ops and, when set, runs against it instead of the local
  // `files` list below (which is exactly the pre-workspace standalone
  // behavior).
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useHotkeys({
    onAddFiles: () => dropRef.current?.open(),
    onRun: () => op.status !== 'running' && run(),
    onClear: op.status === 'running' ? undefined : () => setFiles([]),
  });

  useEffect(() => {
    (async () => {
      const n = await bridgeApi.loadSetting(SETTINGS_KEYS.naming);
      if (n) setNaming(n);
      const dir = await bridgeApi.loadSetting(SETTINGS_KEYS.outputDir);
      if (dir) setOutputDir(dir);
      const p = await bridgeApi.loadSetting(SETTINGS_KEYS.preset);
      if (p) applyPreset(p);
      const op_ = await bridgeApi.loadSetting(SETTINGS_KEYS.opacity);
      if (op_) setOpacityPct(parseInt(op_, 10));
      const r = await bridgeApi.loadSetting(SETTINGS_KEYS.rotation);
      if (r) setRotation(r);
      const fs = await bridgeApi.loadSetting(SETTINGS_KEYS.fontSize);
      if (fs) setFontSize(fs);
      const c = await bridgeApi.loadSetting(SETTINGS_KEYS.color);
      if (c) setColor(c);
      const pos = await bridgeApi.loadSetting(SETTINGS_KEYS.position);
      if (pos) setPosition(pos);
      const m = await bridgeApi.loadSetting(SETTINGS_KEYS.mode);
      if (m) setMode(m);
    })();
    // Deliberately runs once on mount only — loads persisted settings.
  }, []);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const res = op.result.results;
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        const fr = res.files[0];
        if (fr?.status === 'ok' && fr.outputPath) {
          workspace.applyResult(fr.outputPath, `Watermark: "${text.trim()}" (${position})`);
          toast.success('Watermark applied to the working document.');
        } else {
          toast.error(fr?.details || 'Watermark failed — working document unchanged.');
        }
        return;
      }
      const okCount = res.files.filter((f) => f.status === 'ok').length;
      const errCount = res.files.length - okCount;
      if (errCount === 0) {
        toast.success(`Watermark applied to ${okCount} file${okCount === 1 ? '' : 's'}.`);
      } else if (okCount > 0) {
        toast.warning(`${okCount} succeeded, ${errCount} failed.`);
      } else {
        toast.error('All files failed.');
      }
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'An error occurred while applying watermark.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, toast]);

  function applyPreset(key: string) {
    setPreset(key);
    const p = PRESETS.find((x) => x.key === key);
    if (!p || key === 'custom') return;
    setText(p.text);
    setOpacityPct(p.opacity);
    setRotation(String(p.rotation));
    setFontSize(String(p.fontSize));
    setColor(p.color);
    setPosition(p.position);
  }

  const onTextEdit = (value: string) => {
    setText(value);
    const current = PRESETS.find((p) => p.key === preset);
    if (preset !== 'custom' && current && value !== current.text) {
      setPreset('custom');
    }
  };

  const canRun = (workspace.path ? true : files.length > 0) && op.status !== 'running';

  const saveAllSettings = () => {
    bridgeApi.saveSetting(SETTINGS_KEYS.naming, naming);
    bridgeApi.saveSetting(SETTINGS_KEYS.outputDir, outputDir);
    bridgeApi.saveSetting(SETTINGS_KEYS.preset, preset);
    bridgeApi.saveSetting(SETTINGS_KEYS.opacity, String(opacityPct));
    bridgeApi.saveSetting(SETTINGS_KEYS.rotation, rotation);
    bridgeApi.saveSetting(SETTINGS_KEYS.fontSize, fontSize);
    bridgeApi.saveSetting(SETTINGS_KEYS.color, color);
    bridgeApi.saveSetting(SETTINGS_KEYS.position, position);
    bridgeApi.saveSetting(SETTINGS_KEYS.mode, mode);
  };

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) {
      setOutputDir(dir);
      bridgeApi.saveSetting(SETTINGS_KEYS.outputDir, dir);
    }
  };

  const run = () => {
    const trimmedText = text.trim();
    if (!trimmedText) {
      toast.warning('Please enter watermark text.');
      return;
    }

    if (workspace.path) {
      // Workspace mode: run on the current working document and, on
      // success, advance the workspace pointer to a NEW temp file (never
      // overwrite the previous working file — see WorkspaceContext).
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startWatermark({
          files: [wsPath],
          text: trimmedText,
          opacity: opacityPct / 100,
          rotation: parseInt(rotation, 10),
          font_size: parseInt(fontSize, 10),
          color: color.trim() || '#808080',
          position,
          mode,
          page_range: pageRange.trim() || null,
          output_dir: wsDir,
          // Unique per call (regardless of input basename) so each run's
          // output is a genuinely new file — contained_output_path doesn't
          // dedupe, so a repeated name would silently overwrite.
          naming: `{name}_ws${opIndex}`,
        });
      });
      return;
    }

    if (files.length === 0) {
      toast.warning('Please add at least one PDF file.');
      return;
    }
    saveAllSettings();
    op.run(() =>
      bridgeApi.startWatermark({
        files: files.map((f) => f.path),
        text: trimmedText,
        opacity: opacityPct / 100,
        rotation: parseInt(rotation, 10),
        font_size: parseInt(fontSize, 10),
        color: color.trim() || '#808080',
        position,
        mode,
        page_range: pageRange.trim() || null,
        output_dir: outputDir,
        naming: naming || '{name}_watermarked',
      })
    );
  };

  // Suppressed in workspace mode -- the WorkspaceBar's ops count/Preview
  // communicates the result instead of the normal-mode batch ResultsPanel,
  // which assumes a `files` array.
  const r = op.status === 'done' && !workspace.path ? op.result?.results : null;
  const results = r
    ? {
        files: r.files.map((fr) => ({
          name: fr.file,
          status: fr.status === 'ok' ? ('done' as const) : ('error' as const),
          error: fr.status === 'error' ? fr.details : undefined,
        })),
        totalTime: r.elapsed,
        outputDir: r.output_dir,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Watermark" subtitle="Add text watermarks to PDF pages" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — see the bar above to
            Preview, Export, or Clear it.
          </div>
        </Card>
      ) : (
        <>
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

          <div style={{ marginTop: 8 }}>
            <FileList files={files} onRemove={(i) => setFiles((fs) => fs.filter((_, idx) => idx !== i))} />
          </div>
        </>
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
            Watermark settings
          </div>
          <Field label="Preset">
            <Select value={preset} onChange={applyPreset} options={PRESETS.map((p) => ({ value: p.key, label: p.label }))} />
          </Field>
          <div style={{ marginTop: 'var(--space-3)' }}>
            <Field label="Watermark text">
              <TextInput value={text} onChange={onTextEdit} placeholder="e.g. CONFIDENTIAL, DRAFT, SAMPLE" />
            </Field>
          </div>

          <div style={{ marginTop: 'var(--space-4)' }}>
            <Field label="Layout">
              <Select value={mode} onChange={setMode} options={MODES} />
            </Field>
            <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
              {mode === 'tiled'
                ? 'Repeats the text diagonally across the whole page (at the rotation angle below) so it can’t be cropped out by removing one area.'
                : 'One instance of the text, centered on the page.'}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)', marginTop: 'var(--space-4)' }}>
            <Field label="Opacity">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Slider value={opacityPct} min={1} max={100} onChange={setOpacityPct} />
                <span className="mono" style={{ width: 40, textAlign: 'right', fontSize: 'var(--font-size-sm)' }}>
                  {opacityPct}%
                </span>
              </div>
            </Field>
            <Field label={mode === 'tiled' ? 'Diagonal angle (degrees)' : 'Rotation (degrees)'}>
              <TextInput type="number" value={rotation} onChange={setRotation} />
            </Field>
            <Field label="Font size">
              <TextInput type="number" value={fontSize} onChange={setFontSize} />
            </Field>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)', marginTop: 'var(--space-4)' }}>
            <Field label="Color">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div
                  style={{
                    width: 28,
                    height: 28,
                    borderRadius: 'var(--radius-badge)',
                    border: '1px solid var(--border)',
                    background: /^#[0-9A-Fa-f]{6}$/.test(color) ? color : '#808080',
                    flexShrink: 0,
                  }}
                />
                <TextInput value={color} onChange={setColor} placeholder="#808080" />
              </div>
            </Field>
            <Field label="Position">
              <Select value={position} onChange={setPosition} options={POSITIONS} disabled={mode === 'tiled'} />
              {mode === 'tiled' && (
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
                  Ignored in tiled mode — covers the whole page.
                </div>
              )}
            </Field>
            <Field label="Page range">
              <TextInput value={pageRange} onChange={setPageRange} placeholder="All pages (e.g. 1-5, 8, 10-12)" />
            </Field>
          </div>
        </Card>
      </div>

      {!workspace.path && (
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
                  {outputDir || 'Same as source file'}
                </span>
                <button onClick={pickOutputDir} disabled={op.status === 'running'} className="btn-ghost">
                  Browse
                </button>
              </div>
            </div>
            <Field label="Naming template">
              <TextInput value={naming} onChange={setNaming} placeholder="{name}_watermarked" />
            </Field>
            <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 4 }}>
              Use {'{name}'} for original filename.
            </div>
          </Card>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 'var(--space-4)' }}>
        <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>
          {workspace.path
            ? `Working document loaded${workspace.ops.length > 0 ? ` — ${workspace.ops.length} operation${workspace.ops.length === 1 ? '' : 's'} applied` : ''}`
            : files.length > 0
              ? `${files.length} file${files.length === 1 ? '' : 's'} selected`
              : ''}
        </span>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Applying…' : 'Apply Watermark'}
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
              bridgeApi.cancel('watermark');
              workspaceRunRef.current = false;
              op.reset();
              toast.info('Watermark cancelled.');
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  );
}
