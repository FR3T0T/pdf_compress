import { useEffect, useRef, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import type { DropZoneHandle } from '../../components/shared/DropZone';
import { useHotkeys } from '../../bridge/useHotkeys';
import { CompressFileCard } from './CompressFileCard';
import type { FileAnalysis } from './CompressFileCard';
import { PresetCards } from '../../components/shared/PresetCards';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Checkbox } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile, Preset } from '../../types/bridge';

interface CompressResultItem {
  input_path: string;
  output_path: string;
  original_size: number;
  compressed_size: number;
  skipped: boolean;
  originalSizeStr?: string;
  compressedSizeStr?: string;
  savedPct?: number;
}

/**
 * React port of web/js/pages/compress.js (the "premium" version). Bridge
 * call preserved exactly: BridgeAPI.startCompress({ files, preset,
 * output_dir, use_gs }). Has a real progress callback (per-file).
 *
 * Rich file cards (CompressFileCard): each added file gets a page-1
 * thumbnail (getThumbnail) and live analysis (analyzeFile) — size, pages,
 * image count, image DPI with a downscale warning, and the estimated
 * savings for the selected preset, which updates when the preset changes.
 * Mirrors the vanilla page's bespoke card UI. Still deferred: the animated
 * circular savings gauge (pure polish) and per-file editable output names
 * (non-functional without a backend change — see the note below).
 *
 * Output location: this page sends no output path, so each file is written
 * as `<name>_compressed.pdf` beside its source. The backend now supports
 * per-file batch output too — startCompress honors `outputDir` and a
 * `naming` template (via `compress_output_path` in compress_paths.py), and only
 * applies a single explicit `outputPath` to single-file calls (a batch used
 * to overwrite one shared path — fixed). Output Folder / naming controls
 * could be re-added here on top of that; deliberately left out for now.
 */
