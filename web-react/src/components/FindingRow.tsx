import { useState } from 'react';
import type { Finding } from '../types/analyze';
import { SEVERITY_META } from './severityMeta';

export function FindingRow({ finding }: { finding: Finding }) {
  const sev = SEVERITY_META[finding.severity];
  const hasDetail = Boolean(finding.detail) || Boolean(finding.items && finding.items.length > 0);
  const [open, setOpen] = useState(false);

  return (
    <div
      style={{
        borderLeft: `2px solid ${sev.color}`,
        background: 'var(--panel-bg-elevated)',
        borderRadius: '0 var(--radius-panel-sm) var(--radius-panel-sm) 0',
        padding: '10px 12px',
      }}
    >
      <div
        role={hasDetail ? 'button' : undefined}
        tabIndex={hasDetail ? 0 : undefined}
        onClick={() => hasDetail && setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (hasDetail && (e.key === 'Enter' || e.key === ' ')) setOpen((o) => !o);
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          cursor: hasDetail ? 'pointer' : 'default',
        }}
      >
        <span
          className="mono"
          style={{ color: sev.textColor, fontSize: 'var(--font-size-sm)', fontWeight: 600 }}
        >
          {finding.id}
        </span>
        <span style={{ color: 'var(--text-1)', fontSize: 'var(--font-size-sm)', flex: 1 }}>
          {finding.title}
        </span>
        {!!finding.count && finding.count > 1 && (
          <span className="mono" style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)' }}>
            ×{finding.count}
          </span>
        )}
        {hasDetail && (
          <span
            className="mono"
            aria-hidden="true"
            style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', width: 12, textAlign: 'center' }}
          >
            {open ? '▾' : '▸'}
          </span>
        )}
      </div>

      {open && hasDetail && (
        <div style={{ marginTop: 8, paddingLeft: 2 }}>
          {finding.detail && (
            <p
              style={{
                margin: 0,
                color: 'var(--text-2)',
                fontSize: 'var(--font-size-sm)',
                lineHeight: 1.5,
              }}
            >
              {finding.detail}
            </p>
          )}
          {finding.items && finding.items.length > 0 && (
            <ul
              className="mono"
              style={{
                margin: '8px 0 0',
                padding: 0,
                listStyle: 'none',
                color: 'var(--text-2)',
                fontSize: 'var(--font-size-xs)',
              }}
            >
              {finding.items.map((item, i) => (
                <li
                  key={i}
                  style={{
                    padding: '3px 0',
                    borderTop: i === 0 ? 'none' : '1px solid var(--border)',
                    wordBreak: 'break-word',
                  }}
                >
                  {item}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
