import type { AnalyzeResponse, SanitizeOptions, SanitizeResponse } from './analyze';
import type { PresetsResponse } from './bridge';

/**
 * Real shape of window.BridgeAPI, as defined in web/js/bridge.js. Every
 * method here is called with the SAME name/argument order as the vanilla
 * page — ui/bridge.py does not change for this migration.
 */
export interface RealBridgeAPI {
  // -- File dialogs --------------------------------------------------
  openFiles(filter?: string): Promise<string[]>;
  openFolder(): Promise<string | null>;
  saveFile(filter: string, defaultName: string): Promise<string | null>;

  // -- Data queries ----------------------------------------------------
  getPresets(): Promise<PresetsResponse>;
  analyzeFile(path: string): Promise<Record<string, unknown>>;
  getThumbnail(path: string): Promise<{ success: boolean; dataUrl?: string; width?: number; height?: number }>;
  getMetadata(path: string): Promise<{
    title?: string;
    author?: string;
    subject?: string;
    keywords?: string;
    creator?: string;
    producer?: string;
  }>;
  getToc(path: string): Promise<Array<{ level: number; title: string; page: number; end_page?: number }>>;
  analyzeDocument(path: string): Promise<AnalyzeResponse>;
  getSanitizeDefaults(): Promise<SanitizeOptions>;
  sanitizeDocument(path: string, outputPath: string, options: Partial<SanitizeOptions>): Promise<SanitizeResponse>;
  // New for the Redact page's box-drawing canvas (Part 2) -- no vanilla
  // equivalent, so no arg-count overload concern. Renders every page as a
  // full-size PNG at a fixed DPI (ui/bridge.py's getPageImages); `dpi` lets
  // the caller derive PDF points-per-pixel as 72/dpi, uniform across pages.
  getPageImages(path: string): Promise<{
    success: boolean;
    dpi?: number;
    pages?: Array<{ index: number; dataUrl: string; width: number; height: number }>;
    error?: string;
  }>;

  // -- Translation (offline) -------------------------------------------
  getTranslationStatus(): Promise<Record<string, unknown>>;
  // Async counterpart of getTranslationStatus, for the React UI: the
  // sync call above runs translation_status() on the UI thread, and its
  // first call in the process imports argostranslate (pulls in
  // ctranslate2 and friends) which can take several seconds cold --
  // freezing the whole window on Translate-page mount, before any
  // translation. Results arrive via "done" under toolKey
  // "translationStatus".
  startGetTranslationStatus(params: Record<string, unknown>): void;
  // protectTerms: user-supplied words (names/places) to leave untranslated,
  // on top of pdf_translate.py's built-in heuristics (emails, URLs,
  // "City, ST", phone numbers, acronyms, numbers). ui/bridge.py registers
  // both a 3-arg and 4-arg Qt slot overload for these two methods so
  // web/js/bridge.js's unmodified 3-arg calls keep working.
  translateText(
    text: string,
    source: string,
    target: string,
    protectTerms?: string[]
  ): Promise<Record<string, unknown>>;
  translateImage(
    path: string,
    source: string,
    target: string,
    protectTerms?: string[]
  ): Promise<Record<string, unknown>>;
  startTranslatePdf(params: Record<string, unknown>): void;
  // Async counterparts of translateText/translateImage, off the UI
  // thread (ui/bridge.py) -- fire-and-forget like every startXxx, results
  // arrive via the "done" EventBus event under toolKey "translate", same
  // as startTranslatePdf. translateText/translateImage above stay
  // synchronous for web/js/bridge.js's unmodified callers, but the React
  // UI must use these instead: Argos's first-use model load is slow
  // enough on the synchronous path to freeze the whole window.
  startTranslateText(params: Record<string, unknown>): void;
  startTranslateImage(params: Record<string, unknown>): void;
  // One-time, user-initiated translation setup (network): frozen builds
  // download the pinned ML runtime (translate_runtime.py) and then the
  // chosen Argos language packs; source checkouts skip straight to the
  // packs. Progress/done arrive under toolKey "translateSetup".
  startSetupTranslation(params: Record<string, unknown>): void;
  checkEpdf(path: string): Promise<{
    isEpdf: boolean;
    cipher?: string;
    kdf?: string;
    originalFilename?: string;
    created?: string;
  }>;