export function CompressPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [preset, setPreset] = useState('standard');
  const [ghostscriptAvailable, setGhostscriptAvailable] = useState(false);
  const [useGs, setUseGs] = useState(false);
  const op = useOperation<CompressResultItem[]>('compress');
  const [startTime, setStartTime] = useState<number | null>(null);
  const dropRef = useRef<DropZoneHandle>(null);
  // Per-file analysis + thumbnail, keyed by path. analyzedPaths guards
  // against re-fetching (pruned to the current file set, so remove-then-add
  // re-analyzes) — same pattern as the merge page.
  const [analyses, setAnalyses] = useState<Record<string, FileAnalysis>>({});
  const analyzedPaths = useRef<Set<string>>(new Set());

  // -- Workspace (persistent working document) -----------------------------
  // Same pattern as WatermarkPage: when a workspace document is loaded, this
  // page skips its own drop zone and Compress runs against workspace.path,
  // advancing the workspace pointer to a new temp file on success. With no
  // workspace document, this page is byte-for-byte the original standalone
  // behavior below.
  const workspace = useWorkspace();
  const workspaceRunRef = useRef(false);

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    bridgeApi.getPresets().then((res) => {
      setPresets(res.presets);
      setPreset(res.defaultPreset || 'standard');
      setGhostscriptAvailable(res.ghostscriptAvailable);
    });
  }, []);

  // Fetch analysis + thumbnail concurrently for each newly-added file.
  useEffect(() => {
    const currentPaths = new Set(files.map((f) => f.path));
    for (const p of analyzedPaths.current) {
      if (!currentPaths.has(p)) analyzedPaths.current.delete(p);
    }
    setAnalyses((prev) => {
      const next: Record<string, FileAnalysis> = {};
      for (const f of files) if (prev[f.path]) next[f.path] = prev[f.path];
      return next;
    });

    const pending = files.filter((f) => !analyzedPaths.current.has(f.path));
    if (pending.length === 0) return;

    let cancelled = false;
    for (const f of pending) {
      analyzedPaths.current.add(f.path);
      setAnalyses((prev) => ({ ...prev, [f.path]: { status: 'analyzing' } }));
      Promise.allSettled([bridgeApi.analyzeFile(f.path), bridgeApi.getThumbnail(f.path)]).then(
        ([infoRes, thumbRes]) => {
          if (cancelled) return;
          const thumbnail =
            thumbRes.status === 'fulfilled' && thumbRes.value?.success ? thumbRes.value.dataUrl : undefined;

          if (infoRes.status !== 'fulfilled' || !infoRes.value || infoRes.value.success === false) {
            const err =
              infoRes.status === 'fulfilled' && infoRes.value
                ? infoRes.value.encrypted
                  ? 'Password-protected'
                  : String(infoRes.value.error || 'Analysis failed')
                : 'Analysis failed';
            setAnalyses((prev) => ({ ...prev, [f.path]: { status: 'error', error: err, thumbnail } }));
            return;
          }

          const d = infoRes.value;
          setAnalyses((prev) => ({
            ...prev,
            [f.path]: {
              status: 'ready',
              thumbnail,
              size: (d.file_size ?? d.fileSize) as number | undefined,
              pages: (d.page_count ?? d.pageCount) as number | undefined,
              imageCount: (d.image_count ?? d.imageCount ?? 0) as number,
              imageSummary: d.imageSummary as FileAnalysis['imageSummary'],
              estimates: d.estimates as FileAnalysis['estimates'],
            },
          }));
        }
      );
    }
    return () => {
      cancelled = true;
    };
  }, [files]);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const results = op.result.results;
      if (workspaceRunRef.current) {
        workspaceRunRef.current = false;
        const item = results[0];
        if (item?.output_path) {
          workspace.applyResult(item.output_path, `Compress (${preset})`);
          toast.success('Compressed the working document.');
        } else {
          toast.error('Compression failed — working document unchanged.');
        }
        return;
      }
      const totalSaved = results.reduce((s, r) => s + (r.skipped ? 0 : r.original_size - r.compressed_size), 0);
      toast.success(
        `Saved ${bridgeApi.formatSize(totalSaved)} across ${results.length} file${results.length === 1 ? '' : 's'}.`
      );
    } else if (op.status === 'error') {
      workspaceRunRef.current = false;
      toast.error(op.error || 'Compression failed.');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [op.status, op.result, op.error, toast]);

  const canRun = (workspace.path ? true : files.length > 0) && op.status !== 'running';

  useHotkeys({
    onAddFiles: () => dropRef.current?.open(),
    onRun: () => canRun && run(),
    onClear: op.status === 'running' ? undefined : () => setFiles([]),
  });

  const run = () => {
    if (workspace.path) {
      const wsPath = workspace.path;
      const opIndex = workspace.ops.length + 1;
      workspaceRunRef.current = true;
      setStartTime(Date.now());
      op.run(async () => {
        const wsDir = await bridgeApi.getWorkspaceDir();
        bridgeApi.startCompress({
          files: [wsPath],
          preset,
          use_gs: useGs,
          output_dir: wsDir,
          naming: `{name}_ws${opIndex}`,
        });
      });
      return;
    }

    if (files.length === 0) {
      toast.warning('Please add at least one PDF file.');
      return;
    }
    setStartTime(Date.now());
    op.run(() => bridgeApi.startCompress({ files: files.map((f) => f.path), preset, use_gs: useGs }));
  };

  const r = op.status === 'done' && !workspace.path ? op.result?.results : null;
  const results = r
    ? {
        files: r.map((item) => ({
          name: bridgeApi.basename(item.output_path || item.input_path),
          originalSize: item.original_size,
          resultSize: item.compressed_size,
          status: item.skipped ? ('skipped' as const) : ('done' as const),
        })),
        totalTime: startTime ? (Date.now() - startTime) / 1000 : undefined,
        totalSaved: r.reduce((s, item) => s + (item.skipped ? 0 : item.original_size - item.compressed_size), 0),
        outputDir: r.length > 0 ? bridgeApi.dirname(r[0].output_path) : undefined,
      }
    : null;

  return (
    <div className="console">
      <PageHeader title="Compress PDF" subtitle="Reduce file size while preserving quality" backButton={false} />

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
          {files.length > 0 && (
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {files.map((f, i) => (
                <CompressFileCard
                  key={f.path}
                  file={f}
                  analysis={analyses[f.path]}
                  presetKey={preset}
                  disabled={op.status === 'running'}
                  onRemove={() => setFiles((fs) => fs.filter((_, idx) => idx !== i))}
                />
              ))}
            </div>
          )}
        </>
      )}

      <div style={{ marginTop: 'var(--space-4)' }}>
        <div
          style={{
            fontSize: 'var(--font-size-xs)',
            fontWeight: 700,
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
            color: 'var(--text-3)',
            marginBottom: 8,
          }}
        >
          Quality preset
        </div>
        {presets.length > 0 ? (
          <PresetCards presets={presets} selected={preset} onChange={setPreset} />
        ) : (
          <Card>
            <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>Loading presets…</span>
          </Card>
        )}
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <Checkbox
            checked={useGs}
            onChange={setUseGs}
            label={
              <div>
                Use Ghostscript engine
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 2 }}>
                  Advanced compression with font subsetting
                  {!ghostscriptAvailable && ' — not detected on this system'}
                </div>
              </div>
            }
          />
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running'
            ? 'Compressing…'
            : files.length > 1
              ? `Compress ${files.length} Files`
              : 'Compress'}
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
              bridgeApi.cancel('compress');
              op.reset();
              toast.warning('Compression cancelled.');
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
