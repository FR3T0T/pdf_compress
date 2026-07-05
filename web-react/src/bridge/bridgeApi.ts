import type { AnalyzeResponse, SanitizeOptions, SanitizeResponse } from '../types/analyze';
import type { PresetsResponse, ToolRegistry } from '../types/bridge';
import {
  MOCK_REPORT,
  MOCK_SANITIZE_DEFAULTS,
  MOCK_TOOL_REGISTRY,
  MOCK_PRESETS,
  mockSanitizeResult,
} from './mockData';
import { simulateOperation, type MockFileLike } from './mockEventBus';
import { safeJsonParse } from './safeJsonParse';

export const isRealBridge = (): boolean => typeof window !== 'undefined' && !!window.BridgeAPI;

const MOCK_DELAY_MS = 450;
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function basenameOf(path: string): string {
  return path.split(/[/\\]/).pop() ?? path;
}

function dirnameOf(path: string): string {
  const parts = path.split(/[/\\]/);
  parts.pop();
  return parts.join('\\');
}

function formatSizeOf(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatPctOf(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * Best-effort file list extraction from a startXxx params object, for mock
 * progress simulation only. Real params shapes vary per tool (files: [],
 * file: string, file_a/file_b for compare) — this doesn't need to be exact,
 * just plausible enough to drive a believable progress sequence in dev.
 */
function mockFilesFromParams(params: Record<string, unknown>): MockFileLike[] {
  const asName = (v: unknown): string =>
    typeof v === 'string' ? basenameOf(v) : basenameOf(String((v as { path?: string })?.path ?? 'document.pdf'));

  if (Array.isArray(params.files) && params.files.length > 0) {
    return params.files.map((f) => ({ name: asName(f) }));
  }
  if (Array.isArray(params.imagePaths) && params.imagePaths.length > 0) {
    return params.imagePaths.map((f) => ({ name: asName(f) }));
  }
  if (typeof params.file === 'string') return [{ name: asName(params.file) }];
  if (typeof params.file_a === 'string') {
    const names = [asName(params.file_a)];
    if (typeof params.file_b === 'string') names.push(asName(params.file_b));
    return names.map((name) => ({ name }));
  }
  return [{ name: 'document.pdf' }];
}

/** toolKey defaults used by ui/bridge.py's p.get("toolKey", "<default>") for
 *  each startXxx slot — verified against source, not the JS-passed params
 *  (the vanilla pages never pass an explicit toolKey override). */
const TOOL_KEYS = {
  compress: 'compress',
  merge: 'merge',
  split: 'split',
  pageOps: 'page_ops',
  protect: 'protect',
  unlock: 'unlock',
  crop: 'crop',
  watermark: 'watermark',
  pageNumbers: 'page_numbers',
  extractImages: 'extract_images',
  extractText: 'extract_text',
  imagesToPdf: 'images_to_pdf',
  pdfToImages: 'pdf_to_images',
  pdfToWord: 'pdf_to_word',
  flatten: 'flatten',
  repair: 'repair',
  redact: 'redact',
  writeMetadata: 'metadata', // NOT "write_metadata" — see ui/bridge.py:1167
  compare: 'compare',
  nup: 'nup',
  translatePdf: 'translate',
} as const;

/**
 * Thin pass-through to window.BridgeAPI when running inside the PySide6
 * QWebEngine app; otherwise a realistic in-memory mock so `vite dev` and
 * `npm run build` + a plain browser both work standalone. Method names and
 * argument order match web/js/bridge.js exactly.
 */
export const bridgeApi = {
  // -- File dialogs -----------------------------------------------------
  async openFiles(filter = 'PDF Files (*.pdf)'): Promise<string[]> {
    if (window.BridgeAPI) return window.BridgeAPI.openFiles(filter);
    return [];
  },

  async openFolder(): Promise<string | null> {
    if (window.BridgeAPI) return window.BridgeAPI.openFolder();
    // Matches saveFile()'s mock behavior — a synthetic path instead of
    // null, so folder-output tools are testable in plain-browser dev too.
    return 'C:\\Users\\demo\\Documents\\output';
  },

  async saveFile(filter: string, defaultName: string): Promise<string | null> {
    if (window.BridgeAPI) return window.BridgeAPI.saveFile(filter, defaultName);
    return `C:\\Users\\demo\\Documents\\${defaultName}`;
  },

  // -- Data queries -------------------------------------------------------
  async getPresets(): Promise<PresetsResponse> {
    if (window.BridgeAPI) return window.BridgeAPI.getPresets();
    await delay(100);
    return MOCK_PRESETS;
  },

  async analyzeFile(path: string): Promise<Record<string, unknown>> {
    if (window.BridgeAPI) return window.BridgeAPI.analyzeFile(path);
    await delay(MOCK_DELAY_MS);
    return { success: true, file_size: 2_400_000, page_count: 8 };
  },

  async getThumbnail(
    path: string
  ): Promise<{ success: boolean; dataUrl?: string; width?: number; height?: number }> {
    if (window.BridgeAPI) return window.BridgeAPI.getThumbnail(path);
    return { success: false };
  },

  async getPageImages(path: string) {
    if (window.BridgeAPI) return window.BridgeAPI.getPageImages(path);
    // Mock mode: two synthetic "pages" (SVG data URLs) at the same pixel
    // proportions a real 150 DPI US-Letter render would have, so the
    // Draw-boxes canvas is exercisable in `vite dev`. Coordinate mapping
    // itself can only be verified against real renders in the real app.
    await delay(150);
    const mockPage = (i: number) => {
      const svg =
        `<svg xmlns="http://www.w3.org/2000/svg" width="850" height="1100">` +
        `<rect width="850" height="1100" fill="#f5f5f0"/>` +
        `<text x="425" y="550" font-size="28" text-anchor="middle" fill="#999">Mock page ${i + 1} (${basenameOf(path)})</text>` +
        `</svg>`;
      return `data:image/svg+xml;base64,${btoa(svg)}`;
    };
    return {
      success: true,
      dpi: 150,
      pages: [0, 1].map((i) => ({ index: i, dataUrl: mockPage(i), width: 850, height: 1100 })),
    };
  },

  async getMetadata(path: string) {
    if (window.BridgeAPI) return window.BridgeAPI.getMetadata(path);
    await delay(150);
    return { title: basenameOf(path), author: '', subject: '', keywords: '', creator: '', producer: '' };
  },

  async getToc(path: string) {
    if (window.BridgeAPI) return window.BridgeAPI.getToc(path);
    // Mock mode: files named "book*" get sample chapters so the chapters
    // UI is exercisable in vite dev; everything else has no bookmarks.
    if (basenameOf(path).toLowerCase().startsWith('book')) {
      return [
        { level: 1, title: 'Chapter 1: Introduction', page: 1, end_page: 8 },
        { level: 1, title: 'Chapter 2: Getting Started', page: 9, end_page: 20 },
        { level: 2, title: '2.1 Installation', page: 9, end_page: 12 },
        { level: 1, title: 'Chapter 3: Conclusion', page: 21, end_page: 24 },
      ];
    }
    return [];
  },

  async analyzeDocument(path: string): Promise<AnalyzeResponse> {
    if (window.BridgeAPI) return window.BridgeAPI.analyzeDocument(path);
    await delay(MOCK_DELAY_MS);
    return {
      success: true,
      report: {
        ...MOCK_REPORT,
        fileName: basenameOf(path) || MOCK_REPORT.fileName,
        filePath: path || MOCK_REPORT.filePath,
      },
    };
  },

  async getSanitizeDefaults(): Promise<SanitizeOptions> {
    if (window.BridgeAPI) return window.BridgeAPI.getSanitizeDefaults();
    await delay(100);
    return { ...MOCK_SANITIZE_DEFAULTS };
  },

  async sanitizeDocument(
    path: string,
    outputPath: string,
    options: Partial<SanitizeOptions>
  ): Promise<SanitizeResponse> {
    if (window.BridgeAPI) return window.BridgeAPI.sanitizeDocument(path, outputPath, options);
    await delay(MOCK_DELAY_MS * 2);
    return mockSanitizeResult({ ...MOCK_SANITIZE_DEFAULTS, ...options });
  },

  // -- Translation (offline) ---------------------------------------------
  async getTranslationStatus(): Promise<Record<string, unknown>> {
    if (window.BridgeAPI) return window.BridgeAPI.getTranslationStatus();
    return {
      success: true,
      argosAvailable: true,
      ocrAvailable: true,
      ocrLangs: ['eng', 'spa'],
      argosPairs: ['en->es', 'es->en', 'en->fr', 'fr->en'],
      languages: [
        { code: 'en', name: 'English', native: 'English', ocr: true, translateTo: true, translateFrom: true },
        { code: 'es', name: 'Spanish', native: 'Español', ocr: true, translateTo: true, translateFrom: true },
        { code: 'fr', name: 'French', native: 'Français', ocr: false, translateTo: true, translateFrom: true },
        { code: 'de', name: 'German', native: 'Deutsch', ocr: false, translateTo: false, translateFrom: false },
        { code: 'zh', name: 'Mandarin Chinese', native: '中文', ocr: false, translateTo: false, translateFrom: false },
      ],
    };
  },

  async translateText(
    text: string,
    source: string,
    target: string,
    protectTerms?: string[]
  ): Promise<Record<string, unknown>> {
    if (window.BridgeAPI) return window.BridgeAPI.translateText(text, source || 'auto', target, protectTerms);
    await delay(MOCK_DELAY_MS);
    return { success: true, translated: `[mock translation of: ${text}]`, source, target };
  },

  async translateImage(
    path: string,
    source: string,
    target: string,
    protectTerms?: string[]
  ): Promise<Record<string, unknown>> {
    if (window.BridgeAPI) return window.BridgeAPI.translateImage(path, source || 'auto', target, protectTerms);
    await delay(MOCK_DELAY_MS * 2);
    return { success: true, sourceText: '(mock OCR text)', translatedText: '(mock translation)', source, target };
  },

  startTranslatePdf(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startTranslatePdf(params);
    else {
      const outputPath = String(params.outputPath ?? params.output_path ?? '');
      simulateOperation(TOOL_KEYS.translatePdf, mockFilesFromParams(params), () => ({
        output: outputPath,
        outputSize: 12_400,
        pages: 6,
        source: params.source === 'auto' ? 'en' : String(params.source ?? 'en'),
        target: String(params.target ?? 'es'),
      }));
    }
  },

  async checkEpdf(path: string) {
    if (window.BridgeAPI) return window.BridgeAPI.checkEpdf(path);
    if (path.toLowerCase().endsWith('.epdf')) {
      return { isEpdf: true, cipher: 'chacha20-poly1305', kdf: 'argon2id', originalFilename: basenameOf(path).replace(/\.epdf$/i, '.pdf'), created: new Date().toISOString() };
    }
    return { isEpdf: false };
  },

  // -- Async operations (fire-and-forget; results via EventBus "done") --
  // Mock mode simulates progress/done on mockEventBus so useOperation()
  // behaves identically whether or not window.BridgeAPI is present.
  startCompress(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startCompress(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      simulateOperation(TOOL_KEYS.compress, mockFiles, () =>
        mockFiles.map((f) => {
          const original = 2_000_000 + Math.round(Math.random() * 3_000_000);
          const compressed = Math.round(original * 0.45);
          return {
            input_path: f.name,
            output_path: f.name.replace(/\.pdf$/i, '_compressed.pdf'),
            original_size: original,
            compressed_size: compressed,
            skipped: false,
          };
        })
      );
    }
  },
  startMerge(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startMerge(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.merge, mockFiles, () => ({
        output_path: outputPath,
        input_paths: Array.isArray(params.files) ? params.files : [],
        total_pages: mockFiles.length * 4,
      }));
    }
  },
  startSplit(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startSplit(params);
    else {
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents\\split');
      const inputName = String(params.file ?? 'document.pdf').split(/[/\\]/).pop() ?? 'document.pdf';
      const baseName = inputName.replace(/\.pdf$/i, '');
      let outputPaths: string[];
      let pagesPerOutput: number[];
      if (Array.isArray(params.chapters) && params.chapters.length > 0) {
        outputPaths = params.chapters.map((_, i) => `${outputDir}\\${baseName}_chapter${i + 1}.pdf`);
        pagesPerOutput = params.chapters.map(() => 3 + Math.round(Math.random() * 5));
      } else {
        const count = params.mode === 'every_n' ? 3 : params.mode === 'ranges' ? 2 : 5;
        outputPaths = Array.from({ length: count }, (_, i) => `${outputDir}\\${baseName}_page${i + 1}.pdf`);
        pagesPerOutput = Array.from({ length: count }, () => 1);
      }
      simulateOperation(TOOL_KEYS.split, [{ name: inputName }], () => ({
        input_path: String(params.file ?? ''),
        output_paths: outputPaths,
        pages_per_output: pagesPerOutput,
      }));
    }
  },
  startPageOps(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startPageOps(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      const ops: string[] = [];
      if (params.rotations) ops.push('rotate');
      if (params.new_order) ops.push('reorder');
      if (params.delete_pages) ops.push('delete');
      simulateOperation(TOOL_KEYS.pageOps, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_path: outputPath,
        operations: ops,
      }));
    }
  },
  startProtect(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startProtect(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents');
      const mode = String(params.mode ?? 'standard');
      simulateOperation(TOOL_KEYS.protect, mockFiles, () => ({
        files: mockFiles.map((f) => ({
          file: f.name,
          status: 'ok',
          details: mode === 'enhanced' ? String(params.cipher ?? 'chacha20-poly1305') : String(params.encryption ?? 'AES-256'),
          outputPath: `${outputDir}\\${f.name}`,
        })),
        elapsed: 0.8,
        output_dir: outputDir,
      }));
    }
  },
  startUnlock(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startUnlock(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents');
      simulateOperation(TOOL_KEYS.unlock, mockFiles, () => ({
        files: mockFiles.map((f) => ({ file: f.name, status: 'ok', details: 'PDF unlocked', outputPath: `${outputDir}\\${f.name}` })),
        elapsed: 0.6,
        output_dir: outputDir,
      }));
    }
  },
  startCrop(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startCrop(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.crop, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },
  startWatermark(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startWatermark(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents');
      simulateOperation(TOOL_KEYS.watermark, mockFiles, () => ({
        files: mockFiles.map((f) => ({ file: f.name, status: 'ok', details: 'watermarked', outputPath: `${outputDir}\\${f.name}` })),
        elapsed: 1.2,
        output_dir: outputDir,
      }));
    }
  },
  startPageNumbers(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startPageNumbers(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.pageNumbers, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },
  startExtractImages(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startExtractImages(params);
    else {
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents\\extracted');
      const imagePaths = [`${outputDir}\\image_000.png`, `${outputDir}\\image_001.png`];
      simulateOperation(TOOL_KEYS.extractImages, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_dir: outputDir,
        image_paths: imagePaths,
        image_count: imagePaths.length,
      }));
    }
  },
  startExtractText(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startExtractText(params);
    else {
      simulateOperation(TOOL_KEYS.extractText, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_path: String(params.output_path ?? params.outputPath ?? ''),
        page_count: 8,
        char_count: 4213,
      }));
    }
  },
  startImagesToPdf(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startImagesToPdf(params);
    else {
      const mockFiles = mockFilesFromParams(params);
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.imagesToPdf, mockFiles, () => ({
        output_path: outputPath,
        image_count: mockFiles.length,
        page_count: mockFiles.length,
      }));
    }
  },
  startPdfToImages(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startPdfToImages(params);
    else {
      const outputDir = String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents\\pages');
      const fmt = String(params.format ?? 'png');
      const imagePaths = [`${outputDir}\\page_001.${fmt}`, `${outputDir}\\page_002.${fmt}`, `${outputDir}\\page_003.${fmt}`];
      simulateOperation(TOOL_KEYS.pdfToImages, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_dir: outputDir,
        image_paths: imagePaths,
        page_count: imagePaths.length,
        format: fmt,
      }));
    }
  },
  startPdfToWord(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startPdfToWord(params);
    else {
      simulateOperation(TOOL_KEYS.pdfToWord, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_path: String(params.output_path ?? params.outputPath ?? ''),
        page_count: 6,
      }));
    }
  },
  startFlatten(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startFlatten(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.flatten, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },
  startRepair(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startRepair(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.repair, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },
  startRedact(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startRedact(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      const termCount = Array.isArray(params.search_terms) ? params.search_terms.length : 1;
      simulateOperation(TOOL_KEYS.redact, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_path: outputPath,
        redaction_count: termCount * 3,
        pages_affected: Math.min(termCount * 2, 5),
      }));
    }
  },
  startWriteMetadata(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startWriteMetadata(params);
    else {
      const outputPath = String(params.outputPath ?? params.output_path ?? '');
      simulateOperation(TOOL_KEYS.writeMetadata, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },
  startCompare(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startCompare(params);
    else {
      const pathA = String(params.file_a ?? params.fileA ?? '');
      const pathB = String(params.file_b ?? params.fileB ?? '');
      simulateOperation(
        TOOL_KEYS.compare,
        [{ name: basenameOf(pathA) }, { name: basenameOf(pathB) }],
        () => ({
          path_a: pathA,
          path_b: pathB,
          page_diffs: [
            { page: 1, status: 'identical', details: '' },
            { page: 2, status: 'different', details: '+4 words, -1 words' },
            { page: 3, status: 'identical', details: '' },
            { page: 4, status: 'added_in_b', details: 'Page only in document B' },
          ],
          metadata_diffs: [{ field: '/Author', a: 'J. Whitfield', b: 'J. Whitfield (revised)' }],
        })
      );
    }
  },
  startNup(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startNup(params);
    else {
      const outputPath = String(params.output_path ?? params.outputPath ?? '');
      simulateOperation(TOOL_KEYS.nup, mockFilesFromParams(params), () => ({ outputPath }));
    }
  },

  cancel(toolKey: string): void {
    window.BridgeAPI?.cancel(toolKey);
  },

  // -- Shell helpers -------------------------------------------------------
  openFolderPath(path: string): void {
    window.BridgeAPI?.openFolderPath(path);
  },
  openFilePath(path: string): void {
    window.BridgeAPI?.openFilePath(path);
  },

  // -- Settings persistence -------------------------------------------------
  saveSetting(key: string, value: string): void {
    window.BridgeAPI?.saveSetting(key, value);
  },
  async loadSetting(key: string): Promise<string | null> {
    if (window.BridgeAPI) return window.BridgeAPI.loadSetting(key);
    return '';
  },

  // -- Utility helpers (pure JS, no bridge call) ---------------------------
  formatSize(bytes: number): string {
    return window.BridgeAPI ? window.BridgeAPI.formatSize(bytes) : formatSizeOf(bytes);
  },
  formatPct(value: number): string {
    return window.BridgeAPI ? window.BridgeAPI.formatPct(value) : formatPctOf(value);
  },
  basename(path: string): string {
    return window.BridgeAPI ? window.BridgeAPI.basename(path) : basenameOf(path);
  },
  dirname(path: string): string {
    return window.BridgeAPI ? window.BridgeAPI.dirname(path) : dirnameOf(path);
  },

  // -- Boot-time calls that bypass BridgeAPI in the vanilla app too --------
  // (web/js/app.js calls these directly on App.bridge, not via BridgeAPI)
  async getToolRegistry(): Promise<ToolRegistry> {
    if (window.App?.bridge) {
      const json = await window.App.bridge.getToolRegistry();
      return safeJsonParse(json, MOCK_TOOL_REGISTRY);
    }
    await delay(80);
    return MOCK_TOOL_REGISTRY;
  },

  requestThemeToggle(): void {
    window.App?.bridge?.requestThemeToggle();
  },

  /** Optional: native OS drag-drop paths arrive via a Python signal on this
   *  EventBus (see web/js/app.js). No-op when the bus isn't present.
   *  Payload is a bare string[] (see EventMap in eventBus.ts), not
   *  {paths: string[]} -- a prior version expected the wrapped shape and
   *  the callback silently never fired since the real payload has no
   *  `.paths` property. */
  onFilesDropped(cb: (paths: string[]) => void): () => void {
    if (!window.EventBus) return () => {};
    const handler = (data: unknown) => {
      const paths = data as string[] | undefined;
      if (paths?.length) cb(paths);
    };
    window.EventBus.on('files-dropped', handler);
    return () => window.EventBus?.off('files-dropped', handler);
  },
};
