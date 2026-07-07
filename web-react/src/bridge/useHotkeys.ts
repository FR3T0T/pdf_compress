import { useEffect } from 'react';

export interface HotkeyHandlers {
  /** Ctrl/Cmd+O — open the file picker / add files. */
  onAddFiles?: () => void;
  /** Ctrl/Cmd+Enter — run the page's primary action. */
  onRun?: () => void;
  /** Escape — clear the current selection (ignored while typing in a field). */
  onClear?: () => void;
  /** Master switch; when false the listener isn't attached. Default true. */
  enabled?: boolean;
}

function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

/**
 * Restores the per-page keyboard shortcuts the vanilla frontend had on
 * web/js/pages/{merge,split,watermark}.js: Ctrl/Cmd+O add files,
 * Ctrl/Cmd+Enter run, Esc clear. Cross-platform (Ctrl or Cmd).
 *
 * Only one tool page is mounted at a time (the router unmounts the rest),
 * so a per-page window listener never competes with another page's. Any
 * handler left undefined is simply inert for that key. App-level shortcuts
 * (Ctrl+T theme, Ctrl+Home dashboard) live in Python (ui/web_shell.py) and
 * are unaffected.
 */
export function useHotkeys({ onAddFiles, onRun, onClear, enabled = true }: HotkeyHandlers): void {
  useEffect(() => {
    if (!enabled) return;

    const handler = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;

      if (mod && (e.key === 'o' || e.key === 'O')) {
        if (!onAddFiles) return;
        e.preventDefault();
        onAddFiles();
      } else if (mod && e.key === 'Enter') {
        if (!onRun) return;
        e.preventDefault();
        onRun();
      } else if (e.key === 'Escape') {
        // Don't hijack Esc while the user is typing — there it should blur or
        // close, not wipe the file selection.
        if (!onClear || isEditableTarget(e.target)) return;
        e.preventDefault();
        onClear();
      }
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onAddFiles, onRun, onClear, enabled]);
}
