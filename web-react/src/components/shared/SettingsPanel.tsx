import { useState } from 'react';
import type { ReactNode } from 'react';

interface SettingsPanelProps {
  title?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** React port of createSettingsPanel() from web/js/components.js. */
export function SettingsPanel({ title = 'Advanced settings', defaultOpen = false, children }: SettingsPanelProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="panel">
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--text-1)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: 0,
          fontSize: 'var(--font-size-sm)',
          fontWeight: 700,
          cursor: 'pointer',
        }}
      >
        <span className="mono" style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', width: 12 }}>
          {open ? '▾' : '▸'}
        </span>
        {title}
      </button>
      {open && <div style={{ marginTop: 'var(--space-3)' }}>{children}</div>}
    </div>
  );
}
