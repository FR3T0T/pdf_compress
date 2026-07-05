import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  backButton?: boolean;
  onBack?: () => void;
  actions?: ReactNode;
}

/**
 * React port of createPageHeader() from web/js/components.js. The vanilla
 * version's back button navigates straight to 'home' via the global
 * Router; this one defers that decision to the caller (onBack) since the
 * real router doesn't exist until Phase 2 — pages can pass nothing for now.
 */
export function PageHeader({ title, subtitle, backButton = true, onBack, actions }: PageHeaderProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 16,
        marginBottom: 'var(--space-4)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        {backButton && (
          <button
            onClick={onBack}
            aria-label="Back"
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-panel-sm)',
              color: 'var(--text-2)',
              width: 32,
              height: 32,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flex: '0 0 auto',
              marginTop: 2,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round">
              <polyline points="9,2 4,7 9,12" />
            </svg>
          </button>
        )}
        <div>
          <h1 className="console-heading">{title}</h1>
          {subtitle && (
            <p className="console-subheading" style={{ marginBottom: 0 }}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {actions && <div style={{ display: 'flex', gap: 8, flex: '0 0 auto' }}>{actions}</div>}
    </div>
  );
}
