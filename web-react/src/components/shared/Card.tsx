import type { CSSProperties, ReactNode } from 'react';

interface CardProps {
  children: ReactNode;
  elevated?: boolean;
  style?: CSSProperties;
  className?: string;
}

/** Thin semantic wrapper over the `.panel` token from theme.css. */
export function Card({ children, elevated, style, className }: CardProps) {
  return (
    <div
      className={['panel', className].filter(Boolean).join(' ')}
      style={{ background: elevated ? 'var(--panel-bg-elevated)' : undefined, ...style }}
    >
      {children}
    </div>
  );
}
