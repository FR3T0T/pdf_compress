import type { RealEventBus } from '../types/global';
import type { ProgressPayload, DonePayload } from '../types/bridge';
import { safeJsonParse } from './safeJsonParse';
import { MOCK_PRESETS, MOCK_SANITIZE_DEFAULTS } from './mockData';

/**
 * Wires window.App.bridge / window.BridgeAPI / window.EventBus to the real
 * Qt bridge when running inside the PySide6 QWebEngineView. This is the
 * React equivalent of web/js/app.js's QWebChannel bootstrap + web/js/
 * bridge.js's method wrappers, ported method-for-method (same raw method
 * names, same argument order, same JSON.stringify/parse wrapping) since
 * ui/bridge.py does not change for this migration.
 *
 * Resolves immediately without touching window.* when qt.webChannelTransport
 * isn't present (vite dev / plain browser) — bridgeApi's `window.BridgeAPI`
 * checks then fall through to its existing mock implementations, unchanged.
 */

interface RawSignal {
  connect(cb: (payload: string) => void): void;
}

interface RawBridge {
  openFileDialog(filter: string): Promise<string>;
  openFolderDialog(): Promise<string>;
  saveFileDialog(filter: string, defaultName: string): Promise<string>;
  getPresets(scope: string): Promise<string>;
  analyzeFile(path: string): Promise<string>;
  getThumbnail(path: string): Promise<string>;
  getPageImages(path: string): Promise<string>;
  getMetadata(path: string): Promise<string>;
  getToc(path: string): Promise<string>;
  analyzeDocument(path: string): Promise<string>;
  getSanitizeDefaults(scope: string): Promise<string>;
  sanitizeDocument(path: string, outputPath: string, optionsJson: string): Promise<string>;
  getTranslationStatus(): Promise<string>;
  startGetTranslationStatus(paramsJson: string): void;
  translateText(text: string, source: string, target: string, protectTermsJson: string): Promise<string>;
  translateImage(path: string, source: string, target: string, protectTermsJson: string): Promise<string>;
  startTranslatePdf(paramsJson: string): void;
  startTranslateText(paramsJson: string): void;
  startTranslateImage(paramsJson: string): void;
  checkEpdf(path: string): Promise<string>;
  startCompress(paramsJson: string): void;
  startMerge(paramsJson: string): void;
  startSplit(paramsJson: string): void;
  startPageOps(paramsJson: string): void;
  startProtect(paramsJson: string): void;
  startUnlock(paramsJson: string): void;
  startCrop(paramsJson: string): void;
  startWatermark(paramsJson: string): void;
  startPageNumbers(paramsJson: string): void;
  startExtractImages(paramsJson: string): void;
  startExtractText(paramsJson: string): void;
  startImagesToPdf(paramsJson: string): void;
  startPdfToImages(paramsJson: string): void;
  startPdfToWord(paramsJson: string): void;
  startFlatten(paramsJson: string): void;
  startRepair(paramsJson: string): void;
  startRedact(paramsJson: string): void;
  startWriteMetadata(paramsJson: string): void;
  startCompare(paramsJson: string): void;
  startNup(paramsJson: string): void;
  cancelOperation(toolKey: string): void;
  openFolder(path: string): void;
  openFile(path: string): void;
  saveSetting(key: string, value: string): void;
  loadSetting(key: string): Promise<string>;
  getToolRegistry(): Promise<string>;
  requestThemeToggle(): void;
  progressUpdate: RawSignal;
  operationDone: RawSignal;
  filesDropped: RawSignal;
  themeChanged: RawSignal;
}

function createEventBus(): RealEventBus {
  const listeners: Record<string, Array<(data: unknown) => void>> = {};
  return {
    on(event, cb) {
      (listeners[event] ??= []).push(cb);
    },
    off(event, cb) {
      const list = listeners[event];
      if (!list) return;
      const idx = list.indexOf(cb);
      if (idx !== -1) list.splice(idx, 1);
    },
    emit(event, data) {
      (listeners[event] ?? []).forEach((cb) => cb(data));
    },
  };
}

