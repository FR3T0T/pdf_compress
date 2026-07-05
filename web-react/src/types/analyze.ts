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

export interface SanitizeResponse {
  success: boolean;
  removed?: Record<string, number>;
  total_removed?: number;
  output?: string;
  output_size?: number;
  error?: string;
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
