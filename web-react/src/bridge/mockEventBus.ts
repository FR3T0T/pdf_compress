import type { RealEventBus } from '../types/global';
import type { ProgressPayload, DonePayload } from '../types/bridge';

/**
 * Same shape as web/js/app.js's EventBus, used only when window.EventBus
 * doesn't exist (plain-browser dev). Lets bridgeApi's mock startXxx()
 * implementations simulate progress/done sequences so useOperation()
 * behaves identically in mock and real mode — no mock-specific branching
 * needed in the pages that consume it.
 */
function createEventBus(): RealEventBus {
  const listeners: Record<string, Array<(data: unknown) => void>> = {};
  return {
    on(event, cb) {
      (listeners[event] ??= []).push(cb);
    },
    off(event, cb) {
      const list = listeners[event];
      if (!list) return;
      const idx = list.indexOf(cb);
      if (idx !== -1) list.splice(idx, 1);
    },
    emit(event, data) {
      (listeners[event] ?? []).forEach((cb) => cb(data));
    },
  };
}

export const mockEventBus = createEventBus();

/** A minimal file-like shape good enough for a plausible progress sequence. */
export interface MockFileLike {
  name: string;
}

/**
 * Simulate a fire-and-forget operation for mock mode: emits a "progress"
 * event per file, then a "done" event, on mockEventBus. Timing is short
 * but non-instant so loading/progress UI is visible in `vite dev`.
 */
export function simulateOperation(
  toolKey: string,
  files: MockFileLike[],
  buildResults: () => unknown = () => files.map((f) => ({ name: f.name, status: 'done' }))
): void {
  const total = Math.max(files.length, 1);
  const items = files.length > 0 ? files : [{ name: 'document.pdf' }];

  items.forEach((f, i) => {
    setTimeout(() => {
      const payload: ProgressPayload = {
        toolKey,
        current: i + 1,
        total,
        pct: Math.round(((i + 1) / total) * 100),
        filename: f.name,
      };
      mockEventBus.emit('progress', payload);
    }, 300 * (i + 1));
  });

  setTimeout(
    () => {
      const payload: DonePayload = {
        toolKey,
        success: true,
        message: '',
        results: buildResults(),
      };
      mockEventBus.emit('done', payload);
    },
    300 * (items.length + 1) + 250
  );
}
