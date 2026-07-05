import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { Select, TextInput } from '../../components/shared/formControls';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface PageOpsResult {
  input_path: string;
  output_path: string;
  operations: string[];
}
type Tab = 'rotate' | 'reorder' | 'delete';

/**
 * React port of web/js/pages/rotate.js (maps to the "page_ops" tool key).
 * Bridge call preserved exactly: BridgeAPI.startPageOps({ file,
 * output_path, rotations? | new_order? | delete_pages? }). Verified
 * against ui/bridge.py's startPageOps and pdf_ops.py's PageOpResult
 * dataclass ({input_path, output_path, operations}) — no progress
 * callback (single-shot).
 */
export function PageOpsPage() {
  const toast = useToast();
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('rotate');
  const [rotateRange, setRotateRange] = useState('');
  const [rotateAngle, setRotateAngle] = useState('90');
  const [newOrder, setNewOrder] = useState('');
  const [deletePages, setDeletePages] = useState('');
  const op = useOperation<PageOpsResult>('page_ops');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('Page operations applied successfully!');
    } else if (op.status === 'error') {
      toast.error(op.error || 'Page operation failed.');
    }
  }, [op.status, op.error, toast]);

  const file = files[0] ?? null;
  const canRun = !!file && !!outputPath && op.status !== 'running';

  const pickOutput = async () => {
    const path = await bridgeApi.saveFile('PDF Files (*.pdf)', 'output.pdf');
    if (path) setOutputPath(path);
  };

  const run = () => {
    if (!file) {
      toast.warning('Please add a PDF file first.');
      return;
    }
    if (!outputPath) {
      toast.warning('Please choose an output file path.');
      return;
    }
    const params: Record<string, unknown> = { file: file.path, output_path: outputPath };
    if (tab === 'rotate') {
      params.rotations = [{ pages: rotateRange.trim() || 'all', angle: parseInt(rotateAngle, 10) }];
    } else if (tab === 'reorder') {
      const order = newOrder
        .trim()
        .split(',')
        .map((s) => parseInt(s.trim(), 10))
        .filter((n) => !Number.isNaN(n));
      if (order.length > 0) params.new_order = order;
    } else if (tab === 'delete') {
      params.delete_pages = deletePages.trim();
    }
    op.run(() => bridgeApi.startPageOps(params));
  };

  const r = op.status === 'done' ? op.result?.results : null;

  return (
    <div className="console">
      <PageHeader title="Page Operations" subtitle="Rotate, reorder, or delete pages in a PDF" backButton={false} />

      <DropZone
        files={files}
        onFilesChanged={setFiles}
        multiple={false}
        title="Drop a PDF file here"
        subtitle="or click to browse"
        disabled={op.status === 'running'}
      />

      <div style={{ marginTop: 'var(--space-3)' }}>
        <Card>
          <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', marginBottom: 'var(--space-4)' }}>
            {(['rotate', 'reorder', 'delete'] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${tab === t ? 'var(--sev-info)' : 'transparent'}`,
                  color: tab === t ? 'var(--sev-info-text)' : 'var(--text-2)',
                  padding: '0 0 var(--space-2) 0',
                  marginRight: 'var(--space-4)',
                  fontSize: 'var(--font-size-sm)',
                  fontWeight: 700,
                  textTransform: 'capitalize',
                }}
              >
                {t === 'delete' ? 'Delete Pages' : t}
              </button>
            ))}
          </div>

          {tab === 'rotate' && (
            <>
              <Field label="Page range">
                <TextInput value={rotateRange} onChange={setRotateRange} placeholder="e.g. 1-3, 5 (leave empty for all pages)" />
              </Field>
              <div style={{ marginTop: 'var(--space-3)' }}>
                <Field label="Rotation angle">
                  <Select
                    value={rotateAngle}
                    onChange={setRotateAngle}
                    options={[
                      { value: '90', label: '90° clockwise' },
                      { value: '180', label: '180°' },
                      { value: '270', label: '270° clockwise (90° counter-clockwise)' },
                    ]}
                  />
                </Field>
              </div>
            </>
          )}

          {tab === 'reorder' && (
            <Field label="New page order" help="Enter page numbers in the desired order, separated by commas.">
              <TextInput value={newOrder} onChange={setNewOrder} placeholder="e.g. 3, 1, 2, 5, 4" />
            </Field>
          )}

          {tab === 'delete' && (
            <Field label="Pages to delete" help="Enter page numbers or ranges to remove from the PDF.">
              <TextInput value={deletePages} onChange={setDeletePages} placeholder="e.g. 2, 4, 7-9" />
            </Field>
          )}
        </Card>
      </div>

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
        </Card>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Applying…' : 'Apply'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('page_ops');
              op.reset();
              toast.info('Cancelled.');
            }}
          />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
              <span className="mono" style={{ flex: 1, fontSize: 'var(--font-size-sm)' }}>
                {bridgeApi.basename(r.output_path)}
              </span>
              <span style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>{r.operations.join(', ')}</span>
              <button onClick={() => bridgeApi.openFolderPath(bridgeApi.dirname(r.output_path))} className="btn-ghost">
                Open folder
              </button>
            </div>
          </Card>
        </div>
      )}
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
