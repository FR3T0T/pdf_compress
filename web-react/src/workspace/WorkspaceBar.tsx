import { useEffect, useRef, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { bridgeApi, isRealBridge } from '../bridge/bridgeApi';
import { useToast } from '../components/shared/Toast';
import { RiskHeader } from '../components/RiskHeader';
import { FindingRow } from '../components/FindingRow';
import { SEVERITY_ORDER } from '../types/analyze';
import { useWorkspace } from './WorkspaceContext';

interface PreviewPage {
  index: number;
  dataUrl: string;
  width: number;
  height: number;
}

/**
 * Global, persistent bar rendered once in AppShell (above the routed tool
 * content, so every page sees the same instance) — the workspace UI used to
 * live inside WatermarkPage; it's now here so loading a document is a
 * one-time, app-wide action instead of a per-tool toggle. Tool pages read
 * workspace.path/ops from WorkspaceContext and, when a document is loaded,
 * operate on it directly (running-result model) instead of showing their
 * own drop zone.
 */
export function WorkspaceBar() {
  const workspace = useWorkspace();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [previewPages, setPreviewPages] = useState<PreviewPage[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  // Index into previewPages of the page currently shown large, or null when
  // the lightbox is closed. The thumbnail strip stays as-is underneath.
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  // Whether the scan-on-load findings panel is expanded (toggled by the
  // badge below).
  const [findingsOpen, setFindingsOpen] = useState(false);

  // A stale preview/findings view from before the latest run would
  // misleadingly look current, so drop it as soon as the working document
  // pointer moves. (Scan state itself lives in WorkspaceContext and resets
  // itself on load/clear -- this only resets this component's own toggle.)
  useEffect(() => {
    setPreviewPages([]);
    setPreviewOpen(false);
    setLightboxIndex(null);
    setFindingsOpen(false);
  }, [workspace.path]);

  // Esc closes the lightbox; arrow keys page through it.
  useEffect(() => {
    if (lightboxIndex === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setLightboxIndex(null);
      else if (e.key === 'ArrowRight') setLightboxIndex((i) => (i !== null && i < previewPages.length - 1 ? i + 1 : i));
      else if (e.key === 'ArrowLeft') setLightboxIndex((i) => (i !== null && i > 0 ? i - 1 : i));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxIndex, previewPages.length]);

  const loadFile = async () => {
    if (isRealBridge()) {
      const paths = await bridgeApi.openFiles('PDF Files (*.pdf)');
      if (paths.length) workspace.load(paths[0]);
    } else {
      inputRef.current?.click();
    }
  };

  const handlePreview = async () => {
    if (!workspace.path) return;
    if (previewOpen) {
      setPreviewOpen(false);
      return;
    }
    setPreviewLoading(true);
    const res = await bridgeApi.getPageImages(workspace.path);
    setPreviewLoading(false);
    if (!res.success || !res.pages) {
      toast.error(res.error || 'Could not render a preview.');
      return;
    }
    setPreviewPages(res.pages);
    setPreviewOpen(true);
  };

  const handleExport = async () => {
    if (!workspace.path) return;
    const defaultName = (workspace.originalName || 'document.pdf').replace(/\.pdf$/i, '_workspace.pdf');
    const dest = await bridgeApi.saveFile('PDF Files (*.pdf)', defaultName);
    if (!dest) return;
    const ok = await workspace.exportTo(dest);
    if (ok) toast.success(`Exported to ${bridgeApi.basename(dest)}.`);
    else toast.error('Export failed.');
  };

  return (
    <div
      style={{
        borderBottom: '1px solid var(--border)',
        background: 'var(--panel-bg-elevated)',
        flexShrink: 0,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          padding: '10px var(--space-4)',
          flexWrap: 'wrap',
        }}
      >
        {workspace.path ? (
          <>
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-1)' }}>
              <strong>Working on:</strong> {workspace.originalName}{' '}
              <span style={{ color: 'var(--text-3)' }}>
                ({workspace.ops.length} operation{workspace.ops.length === 1 ? '' : 's'})
              </span>
            </span>

            {workspace.scan.status === 'scanning' && (
              <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-3)' }}>
                Scanning for PDF risks…
              </span>
            )}
            {workspace.scan.status === 'done' && workspace.scan.findingCount > 0 && (
              <button
                onClick={() => setFindingsOpen((o) => !o)}
                className="btn-ghost"
                style={{
                  fontSize: 'var(--font-size-xs)',
                  color: 'var(--sev-medium-text)',
                  borderColor: 'var(--sev-medium)',
                  padding: '3px 8px',
                }}
              >
                ⚠ {workspace.scan.findingCount} PDF risk finding{workspace.scan.findingCount === 1 ? '' : 's'} —
                click to {findingsOpen ? 'hide' : 'view'}
              </button>
            )}
            {workspace.scan.status === 'done' && workspace.scan.findingCount === 0 && (
              <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--sev-info-text)' }}>
                ✓ Scanned for PDF risks — no findings
              </span>
            )}

            <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
              <button onClick={handlePreview} disabled={workspace.busy || previewLoading} className="btn-ghost">
                {previewLoading ? 'Rendering…' : previewOpen ? 'Hide Preview' : 'Preview'}
              </button>
              <button onClick={handleExport} disabled={workspace.busy} className="btn-ghost">
                Export…
              </button>
              <button onClick={workspace.clear} disabled={workspace.busy} className="btn-ghost">
                Clear
              </button>
            </div>
          </>
        ) : (
          <>
            <span style={{ fontSize: 'var(--font-size-sm)', color: 'var(--text-3)' }}>
              No workspace document loaded — load one to carry it across tools.
            </span>
            <button onClick={loadFile} disabled={workspace.busy} className="btn-ghost" style={{ marginLeft: 'auto' }}>
              Load a file into workspace
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) workspace.load(file.name);
                e.target.value = '';
              }}
            />
          </>
        )}
      </div>

      {findingsOpen && workspace.scan.status === 'done' && (
        <div style={{ padding: '0 var(--space-4) var(--space-4)' }}>
          <div style={{ marginBottom: 'var(--space-3)' }}>
            <RiskHeader report={workspace.scan.report} />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[...workspace.scan.report.findings]
              .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity))
              .map((f) => (
                <FindingRow key={f.id} finding={f} />
              ))}
          </div>
          <div style={{ marginTop: 'var(--space-3)', color: 'var(--text-3)', fontSize: 'var(--font-size-xs)' }}>
            This scans for known PDF-specific risks (embedded JavaScript, launch/auto-run actions, external
            links, embedded files) — it is not an antivirus scan, and no findings doesn't guarantee the file
            is safe.
          </div>
        </div>
      )}

      {previewOpen && previewPages.length > 0 && (
        <div
          style={{
            padding: '0 var(--space-4) var(--space-4)',
            display: 'flex',
            gap: 'var(--space-3)',
            overflowX: 'auto',
            maxHeight: 360,
          }}
        >
          {previewPages.map((p, i) => (
            <div key={p.index} style={{ flexShrink: 0 }}>
              <div style={{ color: 'var(--text-3)', fontSize: 'var(--font-size-xs)', marginBottom: 4 }}>
                Page {p.index + 1}
              </div>
              <img
                src={p.dataUrl}
                alt={`Working document page ${p.index + 1} — click to enlarge`}
                onClick={() => setLightboxIndex(i)}
                style={{
                  display: 'block',
                  height: 300,
                  width: 'auto',
                  border: '1px solid var(--border)',
                  cursor: 'zoom-in',
                }}
              />
            </div>
          ))}
        </div>
      )}

      {lightboxIndex !== null && previewPages[lightboxIndex] && (
        <div
          onClick={() => setLightboxIndex(null)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            background: 'rgba(0, 0, 0, 0.85)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <button
            onClick={() => setLightboxIndex(null)}
            aria-label="Close preview"
            style={{
              position: 'absolute',
              top: 20,
              right: 24,
              width: 40,
              height: 40,
              borderRadius: '50%',
              border: 'none',
              background: 'rgba(255, 255, 255, 0.12)',
              color: '#fff',
              fontSize: 22,
              lineHeight: '40px',
              textAlign: 'center',
              cursor: 'pointer',
              padding: 0,
            }}
          >
            ×
          </button>

          <div
            style={{
              position: 'absolute',
              top: 24,
              left: 0,
              right: 0,
              textAlign: 'center',
              color: '#fff',
              fontSize: 'var(--font-size-sm)',
            }}
          >
            Page {previewPages[lightboxIndex].index + 1} of {previewPages.length}
          </div>

          {lightboxIndex > 0 && (
            <NavButton side="left" onClick={(e) => { e.stopPropagation(); setLightboxIndex((i) => (i ?? 0) - 1); }} />
          )}
          {lightboxIndex < previewPages.length - 1 && (
            <NavButton side="right" onClick={(e) => { e.stopPropagation(); setLightboxIndex((i) => (i ?? 0) + 1); }} />
          )}

          <img
            src={previewPages[lightboxIndex].dataUrl}
            alt={`Working document page ${previewPages[lightboxIndex].index + 1}, enlarged`}
            onClick={(e) => e.stopPropagation()}
            style={{
              maxWidth: '90vw',
              maxHeight: '85vh',
              width: 'auto',
              height: 'auto',
              boxShadow: '0 8px 40px rgba(0, 0, 0, 0.6)',
              cursor: 'default',
            }}
          />
        </div>
      )}
    </div>
  );
}

function NavButton({ side, onClick }: { side: 'left' | 'right'; onClick: (e: ReactMouseEvent) => void }) {
  return (
    <button
      onClick={onClick}
      aria-label={side === 'left' ? 'Previous page' : 'Next page'}
      style={{
        position: 'absolute',
        [side]: 20,
        top: '50%',
        transform: 'translateY(-50%)',
        width: 48,
        height: 48,
        borderRadius: '50%',
        border: 'none',
        background: 'rgba(255, 255, 255, 0.12)',
        color: '#fff',
        fontSize: 24,
        lineHeight: '48px',
        textAlign: 'center',
        cursor: 'pointer',
        padding: 0,
      }}
    >
      {side === 'left' ? '‹' : '›'}
    </button>
  );
}
