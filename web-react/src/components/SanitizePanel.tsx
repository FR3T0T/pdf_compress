import { useState } from 'react';
import { SANITIZE_FIELDS } from '../types/analyze';
import type { SanitizeOptions } from '../types/analyze';

interface Props {
  defaults: SanitizeOptions;
  onPickOutput: () => Promise<string | null>;
  onSanitize: (outputPath: string, options: SanitizeOptions) => Promise<void>;
  busy: boolean;
  /** Image mode: strip removes all EXIF/GPS metadata, so the per-item PDF
   *  checkboxes don't apply — hide them and show image-appropriate copy. */
  imageMode?: boolean;
}

export function SanitizePanel({ defaults, onPickOutput, onSanitize, busy, imageMode = false }: Props) {
  const [options, setOptions] = useState<SanitizeOptions>(defaults);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [outputLabel, setOutputLabel] = useState('No output file selected');

  const toggle = (key: keyof SanitizeOptions) => {
    setOptions((o) => ({ ...o, [key]: !o[key] }));
  };

  const pickOutput = async () => {
    const path = await onPickOutput();
    if (path) {
      setOutputPath(path);
      setOutputLabel(path.split(/[/\\]/).pop() ?? path);
    }
  };

  const run = async () => {
    if (!outputPath) return;
    await onSanitize(outputPath, options);
  };

  return (
    <div className="panel">
      <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)', marginBottom: 2 }}>
        {imageMode ? 'Strip metadata' : 'Sanitize'}
      </div>
      <div
        style={{
          color: 'var(--text-2)',
          fontSize: 'var(--font-size-sm)',
          marginBottom: 'var(--space-3)',
        }}
      >
        {imageMode
          ? 'Remove all EXIF metadata (GPS location, camera details, thumbnail, authorship) and write a clean copy. Your original file is never modified.'
          : 'Write a cleaned copy with the selected items removed. Your original file is never modified.'}
      </div>

      {!imageMode && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '2px var(--space-4)',
          }}
        >
          {SANITIZE_FIELDS.map(([key, label]) => (
            <label
              key={key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '6px 0',
                cursor: 'pointer',
                fontSize: 'var(--font-size-sm)',
                color: 'var(--text-1)',
              }}
            >
              <input
                type="checkbox"
                checked={options[key]}
                onChange={() => toggle(key)}
                style={{ accentColor: 'var(--sev-info)' }}
              />
              {label}
            </label>
          ))}
        </div>
      )}

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          marginTop: 'var(--space-4)',
          borderTop: '1px solid var(--border)',
          paddingTop: 'var(--space-3)',
        }}
      >
        <span className="mono" style={{ flex: 1, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
          {outputLabel}
        </span>
        <button
          onClick={pickOutput}
          disabled={busy}
          style={{
            background: 'transparent',
            border: '1px solid var(--border-strong)',
            color: 'var(--text-1)',
            borderRadius: 'var(--radius-panel-sm)',
            padding: '7px 14px',
            fontSize: 'var(--font-size-sm)',
          }}
        >
          Output…
        </button>
        <button
          onClick={run}
          disabled={busy || !outputPath}
          style={{
            background: 'var(--sev-info)',
            border: '1px solid var(--sev-info)',
            color: '#0c0d10',
            fontWeight: 700,
            borderRadius: 'var(--radius-panel-sm)',
            padding: '7px 16px',
            fontSize: 'var(--font-size-sm)',
          }}
        >
          {busy
            ? (imageMode ? 'Cleaning…' : 'Sanitizing…')
            : (imageMode ? 'Clean copy' : 'Sanitize')}
        </button>
      </div>
    </div>
  );
}
