import { useCallback, useState } from 'react';
import type { DonePayload, ProgressPayload } from '../types/bridge';
import { useEventBus } from './eventBus';
import { bridgeApi } from './bridgeApi';

export type OperationStatus = 'idle' | 'running' | 'done' | 'error';

export interface UseOperationResult<TResult = unknown> {
  status: OperationStatus;
  progress: ProgressPayload | null;
  result: DonePayload<TResult> | null;
  error: string | null;
  /** Call with a closure that fires the tool's bridgeApi.startXxx(params). */
  run: (startFn: () => void) => void;
  cancel: () => void;
  reset: () => void;
}

/**
 * Shared state machine for the vanilla app's
 * `BridgeAPI.startXxx(params)` -> `EventBus.on('progress'/'done', ...)`
 * pattern. One hook instance per tool page; `toolKey` must match the
 * Python bridge's default tool_key for that operation exactly (see
 * TOOL_KEYS in bridgeApi.ts) so progress/done events aren't dropped.
 */
export function useOperation<TResult = unknown>(toolKey: string): UseOperationResult<TResult> {
  const [status, setStatus] = useState<OperationStatus>('idle');
  const [progress, setProgress] = useState<ProgressPayload | null>(null);
  const [result, setResult] = useState<DonePayload<TResult> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEventBus('progress', (data) => {
    if (data.toolKey !== toolKey) return;
    setProgress(data);
  });

  useEventBus('done', (data) => {
    if (data.toolKey !== toolKey) return;
    setResult(data as DonePayload<TResult>);
    setStatus(data.success ? 'done' : 'error');
    if (!data.success) setError(data.message || 'Operation failed.');
  });

  const run = useCallback((startFn: () => void) => {
    setStatus('running');
    setProgress(null);
    setResult(null);
    setError(null);
    startFn();
  }, []);

  const cancel = useCallback(() => {
    bridgeApi.cancel(toolKey);
  }, [toolKey]);

  const reset = useCallback(() => {
    setStatus('idle');
    setProgress(null);
    setResult(null);
    setError(null);
  }, []);

  return { status, progress, result, error, run, cancel, reset };
}
