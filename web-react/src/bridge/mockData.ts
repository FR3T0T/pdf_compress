import type { AnalyzeReport, SanitizeOptions, SanitizeResponse } from '../types/analyze';
import type { ToolRegistry, PresetsResponse } from '../types/bridge';

/**
 * Realistic sample data for `vite dev` / plain-browser use, so the POC is
 * reviewable without the PySide6 app. Shape matches pdf_analyze.py exactly.
 */
export const MOCK_REPORT: AnalyzeReport = {
  fileName: 'client_intake_scan.pdf',
  filePath: 'C:\\Users\\demo\\Documents\\client_intake_scan.pdf',
  fileSize: 4832145,
  fileSizeStr: '4.6 MB',
  pages: 14,
  pdfVersion: '1.7',
  encrypted: false,
  overallRisk: 'high',
  counts: { high: 2, medium: 2, low: 2, info: 1 },
  findings: [
    {
      id: 'scripts.js',
      category: 'scripts',
      severity: 'high',
      title: 'Embedded JavaScript',
      detail:
        'PDF JavaScript can run automatically in many viewers and is a common malware and tracking vector. Sanitizing removes it.',
      count: 2,
      items: [
        'Named JavaScript entries in /Names tree',
        'Document /OpenAction: JavaScript',
      ],
    },
    {
      id: 'actions.launch',
      category: 'actions',
      severity: 'high',
      title: 'Launch action(s)',
      detail:
        '/Launch actions can start external programs or open files outside the PDF. These are rarely legitimate.',
      count: 1,
      items: ['Page 3 annotation /A: cmd.exe /c calc.exe'],
    },
    {
      id: 'actions.autorun',
      category: 'actions',
      severity: 'medium',
      title: 'Auto-run actions',
      detail:
        'Actions that fire automatically when the document is opened, printed, or closed. Review before trusting the file.',
      count: 3,
      items: [
        'Document /OpenAction',
        'Document /AA /WC',
        'Page 1 /AA /O',
      ],
    },
    {
      id: 'content.invisible_text',
      category: 'content',
      severity: 'medium',
      title: 'Invisible text layer',
      detail:
        "Text drawn in invisible render mode was found. This is normal for OCR scans, but it is also how failed 'redactions' leak — the black box hides text that is still selectable and searchable underneath.",
      count: 4,
    },
    {
      id: 'links.uri',
      category: 'links',
      severity: 'low',
      title: 'External links / URLs',
      detail:
        'Outbound URLs embedded in the document. Clicking (or, in some viewers, opening) can contact these hosts. Review for trackers.',
      count: 6,
      items: ['https://track.example.com', 'https://cdn.example-analytics.net'],
    },
    {
      id: 'meta.docinfo',
      category: 'metadata',
      severity: 'low',
      title: 'Document metadata present',
      detail:
        'These /Info fields travel with the file and can identify the author, software, and creation history.',
      count: 3,
      items: [
        'Author: J. Whitfield',
        'Creator: Adobe Acrobat Pro DC 22.1',
        'ModDate: D:20250311142003Z',
      ],
    },
    {
      id: 'forms.acro',
      category: 'forms',
      severity: 'info',
      title: 'Interactive form fields',
      detail: 'The document contains fillable form fields.',
      count: 9,
    },
  ],
};

export const MOCK_SANITIZE_DEFAULTS: SanitizeOptions = {
  javascript: true,
  launch_actions: true,
  auto_actions: true,
  embedded_files: true,
  submit_actions: true,
  external_links: false,
  metadata: false,
};

export function mockSanitizeResult(options: SanitizeOptions): SanitizeResponse {
  const removed: Record<string, number> = {};
  if (options.javascript) removed.named_javascript = 2;
  if (options.launch_actions) removed.launch_action = 1;
  if (options.auto_actions) removed.open_action = 1;
  if (options.embedded_files) removed.embedded_files = 0;
  if (options.submit_actions) removed.submit_action = 0;
  if (options.external_links) removed.external_link = 6;
  if (options.metadata) {
    removed.xmp_metadata = 1;
    removed.doc_info = 1;
  }
  const total_removed = Object.values(removed).reduce((a, b) => a + b, 0);
  return {
    success: true,
    removed,
    total_removed,
    output: 'C:\\Users\\demo\\Documents\\client_intake_scan_clean.pdf',
    output_size: 4611200,
  };
}

/**
 * Verbatim from ui/tool_registry.py (CATEGORIES + _tools()) — keep in sync
 * if the Python registry changes. Field names match getToolRegistry()'s
 * JSON output (ui/bridge.py:600-609) exactly.
 */
