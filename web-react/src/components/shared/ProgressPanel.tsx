interface ProgressPanelProps {
  pct: number;
  filename?: string;
  current?: number;
  total?: number;
  onCancel?: () => void;
}

/** React port of createProgressPanel() from web/js/components.js. */
export function ProgressPanel({ pct, filename, current, total, onCancel }: ProgressPanelProps) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
          fontSize: 'var(--font-size-sm)',
        }}
      >
        <span className="mono" style={{ color: 'var(--text-2)' }}>
          {typeof current === 'number' && typeof total === 'number' ? `${current} / ${total}` : 'Working…'}
          {filename ? ` — ${filename}` : ''}
        </span>
        <span className="mono" style={{ color: 'var(--text-1)', fontWeight: 700 }}>
          {clamped.toFixed(0)}%
        </span>
      </div>
      <div
        style={{
          height: 6,
          borderRadius: 'var(--radius-badge)',
          background: 'var(--border)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${clamped}%`,
            background: 'var(--accent)',
            transition: 'width 150ms ease-out',
          }}
        />
      </div>
      {onCancel && (
        <div style={{ marginTop: 'var(--space-3)', textAlign: 'right' }}>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              border: '1px solid var(--border-strong)',
              color: 'var(--text-1)',
              borderRadius: 'var(--radius-panel-sm)',
              padding: '6px 12px',
              fontSize: 'var(--font-size-sm)',
            }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
