import type {
  AnalyzeReport,
  RemovalProof,
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
  counts: { high: 2, medium: 3, low: 2, info: 1 },
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
      // #122, as pdf_analyze words it.
      id: 'content.earlier_versions',
      category: 'content',
      severity: 'medium',
      title: 'Earlier versions kept in the file',
      detail:
        'The file keeps 2 earlier versions of itself from incremental saves. Text or pictures removed in a later version may still be in the file. Sanitize writes a copy that keeps only the current version.',
      count: 2,
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
    // The clean copy keeps one version (#122), whatever the options.
    residualFindings: MOCK_REPORT.findings.filter(
      (f) => !promised.has(f.id) && f.id !== 'content.earlier_versions'
    ),
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
    // The proof is the default options' (#100): the mock does not reword it.
    proof: MOCK_PROOF_SANITIZE,
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
    "Pages 1 and 2 are scans with a hidden text layer. The search read that layer and found 2 matches there. If the layer misread a word, or put it in the wrong place, that word is still in the picture. Tick “Also read scanned pages with OCR” to read the pictures too.",
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
 *  (add ?ocr=off to the address). Page 4 is a scan with a text layer that
 *  OCR read too (#140). Made by the same Python functions. */
export const MOCK_REDACT_SUMMARY_OCR = {
  "headline": "Redacted 4 places on 3 pages.",
  "checked": "Checked before saving: a search of the new file no longer finds what you asked to remove, and OCR no longer reads it on pages 1 and 4.",
  "removed": [],
  "notes": [
    "OCR read pages 1 and 2 and found 1 place to black out on them. OCR can miss words: check them by eye.",
    "OCR found no words it could read on page 3. It may be a photo or a drawing, or its words may not be in English: check it by eye.",
    "Page 4 is a scan with a hidden text layer. The search read that layer and found 1 match there, and OCR read the picture too and found 1 more place to black out. OCR can miss words: check it by eye."
  ]
};
/** #143: the search word "picture" shows the refusal for a picture left
 *  holding the word under the search's black box. */
export const MOCK_REDACT_REFUSAL_SEARCH_IMAGE = {
  "refusal": {
    "headline": "Redaction refused: some of what you asked to remove is still in the new file. Nothing was saved.",
    "found": [
      "A picture on page 1 still holds what the search found, under the black box. It could be taken out of the file."
    ],
    "nextStep": "You can turn page 1 into a picture to remove it. The page keeps its look, with what was found blacked out, but its text can no longer be selected or searched."
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
        "id": "redaction.search_image",
        "description": "The pictures under the search's boxes are blanked there",
        "passed": false,
        "evidence": [
          "a picture under a search box on page 1 still holds what was under the box"
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

/** #135: a search that found nothing (search for "nothing"), as redact_pdf
 *  words it for a six-page scan with OCR off. */
export const MOCK_REDACT_NOTHING_FOUND = "No matching content found to redact. Pages 1-6 have no text: a search cannot read words in a scan or a picture. Nothing was saved.";

/** The proof of removal a finished run carries (#100), made by the real
 *  Python functions (pdf_ops.redaction_proof, removal_report.sanitize_proof)
 *  so that the panel reads like the app: an ordinary search, the
 *  "everything" search (every group filled, a source MuPDF repaired, a
 *  setup window open during the run), and Sanitize with its default
 *  options. */
export const MOCK_PROOF_REDACT: RemovalProof = {
  "tool": "Redact",
  "removed": [
    {
      "what": "4 places blacked out",
      "pages": [1],
      "surface": "page_content",
      "where": "Page 1"
    },
    {
      "what": "2 places blacked out",
      "pages": [2],
      "surface": "page_content",
      "where": "Page 2"
    }
  ],
  "notTargeted": [],
  "notChecked": [],
  "checks": [
    {
      "id": "redaction.readable",
      "description": "The new file opens and can be read to check it",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.page_text",
      "description": "None of the searched-for words can be read in the text of the pages",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.raw_content",
      "description": "None of the searched-for words is in the code that draws the pages",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.annotations",
      "description": "None of the searched-for words is in a note or comment",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.form_fields",
      "description": "None of the searched-for words is in what is filled into a form field",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.links",
      "description": "None of the searched-for words is in a link",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.docinfo",
      "description": "None of the searched-for words is in the document's details, such as the title or author",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.xmp",
      "description": "None of the searched-for words is in the hidden document details (XMP)",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.bookmarks",
      "description": "None of the searched-for words is in a bookmark title",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.embedded_files",
      "description": "None of the searched-for words is in the name of an attached file",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.term_sweep",
      "description": "A search of everything else in the file finds none of the searched-for words",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.search_image",
      "description": "The pictures under the words found are blanked there",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.text_outside",
      "description": "Text outside the boxes shows as it did",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.left_behind",
      "description": "Nothing a box covered is left in the file where no page shows it",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.structure_streams",
      "description": "Nothing is kept in the file by its structure alone",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.thumbnails",
      "description": "No page has a small preview picture of itself",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.one_version",
      "description": "The new file keeps no earlier versions of itself",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.file_id",
      "description": "The new file does not carry the original's ID, which would link the two",
      "passed": true,
      "pages": []
    }
  ],
  "earlierVersions": "The source kept 2 earlier versions; this copy keeps none.",
  "network": "Network: none used during this run."
};
export const MOCK_PROOF_REDACT_EVERYTHING: RemovalProof = {
  "tool": "Redact",
  "removed": [
    {
      "what": "28 places blacked out on each",
      "pages": Array.from({ length: 368 }, (_, i) => i + 1),
      "surface": "page_content",
      "where": "Pages 1-368"
    },
    {
      "what": "27 places blacked out on each",
      "pages": Array.from({ length: 832 }, (_, i) => i + 369),
      "surface": "page_content",
      "where": "Pages 369-1200"
    },
    {
      "what": "Turned into a picture, with 2 areas blacked out. Its text can no longer be selected or searched.",
      "pages": [4],
      "surface": "flattened",
      "where": "Page 4"
    },
    {
      "what": "32,768 notes, stamps or other marks taken off the pages",
      "pages": [],
      "surface": "annotations_removed",
      "where": ""
    },
    {
      "what": "1 form field taken off a page",
      "pages": [],
      "surface": "fields_removed",
      "where": ""
    },
    {
      "what": "32,768 links taken off the pages",
      "pages": [],
      "surface": "links_removed",
      "where": ""
    },
    {
      "what": "1,200 small preview pictures of pages",
      "pages": [],
      "surface": "thumbnails",
      "where": ""
    },
    {
      "what": "4 descriptions of removed content, the kind screen readers read aloud",
      "pages": [],
      "surface": "structure_text",
      "where": ""
    },
    {
      "what": "1 hidden copy of removed content that the file still pointed to",
      "pages": [],
      "surface": "structure_refs",
      "where": ""
    },
    {
      "what": "2 entries in the document's details, such as the title or author",
      "pages": [],
      "surface": "docinfo",
      "where": ""
    },
    {
      "what": "3 bookmark titles",
      "pages": [],
      "surface": "bookmarks",
      "where": ""
    }
  ],
  "notTargeted": [
    {
      "what": "A form field under one of your boxes is also shown here. It still shows, and anything filled into it is still in the file.",
      "pages": [1, 2, 3, 9],
      "next": "Draw a box over it here too.",
      "where": "Pages 1-3 and 9"
    }
  ],
  "notChecked": [
    {
      "what": "No text to search: they may be scans or pictures, so any words in them were not searched.",
      "pages": [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60, 63, 66, 69, 72, 75, 78, 81, 84, 87, 90, 93, 96, 99, 102, 105, 108, 111, 114, 117, 120, 123, 126, 129, 132, 135, 138, 141, 144, 147, 150, 153, 156, 159, 162, 165, 168, 171, 174, 177, 180, 183, 186, 189, 192, 195, 198, 201, 204, 207, 210, 213, 216, 219, 222, 225, 228, 231, 234, 237, 240, 243, 246, 249, 252, 255, 258, 261, 264, 267, 270, 273, 276, 279, 282, 285, 288, 291, 294, 297, 300],
      "next": "Run Redact again with “Also read scanned pages with OCR” ticked (OCR must be set up), or check them by eye.",
      "where": "Pages 3, 6, 9, 12, 15, 18 and 94 more"
    },
    {
      "what": "Scans with a hidden text layer: the search read the layer, not the pictures. A word the layer misread, or put in the wrong place, is still in the pictures.",
      "pages": [1, 2],
      "next": "Run Redact again with “Also read scanned pages with OCR” ticked to read the pictures too.",
      "where": "Pages 1 and 2"
    },
    {
      "what": "12 attached files were searched as they are stored, and not opened. All of them are packed (like a zip or Word file), so nothing inside them could be searched.",
      "pages": [],
      "next": "Open them to check them, or run Sanitize with “Embedded files / attachments” ticked to remove them.",
      "where": ""
    },
    {
      "what": "The words under your boxes were not searched for anywhere else in the file.",
      "pages": [],
      "next": "Redact by search terms to find them elsewhere.",
      "where": ""
    },
    {
      "what": "Whether the source kept earlier versions of itself: the file is damaged and was repaired to be read, so its saved versions could not be counted.",
      "pages": [],
      "next": null,
      "where": ""
    }
  ],
  "checks": [
    {
      "id": "redaction.readable",
      "description": "The new file opens and can be read to check it",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.page_text",
      "description": "None of the searched-for words can be read in the text of the pages",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.raw_content",
      "description": "None of the searched-for words is in the code that draws the pages",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.annotations",
      "description": "None of the searched-for words is in a note or comment",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.form_fields",
      "description": "None of the searched-for words is in what is filled into a form field",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.links",
      "description": "None of the searched-for words is in a link",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.docinfo",
      "description": "None of the searched-for words is in the document's details, such as the title or author",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.xmp",
      "description": "None of the searched-for words is in the hidden document details (XMP)",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.bookmarks",
      "description": "None of the searched-for words is in a bookmark title",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.embedded_files",
      "description": "None of the searched-for words is in the name of an attached file",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.term_sweep",
      "description": "A search of everything else in the file finds none of the searched-for words",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.search_image",
      "description": "The pictures under the words found are blanked there",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.text_outside",
      "description": "Text outside the boxes shows as it did",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.left_behind",
      "description": "Nothing a box covered is left in the file where no page shows it",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.structure_streams",
      "description": "Nothing is kept in the file by its structure alone",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.thumbnails",
      "description": "No page has a small preview picture of itself",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.one_version",
      "description": "The new file keeps no earlier versions of itself",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.file_id",
      "description": "The new file does not carry the original's ID, which would link the two",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.rect_regions",
      "description": "No text is left under the boxes",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.rect_annotations",
      "description": "No note, stamp or other mark lies over the boxes",
      "passed": true,
      "pages": []
    },
    {
      "id": "redaction.rect_line_art",
      "description": "No drawing has an edge inside the boxes",
      "passed": true,
      "pages": []
    }
  ],
  "earlierVersions": "Whether the source kept earlier versions could not be checked; this copy keeps none.",
  "network": "Network: open for translation setup during part of this run, from 14:02:11 to 14:05:40. 2 attempts to connect were refused (updates.example.com)."
};
export const MOCK_PROOF_SANITIZE: RemovalProof = {
  "tool": "Sanitize",
  "removed": [
    {
      "what": "1 set of the document's named scripts",
      "pages": [],
      "surface": "javascript",
      "where": ""
    },
    {
      "what": "1 action that starts another program",
      "pages": [],
      "surface": "launch_actions",
      "where": ""
    },
    {
      "what": "3 actions that run by themselves",
      "pages": [],
      "surface": "auto_actions",
      "where": ""
    }
  ],
  "notTargeted": [
    {
      "what": "Invisible text layer",
      "pages": [],
      "next": "Normal for a scan read with OCR. If it is text someone meant to hide, remove it with Redact.",
      "where": ""
    },
    {
      "what": "External links / URLs",
      "pages": [],
      "next": "Run Sanitize with “External links / URLs (trackers)” ticked.",
      "where": ""
    },
    {
      "what": "Document metadata present",
      "pages": [],
      "next": "Run Sanitize with “Document & XMP metadata” ticked.",
      "where": ""
    },
    {
      "what": "Interactive form fields",
      "pages": [],
      "next": "Use Flatten to remove the form fields, if you no longer need them.",
      "where": ""
    }
  ],
  "notChecked": [],
  "checks": [
    {
      "id": "sanitization.readable",
      "description": "The clean copy opens and can be scanned again",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.scripts_js",
      "description": "A new scan finds no JavaScript",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.actions_launch",
      "description": "A new scan finds no actions that start another program",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.actions_autorun",
      "description": "A new scan finds no actions that run by themselves",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.files_embedded",
      "description": "A new scan finds no attached files",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.forms_submit",
      "description": "A new scan finds no actions that send or load form data",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.sweep_javascript",
      "description": "A look at every object in the file finds no JavaScript",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.sweep_launch_actions",
      "description": "A look at every object in the file finds no actions that start another program",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.sweep_auto_actions",
      "description": "A look at every object in the file finds no actions that run by themselves",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.sweep_embedded_files",
      "description": "A look at every object in the file finds no attached files",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.sweep_submit_actions",
      "description": "A look at every object in the file finds no actions that send or load form data",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.one_version",
      "description": "The clean copy keeps no earlier versions of itself",
      "passed": true,
      "pages": []
    },
    {
      "id": "sanitize.file_id",
      "description": "The clean copy does not carry the original's ID, which would link the two",
      "passed": true,
      "pages": []
    }
  ],
  "earlierVersions": "The source kept 2 earlier versions; this copy keeps none.",
  "network": "Network: none used during this run."
};
