import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

type ToastKind = 'success' | 'error' | 'warning' | 'info';

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

interface ToastContextValue {
  push: (kind: ToastKind, message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const KIND_STYLES: Record<ToastKind, string> = {
  success: 'var(--sev-info)',
  error: 'var(--sev-high)',
  warning: 'var(--sev-medium)',
  info: 'var(--sev-low)',
};

/**
 * React-idiomatic replacement for web/js/components.js's Toast singleton
 * (which injects DOM nodes directly). Wrap the app once in main.tsx/App.tsx;
 * any descendant calls useToast().success/error/warning/info(message).
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const push = useCallback((kind: ToastKind, message: string, duration = 4000) => {
    const id = ++idRef.current;
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), duration);
  }, []);

  // Stable context value: without this, every ToastProvider re-render
  // (including ones caused by pushing a toast) creates a NEW object here,
  // which cascades into a new useToast() return object below, which
  // retriggers any effect that lists a toast method in its deps — an
  // infinite render loop in practice (caught via visual testing on
  // RepairPage: a single toast.success() call fired 500+ times).
  const contextValue = useMemo<ToastContextValue>(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={contextValue}>
      {children}
      <div
        style={{
          position: 'fixed',
          right: 16,
          bottom: 16,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          zIndex: 1000,
          maxWidth: 360,
        }}
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className="panel"
            style={{
              borderLeftWidth: 3,
              borderLeftStyle: 'solid',
              borderLeftColor: KIND_STYLES[t.kind],
              fontSize: 'var(--font-size-sm)',
              color: 'var(--text-1)',
              boxShadow: '0 4px 16px rgba(0,0,0,0.35)',
            }}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast() must be used within a <ToastProvider>.');
  // Stable return value (see contextValue comment above) so `toast` is
  // safe to list in a useEffect dependency array without retriggering it
  // every render.
  return useMemo(
    () => ({
      success: (message: string, duration?: number) => ctx.push('success', message, duration),
      error: (message: string, duration?: number) => ctx.push('error', message, duration),
      warning: (message: string, duration?: number) => ctx.push('warning', message, duration),
      info: (message: string, duration?: number) => ctx.push('info', message, duration),
    }),
    [ctx]
  );
}
