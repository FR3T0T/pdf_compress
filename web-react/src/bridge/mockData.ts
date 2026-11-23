import type {
  AnalyzeReport,
  SanitizeOptions,
  SanitizeResponse,
  SanitizeVerification,
} from '../types/analyze';
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

const MOCK_SANITIZE_OUTPUT = 'C:\\Users\\demo\\Documents\\client_intake_scan_clean.pdf';

/** Which findings each option promises to remove — `_SANITIZE_PROMISES` in
 *  pdf_verify.py. Whatever no enabled option promises is a residual. */
const MOCK_PROMISES: Record<keyof SanitizeOptions, string[]> = {
  javascript: ['scripts.js'],
  launch_actions: ['actions.launch'],
  auto_actions: ['actions.autorun'],
  embedded_files: ['files.embedded'],
  submit_actions: ['forms.submit'],
  external_links: ['links.uri'],
  metadata: ['meta.docinfo', 'meta.xmp'],
};

/** A verification report in the shape of `VerificationReport.to_dict()`.
 *  *failing* is the option whose whole-file sweep found something left. */
function mockVerification(
  options: SanitizeOptions,
  failing: keyof SanitizeOptions | null
): SanitizeVerification {
  const enabled = (Object.keys(MOCK_PROMISES) as Array<keyof SanitizeOptions>).filter((k) => options[k]);
  const promised = new Set(enabled.flatMap((k) => MOCK_PROMISES[k]));
  const passing = (id: string, description: string) => ({
    id,
    description,
    passed: true,
    evidence: [],
    pages: [],
    isDocumentLevel: false,
    unevaluated: false,
    objectPaths: [],
  });
  return {
    inputPath: MOCK_REPORT.filePath,
    outputPath: MOCK_SANITIZE_OUTPUT,
    tool: 'sanitization',
    timestamp: '2025-03-11T14:21:07+00:00',
    checks: [
      passing('sanitization.readable', 'Output opens and re-scans for verification'),
      ...enabled.map((k) =>
        k === failing
          ? {
              ...passing(`sanitize.sweep_${k}`, `Whole-file sweep finds nothing option '${k}' removes`),
              passed: false,
              evidence: ['a link to a web address on page 3, annotation 2, under /Stash'],
              pages: [3],
              objectPaths: ['trailer/Root/Pages/Kids[2]/Annots[1]/Stash'],
            }
          : passing(`sanitize.sweep_${k}`, `Whole-file sweep finds nothing option '${k}' removes`)
      ),
      passing('sanitize.file_id', "Output does not carry the source file's identifier (/ID)"),
    ],
    residualFindings: MOCK_REPORT.findings.filter((f) => !promised.has(f.id)),
    verified: failing === null,
    flattenTargetPages: null,
    sourceSha256: null,
    outputSha256: null,
    unevaluated: [],
    diagnostics: [],
  };
}

/**
 * What `sanitizeDocument` answers, for the mock document above. Counters as
 * pdf_analyze.py writes them: one exists only for something that was
 * removed, and one thing is counted once (the mock's open action is a
 * script, so either of two options removes it).
 *
 * To make the refusal reviewable without the app, the mock refuses when
 * "External links" is ticked: one of its six links sits where Sanitize has
 * no way to remove it. Every other combination succeeds and is verified,
 * with whatever the ticked options do not cover listed as residuals.
 */
