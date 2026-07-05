import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { useToast } from '../components/shared/Toast';

export interface RouteDef {
  key: string;
  component: ComponentType;
}

interface RouterContextValue {
  currentRoute: string;
  navigate: (path: string) => void;
  setBusy: (busy: boolean) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

function parseHash(): string {
  const raw = (window.location.hash.slice(2) || '').split('?')[0];
  return raw || 'home';
}

interface RouterProviderProps {
  routes: Record<string, RouteDef>;
  fallback?: string;
  children: ReactNode;
}

/**
 * React port of web/js/router.js. Preserves the exact vanilla contract:
 * hash URL format `#/<key>`, default-to-"home" when the hash is empty,
 * bounce-to-fallback on an unregistered route, and the busy-navigation
 * guard with the exact original warning message (router.js:78).
 *
 * One deliberate behavior change: router.js caches page instances so a
 * page's internal state survives navigating away and back (it never
 * unmounts). This port unmounts on navigate-away like a normal React
 * route — simpler and more maintainable across 20 pages, but state (e.g.
 * a half-filled form) resets on revisit. Flagged for review; can be
 * revisited with a keep-alive wrapper later if that behavior turns out to
 * matter in practice.
 */
export function RouterProvider({ routes, fallback = 'home', children }: RouterProviderProps) {
  const toast = useToast();
  const busyRef = useRef(false);
  const [currentRoute, setCurrentRoute] = useState<string>(() => {
    const path = parseHash();
    return routes[path] ? path : fallback;
  });

  const navigate = useCallback((path: string) => {
    window.location.hash = '#/' + path;
  }, []);

  useEffect(() => {
    const onHashChange = () => {
      const path = parseHash();
      if (path === currentRoute) return;

      if (busyRef.current) {
        window.location.hash = '#/' + currentRoute;
        toast.warning('Operation in progress — please wait or cancel first.');
        return;
      }

      if (!routes[path]) {
        navigate(fallback);
        return;
      }

      setCurrentRoute(path);
    };

    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [currentRoute, routes, fallback, navigate, toast]);

  const setBusy = useCallback((busy: boolean) => {
    busyRef.current = busy;
  }, []);

  return (
    <RouterContext.Provider value={{ currentRoute, navigate, setBusy }}>{children}</RouterContext.Provider>
  );
}

export function useRouter(): RouterContextValue {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error('useRouter() must be used within a <RouterProvider>.');
  return ctx;
}

/**
 * Pages call this with their busy state (e.g. operation.status === 'running')
 * so navigation is blocked mid-operation, mirroring router.js's
 * currentRoute?.instance?.isBusy?.() check.
 */
export function usePageBusy(busy: boolean): void {
  const { setBusy } = useRouter();
  useEffect(() => {
    setBusy(busy);
    return () => setBusy(false);
  }, [busy, setBusy]);
}
