import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { Icon, type IconName } from '../components/shared/Icon';
import { bridgeApi } from '../bridge/bridgeApi';
import { useRouter } from '../router/Router';
import type { ToolRegistry } from '../types/bridge';

const EMPTY_REGISTRY: ToolRegistry = { tools: [], categories: [] };

/**
 * React port of renderSidebar() from web/js/app.js, sourced from
 * BridgeAPI.getToolRegistry() (called directly on App.bridge in the
 * vanilla app, same here — see bridgeApi.getToolRegistry()).
 *
 * Theme toggle is fully self-contained here: it sets [data-theme] on
 * <html> directly and persists via the same saveSetting/loadSetting
 * mechanism already used for sidebar_collapsed below, rather than
 * round-tripping through Python's requestThemeToggle()/themeChanged.
 * That Python-side mechanism pushes vanilla's `--color-*` variable names
 * (see web_shell.py's _theme_to_css_vars), which theme.css never defines,
 * so it can't drive our CSS — and it's a SEPARATE persisted preference
 * from ours (its own QSettings "theme" key, toggled independently by the
 * Ctrl+T shortcut), so trusting its boot-time push here would silently
 * overwrite a just-restored user preference with whatever Python
 * remembers. requestThemeToggle() is still called best-effort on click
 * purely so Ctrl+T (which only affects Python's own, cosmetically inert
 * state) points the same direction the user last picked — it has no
 * bearing on what's actually rendered.
 */
export function Sidebar() {
  const { currentRoute, navigate } = useRouter();
  const [registry, setRegistry] = useState<ToolRegistry>(EMPTY_REGISTRY);
  const [collapsed, setCollapsed] = useState(true);
  const [themeName, setThemeName] = useState<'light' | 'dark'>('dark');

  useEffect(() => {
    bridgeApi.getToolRegistry().then(setRegistry);
    bridgeApi.loadSetting('sidebar_collapsed').then((raw) => {
      // Load-bearing fix vs. vanilla: app.js compares a JSON.parse()'d
      // boolean against the string "false", which is always unequal and
      // silently defeats persistence. Same saveSetting/loadSetting keys
      // and value format ("true"/"false" strings) — just a correct
      // comparison on the read side.
      setCollapsed(raw !== 'false');
    });
    bridgeApi.loadSetting('theme').then((raw) => {
      const name = raw === 'light' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', name);
      setThemeName(name);
    });
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      bridgeApi.saveSetting('sidebar_collapsed', next ? 'true' : 'false');
      return next;
    });
  };

  const toggleTheme = () => {
    setThemeName((cur) => {
      const next = cur === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      bridgeApi.saveSetting('theme', next);
      bridgeApi.requestThemeToggle();
      return next;
    });
  };

  const categorySections = registry.categories
    .map((cat) => ({ ...cat, tools: registry.tools.filter((t) => t.category === cat.key) }))
    .filter((c) => c.tools.length > 0);

  return (
    <aside
      className="sidebar-shell"
      style={{
        width: collapsed ? 56 : 224,
        transition: 'width 150ms ease-out',
        flex: '0 0 auto',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--panel-bg)',
        borderRight: '1px solid var(--border)',
        height: '100%',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 12px',
          borderBottom: '1px solid var(--border)',
          flex: '0 0 auto',
        }}
      >
        <div
          className="mono"
          style={{
            width: 28,
            height: 28,
            borderRadius: 'var(--radius-panel-sm)',
            background: 'var(--sev-info)',
            color: '#0c0d10',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 800,
            flex: '0 0 auto',
          }}
        >
          P
        </div>
        {!collapsed && (
          <span style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', whiteSpace: 'nowrap' }}>
            PDF Toolkit
          </span>
        )}
      </div>

      <nav
        style={{
          flex: '1 1 auto',
          overflowY: 'auto',
          padding: '8px 6px',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        <NavItem
          icon="home"
          label="Home"
          collapsed={collapsed}
          active={currentRoute === 'home'}
          onClick={() => navigate('home')}
        />

        {categorySections.map((cat) => (
          <div key={cat.key} style={{ marginTop: 10 }}>
            {!collapsed && (
              <div
                style={{
                  fontSize: 'var(--font-size-xs)',
                  fontWeight: 700,
                  letterSpacing: '0.04em',
                  textTransform: 'uppercase',
                  color: 'var(--text-3)',
                  padding: '4px 10px',
                }}
              >
                {cat.label}
              </div>
            )}
            {cat.tools.map((tool) => (
              <NavItem
                key={tool.key}
                icon={tool.icon as IconName}
                label={tool.title}
                collapsed={collapsed}
                active={currentRoute === tool.key}
                onClick={() => navigate(tool.key)}
              />
            ))}
          </div>
        ))}
      </nav>

      <div style={{ borderTop: '1px solid var(--border)', padding: '6px', flex: '0 0 auto' }}>
        <button
          onClick={toggleCollapsed}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          style={navButtonStyle}
        >
          <span style={{ display: 'flex', width: 20, justifyContent: 'center' }}>
            <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round">
              <polyline points={collapsed ? '5,3 9,7 5,11' : '9,3 5,7 9,11'} />
            </svg>
          </span>
          {!collapsed && <span>Collapse</span>}
        </button>
        <button
          onClick={toggleTheme}
          title={themeName === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          style={navButtonStyle}
        >
          <Icon name={themeName === 'light' ? 'moon' : 'sun'} size={16} />
          {!collapsed && <span>{themeName === 'light' ? 'Dark mode' : 'Light mode'}</span>}
        </button>
      </div>
    </aside>
  );
}

function NavItem({
  icon,
  label,
  collapsed,
  active,
  onClick,
}: {
  icon: IconName;
  label: string;
  collapsed: boolean;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      style={{
        ...navButtonStyle,
        background: active ? 'var(--panel-bg-elevated)' : 'transparent',
        color: active ? 'var(--sev-info-text)' : 'var(--text-2)',
        borderLeft: active ? '2px solid var(--sev-info)' : '2px solid transparent',
      }}
    >
      <span style={{ display: 'flex', width: 20, justifyContent: 'center', flex: '0 0 auto' }}>
        <Icon name={icon} size={16} />
      </span>
      {!collapsed && <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>}
    </button>
  );
}

const navButtonStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  width: '100%',
  padding: '8px 8px',
  background: 'transparent',
  border: 'none',
  borderRadius: 'var(--radius-panel-sm)',
  color: 'var(--text-2)',
  fontSize: 'var(--font-size-sm)',
  textAlign: 'left',
  cursor: 'pointer',
};