  // -- Async operations (fire-and-forget; results via EventBus "done") --
  startCompress(params: Record<string, unknown>): void;
  startMerge(params: Record<string, unknown>): void;
  startSplit(params: Record<string, unknown>): void;
  startPageOps(params: Record<string, unknown>): void;
  startProtect(params: Record<string, unknown>): void;
  startUnlock(params: Record<string, unknown>): void;
  startCrop(params: Record<string, unknown>): void;
  startWatermark(params: Record<string, unknown>): void;
  startPageNumbers(params: Record<string, unknown>): void;
  startExtractImages(params: Record<string, unknown>): void;
  startExtractText(params: Record<string, unknown>): void;
  startImagesToPdf(params: Record<string, unknown>): void;
  startPdfToImages(params: Record<string, unknown>): void;
  startPdfToWord(params: Record<string, unknown>): void;
  startFlatten(params: Record<string, unknown>): void;
  startRepair(params: Record<string, unknown>): void;
  startRedact(params: Record<string, unknown>): void;
  startWriteMetadata(params: Record<string, unknown>): void;
  startCompare(params: Record<string, unknown>): void;
  startNup(params: Record<string, unknown>): void;

  cancel(toolKey: string): void;

  // -- Workspace (persistent working document) ---------------------------
  // Backs WorkspaceContext's running-result model: a per-process temp dir
  // for successive transform outputs, plus best-effort cleanup/export of
  // individual files. See ui/bridge.py's "Workspace" section.
  getWorkspaceDir(): Promise<string>;
  deleteFile(path: string): Promise<{ success: boolean; error?: string }>;
  copyFile(srcPath: string, destPath: string): Promise<{ success: boolean; error?: string }>;

  // -- Shell helpers -----------------------------------------------------
  openFolderPath(path: string): void;
  openFilePath(path: string): void;

  // -- Settings persistence -----------------------------------------------
  saveSetting(key: string, value: string): void;
  // Python's loadSetting (ui/bridge.py) returns json.dumps(value) -- always
  // JSON-encoded, and `null` (not the string "null") for a never-saved key.
  // Callers get the real, already-parsed value here (see
  // qwebchannel-connect.ts), not the raw wire string.
  loadSetting(key: string): Promise<string | null>;

  // -- Pure JS utility helpers (no bridge call) --------------------------
  formatSize(bytes: number): string;
  formatPct(value: number): string;
  basename(path: string): string;
  dirname(path: string): string;
}

/** Lightweight pub/sub defined in web/js/app.js, fed by Python Qt signals. */
export interface RealEventBus {
  on(event: string, cb: (data: unknown) => void): void;
  off(event: string, cb: (data: unknown) => void): void;
  emit(event: string, data: unknown): void;
}

/**
 * The raw QWebChannel proxy object (web/js/app.js: `App.bridge`). A couple
 * of vanilla calls go straight to this instead of through BridgeAPI —
 * `getToolRegistry` and `requestThemeToggle` — so we mirror that here
 * rather than inventing new BridgeAPI methods that don't exist.
 */
export interface RealAppBridge {
  getToolRegistry(): Promise<string>;
  requestThemeToggle(): void;
}

declare global {
  interface Window {
    BridgeAPI?: RealBridgeAPI;
    EventBus?: RealEventBus;
    App?: { bridge?: RealAppBridge };
    /** Injected by QWebEngineView when a QWebChannel is attached (web/js/app.js). */
    qt?: { webChannelTransport: unknown };
    /** Defined by qrc:///qtwebchannel/qwebchannel.js, a built-in Qt resource. */
    QWebChannel?: new (
      transport: unknown,
      callback: (channel: { objects: Record<string, unknown> }) => void
    ) => void;
  }
}
