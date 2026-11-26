/**
 * Shapes exactly mirror pdf_analyze.py's `AnalysisResult.to_dict()` /
 * `Finding` dataclass and ui/bridge.py's JSON envelopes. Field names are
 * verbatim — do not rename without changing the Python side.
 */

export type Severity = 'high' | 'medium' | 'low' | 'info';

export const SEVERITY_ORDER: Severity[] = ['high', 'medium', 'low', 'info'];

export interface Finding {
  id: string;
  category: string;
  severity: Severity;
  title: string;
  detail?: string;
  count?: number;
  items?: string[];
}

export interface SeverityCounts {
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface AnalyzeReport {
  fileName: string;
  filePath: string;
  fileSize: number;
  fileSizeStr: string;
  pages: number;
  pdfVersion: string;
  encrypted: boolean;
  findings: Finding[];
  counts: SeverityCounts;
  overallRisk: Severity;
  /** Finding id -> why it could not be fully checked. Not empty means the
   *  report carries an `audit.incomplete` finding and never `clean`. */
  unevaluated?: Record<string, string[]>;
}

export interface AnalyzeResponse {
  success: boolean;
  report?: AnalyzeReport;
  error?: string;
}

/** Keys match DEFAULT_SANITIZE in pdf_analyze.py exactly. */
export interface SanitizeOptions {
  javascript: boolean;
  launch_actions: boolean;
  auto_actions: boolean;
  embedded_files: boolean;
  submit_actions: boolean;
  external_links: boolean;
  metadata: boolean;
}

/** One check of pdf_verify.py's `VerificationReport.to_dict()`. */
export interface VerificationCheck {
  id: string;
  description: string;
  passed: boolean;
  evidence: string[];
  pages: number[];
  isDocumentLevel: boolean;
  /** Failed because it could not be carried out, not because it found something. */
  unevaluated: boolean;
  objectPaths: string[];
}

/** pdf_verify.py's `VerificationReport.to_dict()` as Sanitize returns it:
 *  the report the cleaned copy passed before it was saved, or the one it
 *  failed when the run was refused. */
export interface SanitizeVerification {
  inputPath: string;
  outputPath: string;
  tool: string;
  timestamp: string;
  checks: VerificationCheck[];
  /** Findings no selected option promised to remove — not a failure. */
  residualFindings: Finding[];
  verified: boolean;
  flattenTargetPages: number[] | null;
  sourceSha256: string | null;
  outputSha256: string | null;
  /** Checks outside the selected options that could not be completed. */
  unevaluated: Array<{ id: string; reasons: string[] }>;
  diagnostics: string[];
  /** #122: earlier versions kept by incremental saves; null = not counted. */
  earlierVersions?: { source?: number | null; output?: number | null; sourceWhy?: string };
  /** #120 d: what the app's process did on the network during the run. */
  network?: { watched: boolean; windows: unknown[]; refused: number; hosts: string[] } | null;
}

/** One line of the proof of removal (removal_report.assemble, #100). Its
 *  words never name its pages: the panel puts `where` in front. */
export interface ProofItem {
  what: string;
  pages: number[];
  /** The pages in words ("Pages 1-3 and 9"), "" for none. */
  where: string;
  /** Found but not targeted, and Not checked: what to do about it. */
  next?: string | null;
  /** Removed: where it was (page_content, docinfo, embedded_files, …). */
  surface?: string;
}

/** A check in plain words, with its id for Technical details. */
export interface ProofCheck {
  id: string;
  description: string;
  passed: boolean;
  pages: number[];
}

/** The proof of removal a finished Redact or Sanitize carries (#100):
 *  three groups, the earlier-versions line and the network line, all
 *  worded by the Python side. */
export interface RemovalProof {
  tool: string;
  removed: ProofItem[];
  notTargeted: ProofItem[];
  notChecked: ProofItem[];
  checks: ProofCheck[];
  earlierVersions: string;
  network: string;
}

/** ui/bridge.py's saveRemovalReport: the two file names written beside the
 *  new file, or why nothing was. */
export interface SaveReportResponse {
  success: boolean;
  files?: string[];
  error?: string;
}

/** pdf_verify.py's `sanitize_refusal_detail()`: a refusal as the short lists
 *  a screen shows. The wording comes from Python; nothing here rephrases it. */
export interface SanitizeRefusal {
  stillPresent: string[];
  notChecked: string[];
  identifier: boolean;
  unreadable: boolean;
}

/** pdf_analyze.py's `sanitize_payload()`. Three shapes: success (a PDF has
 *  `verification`, an image has none); a refusal (`success: false` with
 *  `verification` and `refusal` — nothing was written); a failure
 *  (`success: false` with `error` and `errorType`). */
export interface SanitizeResponse {
  success: boolean;
  removed?: Record<string, number>;
  total_removed?: number;
  output?: string;
  output_size?: number;
  verification?: SanitizeVerification;
  /** A PDF's finished run in plain words (#100); never on a refusal. */
  proof?: RemovalProof;
  refusal?: SanitizeRefusal;
  error?: string;
  errorType?: string;
}

export const SANITIZE_FIELDS: Array<[keyof SanitizeOptions, string]> = [
  ['javascript', 'Embedded JavaScript'],
  ['launch_actions', 'Launch actions (run external programs)'],
  ['auto_actions', 'Auto-run actions (/OpenAction, /AA)'],
  ['embedded_files', 'Embedded files / attachments'],
  ['submit_actions', 'Form submit / import actions'],
  ['external_links', 'External links / URLs (trackers)'],
  ['metadata', 'Document & XMP metadata'],
];
