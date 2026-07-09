import type { Preset } from '../../types/bridge';

interface PresetCardsProps {
  presets: Preset[];
  selected: string;
  onChange: (key: string) => void;
}

/** React port of createPresetCards() from web/js/components.js. */
export function PresetCards({ presets, selected, onChange }: PresetCardsProps) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${presets.length}, 1fr)`, gap: 8 }}>
      {presets.map((p) => {
        const active = p.key === selected;
        return (
          <button
            key={p.key}
            onClick={() => onChange(p.key)}
            style={{
              textAlign: 'left',
              background: active ? 'var(--panel-bg-elevated)' : 'var(--panel-bg)',
              border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 'var(--radius-panel-sm)',
              padding: '10px 12px',
              color: 'var(--text-1)',
              cursor: 'pointer',
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)' }}>{p.name}</div>
            <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-xs)', marginTop: 2 }}>
              {p.description}
            </div>
          </button>
        );
      })}
    </div>
  );
}
