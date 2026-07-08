import { SEVERITY_ORDER } from '../types/analyze';
import type { AnalyzeReport } from '../types/analyze';
import { SEVERITY_META } from './severityMeta';

export function RiskHeader({ report }: { report: AnalyzeReport }) {
  const sev = SEVERITY_META[report.overallRisk];
  const chips = SEVERITY_ORDER.filter((k) => report.counts[k] > 0);

  return (
    <div
      className="panel"
      style={{ borderLeft: `3px solid ${sev.color}` }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 'var(--space-2)',
        }}
      >
        <div
          style={{
            fontFamily: 'var(--font-sans)',
            fontWeight: 800,
            fontSize: 20,
            letterSpacing: '0.02em',
            color: sev.color,
          }}
        >
          RISK: {sev.short}
        </div>
        <div className="mono" style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
          {report.fileName}
          {report.pages > 0 ? ` · ${report.pages} page${report.pages === 1 ? '' : 's'}` : ''}
          {` · ${report.fileSizeStr}`}
          {report.pdfVersion ? ` · PDF ${report.pdfVersion}` : ''}
          {report.encrypted ? ' · encrypted' : ''}
        </div>
      </div>

      {chips.length > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 'var(--space-3)' }}>
          {chips.map((k) => {
            const m = SEVERITY_META[k];
            return (
              <span key={k} className={`badge ${m.badgeClass}`}>
                {report.counts[k]} {m.short}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
