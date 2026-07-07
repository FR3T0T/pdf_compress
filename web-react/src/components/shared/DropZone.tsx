import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import type { PickedFile } from '../../types/bridge';
import { bridgeApi, isRealBridge } from '../../bridge/bridgeApi';

/** Imperative handle so a page can trigger the picker (e.g. from a Ctrl+O hotkey). */
export interface DropZoneHandle {
  open: () => void;
}

interface DropZoneProps {
  /** Current picked files — DropZone is controlled; it never holds its own list. */
  files: PickedFile[];
  onFilesChanged: (files: PickedFile[]) => void;
  title?: string;
  subtitle?: string;
  accept?: string;
  multiple?: boolean;
  compact?: boolean;
  disabled?: boolean;
}

/**
 * React port of createDropZone() from web/js/components.js. Controlled by
 * the parent's `files` state (unlike the vanilla version's internal
 * `_files` array) so pages can remove/reorder via a separate FileList
 * without two sources of truth.
 *
 * Real app: native OS drag-drop paths arrive from Python via the
 * `files-dropped` EventBus signal (HTML5 File.path isn't available inside
 * QWebEngine), and click opens BridgeAPI.openFiles(accept). Plain-browser
 * dev (no window.BridgeAPI): falls back to a real <input type="file">.
 */
export const DropZone = forwardRef<DropZoneHandle, DropZoneProps>(function DropZone(
  {
    files,
    onFilesChanged,
    title = 'Drop PDF files here',
    subtitle = 'or click to browse',
    accept = 'PDF Files (*.pdf)',
    multiple = true,
    compact = false,
    disabled = false,
  },
  ref
) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addPaths = (paths: string[]) => {
    const existing = new Set(files.map((f) => f.path));
    const additions = paths
      .filter((p) => !existing.has(p))
      .map((p) => ({ path: p, name: bridgeApi.basename(p) }));
    if (additions.length === 0) return;
    onFilesChanged(multiple ? [...files, ...additions] : [additions[0]]);
  };

  // Resubscribes whenever `files` changes so addPaths' dedup/merge closes
  // over the current list rather than a stale one from the first render.
  useEffect(
    () => bridgeApi.onFilesDropped((paths) => addPaths(multiple ? paths : paths.slice(0, 1))),
    [multiple, files]
  );

  const browse = async () => {
    if (disabled) return;
    if (isRealBridge()) {
      const paths = await bridgeApi.openFiles(accept);
      if (paths.length) addPaths(multiple ? paths : paths.slice(0, 1));
    } else {
      inputRef.current?.click();
    }
  };

  useImperativeHandle(ref, () => ({ open: browse }));

  const summary =
    files.length === 1
      ? files[0].name
      : `${files.length} files selected`;

  return (
    <div
      className="panel"
      onClick={browse}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        // Real path handling for OS drops is via the EventBus listener
        // above; this just clears the browser's own drag state.
      }}
      style={{
        cursor: disabled ? 'default' : 'pointer',
        textAlign: 'center',
        padding: compact ? 'var(--space-3) var(--space-4)' : 'var(--space-5) var(--space-4)',
        borderStyle: 'dashed',
        borderColor: dragOver ? 'var(--sev-info)' : 'var(--border)',
        background: dragOver ? 'var(--panel-bg-elevated)' : 'var(--panel-bg)',
        transition: 'border-color 120ms, background 120ms',
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {compact && files.length > 0 ? (
        <div className="mono" style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-2)' }}>
          {summary} — click or drop to add more
        </div>
      ) : (
        <>
          <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)' }}>{title}</div>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginTop: 4 }}>
            {subtitle} — the file never leaves your computer
          </div>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        multiple={multiple}
        hidden
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => {
          const picked = Array.from(e.target.files ?? []);
          if (picked.length) {
            const additions = picked.map((f) => ({ path: f.name, name: f.name, size: f.size }));
            onFilesChanged(multiple ? [...files, ...additions] : [additions[0]]);
          }
          e.target.value = '';
        }}
      />
    </div>
  );
});
