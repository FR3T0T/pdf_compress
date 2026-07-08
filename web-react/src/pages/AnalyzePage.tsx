import { useEffect, useMemo, useState } from 'react';
import { bridgeApi } from '../bridge/bridgeApi';
import { MOCK_SANITIZE_DEFAULTS } from '../bridge/mockData';
import { SEVERITY_ORDER } from '../types/analyze';
import type { AnalyzeReport, SanitizeOptions } from '../types/analyze';
import type { PickedFile } from '../types/bridge';
import { DropZone } from '../components/shared/DropZone';
import { PageHeader } from '../components/shared/PageHeader';
import { useToast } from '../components/shared/Toast';
import { RiskHeader } from '../components/RiskHeader';
import { FindingRow } from '../components/FindingRow';
import { SanitizePanel } from '../components/SanitizePanel';
import { Card } from '../components/shared/Card';
import { useWorkspace, useWorkspaceBusy } from '../workspace/WorkspaceContext';

type Status = 'idle' | 'loading' | 'ready' | 'error';

export function AnalyzePage() {
  const toast = useToast();
  const [status, setStatus] = useState<Status>('idle');
  const [files, setFiles] = useState<PickedFile[]>([]);
  const [report, setReport] = useState<AnalyzeReport | null>(null);
  // null until getSanitizeDefaults() resolves — SanitizePanel's checkbox
  // state initializes from this prop, so we gate rendering on it rather
  // than seed it with a placeholder that could still be in flight.
  const [sanitizeDefaults, setSanitizeDefaults] = useState<SanitizeOptions | null>(null);
  const [sanitizing, setSanitizing] = useState(false);

  // -- Workspace (persistent working document) -----------------------------
  // Read-only: analysis can run against the workspace document, and Sanitize
  // can still write a separate "clean copy" the user picks a path for, but
  // neither ever advances the workspace pointer or touches the working
  // file itself — this page only ever reads it.
  const workspace = useWorkspace();
  useWorkspaceBusy((status === 'loading' || sanitizing) && !!workspace.path);

  const filePath = workspace.path ?? files[0]?.path ?? null;

  useEffect(() => {
    bridgeApi
      .getSanitizeDefaults()
      .then(setSanitizeDefaults)
      .catch(() => setSanitizeDefaults(MOCK_SANITIZE_DEFAULTS));
  }, []);

  // Deliberately depends on filePath only — analyze() is redefined every
  // render and would otherwise re-run this on every keystroke elsewhere.
  useEffect(() => {
    if (filePath) void analyze(filePath);
    else {
      setReport(null);
      setStatus('idle');
    }
  }, [filePath]);

  const findings = useMemo(() => {
    if (!report) return [];
    const order = Object.fromEntries(SEVERITY_ORDER.map((s, i) => [s, i]));
    return [...report.findings].sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
  }, [report]);

  const analyze = async (path: string) => {
    setStatus('loading');
    setReport(null);
    try {
      const res = await bridgeApi.analyzeDocument(path);
      if (!res.success || !res.report) {
        toast.error(res.error || 'Analysis failed.');
        setStatus('error');
        return;
      }
      setReport(res.report);
      setStatus('ready');
    } catch {
      toast.error('Could not analyze the file.');
      setStatus('error');
    }
  };

  const pickOutput = async (): Promise<string | null> => {
    if (!filePath) return null;
    const base = bridgeApi.basename(filePath).replace(/\.pdf$/i, '');
    return bridgeApi.saveFile('PDF Files (*.pdf)', `${base}_clean.pdf`);
  };

  const runSanitize = async (outputPath: string, options: SanitizeOptions) => {
    if (!filePath) return;
    setSanitizing(true);
    try {
      const res = await bridgeApi.sanitizeDocument(filePath, outputPath, options);
      if (!res.success) {
        toast.error(res.error || 'Sanitize failed.');
      } else if (!res.total_removed) {
        toast.info('Nothing matched the selected options — clean copy written.');
      } else {
        toast.success(`Removed ${res.total_removed} item${res.total_removed === 1 ? '' : 's'}. Clean copy saved.`);
      }
    } catch {
      toast.error('Could not sanitize the file.');
    } finally {
      setSanitizing(false);
    }
  };

  const hasRisk = report ? report.overallRisk !== 'info' : false;

  return (
    <div className="console">
      <PageHeader title="Analyze Document" subtitle="Offline privacy & security audit" backButton={false} />

      {workspace.path ? (
        <Card>
          <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
            Analyzing the workspace document ({workspace.originalName}) — read-only; this never changes
            the working document. See the bar above to Preview, Export, or Clear it.
          </div>
        </Card>
      ) : (
        <DropZone
          files={files}
          onFilesChanged={setFiles}
          multiple={false}
          title="Drop a PDF to analyze"
          subtitle="or click to browse"
          disabled={status === 'loading'}
        />
      )}

      {status === 'loading' && (
        <div
          className="panel"
          style={{ marginTop: 'var(--space-3)', textAlign: 'center', color: 'var(--text-2)' }}
        >
          Scanning document locally…
        </div>
      )}

      {status === 'ready' && report && (
        <>
          <div style={{ marginTop: 'var(--space-3)' }}>
            <RiskHeader report={report} />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 'var(--space-3)' }}>
            {findings.map((f) => (
              <FindingRow key={f.id} finding={f} />
            ))}
          </div>

          {hasRisk && sanitizeDefaults && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <SanitizePanel
                defaults={sanitizeDefaults}
                onPickOutput={pickOutput}
                onSanitize={runSanitize}
                busy={sanitizing}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
