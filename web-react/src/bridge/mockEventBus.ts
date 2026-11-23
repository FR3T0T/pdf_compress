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

/** Tools whose simulated run the user cancelled: it ends the way ui/bridge.py's
 *  worker ends a cancelled run (#135), with a "done" that says "cancelled". */
const cancelledTools = new Set<string>();

export function cancelSimulated(toolKey: string): void {
  cancelledTools.add(toolKey);
}

function endedByCancel(toolKey: string): boolean {
  if (!cancelledTools.has(toolKey)) return false;
  cancelledTools.delete(toolKey);
  mockEventBus.emit('done', { toolKey, success: false, message: 'Cancelled', cancelled: true });
  return true;
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
  cancelledTools.delete(toolKey);

  items.forEach((f, i) => {
    setTimeout(() => {
      if (cancelledTools.has(toolKey)) return;
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
      if (endedByCancel(toolKey)) return;
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

/**
 * Simulate an operation that ends refused: a short progress, then a "done"
 * event with success false, *message*, and *extra* merged in at the top
 * level — the shape ui/bridge.py's _done_payload gives a refusal.
 */
export function simulateFailure(toolKey: string, message: string, extra: Record<string, unknown>): void {
  cancelledTools.delete(toolKey);
  setTimeout(() => {
    if (cancelledTools.has(toolKey)) return;
    mockEventBus.emit('progress', { toolKey, current: 1, total: 1, pct: 100, filename: 'document.pdf' });
  }, 300);
  setTimeout(() => {
    if (endedByCancel(toolKey)) return;
    mockEventBus.emit('done', { toolKey, success: false, message, ...extra });
  }, 550);
}
