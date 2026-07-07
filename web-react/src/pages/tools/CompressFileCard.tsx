import type { CSSProperties } from 'react';
import { bridgeApi } from '../../bridge/bridgeApi';
import type { PickedFile } from '../../types/bridge';

/** Per-preset size estimate from analyzeFile().estimates[key] (ui/bridge.py). */
export interface PresetEstimate {
  savedPct: number;
  estimatedSizeStr: string;
  targetDpi: number;
}

/** analyzeFile().imageSummary (ui/bridge.py). Only fields the card uses. */
export interface ImageSummary {
  maxDpi: number;
}

/** Per-file analysis + thumbnail state, keyed by path in CompressPage. */
export interface FileAnalysis {
  status: 'analyzing' | 'ready' | 'error';
  size?: number;
  pages?: number;
  imageCount?: number;
  thumbnail?: string;
  imageSummary?: ImageSummary;
  estimates?: Record<string, PresetEstimate>;
  error?: string;
}

interface CompressFileCardProps {
  file: PickedFile;
  analysis?: FileAnalysis;
  /** Selected preset key — drives which estimate / target DPI is shown. */
  presetKey: string;
  onRemove: () => void;
  disabled?: boolean;
}

/**
 * Rich per-file card for the Compress page: page-1 thumbnail, the output
 * filename that will actually be written, meta chips (size / pages / images /
 * DPI with a downscale warning), and the estimated savings for the selected
 * preset. Port of the vanilla compress.js file-card (web/js/pages/compress.js
 * _renderFileCards). The output name is read-only: startCompress always writes
 * `<name>_compressed.pdf` beside the source (see CompressPage's note), so an
 * editable field would be non-functional without a backend change.
 */
export function CompressFileCard({ file, analysis, presetKey, onRemove, disabled }: CompressFileCardProps) {
  const a = analysis;
  const analyzing = !a || a.status === 'analyzing';
  const errored = a?.status === 'error';
  const est = a?.estimates?.[presetKey];
  const outputName = file.name.replace(/\.pdf$/i, '') + '_compressed.pdf';

  const savingsColor =
    est == null
      ? 'var(--text-3)'
      : est.savedPct >= 30
        ? 'var(--sev-info)' // green
        : est.savedPct >= 10
          ? 'var(--sev-medium)' // amber
          : 'var(--text-3)';

  const maxDpi = a?.imageSummary?.maxDpi ?? 0;
  const targetDpi = est?.targetDpi ?? 0;
  const willDownscale = maxDpi > 0 && targetDpi > 0 && maxDpi > targetDpi * 1.1;

  return (
    <div style={cardStyle}>
      <div style={thumbWrapStyle}>
        {a?.thumbnail ? (
          <img src={a.thumbnail} alt={file.name} draggable={false} style={thumbImgStyle} />
        ) : (
          <div style={thumbPlaceholderStyle}>{analyzing ? '…' : '📄'}</div>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          className="mono"
          title={file.path}
          style={{ fontSize: 'var(--font-size-sm)', fontWeight: 600, wordBreak: 'break-word' }}
        >
          {file.name}
        </div>
        <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginTop: 2 }}>
          → <span className="mono">{outputName}</span>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {analyzing && <Chip label="Analyzing…" tone="info" />}
          {errored && <Chip label={a?.error || 'Analysis failed'} tone="danger" />}
          {!analyzing && !errored && (
            <>
              {typeof a?.size === 'number' && <Chip label={bridgeApi.formatSize(a.size)} />}
              {typeof a?.pages === 'number' && (
                <Chip label={`${a.pages} page${a.pages === 1 ? '' : 's'}`} />
              )}
              {typeof a?.imageCount === 'number' && a.imageCount > 0 && (
                <Chip label={`${a.imageCount} image${a.imageCount === 1 ? '' : 's'}`} />
              )}
              {maxDpi > 0 &&
                (willDownscale ? (
                  <Chip label={`${maxDpi} → ${targetDpi} DPI`} tone="warn" />
                ) : (
                  <Chip label={`${maxDpi} DPI`} />
                ))}
            </>
          )}
        </div>
      </div>

      <div style={{ textAlign: 'right', minWidth: 66 }}>
        {est != null && (a?.size ?? 0) > 0 ? (
          <>
            <div style={{ fontSize: 'var(--font-size-md)', fontWeight: 700, color: savingsColor }}>
              {est.savedPct > 0 ? `-${est.savedPct}%` : '~0%'}
            </div>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)' }}>
              → {est.estimatedSizeStr}
            </div>
          </>
        ) : analyzing ? (
          <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)' }}>…</div>
        ) : null}
      </div>

      <button
        aria-label={`Remove ${file.name}`}
        onClick={onRemove}
        disabled={disabled}
        style={removeBtnStyle}
      >
        ✕
      </button>
    </div>
  );
}

function Chip({ label, tone }: { label: string; tone?: 'info' | 'warn' | 'danger' }) {
  const color =
    tone === 'warn'
      ? 'var(--sev-medium)'
      : tone === 'danger'
        ? 'var(--sev-high)'
        : tone === 'info'
          ? 'var(--sev-info)'
          : 'var(--text-2)';
  return (
    <span
      className="mono"
      style={{
        fontSize: 'var(--font-size-xs)',
        color,
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-panel-sm)',
        padding: '2px 6px',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  );
}

const cardStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  gap: 12,
  background: 'var(--panel-bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-panel-sm)',
  padding: 10,
};

const thumbWrapStyle: CSSProperties = {
  width: 46,
  height: 60,
  flexShrink: 0,
  borderRadius: 4,
  overflow: 'hidden',
  border: '1px solid var(--border)',
  background: 'var(--panel-bg)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

const thumbImgStyle: CSSProperties = { width: '100%', height: '100%', objectFit: 'cover' };
const thumbPlaceholderStyle: CSSProperties = { fontSize: 18, color: 'var(--text-3)' };

const removeBtnStyle: CSSProperties = {
  background: 'transparent',
  border: 'none',
  color: 'var(--text-3)',
  cursor: 'pointer',
  fontSize: 12,
  padding: 4,
  lineHeight: 1,
};
