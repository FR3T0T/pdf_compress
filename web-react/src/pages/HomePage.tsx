import { useEffect, useMemo, useState } from 'react';
import { Icon, type IconName } from '../components/shared/Icon';
import { bridgeApi } from '../bridge/bridgeApi';
import { useRouter } from '../router/Router';
import type { ToolDef, ToolRegistry } from '../types/bridge';

const EMPTY_REGISTRY: ToolRegistry = { tools: [], categories: [] };

/**
 * React port of web/js/pages/home.js: hero + search bar + tool-count
 * badge + category sections of tool cards. Search matches title/
 * description substrings, same as the vanilla _onSearch().
 */
export function HomePage() {
  const { navigate } = useRouter();
  const [registry, setRegistry] = useState<ToolRegistry>(EMPTY_REGISTRY);
  const [query, setQuery] = useState('');

  useEffect(() => {
    bridgeApi.getToolRegistry().then(setRegistry);
  }, []);

  const q = query.trim().toLowerCase();

  const sections = useMemo(() => {
    return registry.categories
      .map((cat) => ({
        ...cat,
        tools: registry.tools.filter((t) => t.category === cat.key),
      }))
      .filter((c) => c.tools.length > 0)
      .map((c) => ({
        ...c,
        visibleTools: c.tools.filter(
          (t) =>
            q.length === 0 ||
            t.title.toLowerCase().includes(q) ||
            t.description.toLowerCase().includes(q)
        ),
      }))
      .filter((c) => c.visibleTools.length > 0);
  }, [registry, q]);

  const visibleCount = sections.reduce((n, s) => n + s.visibleTools.length, 0);
  const countLabel =
    q.length > 0
      ? `${visibleCount} match${visibleCount === 1 ? '' : 'es'}`
      : `${registry.tools.length} tools`;

  return (
    <div className="console">
      <div style={{ marginBottom: 'var(--space-5)' }}>
        <h1 className="console-heading">PDF Toolkit</h1>
        <p className="console-subheading">
          Everything you need to work with PDFs — fully offline, no account required
        </p>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 'var(--space-3)' }}>
          <div className="field" style={{ flex: '1 1 auto', maxWidth: 400 }}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="var(--text-3)" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
              <circle cx="7" cy="7" r="4.5" />
              <line x1="10.5" y1="10.5" x2="14" y2="14" />
            </svg>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search tools…"
              style={{
                flex: 1,
                background: 'transparent',
                border: 'none',
                color: 'var(--text-1)',
                fontSize: 'var(--font-size-sm)',
                outline: 'none',
              }}
            />
            {query.length > 0 && (
              <button
                onClick={() => setQuery('')}
                aria-label="Clear search"
                style={{ background: 'transparent', border: 'none', color: 'var(--text-3)', cursor: 'pointer', padding: 2 }}
              >
                ✕
              </button>
            )}
          </div>
          <span className="badge badge--info">{countLabel}</span>
        </div>
      </div>

      {sections.map((section) => (
        <div key={section.key} style={{ marginBottom: 'var(--space-5)' }}>
          <div
            style={{
              fontSize: 'var(--font-size-xs)',
              fontWeight: 700,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              color: 'var(--text-3)',
              marginBottom: 8,
            }}
          >
            {section.label} · {section.visibleTools.length}
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
              gap: 10,
            }}
          >
            {section.visibleTools.map((tool) => (
              <ToolCard key={tool.key} tool={tool} onClick={() => navigate(tool.key)} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ToolCard({ tool, onClick }: { tool: ToolDef; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="panel"
      style={{
        textAlign: 'left',
        cursor: 'pointer',
        color: 'var(--text-1)',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span
          style={{
            width: 32,
            height: 32,
            borderRadius: 'var(--radius-panel-sm)',
            background: 'var(--panel-bg-elevated)',
            border: '1px solid var(--border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-text)',
          }}
        >
          <Icon name={tool.icon as IconName} size={18} />
        </span>
        <span style={{ color: 'var(--text-3)', fontSize: 18 }}>›</span>
      </div>
      {/* Title + description grouped as one anchored block — the 2px gap
          keeps the description reading as the title's subline instead of
          a floating third row. */}
      <div>
        <div style={{ fontWeight: 700, fontSize: 'var(--font-size-sm)', marginBottom: 2 }}>{tool.title}</div>
        <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-xs)', lineHeight: 1.45 }}>
          {tool.description}
        </div>
      </div>
    </button>
  );
}
