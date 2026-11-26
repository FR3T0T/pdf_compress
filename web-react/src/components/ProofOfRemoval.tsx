import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { bridgeApi } from '../bridge/bridgeApi';
import type { ProofItem, RemovalProof } from '../types/analyze';
import { useToast } from './shared/Toast';

/** Lines that may be long (page lists, file names) wrap anywhere. */
const wrap: CSSProperties = { overflowWrap: 'anywhere', wordBreak: 'break-word' };
const small: CSSProperties = { fontSize: 'var(--font-size-sm)', color: 'var(--text-2)', ...wrap };

/**
 * The proof of removal of a finished Redact or Sanitize run (#100), shown
 * collapsed under the run's own result line, which it does not change.
 *
 * Three groups, each line with its pages in front where it has any: what
 * was removed and the checks that confirm it is gone; what was found and
 * not targeted, with a next step; and what was not checked, so that nothing
 * reads as checked when it was not. Then the earlier-versions line (#122)
 * and the network line (#120 d). Every word comes from the Python side
 * (removal_report, pdf_ops.redaction_proof); check ids only under
 * "Technical details".
 *
 * Save report asks the bridge to write the same, as .json and .txt, beside
 * the new file (never over a file). Only the tool is sent: the bridge kept
 * the run. Not offered for a workspace run, whose file is the app's own.
 */
export function ProofOfRemoval({
  proof,
  tool,
  canSave,
}: {
  proof: RemovalProof;
  tool: 'redact' | 'sanitize';
  canSave: boolean;
}) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const confirmed = proof.checks.filter((c) => c.passed);

  const save = async () => {
    setSaving(true);
    try {
      const res = await bridgeApi.saveRemovalReport(tool);
      if (res.success && res.files?.length) {
        toast.success(`Report saved beside the new file: ${res.files.join(' and ')}.`);
      } else {
        toast.error(res.error || 'The report could not be saved.');
      }
    } catch {
      toast.error('The report could not be saved.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel" style={{ marginTop: 'var(--space-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          style={{
            background: 'transparent',
            border: 'none',
            padding: 0,
            color: 'var(--text-1)',
            fontWeight: 700,
            fontSize: 'var(--font-size-sm)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span className="mono" aria-hidden="true" style={{ color: 'var(--text-3)', width: 12 }}>
            {open ? '▾' : '▸'}
          </span>
          Proof of removal
        </button>
        <span style={{ flex: 1 }} />
        {canSave && (
          <button type="button" className="btn-ghost" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save report'}
          </button>
        )}
      </div>

      {open && (
        <div style={{ marginTop: 'var(--space-2)' }}>
          <Group title="Removed and confirmed gone" items={proof.removed}>
            {confirmed.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={small}>Confirmed by these checks of the new file, all passed:</div>
                <ul style={listStyle}>
                  {confirmed.map((c) => (
                    <li key={c.id} style={{ marginBottom: 2 }}>
                      {c.description}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Group>
          <Group title="Found but not targeted" items={proof.notTargeted} />
          <Group title="Not checked" items={proof.notChecked} />

          <div style={{ ...small, marginTop: 'var(--space-3)' }}>{proof.earlierVersions}</div>
          <div style={{ ...small, marginTop: 4 }}>{proof.network}</div>

          <details style={{ marginTop: 'var(--space-3)' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              Technical details
            </summary>
            <ul style={{ ...listStyle, color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {proof.checks.map((c) => (
                <li key={c.id} style={{ marginBottom: 2 }}>
                  {c.passed ? 'passed' : 'FAILED'} · <span className="mono">{c.id}</span>
                  {c.pages.length > 0 && ` · pages ${c.pages.join(', ')}`}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </div>
  );
}

const listStyle: CSSProperties = {
  margin: '4px 0 0',
  paddingLeft: 18,
  color: 'var(--text-2)',
  fontSize: 'var(--font-size-sm)',
  ...wrap,
};

function Group({ title, items, children }: { title: string; items: ProofItem[]; children?: ReactNode }) {
  return (
    <div style={{ marginTop: 'var(--space-3)' }}>
      <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>{title}</div>
      {items.length === 0 ? (
        <div style={{ ...small, marginTop: 4 }}>Nothing.</div>
      ) : (
        <ul style={listStyle}>
          {items.map((item, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              {item.where && <span style={{ color: 'var(--text-1)' }}>{item.where}: </span>}
              {item.what}
              {item.next && (
                <div style={{ color: 'var(--text-3)', marginTop: 2 }}>Next step: {item.next}</div>
              )}
            </li>
          ))}
        </ul>
      )}
      {children}
    </div>
  );
}
