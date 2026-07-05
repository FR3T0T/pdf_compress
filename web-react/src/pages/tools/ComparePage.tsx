import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shared/PageHeader';
import { Card } from '../../components/shared/Card';
import { DropZone } from '../../components/shared/DropZone';
import { ProgressPanel } from '../../components/shared/ProgressPanel';
import { useToast } from '../../components/shared/Toast';
import { useOperation } from '../../bridge/useOperation';
import { bridgeApi } from '../../bridge/bridgeApi';
import { usePageBusy } from '../../router/Router';
import type { PickedFile } from '../../types/bridge';

interface PageDiff {
  page: number;
  status: 'added_in_b' | 'removed_in_b' | 'identical' | 'different';
  details: string;
}
interface MetaDiff {
  field: string;
  a: string;
  b: string;
}
interface CompareResult {
  path_a: string;
  path_b: string;
  page_diffs: PageDiff[];
  metadata_diffs: MetaDiff[];
}

/**
 * React port of web/js/pages/compare.js. Bridge call preserved exactly:
 * BridgeAPI.startCompare({ file_a, file_b }). Result shape verified
 * against pdf_ops.py's CompareResult dataclass — page_diffs includes an
 * entry for EVERY page (not just differing ones) with status
 * 'identical'|'different'|'added_in_b'|'removed_in_b', and metadata_diffs
 * entries use {field, a, b} (not value_a/value_b as vanilla assumed).
 * "Identical" is derived here from the real data rather than read from a
 * nonexistent data.identical field.
 */
export function ComparePage() {
  const toast = useToast();
  const [fileA, setFileA] = useState<PickedFile[]>([]);
  const [fileB, setFileB] = useState<PickedFile[]>([]);
  const op = useOperation<CompareResult>('compare');

  usePageBusy(op.status === 'running');

  useEffect(() => {
    if (op.status === 'done') {
      toast.success('Comparison complete.');
    } else if (op.status === 'error') {
      toast.error(op.error || 'An error occurred during comparison.');
    }
  }, [op.status, op.error, toast]);

  const canRun = fileA.length > 0 && fileB.length > 0 && op.status !== 'running';

  const run = () => {
    if (fileA.length === 0) {
      toast.warning('Please add PDF A.');
      return;
    }
    if (fileB.length === 0) {
      toast.warning('Please add PDF B.');
      return;
    }
    op.run(() => bridgeApi.startCompare({ file_a: fileA[0].path, file_b: fileB[0].path }));
  };

  const r = op.status === 'done' ? op.result?.results : null;
  const changedPages = r ? r.page_diffs.filter((d) => d.status !== 'identical') : [];
  const identical = r ? changedPages.length === 0 && r.metadata_diffs.length === 0 : false;

  return (
    <div className="console">
      <PageHeader title="Compare PDFs" subtitle="Compare two PDF files for differences" backButton={false} />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>PDF A</div>
          <DropZone
            files={fileA}
            onFilesChanged={setFileA}
            multiple={false}
            compact
            title="Drop PDF A here"
            subtitle="or click to browse"
            disabled={op.status === 'running'}
          />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>PDF B</div>
          <DropZone
            files={fileB}
            onFilesChanged={setFileB}
            multiple={false}
            compact
            title="Drop PDF B here"
            subtitle="or click to browse"
            disabled={op.status === 'running'}
          />
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 'var(--space-4)' }}>
        <button onClick={run} disabled={!canRun} className="btn-primary">
          {op.status === 'running' ? 'Comparing…' : 'Compare'}
        </button>
      </div>

      {op.status === 'running' && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <ProgressPanel
            pct={op.progress?.pct ?? 0}
            filename={op.progress?.filename}
            onCancel={() => {
              bridgeApi.cancel('compare');
              op.reset();
              toast.info('Comparison cancelled.');
            }}
          />
        </div>
      )}

      {r && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <Card>
            <div style={{ display: 'flex', gap: 'var(--space-5)', marginBottom: 'var(--space-2)' }}>
              <Stat label="Status" value={identical ? 'Identical' : 'Differences found'} color={identical ? 'var(--sev-info-text)' : 'var(--sev-medium-text)'} />
              <Stat label="PDF A pages" value={String(r.page_diffs.filter((d) => d.status !== 'added_in_b').length)} />
              <Stat label="PDF B pages" value={String(r.page_diffs.filter((d) => d.status !== 'removed_in_b').length)} />
            </div>
          </Card>

          {changedPages.length > 0 && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Card>
                <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>Page differences</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {changedPages.map((d) => (
                    <div
                      key={d.page}
                      style={{
                        display: 'flex',
                        gap: 'var(--space-3)',
                        fontSize: 'var(--font-size-sm)',
                        padding: '6px 10px',
                        background: 'var(--panel-bg-elevated)',
                        borderRadius: 'var(--radius-panel-sm)',
                      }}
                    >
                      <span className="mono" style={{ width: 50, color: 'var(--text-3)' }}>
                        p.{d.page}
                      </span>
                      <span style={{ width: 110, color: 'var(--sev-medium-text)' }}>{d.status.replace(/_/g, ' ')}</span>
                      <span style={{ color: 'var(--text-2)' }}>{d.details}</span>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}

          {r.metadata_diffs.length > 0 && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <Card>
                <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 8 }}>Metadata differences</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {r.metadata_diffs.map((d) => (
                    <div
                      key={d.field}
                      style={{
                        display: 'flex',
                        gap: 'var(--space-3)',
                        fontSize: 'var(--font-size-sm)',
                        padding: '6px 10px',
                        background: 'var(--panel-bg-elevated)',
                        borderRadius: 'var(--radius-panel-sm)',
                      }}
                    >
                      <span className="mono" style={{ width: 90, color: 'var(--text-3)' }}>
                        {d.field.replace('/', '')}
                      </span>
                      <span style={{ flex: 1, color: 'var(--text-2)' }}>A: {d.a || '(empty)'}</span>
                      <span style={{ flex: 1, color: 'var(--text-2)' }}>B: {d.b || '(empty)'}</span>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          )}

          {identical && (
            <div style={{ marginTop: 'var(--space-3)', textAlign: 'center', color: 'var(--text-2)', padding: 'var(--space-5)' }}>
              The two PDF files are identical.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>{label}</div>
      <div className="mono" style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', color: color ?? 'var(--text-1)' }}>
        {value}
      </div>
    </div>
  );
}
