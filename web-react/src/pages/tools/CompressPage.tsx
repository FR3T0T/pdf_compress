import { useEffect, useRef, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import type { DropZoneHandle } from '../../components/shared/DropZone';
import { useHotkeys } from '../../bridge/useHotkeys';
import { FileList } from '../../components/shared/FileList';
import { PresetCards } from '../../components/shared/PresetCards';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { ResultsPanel } from '../../components/shared/ResultsPanel';
import { Checkbox } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
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
 * DELIBERATE SIMPLIFICATION, flagged rather than silent: the vanilla page
 * has per-file thumbnails, live DPI/image analysis chips, an animated
 * circular savings gauge, and individually editable output filenames —
 * a large bespoke visualization system. This port keeps the full bridge
 * contract and core workflow (multi-file, presets, progress, real
 * before/after sizes) but uses the shared FileList/ResultsPanel instead
 * of rebuilding that custom UI, given the scope of a full 20-page
 * migration.
 *
 * Bug found, not replicated: startCompress's Python side reads
 * `outputPath` (singular) — safe only for single-file calls, since a
 * batch call passes ONE outputPath used for every file in the loop
 * (verified in ui/bridge.py). It never reads output_dir/naming at all.
 * compress_pdf() defaults to `<name>_compressed.pdf` next to the source
 * when output_path is None. So the vanilla page's "Output Folder" /
 * "Output Suffix" settings are silently non-functional for the batch
 * case it always uses. Rather than ship decorative controls that do
 * nothing, this page omits them — output always lands as
 * `<name>_compressed.pdf` beside each source file, same real behavior
 * as vanilla, just not advertised as configurable.
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

  usePageBusy(op.status === 'running');

  useEffect(() => {
    bridgeApi.getPresets().then((res) => {
      setPresets(res.presets);
      setPreset(res.defaultPreset || 'standard');
      setGhostscriptAvailable(res.ghostscriptAvailable);
    });
  }, []);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const results = op.result.results;
      const totalSaved = results.reduce((s, r) => s + (r.skipped ? 0 : r.original_size - r.compressed_size), 0);
      toast.success(
        `Saved ${bridgeApi.formatSize(totalSaved)} across ${results.length} file${results.length === 1 ? '' : 's'}.`
      );
    } else if (op.status === 'error') {
      toast.error(op.error || 'Compression failed.');
    }
  }, [op.status, op.result, op.error, toast]);

  const canRun = files.length > 0 && op.status !== 'running';

  useHotkeys({
    onAddFiles: () => dropRef.current?.open(),
    onRun: () => canRun && run(),
    onClear: op.status === 'running' ? undefined : () => setFiles([]),
  });

  const run = () => {
    if (files.length === 0) {
      toast.warning('Please add at least one PDF file.');
      return;
    }
    setStartTime(Date.now());
    op.run(() => bridgeApi.startCompress({ files: files.map((f) => f.path), preset, use_gs: useGs }));
  };

  const r = op.status === 'done' ? op.result?.results : null;
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