export function mockSanitizeResult(options: SanitizeOptions): SanitizeResponse {
  if (options.external_links) {
    return {
      success: false,
      error: 'Sanitize could not be verified: still present in the output: external links.',
      verification: mockVerification(options, 'external_links'),
      refusal: {
        stillPresent: ['External links: a link to a web address on page 3, annotation 2, under /Stash'],
        notChecked: [],
        identifier: false,
        unreadable: false,
      },
    };
  }
  const removed: Record<string, number> = {};
  if (options.javascript) removed.named_javascript = 1;
  if (options.javascript || options.auto_actions) removed.open_action = 1;
  if (options.launch_actions) removed.launch_action = 1;
  if (options.auto_actions) {
    removed.document_aa = 1;
    removed.page_aa = 1;
  }
  if (options.metadata) removed.doc_info = 1;
  const total_removed = Object.values(removed).reduce((a, b) => a + b, 0);
  return {
    success: true,
    removed,
    total_removed,
    output: MOCK_SANITIZE_OUTPUT,
    output_size: 4611200,
    verification: mockVerification(options, null),
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

/** What the Redact page shows in browser-dev mode, made by the real Python
 *  functions (pdf_ops.redaction_summary, pdf_verify.redaction_refusal_detail)
 *  so that the mock reads like the app. Search for "everything", "lineart",
 *  "refuse" or "xfa" to see each state (#119). */
export const MOCK_REDACT_SUMMARY = {
  "headline": "Redacted 6 places on 2 pages.",
  "checked": "Checked before saving: what you asked to remove is gone from the new file.",
  "removed": [],
  "notes": []
};
export const MOCK_REDACT_SUMMARY_FLATTENED = {
  "headline": "Redacted 6 places on 1 page.",
  "checked": "Checked before saving: what you asked to remove is gone from the new file.",
  "removed": [],
  "notes": [
    "Page 1 was turned into a picture, with 6 areas blacked out on it. Its text can no longer be selected or searched."
  ]
};
export const MOCK_REDACT_SUMMARY_EVERYTHING = {
  "headline": "Redacted 32,768 places on 1,200 pages.",
  "checked": "Checked before saving: what you asked to remove is gone from the new file.",
  "removed": [
    "32,768 notes, stamps or other marks taken off the pages",
    "1 form field taken off a page",
    "32,768 links taken off the pages",
    "1,200 small preview pictures of pages",
    "4 descriptions of removed content, the kind screen readers read aloud",
    "1 hidden copy of removed content that the file still pointed to",
    "2 entries in the document's details, such as the title or author",
    "3 bookmark titles"
  ],
  "notes": [
    "Pages 3, 6, 9, 12, 15, 18 and 94 more have no text to search. They may be scans or pictures, so any words in them were not searched.",
    "Pages 1 and 2 are scans with a hidden text layer. The search read that layer and found 2 matches there. If the layer misread a word, that word is still in the picture.",
    "This file has 12 attached files: quarterly-report-final-version-00.zip, quarterly-report-final-version-01.zip, quarterly-report-final-version-02.zip, quarterly-report-final-version-03.zip, quarterly-report-final-version-04.zip and 7 more. Attached files were searched as they are stored, and were not opened. 12 of them are packed (like a zip or Word file), so nothing inside them could be searched.",
    "Page 4 was turned into a picture, with 2 areas blacked out on it. Its text can no longer be selected or searched.",
    "A form field under one of your boxes is also shown elsewhere, on pages 1-3 and 9. It is still shown there, and anything filled into it is still in the file.",
    "Only the boxes you drew were redacted. The words under them were not searched for anywhere else in the file.",
    "This run used version 2.1 of the PDF library. With this version, text after a removed word can shift along its line."
  ]
};
export const MOCK_REDACT_REFUSAL_LINE_ART = {
  "refusal": {
    "headline": "Redaction refused: some of what you asked to remove is still in the new file. Nothing was saved.",
    "found": [
      "A drawing under a box on page 1 could not be removed without also removing the part of it that goes on outside the box."
    ],
    "nextStep": "You can turn page 1 into a picture to cover it. The page keeps its look, with what was found blacked out, but its text can no longer be selected or searched."
  },
  "flattenablePages": [
    1
  ],
  "verification": {
    "inputPath": "",
    "outputPath": "",
    "tool": "redaction",
    "timestamp": "",
    "checks": [
      {
        "id": "redaction.rect_line_art",
        "description": "redaction.rect_line_art",
        "passed": false,
        "evidence": [
          "a drawing still has an edge inside a redaction rectangle on page 1"
        ],
        "pages": [
          1
        ],
        "isDocumentLevel": false,
        "unevaluated": false,
        "objectPaths": []
      }
    ],
    "residualFindings": [],
    "verified": false,
    "flattenTargetPages": [
      1
    ],
    "sourceSha256": null,
    "outputSha256": null,
    "unevaluated": [],
    "diagnostics": []
  }
};
export const MOCK_REDACT_REFUSAL_DOCUMENT_LEVEL = {
  "refusal": {
    "headline": "Redaction refused: some of what you asked to remove is still in the new file. Nothing was saved.",
    "found": [
      "What you searched for is still in the document's details, such as the title or author.",
      "What you searched for is still hidden in what the file does when it opens."
    ],
    "nextStep": "It is not drawn on a page, so turning pages into pictures would not remove it."
  },
  "flattenablePages": null,
  "verification": {
    "inputPath": "",
    "outputPath": "",
    "tool": "redaction",
    "timestamp": "",
    "checks": [
      {
        "id": "redaction.docinfo",
        "description": "redaction.docinfo",
        "passed": false,
        "evidence": [
          "term 'refuse' found in document info field 'title' (document-level)"
        ],
        "pages": [],
        "isDocumentLevel": true,
        "unevaluated": false,
        "objectPaths": []
      },
      {
        "id": "redaction.term_sweep",
        "description": "redaction.term_sweep",
        "passed": false,
        "evidence": [
          "term 'refuse' found in a text string in the document information, under /Title (document-level)",
          "term 'refuse' found in a text string in the document's open action, under /JS (document-level)"
        ],
        "pages": [],
        "isDocumentLevel": true,
        "unevaluated": false,
        "objectPaths": []
      }
    ],
    "residualFindings": [],
    "verified": false,
    "flattenTargetPages": null,
    "sourceSha256": null,
    "outputSha256": null,
    "unevaluated": [],
    "diagnostics": []
  }
};
export const MOCK_REDACT_NOT_POSSIBLE = "This file's form is stored in a format (XFA) that redaction cannot change, and what you searched for is in that form. Nothing was saved.";

/** #128: a run with "Also read scanned pages with OCR" ticked (any search
 *  word; "damage" shows the refusal for a page that would have changed
 *  outside the places found), and the line shown when OCR is not set up
 *  (add ?ocr=off to the address). Made by the same Python functions. */
export const MOCK_REDACT_SUMMARY_OCR = {
  "headline": "Redacted 3 places on 2 pages.",
  "checked": "Checked before saving: a search of the new file no longer finds what you asked to remove, and OCR no longer reads it on page 1.",
  "removed": [],
  "notes": [
    "OCR read pages 1 and 2 and found 2 places to black out on them. OCR can miss words: check them by eye.",
    "OCR found no words it could read on page 3. It may be a photo or a drawing, or its words may not be in English: check it by eye."
  ]
};
export const MOCK_REDACT_REFUSAL_OCR_OUTSIDE = {
  "refusal": {
    "headline": "Redaction refused: it would have changed more of the page than the places found. Nothing was saved.",
    "found": [
      "The picture of page 1 would have changed outside the places that were blacked out."
    ],
    "nextStep": "The file was left as it was."
  },
  "flattenablePages": null,
  "verification": {
    "inputPath": "",
    "outputPath": "",
    "tool": "redaction",
    "timestamp": "",
    "checks": [
      {
        "id": "redaction.ocr_outside",
        "description": "redaction.ocr_outside",
        "passed": false,
        "evidence": [
          "1,204 pixels changed outside the boxes on page 1"
        ],
        "pages": [
          1
        ],
        "isDocumentLevel": false,
        "unevaluated": false,
        "objectPaths": []
      }
    ],
    "residualFindings": [],
    "verified": false,
    "flattenTargetPages": null,
    "sourceSha256": null,
    "outputSha256": null,
    "unevaluated": [],
    "diagnostics": []
  }
};
export const MOCK_OCR_NOT_READY = "OCR isn't set up: run python setup_ocr.py --install, then restart the app.";
