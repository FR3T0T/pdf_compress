import { useEffect, useState } from 'react';
import { Sidebar } from './Sidebar';
import { useRouter, PageActiveContext } from '../router/Router';
import type { RouteDef } from '../router/Router';
import { WorkspaceBar } from '../workspace/WorkspaceBar';

/**
 * Top-level layout: sidebar + main content area, matching web/index.html's
 * #sidebar / #main / #page-content structure. There's no separate global
 * topbar in the vanilla app — each page owns its own header via
 * PageHeader (see web/js/components.js's createPageHeader, ported in
 * Phase 1) — so none is added here. Flagged for you to confirm; happy to
 * add one (e.g. for global search) if that's what was intended.
 *
 * Keep-alive: each route is mounted the first time it's visited and then
 * kept mounted, toggled with `display:none`, instead of unmounting on
 * navigate-away. This restores the vanilla router's cached-page-instance
 * behavior (web/js/router.js) so a half-filled form survives navigating
 * away and back. Pages are mounted lazily (only once visited), and only
 * one tool page runs an operation at a time — the router blocks navigation
 * while an op is in progress — so hidden pages are inert. Mount-only
 * effects (settings load, preset fetch) run once per session rather than
 * on every visit, which is the intended win, not a regression.
 */
export function AppShell({ routes }: { routes: Record<string, RouteDef> }) {
  const { currentRoute } = useRouter();
  const [mounted, setMounted] = useState<string[]>(() =>
    routes[currentRoute] ? [currentRoute] : []
  );

  useEffect(() => {
    if (!routes[currentRoute]) return;
    setMounted((prev) => (prev.includes(currentRoute) ? prev : [...prev, currentRoute]));
  }, [currentRoute, routes]);

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <main style={{ flex: '1 1 auto', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        {/* Global, persistent across every tool page -- driven by
            WorkspaceContext (wraps RouterProvider in App.tsx), not by
            AppShell's per-route keep-alive below. */}
        <WorkspaceBar />
        <div style={{ flex: '1 1 auto', overflowY: 'auto' }}>
          {mounted.map((key) => {
            const Route = routes[key]?.component;
            if (!Route) return null;
            const active = key === currentRoute;
            return (
              <div key={key} style={{ display: active ? 'block' : 'none' }} aria-hidden={!active}>
                <PageActiveContext.Provider value={active}>
                  <Route />
                </PageActiveContext.Provider>
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
