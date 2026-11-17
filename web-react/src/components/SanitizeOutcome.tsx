import type { ReactNode } from 'react';
import type { SanitizeResponse } from '../types/analyze';

/**
 * What the last Sanitize run came to, shown under the Sanitize panel.
 *
 * Deliberately small: the full, exportable report is its own piece of work
 * (#100). A verified run says so, and counts what the selected options left
 * alone. A refused run says what is still present, what could not be
 * checked, or that it is the file identifier — in the words pdf_verify.py
 * produced (`refusal`); nothing is rephrased here.
 *
 * Renders nothing for an image (no verification) or a plain failure (the
 * toast carries its one line).
 */
export function SanitizeOutcome({ result }: { result: SanitizeResponse }) {
  const report = result.verification;
  if (!report) return null;

  if (result.success) {
    const residuals = report.residualFindings;
    const unchecked = report.unevaluated;
    return (
      <Block color="var(--sev-info)" title="✓ Verified — nothing the selected options remove is left in the clean copy.">
        {residuals.length > 0 && (
          <Line>
            {residuals.length} other finding{residuals.length === 1 ? ' is' : 's are'} still in it, because no
            selected option removes {residuals.length === 1 ? 'it' : 'them'}:{' '}
            {residuals.map((f) => f.title).join(', ')}.
          </Line>
        )}
        {unchecked.length > 0 && (
          <Line>
            Not checked, and outside the selected options:{' '}
            <span className="mono">{unchecked.map((u) => u.id).join(', ')}</span>.
          </Line>
        )}
      </Block>
    );
  }

  const refusal = result.refusal;
  return (
    <Block color="var(--sev-high)" title="Sanitize refused — nothing was saved.">
      <Line>
        The cleaned copy did not pass verification, so it was not written. Your original file is unchanged.
      </Line>
      {refusal && refusal.stillPresent.length > 0 && (
        <List heading="Still present in the cleaned copy:" items={refusal.stillPresent} />
      )}
      {refusal && refusal.notChecked.length > 0 && (
        <List heading="Could not be checked:" items={refusal.notChecked} />
      )}
      {refusal?.unreadable && <Line>The cleaned copy could not be read back to check it.</Line>}
      {refusal?.identifier && (
        <Line>
          The cleaned copy would still carry the original file's identifier, which links the two files.
        </Line>
      )}
      {!refusal && result.error && <Line>{result.error}</Line>}
    </Block>
  );
}

function Block({ color, title, children }: { color: string; title: string; children?: ReactNode }) {
  return (
    <div className="panel" role="status" style={{ marginTop: 'var(--space-3)', borderLeft: `3px solid ${color}` }}>
      <strong style={{ fontSize: 'var(--font-size-sm)', color }}>{title}</strong>
      {children}
    </div>
  );
}

function Line({ children }: { children: ReactNode }) {
  return (
    <div style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-sm)', marginTop: 4 }}>{children}</div>
  );
}

function List({ heading, items }: { heading: string; items: string[] }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ color: 'var(--text-1)', fontSize: 'var(--font-size-sm)' }}>{heading}</div>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18, color: 'var(--text-2)', fontSize: 'var(--font-size-sm)' }}>
        {items.map((item) => (
          <li key={item} style={{ marginBottom: 2, wordBreak: 'break-word' }}>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}
