/* ==========================================================================
   PDF Toolkit - Bridge API
   Async wrapper around the raw QWebChannel bridge object (App.bridge).
   Handles JSON serialization / deserialization so callers work with
   plain JS objects instead of JSON strings.
   ========================================================================== */

"use strict";

const BridgeAPI = {

    /* ------------------------------------------------------------------
       File Dialogs
       ------------------------------------------------------------------ */

    /**
     * Open a native file-picker filtered to the given pattern.
     * @param {string} [filter='PDF Files (*.pdf)']
     * @returns {Promise<string[]>}  Array of selected file paths (may be empty)
     */
    async openFiles(filter = 'PDF Files (*.pdf)') {
        const json = await App.bridge.openFileDialog(filter);
        return JSON.parse(json);
    },

    /**
     * Open a native folder-picker.
     * @returns {Promise<string|null>}  Selected folder path or null
     */
    async openFolder() {
        const json = await App.bridge.openFolderDialog();
        return JSON.parse(json);
    },

    /**
     * Open a native "Save As" dialog.
     * @param {string} filter       File type filter string
     * @param {string} defaultName  Suggested file name
     * @returns {Promise<string|null>}  Chosen save path or null
     */
    async saveFile(filter, defaultName) {
        const json = await App.bridge.saveFileDialog(filter, defaultName);
        return JSON.parse(json);
    },

    /* ------------------------------------------------------------------
       Data Queries
       ------------------------------------------------------------------ */

    /**
     * Fetch the list of compression presets.
     * @returns {Promise<Object[]>}
     */
    async getPresets() {
        const json = await App.bridge.getPresets();
        return JSON.parse(json);
    },

    /**
     * Analyze a PDF file (page count, size, etc.).
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async analyzeFile(path) {
        const json = await App.bridge.analyzeFile(path);
        return JSON.parse(json);
    },

    /**
     * Get a JPEG thumbnail of page 1 of a PDF (base64 data URL).
     * @param {string} path
     * @returns {Promise<Object>}  { success, dataUrl, width, height }
     */
    async getThumbnail(path) {
        const json = await App.bridge.getThumbnail(path);
        return JSON.parse(json);
    },

    /**
     * Read PDF metadata (title, author, dates, etc.).
     * @param {string} path
     * @returns {Promise<Object>}
     */
    async getMetadata(path) {
        const json = await App.bridge.getMetadata(path);
        return JSON.parse(json);
    },

    /**
     * Extract the table of contents (bookmarks/outlines) from a PDF.
     * @param {string} path
     * @returns {Promise<Object[]>}  Array of {level, title, page, end_page}
     */
    async getToc(path) {
        const json = await App.bridge.getToc(path);
        return JSON.parse(json);
    },

    /**
     * Check if a file is an .epdf encrypted container and get its metadata.
     * @param {string} path
     * @returns {Promise<Object>}  { isEpdf, cipher?, kdf?, originalFilename?, created? }
     */
    async checkEpdf(path) {
        const json = await App.bridge.checkEpdf(path);
        return JSON.parse(json);
    },

    /* ------------------------------------------------------------------
       Operations  (fire-and-forget -- results arrive via EventBus "done")
       ------------------------------------------------------------------ */

    startCompress(params)      { App.bridge.startCompress(JSON.stringify(params)); },
    startMerge(params)         { App.bridge.startMerge(JSON.stringify(params)); },
    startSplit(params)         { App.bridge.startSplit(JSON.stringify(params)); },
    startPageOps(params)       { App.bridge.startPageOps(JSON.stringify(params)); },
    startProtect(params)       { App.bridge.startProtect(JSON.stringify(params)); },
    startUnlock(params)        { App.bridge.startUnlock(JSON.stringify(params)); },
    startCrop(params)          { App.bridge.startCrop(JSON.stringify(params)); },
    startWatermark(params)     { App.bridge.startWatermark(JSON.stringify(params)); },
    startPageNumbers(params)   { App.bridge.startPageNumbers(JSON.stringify(params)); },
    startExtractImages(params) { App.bridge.startExtractImages(JSON.stringify(params)); },
    startExtractText(params)   { App.bridge.startExtractText(JSON.stringify(params)); },
    startImagesToPdf(params)   { App.bridge.startImagesToPdf(JSON.stringify(params)); },
    startPdfToImages(params)   { App.bridge.startPdfToImages(JSON.stringify(params)); },
    startPdfToWord(params)     { App.bridge.startPdfToWord(JSON.stringify(params)); },
    startFlatten(params)       { App.bridge.startFlatten(JSON.stringify(params)); },
    startRepair(params)        { App.bridge.startRepair(JSON.stringify(params)); },
    startRedact(params)        { App.bridge.startRedact(JSON.stringify(params)); },
    startWriteMetadata(params) { App.bridge.startWriteMetadata(JSON.stringify(params)); },
    startCompare(params)       { App.bridge.startCompare(JSON.stringify(params)); },
    startNup(params)           { App.bridge.startNup(JSON.stringify(params)); },

    /**
     * Cancel a running operation.
     * @param {string} toolKey  The key of the tool whose operation to cancel
     */
    cancel(toolKey) {
        App.bridge.cancelOperation(toolKey);
    },

    /* ------------------------------------------------------------------
       Shell helpers
       ------------------------------------------------------------------ */

    /**
     * Ask the OS to open a folder in the file manager.
     * @param {string} path
     */
    openFolderPath(path) {
        App.bridge.openFolder(path);
    },

    /**
     * Ask the OS to open a file with its default application.
     * @param {string} path
     */
    openFilePath(path) {
        App.bridge.openFile(path);
    },

    /* ------------------------------------------------------------------
       Settings persistence
       ------------------------------------------------------------------ */

    /**
     * Save a key/value setting.
     * @param {string} key
     * @param {string} value
     */
    saveSetting(key, value) {
        App.bridge.saveSetting(key, value);
    },

    /**
     * Load a previously saved setting.
     * @param {string} key
     * @returns {Promise<string>}
     */
    async loadSetting(key) {
        return await App.bridge.loadSetting(key);
    },

    /* ------------------------------------------------------------------
       Utility helpers  (pure JS -- no bridge call)
       ------------------------------------------------------------------ */

    /**
     * Human-readable file-size string.
     * @param {number} bytes
     * @returns {string}
     */
    formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
    },

    /**
     * Format a 0-1 fraction as a percentage string.
     * @param {number} value
     * @returns {string}
     */
    formatPct(value) {
        return (value * 100).toFixed(1) + '%';
    },

    /**
     * Extract the filename from a full path.
     * @param {string} path
     * @returns {string}
     */
    basename(path) {
        return path.split(/[/\\]/).pop();
    },

    /**
     * Extract the directory portion of a path.
     * @param {string} path
     * @returns {string}
     */
    dirname(path) {
        const parts = path.split(/[/\\]/);
        parts.pop();
        return parts.join('\\');
    },
};
