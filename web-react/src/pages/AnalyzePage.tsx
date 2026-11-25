import { useEffect, useMemo, useState } from 'react';
import { bridgeApi } from '../bridge/bridgeApi';
import { MOCK_SANITIZE_DEFAULTS } from '../bridge/mockData';
import { SEVERITY_ORDER } from '../types/analyze';
import type { AnalyzeReport, SanitizeOptions, SanitizeResponse } from '../types/analyze';
import type { PickedFile } from '../types/bridge';
import { DropZone } from '../components/shared/DropZone';
import { PageHeader } from '../components/shared/PageHeader';
import { useToast } from '../components/shared/Toast';
import { RiskHeader } from '../components/RiskHeader';
import { FindingRow } from '../components/FindingRow';
import { SanitizePanel } from '../components/SanitizePanel';
import { SanitizeOutcome } from '../components/SanitizeOutcome';
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
  // The last Sanitize answer for this file: a verified run or a refusal is
  // shown under the panel (SanitizeOutcome), because a toast cannot carry
  // what was left or why nothing was saved.
  const [outcome, setOutcome] = useState<SanitizeResponse | null>(null);

  // -- Workspace (persistent working document) -----------------------------
  // Read-only: analysis can run against the workspace document, and Sanitize
  // can still write a separate "clean copy" the user picks a path for, but
  // neither ever advances the workspace pointer or touches the working
  // file itself — this page only ever reads it.
  const workspace = useWorkspace();
  useWorkspaceBusy((status === 'loading' || sanitizing) && !!workspace.path);

  const filePath = workspace.path ?? files[0]?.path ?? null;
  // Images (Phase 2) analyze fine but have no sanitiser yet, so the clean/
  // Sanitize action stays PDF-only for now (Phase 3 adds image strip).
  const isImage = filePath ? /\.(jpe?g|png)$/i.test(filePath) : false;

  useEffect(() => {
    bridgeApi
      .getSanitizeDefaults()
      .then(setSanitizeDefaults)
      .catch(() => setSanitizeDefaults(MOCK_SANITIZE_DEFAULTS));
  }, []);

  // Deliberately depends on filePath only — analyze() is redefined every
  // render and would otherwise re-run this on every keystroke elsewhere.
  useEffect(() => {
    setOutcome(null);
    if (filePath) void analyze(filePath);
    else {
      setReport(null);
      setStatus('idle');
    }
  }, [filePath]);

  const findings = useMemo(() => {
    if (!report) return [];
    const order = Object.fromEntries(SEVERITY_ORDER.map((s, i) => [s, i]));
    // "Audit incomplete" goes first whatever its severity: it qualifies
    // every other row, and the absence of rows.
    const rank = (f: AnalyzeReport['findings'][number]) =>
      f.id === 'audit.incomplete' ? -1 : (order[f.severity] ?? 9);
    return [...report.findings].sort((a, b) => rank(a) - rank(b));
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
    if (isImage) {
      // Keep the image's own extension — a stripped JPEG stays a JPEG.
      const m = bridgeApi.basename(filePath).match(/^(.*?)(\.(jpe?g|png))$/i);
      const base = m ? m[1] : bridgeApi.basename(filePath);
      const ext = (m ? m[2] : '.jpg').toLowerCase();
      const filter = /\.png$/i.test(ext) ? 'PNG Image (*.png)' : 'JPEG Image (*.jpg *.jpeg)';
      return bridgeApi.saveFile(filter, `${base}_clean${ext}`);
    }
    const base = bridgeApi.basename(filePath).replace(/\.pdf$/i, '');
    return bridgeApi.saveFile('PDF Files (*.pdf)', `${base}_clean.pdf`);
  };

  const runSanitize = async (outputPath: string, options: SanitizeOptions) => {
    if (!filePath) return;
    setSanitizing(true);
    setOutcome(null);
    try {
      const res = await bridgeApi.sanitizeDocument(filePath, outputPath, options);
      setOutcome(res);
      // A PDF's clean copy is verified before it is saved (#101), so a
      // success with a report means verified; images carry no report.
      const left = res.verification?.residualFindings.length ?? 0;
      const proof = !res.verification?.verified
        ? ''
        : left
          ? ` Verified — ${left} finding${left === 1 ? '' : 's'} outside the selected options remain${left === 1 ? 's' : ''}.`
          : ' Verified.';
      if (!res.success) {
        // A refusal carries its report and is spelled out under the panel;
        // anything else is a one-line failure.
        toast.error(
          res.verification
            ? 'Sanitize refused — nothing was saved. The reason is shown below.'
            : res.error || 'Sanitize failed.'
        );
      } else if (!res.total_removed) {
        toast.info(
          isImage
            ? 'No metadata needed removing — clean copy written.'
            : `Nothing matched the selected options — clean copy written.${proof}`
        );
      } else {
        toast.success(
          `Removed ${res.total_removed} item${res.total_removed === 1 ? '' : 's'}. Clean copy saved.${proof}`
        );
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
          compact={files.length > 0}
          accept="PDF & Images (*.pdf *.jpg *.jpeg *.png)"
          title="Drop a PDF or image to analyze"
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
                imageMode={isImage}
              />
              {outcome && <SanitizeOutcome result={outcome} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}
