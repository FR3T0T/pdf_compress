import type { Severity } from '../types/analyze';

export const SEVERITY_META: Record<
  Severity,
  { label: string; short: string; color: string; textColor: string; badgeClass: string }
> = {
  high: {
    label: 'High',
    short: 'HIGH',
    color: 'var(--sev-high)',
    textColor: 'var(--sev-high-text)',
    badgeClass: 'badge--high',
  },
  medium: {
    label: 'Medium',
    short: 'MED',
    color: 'var(--sev-medium)',
    textColor: 'var(--sev-medium-text)',
    badgeClass: 'badge--medium',
  },
  low: {
    label: 'Low',
    short: 'LOW',
    color: 'var(--sev-low)',
    textColor: 'var(--sev-low-text)',
    badgeClass: 'badge--low',
  },
  info: {
    label: 'Info',
    short: 'INFO',
    color: 'var(--sev-info)',
    textColor: 'var(--sev-info-text)',
    badgeClass: 'badge--info',
  },
};
