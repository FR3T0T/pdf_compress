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

/** Small stable string hash, so mock analysis is deterministic per filename. */
function mockHash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) >>> 0;
  return h;
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
  translationStatus: 'translationStatus',
  translateSetup: 'translateSetup',
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
    // Deterministic-from-filename mock so the Compress rich cards (size,
    // pages, image DPI, per-preset savings estimate) are exercisable in
    // vite dev. The real bridge returns these same fields from ui/bridge.py
    // (estimates{}, imageSummary{}); shapes match field-for-field.
    const h = mockHash(basenameOf(path));
    const fileSize = 900_000 + (h % 9) * 620_000; // ~0.9–6 MB
    const pages = 2 + (h % 22);
    const imageCount = h % 11;
    const maxDpi = [96, 150, 220, 300][h % 4];
    const mkEst = (colorDpi: number, jpegQ: number) => {
      const dpiFactor = maxDpi > colorDpi ? 1 - colorDpi / maxDpi : 0;
      const savedPct = Math.min(
        82,
        Math.round(6 + dpiFactor * 62 + (85 - jpegQ) * 0.3 + (imageCount > 0 ? 8 : 0))
      );
      const est = Math.round(fileSize * (1 - savedPct / 100));
      return {
        estimatedSize: est,
        estimatedSizeStr: formatSizeOf(est),
        savedBytes: fileSize - est,
        savedBytesStr: formatSizeOf(fileSize - est),
        savedPct,
        targetDpi: colorDpi,
        jpegQuality: jpegQ,
      };
    };
    return {
      success: true,
      file_size: fileSize,
      page_count: pages,
      image_count: imageCount,
      imageSummary: {
        count: imageCount,
        totalBytes: Math.round(fileSize * 0.6),
        totalBytesStr: formatSizeOf(Math.round(fileSize * 0.6)),
        avgDpi: Math.round(maxDpi * 0.8),
        maxDpi,
        minDpi: Math.round(maxDpi * 0.5),
        jpegCount: imageCount,
        grayscaleCount: 0,
        monochromeCount: 0,
        pctOfFile: imageCount > 0 ? 60 : 0,
      },
      estimates: { light: mkEst(200, 85), standard: mkEst(150, 75), aggressive: mkEst(100, 60) },
    };
  },

  async getThumbnail(
    path: string
  ): Promise<{ success: boolean; dataUrl?: string; width?: number; height?: number }> {
    if (window.BridgeAPI) return window.BridgeAPI.getThumbnail(path);
    // Synthetic page-1 preview (SVG data URL) so the thumbnail column is
    // reviewable in vite dev. The real bridge renders a JPEG via PyMuPDF.
    await delay(200);
    const name = basenameOf(path).replace(/[^\x20-\x7e]/g, '');
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="140" height="180">` +
      `<rect width="140" height="180" fill="#f7f6f2"/>` +
      `<rect x="14" y="18" width="112" height="8" rx="2" fill="#c9c6be"/>` +
      `<rect x="14" y="34" width="90" height="6" rx="2" fill="#dcd9d1"/>` +
      `<rect x="14" y="50" width="112" height="6" rx="2" fill="#dcd9d1"/>` +
      `<rect x="14" y="64" width="70" height="6" rx="2" fill="#dcd9d1"/>` +
      `<text x="70" y="168" font-size="9" text-anchor="middle" fill="#9a978f">${name.slice(0, 20)}</text>` +
      `</svg>`;
    return { success: true, dataUrl: `data:image/svg+xml;base64,${btoa(svg)}`, width: 140, height: 180 };
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

  // Async counterpart of getTranslationStatus above -- see ui/bridge.py's
  // startGetTranslationStatus for why the React Translate page must use
  // this instead: the sync call's first-use argostranslate import can
  // freeze the window for several seconds on page mount.
  startGetTranslationStatus(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startGetTranslationStatus(params);
    else {
      simulateOperation(TOOL_KEYS.translationStatus, [], () => ({
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
      }));
    }
  },

  // One-time, user-initiated translation setup (the app's only network
  // operation): in the frozen build this downloads the pinned ML runtime
  // first, then the chosen Argos language packs; from source it skips
  // straight to the language packs. See ui/bridge.py startSetupTranslation.
  startSetupTranslation(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startSetupTranslation(params);
    else {
      simulateOperation(TOOL_KEYS.translateSetup, [], () => ({
        runtimeInstalled: true,
        installed: (params.codes as string[] | undefined)?.length ?? 0,
        skipped: 0,
        requested: (params.codes as string[] | undefined)?.length ?? 0,
      }));
    }
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

  // Async counterparts of translateText/translateImage above -- fire-and-
  // forget like every other startXxx, off the UI thread on the real
  // bridge (see ui/bridge.py) so Argos's slow first-use model load
  // doesn't freeze the window. Same toolKey ("translate") as
  // startTranslatePdf so one useOperation('translate') on the frontend
  // handles all three flows.
  startTranslateText(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startTranslateText(params);
    else {
      const text = String(params.text ?? '');
      const source = params.source === 'auto' ? 'en' : String(params.source ?? 'en');
      const target = String(params.target ?? 'es');
      simulateOperation(TOOL_KEYS.translatePdf, [{ name: 'text' }], () => ({
        translated: `[mock translation of: ${text}]`,
        source,
        target,
      }));
    }
  },

  startTranslateImage(params: Record<string, unknown>): void {
    if (window.BridgeAPI) window.BridgeAPI.startTranslateImage(params);
    else {
      const path = String(params.path ?? '');
      const source = params.source === 'auto' ? 'en' : String(params.source ?? 'en');
      const target = String(params.target ?? 'es');
      simulateOperation(TOOL_KEYS.translatePdf, [{ name: basenameOf(path) }], () => ({
        sourceText: '(mock OCR text)',
        translatedText: '(mock translation)',
        source,
        target,
        ocrLang: 'eng',
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
      const explicit = params.outputPath ? String(params.outputPath) : '';
      const single = explicit && mockFiles.length === 1;
      const outputDir = single ? dirnameOf(explicit) : String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents');
      const mode = String(params.mode ?? 'standard');
      simulateOperation(TOOL_KEYS.protect, mockFiles, () => ({
        files: mockFiles.map((f) => ({
          file: f.name,
          status: 'ok',
          details: mode === 'enhanced' ? String(params.cipher ?? 'chacha20-poly1305') : String(params.encryption ?? 'AES-256'),
          outputPath: single ? explicit : `${outputDir}\\${f.name}`,
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
      const explicit = params.outputPath ? String(params.outputPath) : '';
      const single = explicit && mockFiles.length === 1;
      const outputDir = single ? dirnameOf(explicit) : String(params.output_dir ?? params.outputDir ?? 'C:\\Users\\demo\\Documents');
      simulateOperation(TOOL_KEYS.unlock, mockFiles, () => ({
        files: mockFiles.map((f) => ({ file: f.name, status: 'ok', details: 'PDF unlocked', outputPath: single ? explicit : `${outputDir}\\${f.name}` })),
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
      const flattened = Array.isArray(params.flatten_pages) ? (params.flatten_pages as number[]) : [];
      simulateOperation(TOOL_KEYS.redact, mockFilesFromParams(params), () => ({
        input_path: String(params.file ?? ''),
        output_path: outputPath,
        redaction_count: termCount * 3,
        pages_affected: Math.min(termCount * 2, 5),
        surface_counts: { page_content: termCount * 3 },
        // Browser-dev mock always "verifies" (no real backend to refuse).
        verification: { verified: true, checks: [], flattenTargetPages: null },
        flattened_pages: flattened,
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

  // -- Workspace (persistent working document) -----------------------------
  async getWorkspaceDir(): Promise<string> {
    if (window.BridgeAPI) return window.BridgeAPI.getWorkspaceDir();
    return 'C:\\Users\\demo\\AppData\\Local\\Temp\\pdfcompress_workspace_mock';
  },
  async deleteFile(path: string): Promise<{ success: boolean; error?: string }> {
    if (window.BridgeAPI) return window.BridgeAPI.deleteFile(path);
    return { success: true };
  },
  async copyFile(srcPath: string, destPath: string): Promise<{ success: boolean; error?: string }> {
    if (window.BridgeAPI) return window.BridgeAPI.copyFile(srcPath, destPath);
    return { success: true };
  },

  // -- Shell helpers -------------------------------------------------------
  openFolderPath(path: string): void {
    window.BridgeAPI?.openFolderPath(path);
  },
  openFilePath(path: string): void {
    window.BridgeAPI?.openFilePath(path);
  },
  /** Reveal a file in the OS file manager with the file itself selected. No-op
   *  in plain-browser dev (no bridge). Preferred over openFilePath for tool
   *  outputs like .epdf that have no default-app association. */
  revealFile(path: string): void {
    window.BridgeAPI?.revealFilePath(path);
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
