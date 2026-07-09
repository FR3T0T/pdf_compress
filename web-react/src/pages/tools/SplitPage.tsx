import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import type { DropZoneHandle } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useHotkeys } from '../../bridge/useHotkeys';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import { useWorkspace, useWorkspaceBusy } from '../../workspace/WorkspaceContext';
import type { PickedFile } from '../../types/bridge';

interface TocEntry {
  level: number;
  title: string;
  page: number;
  end_page: number;
}
interface SplitResult {
  input_path: string;
  output_paths: string[];
  pages_per_output: number[];
}
type Mode = 'all' | 'ranges' | 'every_n' | 'chapters';

/**
 * React port of web/js/pages/split.js. Bridge call preserved exactly:
 * BridgeAPI.startSplit({ file, output_dir, mode, name_template,
 * ranges?, every_n?, chapters? }). Verified against ui/bridge.py: no
 * progress callback (single-shot), result is
 * { input_path, output_paths, pages_per_output } — matches vanilla's own
 * reading. Settings persistence (split/outputDir, split/nameTemplate,
 * split/mode, split/everyN) preserved. Keyboard shortcuts intentionally
 * not carried over.
 */
export function SplitPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [fileInfo, setFileInfo] = useState<{ size?: string; pages?: number } | null>(null);
  const [mode, setMode] = useState<Mode>('all');
  const [ranges, setRanges] = useState('');
  const [everyN, setEveryN] = useState('2');
  const [toc, setToc] = useState<TocEntry[]>([]);
  const [tocFetched, setTocFetched] = useState(false);
  const [selectedChapters, setSelectedChapters] = useState<Set<number>>(new Set());
  const [outputDir, setOutputDir] = useState('');
  const [nameTemplate, setNameTemplate] = useState('{filename}_page_{n}');
  const op = useOperation<SplitResult>('split');
  const dropRef = useRef<DropZoneHandle>(null);

  // -- Workspace (persistent working document) -----------------------------
  // Terminal tool: when a workspace document is loaded, Split reads it as
  // input but its output is a normal side download — the workspace pointer
  // is never advanced (see the Step B spec's terminal-tool list).
  const workspace = useWorkspace();

  usePageBusy(op.status === 'running');
  useWorkspaceBusy(op.status === 'running' && !!workspace.path);

  useEffect(() => {
    (async () => {
      const dir = await bridgeApi.loadSetting('split/outputDir');
      if (dir) setOutputDir(dir);
      const tmpl = await bridgeApi.loadSetting('split/nameTemplate');
      if (tmpl) setNameTemplate(tmpl);
      const m = await bridgeApi.loadSetting('split/mode');
      if (m === 'all' || m === 'ranges' || m === 'every_n' || m === 'chapters') setMode(m);
      const n = await bridgeApi.loadSetting('split/everyN');
      if (n) setEveryN(n);
    })();
  }, []);

  useEffect(() => {
    if (op.status === 'done' && op.result?.results) {
      const n = op.result.results.output_paths.length;
      toast.success(`Split into ${n} file${n === 1 ? '' : 's'}!`);
    } else if (op.status === 'error') {
      toast.error(op.error || 'Split failed.');
    }
  }, [op.status, op.result, op.error, toast]);

  const file = files[0] ?? null;
  const effectivePath = workspace.path ?? file?.path ?? null;

  useEffect(() => {
    setTocFetched(false);
    setToc([]);
    setSelectedChapters(new Set());
    if (!effectivePath) {
      setFileInfo(null);
      return;
    }
    bridgeApi.analyzeFile(effectivePath).then((info) => {
      const size = (info.size ?? info.file_size) as number | undefined;
      const pages = (info.pages ?? info.page_count) as number | undefined;
      setFileInfo({ size: size != null ? bridgeApi.formatSize(size) : undefined, pages });
    });
    bridgeApi
      .getToc(effectivePath)
      .then((entries) => {
        setToc(entries as TocEntry[]);
        setSelectedChapters(new Set((entries as TocEntry[]).map((_, i) => i)));
      })
      .finally(() => setTocFetched(true));
  }, [effectivePath]);

  const saveSettings = (dir: string, tmpl: string, m: Mode, n: string) => {
    bridgeApi.saveSetting('split/outputDir', dir);
    bridgeApi.saveSetting('split/nameTemplate', tmpl);
    bridgeApi.saveSetting('split/mode', m);
    bridgeApi.saveSetting('split/everyN', n);
  };

  const pickOutputDir = async () => {
    const dir = await bridgeApi.openFolder();
    if (dir) {
      setOutputDir(dir);
      saveSettings(dir, nameTemplate, mode, everyN);
    }
  };

  const toggleChapter = (i: number) => {
    setSelectedChapters((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const canRun = !!effectivePath && op.status !== 'running';

  useHotkeys({
    onAddFiles: () => dropRef.current?.open(),
    onRun: () => canRun && run(),
    onClear: op.status === 'running' ? undefined : () => setFiles([]),
  });

  const run = () => {
    if (!effectivePath) {
      toast.warning('Please add a PDF file first.');
      return;
    }
    const dir = outputDir || bridgeApi.dirname(effectivePath);
    const params: Record<string, unknown> = {
      file: effectivePath,
      output_dir: dir,
      mode,
      name_template: nameTemplate || '{filename}_page_{n}',
    };

    if (mode === 'ranges') {
      const r = ranges.trim();
      if (!r) {
        toast.warning('Please enter page ranges.');
        return;
      }
      params.ranges = r;
    } else if (mode === 'every_n') {
      params.every_n = parseInt(everyN, 10) || 1;
    } else if (mode === 'chapters') {
      const chapters = toc
        .filter((_, i) => selectedChapters.has(i))
        .map((c) => ({ title: c.title, start_page: c.page, end_page: c.end_page }));
      if (chapters.length === 0) {
        toast.warning('Please select at least one chapter.');
        return;
      }
      params.chapters = chapters;
      if (!nameTemplate || nameTemplate === '{filename}_page_{n}') {
        params.name_template = '{filename}_{title}';
      }
    }

    saveSettings(dir, nameTemplate, mode, everyN);
    op.run(() => bridgeApi.startSplit(params));
  };

  const r = op.status === 'done' ? op.result?.results : null;

  return (
    <div className="console">
      <PageHeader title="Split PDF" subtitle="Split a PDF into individual pages, page ranges, or chapters" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Operating on the workspace document ({workspace.originalName}) — this reads from it without
            changing it; see the bar above to Preview, Export, or Clear it.
          </div>
        </Card>
      ) : (
        <DropZone
          ref={dropRef}
          files={files}
          onFilesChanged={setFiles}
          multiple={false}
          title="Drop a PDF file here"
          subtitle="or click to browse"
          disabled={op.status === 'running'}
        />
      )}

      {!workspace.path && file && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)' }}>{file.name}</div>
                <div className="mono" style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)', wordBreak: 'break-all' }}>
                  {file.path}
                </div>
              </div>
              <Stat label="Size" value={fileInfo?.size ?? '--'} />
              <Stat label="Pages" value={fileInfo?.pages != null ? String(fileInfo.pages) : '--'} accent />
            </div>
          </Card>
        </div>
      )}

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
            Split options
          </div>
          <Field label="Split mode">
            <Select
              value={mode}
              onChange={(v) => setMode(v as Mode)}
              options={[
                { value: 'all', label: 'All pages (one file per page)' },
                { value: 'ranges', label: 'Custom page ranges' },
                { value: 'every_n', label: 'Every N pages' },
                { value: 'chapters', label: 'By chapters (from bookmarks)' },
              ]}
            />
          </Field>

          {mode === 'ranges' && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Field label="Page ranges" help="Separate ranges with commas. Each range becomes a separate file.">
                <TextInput value={ranges} onChange={setRanges} placeholder="e.g. 1-3, 5, 7-10" />
              </Field>
            </div>
          )}

          {mode === 'every_n' && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Field label="Pages per file">
                <TextInput type="number" value={everyN} onChange={setEveryN} placeholder="Number of pages per output file" />
              </Field>
            </div>
          )}

          {mode === 'chapters' && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              {!tocFetched ? (
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>Loading bookmarks…</div>
              ) : toc.length === 0 ? (
                <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)', textAlign: 'center', padding: 'var(--space-4)' }}>
                  No bookmarks found in this PDF. Use "Custom page ranges" mode instead.
                </div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                    <button onClick={() => setSelectedChapters(new Set(toc.map((_, i) => i)))} className="btn-ghost">
                      Select all
                    </button>
                    <button onClick={() => setSelectedChapters(new Set())} className="btn-ghost">
                      Deselect all
                    </button>
                  </div>
                  <div
                    style={{
                      maxHeight: 320,
                      overflowY: 'auto',
                      border: '1px solid var(--border)',
                      borderRadius: 'var(--radius-panel-sm)',
                      padding: 6,
                    }}
                  >
                    {toc.map((entry, i) => (
                      <label
                        key={i}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 8px',
                          paddingLeft: 8 + (entry.level - 1) * 20,
                          cursor: 'pointer',
                          fontSize: 'var(--font-size-sm)',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selectedChapters.has(i)}
                          onChange={() => toggleChapter(i)}
                          style={{ accentColor: 'var(--accent)', flexShrink: 0 }}
                        />
                        <span
                          style={{
                            flex: 1,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            fontWeight: entry.level === 1 ? 700 : 400,
                            color: entry.level === 1 ? 'var(--text-1)' : 'var(--text-2)',
                          }}
                          title={entry.title}
                        >
                          {entry.title}
                        </span>
                        <span className="mono" style={{ flexShrink: 0, fontSize: 'var(--font-size-xs)', color: 'var(--text-3)' }}>
                          pp. {entry.page}–{entry.end_page} ({entry.end_page - entry.page + 1})
                        </span>
                      </label>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
        </Card>
      </div>

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-3)' }}>
            Output
          </div>
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
                {outputDir || 'Same as source file'}
              </span>
              <button onClick={pickOutputDir} disabled={op.status === 'running'} className="btn-ghost">
                Browse
              </button>
            </div>
          </Field>
          <div style={{ marginTop: 'var(--space-3)' }}>
            <Field label="Name template" help="Use {filename} for original name, {n} for page number, {title} for chapter name.">
              <TextInput value={nameTemplate} onChange={setNameTemplate} placeholder="{filename}_page_{n}" />
            </Field>
          </div>
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Splitting…' : 'Split'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('split');
              op.reset();
              toast.info('Split cancelled.');
            }}
          />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>
              {r.output_paths.length} file{r.output_paths.length === 1 ? '' : 's'} created
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {r.output_paths.map((p, i) => (
                <div
                  key={p}
                  className="mono"
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    fontSize: 'var(--font-size-sm)',
                    padding: '6px 10px',
                    background: 'var(--panel-bg-elevated)',
                    borderRadius: 'var(--radius-panel-sm)',
                  }}
                >
                  <span>{bridgeApi.basename(p)}</span>
                  <span style={{ color: 'var(--text-3)' }}>{r.pages_per_output[i] ?? '?'} pages</span>
                </div>
              ))}
            </div>
            {r.output_paths.length > 0 && (
              <div style={{ marginTop: 'var(--space-3)', textAlign: 'right' }}>
                <button onClick={() => bridgeApi.openFolderPath(bridgeApi.dirname(r.output_paths[0]))} className="btn-ghost">
                  Open folder
                </button>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {label}
      </div>
      <div className="mono" style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', color: accent ? 'var(--accent-text)' : 'var(--text-1)' }}>
        {value}
      </div>
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
