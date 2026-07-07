import type { CSSProperties } from 'react';
import type { PickedFile } from '../../types/bridge';
import { bridgeApi } from '../../bridge/bridgeApi';

interface FileListProps {
  files: PickedFile[];
  onRemove: (index: number) => void;
  onReorder?: (fromIndex: number, toIndex: number) => void;
  emptyMessage?: string;
}

/**
 * React port of createFileList() from web/js/components.js. Reordering
 * uses up/down buttons rather than drag-and-drop — no extra dependency,
 * and the vanilla drag-reorder UX isn't part of the bridge contract we
 * need to preserve.
 */
export function FileList({ files, onRemove, onReorder, emptyMessage = 'No files added yet.' }: FileListProps) {
  if (files.length === 0) {
    return (
      <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-sm)', padding: 'var(--space-3) 0' }}>
        {emptyMessage}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {files.map((f, i) => (
        <div
          key={f.path}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            background: 'var(--panel-bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-panel-sm)',
            padding: '7px 10px',
          }}
        >
          <span className="mono" style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', width: 18 }}>
            {i + 1}
          </span>
          <span className="mono" style={{ flex: 1, fontSize: 'var(--font-size-sm)', wordBreak: 'break-word' }} title={f.path}>
            {f.name}
          </span>
          {typeof f.pages === 'number' && (
            <span className="mono" style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {f.pages} {f.pages === 1 ? 'page' : 'pages'}
            </span>
          )}
          {typeof f.size === 'number' && (
            <span className="mono" style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
              {bridgeApi.formatSize(f.size)}
            </span>
          )}
          {onReorder && (
            <>
              <button
                aria-label="Move up"
                disabled={i === 0}
                onClick={() => onReorder(i, i - 1)}
                style={iconButtonStyle}
              >
                ▲
              </button>
              <button
                aria-label="Move down"
                disabled={i === files.length - 1}
                onClick={() => onReorder(i, i + 1)}
                style={iconButtonStyle}
              >
                ▼
              </button>
            </>
          )}
          <button aria-label="Remove" onClick={() => onRemove(i)} style={iconButtonStyle}>
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

const iconButtonStyle: CSSProperties = {
  background: 'transparent',
  border: 'none',
  color: 'var(--text-3)',
  cursor: 'pointer',
  fontSize: 11,
  padding: 4,
  lineHeight: 1,
};
