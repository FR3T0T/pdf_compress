import { ICON_PATHS, type IconName } from './iconPaths';

export type { IconName };

interface IconProps {
  name: IconName;
  size?: number;
  color?: string;
  className?: string;
}

/**
 * The static path data in iconPaths.ts is authored by us (ported verbatim
 * from web/js/icons.js), not user input, so dangerouslySetInnerHTML here
 * carries no injection risk — it's the only practical way to inject
 * multi-element raw SVG markup without hand-converting 27 icons to JSX.
 */
export function Icon({ name, size = 20, color = 'currentColor', className }: IconProps) {
  const inner = ICON_PATHS[name];
  if (!inner) {
    console.warn(`[Icon] Unknown icon: "${name}"`);
    return null;
  }

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      dangerouslySetInnerHTML={{ __html: inner }}
    />
  );
}
