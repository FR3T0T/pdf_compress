import { useEffect, useRef } from 'react';
import type { DonePayload, ProgressPayload } from '../types/bridge';
import { mockEventBus } from './mockEventBus';

/**
 * Event name -> payload type map for window.EventBus (web/js/app.js).
 * "progress"/"done" are fed by the progressUpdate/operationDone Qt signals;
 * "files-dropped" by the filesDropped signal; "theme" is emitted locally by
 * applyTheme() after a themeChanged signal (payload is just the theme name,
 * not the full CSS var map — see app.js:216).
 *
 * "files-dropped" payload is a bare string[], NOT {paths: string[]} --
 * web_shell.py emits `json.dumps(paths)` (a plain list) and app.js forwards
 * `JSON.parse(jsonStr)` straight through with no wrapping (app.js:132-135).
 */
export interface EventMap {
  progress: ProgressPayload;
  done: DonePayload;
  'files-dropped': string[];
  theme: string;
}

/**
 * Subscribe to an EventBus event for the lifetime of a component. Uses
 * window.EventBus when running inside the app; falls back to the local
 * mockEventBus in plain-browser dev, so pages don't need mock-specific
 * branching — bridgeApi's mock startXxx() calls emit on the same bus this
 * hook listens to (see mockEventBus.ts).
 */
export function useEventBus<K extends keyof EventMap>(
  event: K,
  handler: (data: EventMap[K]) => void
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const bus = window.EventBus ?? mockEventBus;
    const wrapped = (data: unknown) => handlerRef.current(data as EventMap[K]);
    bus.on(event, wrapped);
    return () => bus.off(event, wrapped);
  }, [event]);
}