export const MOCK_TOOL_REGISTRY: ToolRegistry = {
  categories: [
    { key: 'compress', label: 'Compress & Optimize' },
    { key: 'merge', label: 'Merge & Split' },
    { key: 'convert', label: 'Convert' },
    { key: 'security', label: 'Security' },
    { key: 'pages', label: 'Page Operations' },
    { key: 'content', label: 'Content & Watermark' },
    { key: 'extract', label: 'Extract' },
    { key: 'repair', label: 'Repair & Analysis' },
  ],
  tools: [
    { key: 'compress', title: 'Compress PDF', description: 'Reduce file size with smart image recompression', icon: 'compress', category: 'compress', acceptedExtensions: ['.pdf'] },
    { key: 'merge', title: 'Merge PDFs', description: 'Combine multiple PDFs into one document', icon: 'merge', category: 'merge', acceptedExtensions: ['.pdf'] },
    { key: 'split', title: 'Split PDF', description: 'Divide a PDF into separate files', icon: 'split', category: 'merge', acceptedExtensions: ['.pdf'] },
    { key: 'pdf_to_images', title: 'PDF to Images', description: 'Export pages as PNG or JPEG', icon: 'image', category: 'convert', acceptedExtensions: ['.pdf'] },
    { key: 'images_to_pdf', title: 'Images to PDF', description: 'Convert images into a PDF document', icon: 'image_to_pdf', category: 'convert', acceptedExtensions: ['.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'] },
    { key: 'pdf_to_word', title: 'PDF to Word', description: 'Extract text to a Word document', icon: 'word', category: 'convert', acceptedExtensions: ['.pdf'] },
    { key: 'translate', title: 'Translate', description: 'Offline translation of PDF text and text in photos/scans', icon: 'translate', category: 'convert', acceptedExtensions: ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.gif'] },
    { key: 'protect', title: 'Protect PDF', description: 'Add password protection with standard or enhanced encryption', icon: 'lock', category: 'security', acceptedExtensions: ['.pdf'] },
    { key: 'unlock', title: 'Unlock PDF', description: 'Remove password protection from PDF or EPDF files', icon: 'unlock', category: 'security', acceptedExtensions: ['.pdf', '.epdf'] },
    { key: 'redact', title: 'Redact PDF', description: 'Permanently remove sensitive text', icon: 'redact', category: 'security', acceptedExtensions: ['.pdf'] },
    { key: 'page_ops', title: 'Rotate & Reorder', description: 'Rotate, reorder, or delete pages', icon: 'pages', category: 'pages', acceptedExtensions: ['.pdf'] },
    { key: 'crop', title: 'Crop Pages', description: 'Trim page margins', icon: 'crop', category: 'pages', acceptedExtensions: ['.pdf'] },
    { key: 'flatten', title: 'Flatten PDF', description: 'Remove annotations and form fields', icon: 'flatten', category: 'pages', acceptedExtensions: ['.pdf'] },
    { key: 'nup', title: 'N-up Layout', description: 'Arrange multiple pages per sheet', icon: 'grid', category: 'pages', acceptedExtensions: ['.pdf'] },
    { key: 'watermark', title: 'Add Watermark', description: 'Batch text watermarking with presets and positioning', icon: 'watermark', category: 'content', acceptedExtensions: ['.pdf'] },
    { key: 'page_numbers', title: 'Add Page Numbers', description: 'Insert page numbering', icon: 'numbers', category: 'content', acceptedExtensions: ['.pdf'] },
    { key: 'metadata', title: 'Edit Metadata', description: 'View and edit PDF properties', icon: 'metadata', category: 'content', acceptedExtensions: ['.pdf'] },
    { key: 'extract_images', title: 'Extract Images', description: 'Pull all images from a PDF', icon: 'extract_img', category: 'extract', acceptedExtensions: ['.pdf'] },
    { key: 'extract_text', title: 'Extract Text', description: 'Export text content to a file', icon: 'extract_text', category: 'extract', acceptedExtensions: ['.pdf'] },
    { key: 'repair', title: 'Repair PDF', description: 'Fix corrupted PDF files', icon: 'repair', category: 'repair', acceptedExtensions: ['.pdf'] },
    { key: 'compare', title: 'Compare PDFs', description: 'Find differences between two PDFs', icon: 'compare', category: 'repair', acceptedExtensions: ['.pdf'] },
    { key: 'analyze', title: 'Analyze Document', description: 'Privacy & security audit — find trackers, scripts, hidden data', icon: 'shield', category: 'repair', acceptedExtensions: ['.pdf'] },
  ],
};

/** Verbatim shape from ui/bridge.py getPresets() (line 360-376). */
export const MOCK_PRESETS: PresetsResponse = {
  defaultPreset: 'standard',
  ghostscriptAvailable: false,
  presets: [
    { key: 'light', name: 'Light', description: 'Minimal compression, best quality', targetDpi: { color: 200, grayscale: 200, monochrome: 400 }, jpegQuality: 85 },
    { key: 'standard', name: 'Standard', description: 'Balanced size and quality', targetDpi: { color: 150, grayscale: 150, monochrome: 300 }, jpegQuality: 75 },
    { key: 'aggressive', name: 'Aggressive', description: 'Maximum compression', targetDpi: { color: 100, grayscale: 100, monochrome: 200 }, jpegQuality: 60 },
  ],
};
