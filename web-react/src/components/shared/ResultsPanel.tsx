import { bridgeApi } from '../../bridge/bridgeApi';

export interface ResultFile {
  name: string;
  path?: string;
  outputPath?: string;
  originalSize?: number;
  resultSize?: number;
  status?: 'done' | 'error' | 'skipped';
  error?: string;
}

export interface ResultsData {
  files: ResultFile[];
  totalTime?: number;
  totalSaved?: number;
  outputDir?: string;
}

/**
 * React port of createResultsPanel() from web/js/components.js. `results`
 * shape is generic on purpose — each tool's `done` payload varies, so
 * Phase 3 pages map their specific result shape into this one before
 * rendering.
 */
export function ResultsPanel({ results }: { results: ResultsData }) {
  const { files, totalTime, totalSaved, outputDir } = results;

  return (
    <div className="panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: 'var(--space-3)',
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 'var(--font-size-md)' }}>
          {files.length} file{files.length === 1 ? '' : 's'} processed
        </div>
        <div className="mono" style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
          {typeof totalTime === 'number' ? `${totalTime.toFixed(1)}s` : ''}
          {typeof totalSaved === 'number' && totalSaved > 0
            ? ` · saved ${bridgeApi.formatSize(totalSaved)}`
            : ''}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {files.map((f, i) => {
          const sev = f.status === 'error' ? 'var(--sev-high)' : f.status === 'skipped' ? 'var(--sev-medium)' : 'var(--sev-info)';
          return (
            <div
              key={f.path ?? f.name ?? i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                borderLeft: `2px solid ${sev}`,
                background: 'var(--panel-bg-elevated)',
                borderRadius: '0 var(--radius-panel-sm) var(--radius-panel-sm) 0',
                padding: '8px 12px',
              }}
            >
              <span className="mono" style={{ flex: 1, fontSize: 'var(--font-size-sm)', wordBreak: 'break-word' }}>
                {f.name}
              </span>
              {typeof f.originalSize === 'number' && typeof f.resultSize === 'number' && (
                <span className="mono" style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-xs)' }}>
                  {bridgeApi.formatSize(f.originalSize)} → {bridgeApi.formatSize(f.resultSize)}
                </span>
              )}
              {f.status === 'error' && f.error && (
                <span style={{ color: 'var(--sev-high-text)', fontSize: 'var(--font-size-xs)' }}>{f.error}</span>
              )}
            </div>
          );
        })}
      </div>

      {outputDir && (
        <div style={{ marginTop: 'var(--space-3)', textAlign: 'right' }}>
          <button
            onClick={() => bridgeApi.openFolderPath(outputDir)}
            style={{
              background: 'transparent',
              border: '1px solid var(--border-strong)',
              color: 'var(--text-1)',
              borderRadius: 'var(--radius-panel-sm)',
              padding: '6px 12px',
              fontSize: 'var(--font-size-sm)',
            }}
          >
            Open folder
          </button>
        </div>
      )}
    </div>
  );
}