/** Port of web/js/app.js's applyTheme() — same CSS var injection + "theme"
 *  emit, so the contract holds even though theme.css currently uses a
 *  different token set (see shell/Sidebar.tsx comment). */
function applyTheme(bus: RealEventBus, vars: Record<string, string>): void {
  const root = document.documentElement;
  const themeName = vars['--theme-name'] || 'light';
  for (const key of Object.keys(vars)) {
    if (key === '--theme-name') continue;
    root.style.setProperty(key, vars[key]);
  }
  root.setAttribute('data-theme', themeName);
  bus.emit('theme', themeName);
}

export function connectQWebChannel(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window.qt === 'undefined' || !window.qt.webChannelTransport || typeof window.QWebChannel === 'undefined') {
      resolve();
      return;
    }

    const bus = createEventBus();

    new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
      const raw = channel.objects.bridge as RawBridge | undefined;
      if (!raw) {
        console.error('[qwebchannel-connect] "bridge" object not found on the QWebChannel.');
        resolve();
        return;
      }

      window.EventBus = bus;
      window.App = {
        bridge: {
          getToolRegistry: () => raw.getToolRegistry(),
          requestThemeToggle: () => raw.requestThemeToggle(),
        },
      };

      window.BridgeAPI = {
        async openFiles(filter = 'PDF Files (*.pdf)') {
          return safeJsonParse(await raw.openFileDialog(filter), []);
        },
        async openFolder() {
          return safeJsonParse(await raw.openFolderDialog(), null);
        },
        async saveFile(filter, defaultName) {
          return safeJsonParse(await raw.saveFileDialog(filter, defaultName), null);
        },
        async getPresets() {
          // Slot(str, result=str) on the Python side — it has a default
          // value for its Python parameter, but the *registered Qt slot*
          // still requires exactly one argument. Calling with 0 args (as
          // this used to) fails QWebChannel's overload lookup with "No
          // candidates found ... with 0 arguments" and resolves empty,
          // which then blew up JSON.parse with "Unexpected end of JSON
          // input". Same convention as getSanitizeDefaults('') below.
          return safeJsonParse(await raw.getPresets(''), MOCK_PRESETS);
        },
        async analyzeFile(path) {
          return safeJsonParse(await raw.analyzeFile(path), { success: false });
        },
        async getThumbnail(path) {
          return safeJsonParse(await raw.getThumbnail(path), { success: false });
        },
        async getPageImages(path) {
          return safeJsonParse(await raw.getPageImages(path), { success: false });
        },
        async getMetadata(path) {
          return safeJsonParse(await raw.getMetadata(path), {});
        },
        async getToc(path) {
          return safeJsonParse(await raw.getToc(path), []);
        },
        async analyzeDocument(path) {
          return safeJsonParse(await raw.analyzeDocument(path), { success: false });
        },
        async getSanitizeDefaults() {
          return safeJsonParse(await raw.getSanitizeDefaults(''), MOCK_SANITIZE_DEFAULTS);
        },
        async sanitizeDocument(path, outputPath, options) {
          return safeJsonParse(
            await raw.sanitizeDocument(path, outputPath, JSON.stringify(options || {})),
            { success: false }
          );
        },
        async getTranslationStatus() {
          return safeJsonParse(await raw.getTranslationStatus(), {});
        },
        startGetTranslationStatus(params) {
          raw.startGetTranslationStatus(JSON.stringify(params));
        },
        async translateText(text, source, target, protectTerms) {
          return safeJsonParse(
            await raw.translateText(text, source || 'auto', target, JSON.stringify(protectTerms ?? [])),
            { success: false }
          );
        },
        async translateImage(path, source, target, protectTerms) {
          return safeJsonParse(
            await raw.translateImage(path, source || 'auto', target, JSON.stringify(protectTerms ?? [])),
            { success: false }
          );
        },
        startTranslatePdf(params) {
          raw.startTranslatePdf(JSON.stringify(params));
        },
        startTranslateText(params) {
          raw.startTranslateText(JSON.stringify(params));
        },
        startTranslateImage(params) {
          raw.startTranslateImage(JSON.stringify(params));
        },
        async checkEpdf(path) {
          return safeJsonParse(await raw.checkEpdf(path), { isEpdf: false });
        },
        startCompress(params) {
          raw.startCompress(JSON.stringify(params));
        },
        startMerge(params) {
          raw.startMerge(JSON.stringify(params));
        },
        startSplit(params) {
          raw.startSplit(JSON.stringify(params));
        },
        startPageOps(params) {
          raw.startPageOps(JSON.stringify(params));
        },
        startProtect(params) {
          raw.startProtect(JSON.stringify(params));
        },
        startUnlock(params) {
          raw.startUnlock(JSON.stringify(params));
        },
        startCrop(params) {
          raw.startCrop(JSON.stringify(params));
        },
        startWatermark(params) {
          raw.startWatermark(JSON.stringify(params));
        },
        startPageNumbers(params) {
          raw.startPageNumbers(JSON.stringify(params));
        },
        startExtractImages(params) {
          raw.startExtractImages(JSON.stringify(params));
        },
        startExtractText(params) {
          raw.startExtractText(JSON.stringify(params));
        },
        startImagesToPdf(params) {
          raw.startImagesToPdf(JSON.stringify(params));
        },
        startPdfToImages(params) {
          raw.startPdfToImages(JSON.stringify(params));
        },
        startPdfToWord(params) {
          raw.startPdfToWord(JSON.stringify(params));
        },
        startFlatten(params) {
          raw.startFlatten(JSON.stringify(params));
        },
        startRepair(params) {
          raw.startRepair(JSON.stringify(params));
        },
        startRedact(params) {
          raw.startRedact(JSON.stringify(params));
        },
        startWriteMetadata(params) {
          raw.startWriteMetadata(JSON.stringify(params));
        },
        startCompare(params) {
          raw.startCompare(JSON.stringify(params));
        },
        startNup(params) {
          raw.startNup(JSON.stringify(params));
        },
        cancel(toolKey) {
          raw.cancelOperation(toolKey);
        },
        openFolderPath(path) {
          raw.openFolder(path);
        },
        openFilePath(path) {
          raw.openFile(path);
        },
        saveSetting(key, value) {
          raw.saveSetting(key, value);
        },
        async loadSetting(key) {
          // Python's loadSetting returns json.dumps(value) -- an unset key
          // comes back as the JSON literal `null`, which the vanilla page
          // (and this connector, before this fix) returned to callers
          // as-is: the raw wire text, i.e. the *string* "null". That
          // string is truthy, so it sailed past every `if (v) setX(v)`
          // guard and landed directly in state -- including numeric
          // <input>s, producing 'The specified value "null" cannot be
          // parsed'. Parsing here means callers get the real value (or
          // an actual null) instead of wire noise.
          return safeJsonParse(await raw.loadSetting(key), null);
        },
        formatSize(bytes) {
          if (bytes < 1024) return `${bytes} B`;
          if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
          if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
          return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
        },
        formatPct(value) {
          return `${(value * 100).toFixed(1)}%`;
        },
        basename(path) {
          return path.split(/[/\\]/).pop() ?? path;
        },
        dirname(path) {
          const parts = path.split(/[/\\]/);
          parts.pop();
          return parts.join('\\');
        },
      };

      raw.progressUpdate.connect((jsonStr) => {
        try {
          bus.emit('progress', JSON.parse(jsonStr) as ProgressPayload);
        } catch (e) {
          console.error('[qwebchannel-connect] Bad progressUpdate payload', e);
        }
      });
      raw.operationDone.connect((jsonStr) => {
        try {
          bus.emit('done', JSON.parse(jsonStr) as DonePayload);
        } catch (e) {
          console.error('[qwebchannel-connect] Bad operationDone payload', e);
        }
      });
      raw.filesDropped.connect((jsonStr) => {
        try {
          bus.emit('files-dropped', JSON.parse(jsonStr));
        } catch (e) {
          console.error('[qwebchannel-connect] Bad filesDropped payload', e);
        }
      });
      raw.themeChanged.connect((jsonStr) => {
        try {
          applyTheme(bus, JSON.parse(jsonStr));
        } catch (e) {
          console.error('[qwebchannel-connect] Bad themeChanged payload', e);
        }
      });

      resolve();
    });
  });
}
