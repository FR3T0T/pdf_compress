import { Sidebar } from './Sidebar';
import { useRouter } from '../router/Router';
import type { RouteDef } from '../router/Router';

/**
 * Top-level layout: sidebar + main content area, matching web/index.html's
 * #sidebar / #main / #page-content structure. There's no separate global
 * topbar in the vanilla app — each page owns its own header via
 * PageHeader (see web/js/components.js's createPageHeader, ported in
 * Phase 1) — so none is added here. Flagged for you to confirm; happy to
 * add one (e.g. for global search) if that's what was intended.
 */
export function AppShell({ routes }: { routes: Record<string, RouteDef> }) {
  const { currentRoute } = useRouter();
  const Route = routes[currentRoute]?.component;

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      <Sidebar />
      <main style={{ flex: '1 1 auto', overflowY: 'auto' }}>
        {Route ? <Route /> : null}
      </main>
    </div>
  );
}
