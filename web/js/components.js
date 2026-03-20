/* ==========================================================================
   PDF Toolkit - Reusable UI Components
   Factory functions that return DOM elements + a control API.
   All components use CSS classes from the design-system stylesheets.
   DOM is built with document.createElement (no innerHTML for dynamic data)
   to avoid XSS.  Static structural markup may use innerHTML for templates.
   ========================================================================== */

"use strict";

/* ==========================================================================
   Toast  (static singleton -- auto-creates its container)
   ========================================================================== */

const Toast = (() => {
    let _container = null;

    function _ensureContainer() {
        if (_container) return _container;
        _container = document.createElement('div');
        _container.className = 'toast-container';
        document.body.appendChild(_container);
        return _container;
    }

    const ICONS = {
        success: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><path d="M7 10l2 2 4-4"/></svg>',
        error:   '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><line x1="8" y1="8" x2="12" y2="12"/><line x1="12" y1="8" x2="8" y2="12"/></svg>',
        warning: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3L2 17h16L10 3z"/><line x1="10" y1="8" x2="10" y2="12"/><circle cx="10" cy="14.5" r="0.5" fill="currentColor"/></svg>',
        info:    '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><line x1="10" y1="9" x2="10" y2="14"/><circle cx="10" cy="6.5" r="0.5" fill="currentColor"/></svg>',
    };

    /**
     * Show a toast notification.
     * @param {string} message
     * @param {string} [type='info']  One of success | error | warning | info
     * @param {number} [duration=4000]
     */
    function show(message, type = 'info', duration = 4000) {
        const container = _ensureContainer();

        const toast = document.createElement('div');
        toast.className = 'toast toast-' + type;
        toast.style.position = 'relative'; // for progress bar

        // Icon
        const iconEl = document.createElement('span');
        iconEl.className = 'toast-icon';
        iconEl.innerHTML = ICONS[type] || ICONS.info;  // static SVG
        toast.appendChild(iconEl);

        // Body
        const body = document.createElement('div');
        body.className = 'toast-body';
        const msgEl = document.createElement('div');
        msgEl.className = 'toast-message';
        msgEl.textContent = message;
        body.appendChild(msgEl);
        toast.appendChild(body);

        // Close button
        const closeBtn = document.createElement('button');
        closeBtn.className = 'toast-close';
        closeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="3" x2="11" y2="11"/><line x1="11" y1="3" x2="3" y2="11"/></svg>';
        closeBtn.addEventListener('click', () => _dismiss(toast));
        toast.appendChild(closeBtn);

        // Progress bar
        const progress = document.createElement('div');
        progress.className = 'toast-progress';
        progress.style.animationDuration = duration + 'ms';
        toast.appendChild(progress);

        container.appendChild(toast);

        // Auto-dismiss
        const timer = setTimeout(() => _dismiss(toast), duration);
        toast._timer = timer;
    }

    function _dismiss(toast) {
        if (toast._dismissed) return;
        toast._dismissed = true;
        clearTimeout(toast._timer);
        toast.classList.add('toast-exit');
        toast.addEventListener('animationend', () => toast.remove());
    }

    return {
        show,
        success(msg) { show(msg, 'success'); },
        error(msg)   { show(msg, 'error'); },
        warning(msg) { show(msg, 'warning'); },
        info(msg)    { show(msg, 'info'); },
    };
})();


/* ==========================================================================
   Modal
   ========================================================================== */

function createModal({ title = '', closable = true, large = false } = {}) {
    let _confirmCb = null;

    // Overlay
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';

    // Modal box
    const modal = document.createElement('div');
    modal.className = 'modal' + (large ? ' modal-lg' : '');

    // Header
    const header = document.createElement('div');
    header.className = 'modal-header';
    const titleEl = document.createElement('div');
    titleEl.className = 'modal-title';
    titleEl.textContent = title;
    header.appendChild(titleEl);

    if (closable) {
        const closeBtn = document.createElement('button');
        closeBtn.className = 'btn-icon btn-sm';
        closeBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="4" y1="4" x2="12" y2="12"/><line x1="12" y1="4" x2="4" y2="12"/></svg>';
        closeBtn.addEventListener('click', () => hide());
        header.appendChild(closeBtn);
    }
    modal.appendChild(header);

    // Body
    const body = document.createElement('div');
    body.className = 'modal-body';
    modal.appendChild(body);

    // Footer
    const footer = document.createElement('div');
    footer.className = 'modal-footer';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', () => hide());

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-primary';
    confirmBtn.textContent = 'Confirm';
    confirmBtn.addEventListener('click', () => {
        if (_confirmCb) _confirmCb();
        hide();
    });

    footer.appendChild(cancelBtn);
    footer.appendChild(confirmBtn);
    modal.appendChild(footer);

    overlay.appendChild(modal);

    // Backdrop click
    if (closable) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) hide();
        });
    }

    function show() {
        document.body.appendChild(overlay);
    }

    function hide() {
        overlay.classList.add('closing');
        overlay.addEventListener('animationend', () => overlay.remove(), { once: true });
        // Fallback removal if animation doesn't fire
        setTimeout(() => { if (overlay.parentNode) overlay.remove(); }, 300);
    }

    function setContent(html) {
        body.innerHTML = html;
    }

    function setContentEl(el) {
        body.innerHTML = '';
        body.appendChild(el);
    }

    function onConfirm(fn) {
        _confirmCb = fn;
    }

    return {
        el: overlay,
        show,
        hide,
        setContent,
        setContentEl,
        onConfirm,
        body,
        footer,
        titleEl,
    };
}


/* ==========================================================================
   PageHeader
   ========================================================================== */

/**
 * @param {Object}  opts
 * @param {string}  opts.title
 * @param {string}  [opts.subtitle]
 * @param {boolean} [opts.backButton=true]
 * @returns {{ el: HTMLElement, actionsEl: HTMLElement }}
 */
function createPageHeader({ title, subtitle = '', backButton = true } = {}) {
    const el = document.createElement('div');
    el.className = 'page-header';

    if (backButton) {
        const backBtn = document.createElement('button');
        backBtn.className = 'page-back-btn';
        backBtn.title = 'Back to home';
        backBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4L6 9l5 5"/></svg>';
        backBtn.addEventListener('click', () => Router.navigate('home'));
        el.appendChild(backBtn);
    }

    const content = document.createElement('div');
    content.className = 'page-header-content';

    const titleEl = document.createElement('div');
    titleEl.className = 'page-title';
    titleEl.textContent = title;
    content.appendChild(titleEl);

    if (subtitle) {
        const subEl = document.createElement('div');
        subEl.className = 'page-subtitle';
        subEl.textContent = subtitle;
        content.appendChild(subEl);
    }
    el.appendChild(content);

    const actionsEl = document.createElement('div');
    actionsEl.className = 'page-header-actions';
    el.appendChild(actionsEl);

    return { el, actionsEl };
}


/* ==========================================================================
   DropZone
   ========================================================================== */

/**
 * @param {Object}  opts
 * @param {string}  [opts.icon]       Emoji or text for the icon area
 * @param {string}  [opts.title]      Main prompt text
 * @param {string}  [opts.subtitle]   Smaller hint text
 * @param {string}  [opts.accept]     File filter for BridgeAPI.openFiles
 * @param {boolean} [opts.multiple=true]
 * @param {boolean} [opts.compact=false]
 * @returns {{ el, setFiles, getFiles, clear, onFilesChanged }}
 */
function createDropZone({
    icon = '\uD83D\uDCC4',          // page-facing-up emoji
    title = 'Drop PDF files here',
    subtitle = 'or click to browse',
    accept = 'PDF Files (*.pdf)',
    multiple = true,
    compact = false,
} = {}) {
    let _files = [];
    let _changeCallback = null;

    const el = document.createElement('div');
    el.className = 'drop-zone' + (compact ? ' drop-zone-compact' : '');

    // Icon
    const iconEl = document.createElement('div');
    iconEl.className = 'drop-zone-icon';
    iconEl.textContent = icon;
    el.appendChild(iconEl);

    // Title
    const titleEl = document.createElement('div');
    titleEl.className = 'drop-zone-title';
    titleEl.textContent = title;
    el.appendChild(titleEl);

    // Subtitle with browse link
    const subRow = document.createElement('div');
    subRow.className = 'drop-zone-subtitle';
    const subText = document.createTextNode(subtitle + ' ');
    subRow.appendChild(subText);
    const browseSpan = document.createElement('span');
    browseSpan.className = 'drop-zone-browse';
    browseSpan.textContent = 'Browse files';
    subRow.appendChild(browseSpan);
    el.appendChild(subRow);

    // Click to browse
    el.addEventListener('click', async () => {
        try {
            const paths = await BridgeAPI.openFiles(accept);
            if (paths && paths.length > 0) {
                _addPaths(multiple ? paths : [paths[0]]);
            }
        } catch (err) {
            console.error('[DropZone] openFiles error:', err);
        }
    });

    // Drag-and-drop visual states
    el.addEventListener('dragenter', (e) => { e.preventDefault(); el.classList.add('drag-over'); });
    el.addEventListener('dragover',  (e) => { e.preventDefault(); el.classList.add('drag-over'); });
    el.addEventListener('dragleave', ()  => { el.classList.remove('drag-over'); });
    el.addEventListener('drop',      (e) => { e.preventDefault(); el.classList.remove('drag-over'); });

    // Listen for files dropped from the Python side (native drag)
    EventBus.on('files-dropped', (data) => {
        if (data && data.paths && data.paths.length > 0) {
            _addPaths(multiple ? data.paths : [data.paths[0]]);
        }
    });

    function _addPaths(paths) {
        if (!multiple) {
            _files = paths.map(_pathToFileObj);
        } else {
            // Deduplicate
            const existing = new Set(_files.map(f => f.path));
            for (const p of paths) {
                if (!existing.has(p)) {
                    _files.push(_pathToFileObj(p));
                    existing.add(p);
                }
            }
        }
        if (_changeCallback) _changeCallback(_files);
    }

    function _pathToFileObj(p) {
        return { path: p, name: BridgeAPI.basename(p) };
    }

    function setFiles(files) {
        _files = files.map(f => typeof f === 'string' ? _pathToFileObj(f) : f);
        if (_changeCallback) _changeCallback(_files);
    }

    function getFiles() {
        return _files.slice();
    }

    function clear() {
        _files = [];
        if (_changeCallback) _changeCallback(_files);
    }

    /**
     * Register a callback that fires whenever the file list changes.
     * @param {Function} fn  Receives the full file array
     */
    function onFilesChanged(fn) {
        _changeCallback = fn;
    }

    return { el, setFiles, getFiles, clear, onFilesChanged };
}


/* ==========================================================================
   FileList
   ========================================================================== */

/**
 * @param {Object}  opts
 * @param {boolean} [opts.showPages=true]   Show a "Pages" column
 * @param {boolean} [opts.reorderable=false] Show up/down buttons
 * @param {string}  [opts.emptyMessage='No files added yet.']
 * @returns {{ el, addFiles, removeFile, getFiles, clear, setStatus, refresh }}
 */
function createFileList({
    showPages = true,
    reorderable = false,
    emptyMessage = 'No files added yet.',
} = {}) {
    let _files = [];   // { path, name, size?, pages?, status? }
    let _removeCb = null;

    const el = document.createElement('div');
    el.className = 'file-table';

    function _render() {
        el.innerHTML = '';  // safe -- we rebuild with createElement below

        if (_files.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.style.padding = 'var(--space-8) var(--space-4)';
            const emptyIcon = document.createElement('div');
            emptyIcon.className = 'empty-state-icon';
            emptyIcon.textContent = '\uD83D\uDCC2'; // open file folder
            empty.appendChild(emptyIcon);
            const emptyText = document.createElement('div');
            emptyText.className = 'empty-state-text';
            emptyText.textContent = emptyMessage;
            empty.appendChild(emptyText);
            el.appendChild(empty);
            return;
        }

        // Header row
        const header = document.createElement('div');
        header.className = 'file-table-header';
        _addHeaderCol(header, 'File', 'col-name');
        _addHeaderCol(header, 'Size', 'col-size');
        if (showPages) _addHeaderCol(header, 'Pages', 'col-size');
        _addHeaderCol(header, 'Status', 'col-status');
        _addHeaderCol(header, '', 'col-actions');
        if (reorderable) _addHeaderCol(header, '', 'col-actions');
        el.appendChild(header);

        // File rows
        for (let i = 0; i < _files.length; i++) {
            el.appendChild(_createRow(i));
        }
    }

    function _addHeaderCol(parent, text, cls) {
        const col = document.createElement('div');
        col.className = 'file-table-col ' + cls;
        col.textContent = text;
        parent.appendChild(col);
    }

    function _createRow(index) {
        const f = _files[index];
        const row = document.createElement('div');
        row.className = 'file-table-row';

        // Name column
        const nameCol = document.createElement('div');
        nameCol.className = 'file-table-col col-name';
        const fileIcon = document.createElement('div');
        fileIcon.className = 'file-icon';
        fileIcon.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 1H4a1 1 0 00-1 1v12a1 1 0 001 1h8a1 1 0 001-1V5L9 1z"/><polyline points="9,1 9,5 13,5"/></svg>';
        nameCol.appendChild(fileIcon);
        const nameSpan = document.createElement('span');
        nameSpan.className = 'file-name';
        nameSpan.textContent = f.name || BridgeAPI.basename(f.path);
        nameSpan.title = f.path;
        nameCol.appendChild(nameSpan);
        row.appendChild(nameCol);

        // Size column
        const sizeCol = document.createElement('div');
        sizeCol.className = 'file-table-col col-size';
        sizeCol.textContent = f.size != null ? BridgeAPI.formatSize(f.size) : '--';
        row.appendChild(sizeCol);

        // Pages column
        if (showPages) {
            const pagesCol = document.createElement('div');
            pagesCol.className = 'file-table-col col-size';
            pagesCol.textContent = f.pages != null ? f.pages : '--';
            row.appendChild(pagesCol);
        }

        // Status column
        const statusCol = document.createElement('div');
        statusCol.className = 'file-table-col col-status';
        if (f.status) {
            const badge = document.createElement('span');
            const statusMap = {
                pending:    { cls: 'badge-neutral',  text: 'Pending' },
                processing: { cls: 'badge-accent',   text: 'Processing' },
                done:       { cls: 'badge-green',    text: 'Done' },
                error:      { cls: 'badge-red',      text: 'Error' },
            };
            const info = statusMap[f.status] || { cls: 'badge-neutral', text: f.status };
            badge.className = 'badge ' + info.cls;
            badge.textContent = info.text;
            statusCol.appendChild(badge);
        }
        row.appendChild(statusCol);

        // Remove button
        const actCol = document.createElement('div');
        actCol.className = 'file-table-col col-actions';
        const removeBtn = document.createElement('button');
        removeBtn.className = 'file-remove-btn';
        removeBtn.title = 'Remove';
        removeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="3" y1="3" x2="11" y2="11"/><line x1="11" y1="3" x2="3" y2="11"/></svg>';
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeFile(index);
        });
        actCol.appendChild(removeBtn);
        row.appendChild(actCol);

        // Reorder buttons
        if (reorderable) {
            const reorderCol = document.createElement('div');
            reorderCol.className = 'file-table-col col-actions';
            reorderCol.style.display = 'flex';
            reorderCol.style.flexDirection = 'column';
            reorderCol.style.gap = '2px';

            if (index > 0) {
                const upBtn = document.createElement('button');
                upBtn.className = 'file-remove-btn';
                upBtn.title = 'Move up';
                upBtn.style.opacity = '1';
                upBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2v8"/><path d="M2 6l4-4 4 4"/></svg>';
                upBtn.addEventListener('click', (e) => { e.stopPropagation(); _swap(index, index - 1); });
                reorderCol.appendChild(upBtn);
            }

            if (index < _files.length - 1) {
                const downBtn = document.createElement('button');
                downBtn.className = 'file-remove-btn';
                downBtn.title = 'Move down';
                downBtn.style.opacity = '1';
                downBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 10V2"/><path d="M2 6l4 4 4-4"/></svg>';
                downBtn.addEventListener('click', (e) => { e.stopPropagation(); _swap(index, index + 1); });
                reorderCol.appendChild(downBtn);
            }

            row.appendChild(reorderCol);
        }

        return row;
    }

    function _swap(a, b) {
        const tmp = _files[a];
        _files[a] = _files[b];
        _files[b] = tmp;
        _render();
    }

    function addFiles(files) {
        for (const f of files) {
            const obj = typeof f === 'string' ? { path: f, name: BridgeAPI.basename(f) } : f;
            _files.push(obj);
        }
        _render();
    }

    function removeFile(index) {
        if (index >= 0 && index < _files.length) {
            _files.splice(index, 1);
            _render();
            if (_removeCb) _removeCb(index);
        }
    }

    function onRemove(fn) { _removeCb = fn; }

    function getFiles() {
        return _files.slice();
    }

    function clear() {
        _files = [];
        _render();
    }

    function setStatus(index, status) {
        if (index >= 0 && index < _files.length) {
            _files[index].status = status;
            _render();
        }
    }

    function refresh() { _render(); }

    // Initial render
    _render();

    return { el, addFiles, removeFile, getFiles, clear, setStatus, refresh, onRemove };
}


/* ==========================================================================
   ProgressPanel
   ========================================================================== */

/**
 * @returns {{ el, show, hide, update, reset }}
 */
function createProgressPanel() {
    let _startTime = null;
    let _timerInterval = null;

    const el = document.createElement('div');
    el.className = 'card';
    el.style.display = 'none';

    // Label row
    const labelRow = document.createElement('div');
    labelRow.className = 'progress-label';

    const labelText = document.createElement('span');
    labelText.className = 'progress-label-text';
    labelText.textContent = 'Processing...';
    labelRow.appendChild(labelText);

    const labelPct = document.createElement('span');
    labelPct.className = 'progress-label-percent';
    labelPct.textContent = '0%';
    labelRow.appendChild(labelPct);
    el.appendChild(labelRow);

    // Progress bar
    const bar = document.createElement('div');
    bar.className = 'progress progress-lg';
    const fill = document.createElement('div');
    fill.className = 'progress-fill';
    fill.style.width = '0%';
    bar.appendChild(fill);
    el.appendChild(bar);

    // Info row (file label + elapsed)
    const infoRow = document.createElement('div');
    infoRow.style.display = 'flex';
    infoRow.style.justifyContent = 'space-between';
    infoRow.style.alignItems = 'center';
    infoRow.style.marginTop = 'var(--space-3)';

    const fileLabel = document.createElement('span');
    fileLabel.style.fontSize = 'var(--font-size-sm)';
    fileLabel.style.color = 'var(--color-text-2)';
    fileLabel.textContent = '';
    infoRow.appendChild(fileLabel);

    const elapsed = document.createElement('span');
    elapsed.style.fontSize = 'var(--font-size-sm)';
    elapsed.style.color = 'var(--color-text-3)';
    elapsed.style.fontVariantNumeric = 'tabular-nums';
    elapsed.textContent = '00:00';
    infoRow.appendChild(elapsed);
    el.appendChild(infoRow);

    // Cancel button
    const btnRow = document.createElement('div');
    btnRow.style.display = 'flex';
    btnRow.style.justifyContent = 'flex-end';
    btnRow.style.marginTop = 'var(--space-3)';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-secondary btn-sm';
    cancelBtn.textContent = 'Cancel';
    btnRow.appendChild(cancelBtn);
    el.appendChild(btnRow);

    // Public cancel hook
    let _cancelCb = null;
    cancelBtn.addEventListener('click', () => { if (_cancelCb) _cancelCb(); });

    function _startTimer() {
        _startTime = Date.now();
        _timerInterval = setInterval(() => {
            const secs = Math.floor((Date.now() - _startTime) / 1000);
            const m = String(Math.floor(secs / 60)).padStart(2, '0');
            const s = String(secs % 60).padStart(2, '0');
            elapsed.textContent = m + ':' + s;
        }, 500);
    }

    function _stopTimer() {
        if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    }

    function show() {
        el.style.display = '';
        _startTimer();
    }

    function hide() {
        el.style.display = 'none';
        _stopTimer();
    }

    /**
     * @param {number} pct       0-100
     * @param {string} filename  Name of the file currently being processed
     * @param {number} current   1-based index of current file
     * @param {number} total     Total file count
     * @param {string} [eta]     Estimated time remaining string
     */
    function update(pct, filename, current, total, eta) {
        fill.style.width = pct + '%';
        labelPct.textContent = Math.round(pct) + '%';
        if (filename) {
            fileLabel.textContent = filename + (total > 1 ? (' (' + current + '/' + total + ')') : '');
        }
        if (pct >= 100) {
            labelText.textContent = 'Complete!';
        } else if (eta) {
            labelText.textContent = eta;
        } else {
            labelText.textContent = 'Processing...';
        }
    }

    function reset() {
        _stopTimer();
        fill.style.width = '0%';
        labelPct.textContent = '0%';
        labelText.textContent = 'Processing...';
        fileLabel.textContent = '';
        elapsed.textContent = '00:00';
    }

    function onCancel(fn) { _cancelCb = fn; }

    return { el, show, hide, update, reset, onCancel };
}


/* ==========================================================================
   ResultsPanel
   ========================================================================== */

/**
 * @returns {{ el, show, hide }}
 */
function createResultsPanel() {
    const el = document.createElement('div');
    el.className = 'results-section';
    el.style.display = 'none';

    /**
     * Show results.
     * @param {Object} results
     * @param {Object[]} results.files       Per-file results
     * @param {number}   results.totalTime   Elapsed time in seconds
     * @param {number}   [results.totalSaved] Total bytes saved (optional)
     * @param {string}   [results.outputDir]  Output directory path
     */
    function show(results) {
        el.style.display = '';
        el.innerHTML = '';  // will rebuild with safe DOM

        // Summary stats
        const summary = document.createElement('div');
        summary.className = 'results-summary';

        _addStat(summary, 'Files', String(results.files.length));
        _addStat(summary, 'Time', _formatDuration(results.totalTime));
        if (results.totalSaved != null) {
            _addStat(summary, 'Saved', BridgeAPI.formatSize(results.totalSaved));
        }
        el.appendChild(summary);

        // Per-file table
        if (results.files.length > 0) {
            const table = document.createElement('div');
            table.className = 'results-table';
            table.style.marginTop = 'var(--space-4)';

            // Header
            const header = document.createElement('div');
            header.className = 'results-table-header';
            const cols = ['File', 'Original', 'Result', 'Savings', ''];
            for (const c of cols) {
                const hcol = document.createElement('div');
                hcol.textContent = c;
                header.appendChild(hcol);
            }
            table.appendChild(header);

            // Rows
            for (const f of results.files) {
                const row = document.createElement('div');
                row.className = 'results-table-row';

                const nameCell = document.createElement('div');
                nameCell.className = 'results-filename';
                nameCell.textContent = f.name || BridgeAPI.basename(f.path || '');
                nameCell.title = f.path || '';
                row.appendChild(nameCell);

                const origCell = document.createElement('div');
                origCell.className = 'results-size';
                origCell.textContent = f.originalSize != null ? BridgeAPI.formatSize(f.originalSize) : '--';
                row.appendChild(origCell);

                const resultCell = document.createElement('div');
                resultCell.className = 'results-size';
                resultCell.textContent = f.resultSize != null ? BridgeAPI.formatSize(f.resultSize) : '--';
                row.appendChild(resultCell);

                const savingsCell = document.createElement('div');
                savingsCell.className = 'results-reduction';
                if (f.originalSize != null && f.resultSize != null && f.originalSize > 0) {
                    const pct = ((f.originalSize - f.resultSize) / f.originalSize) * 100;
                    savingsCell.textContent = pct.toFixed(1) + '%';
                    if (pct >= 30) savingsCell.classList.add('good');
                    else if (pct >= 10) savingsCell.classList.add('moderate');
                    else savingsCell.classList.add('poor');
                } else {
                    savingsCell.textContent = f.status === 'error' ? 'Error' : '--';
                    if (f.status === 'error') savingsCell.classList.add('poor');
                }
                row.appendChild(savingsCell);

                const actCell = document.createElement('div');
                actCell.className = 'results-actions';
                if (f.outputPath) {
                    const openBtn = document.createElement('button');
                    openBtn.className = 'btn btn-ghost btn-sm';
                    openBtn.textContent = 'Open';
                    openBtn.addEventListener('click', () => BridgeAPI.openFilePath(f.outputPath));
                    actCell.appendChild(openBtn);
                }
                row.appendChild(actCell);

                table.appendChild(row);
            }
            el.appendChild(table);
        }

        // Open folder button
        if (results.outputDir) {
            const btnRow = document.createElement('div');
            btnRow.style.display = 'flex';
            btnRow.style.justifyContent = 'flex-end';
            btnRow.style.marginTop = 'var(--space-4)';
            const folderBtn = document.createElement('button');
            folderBtn.className = 'btn btn-primary';
            folderBtn.textContent = 'Open Output Folder';
            folderBtn.addEventListener('click', () => BridgeAPI.openFolderPath(results.outputDir));
            btnRow.appendChild(folderBtn);
            el.appendChild(btnRow);
        }
    }

    function hide() {
        el.style.display = 'none';
        el.innerHTML = '';
    }

    function _addStat(parent, label, value) {
        const stat = document.createElement('div');
        stat.className = 'results-stat';
        const lbl = document.createElement('div');
        lbl.className = 'results-stat-label';
        lbl.textContent = label;
        stat.appendChild(lbl);
        const val = document.createElement('div');
        val.className = 'results-stat-value';
        val.textContent = value;
        stat.appendChild(val);
        parent.appendChild(stat);
    }

    function _formatDuration(secs) {
        if (secs == null) return '--';
        if (secs < 60) return secs.toFixed(1) + 's';
        const m = Math.floor(secs / 60);
        const s = Math.round(secs % 60);
        return m + 'm ' + s + 's';
    }

    return { el, show, hide };
}


/* ==========================================================================
   PresetCards
   ========================================================================== */

/**
 * @param {Object[]} presets  Array of { key, icon, title, description, badge? }
 * @param {string}   [defaultKey]  Initially selected key
 * @returns {{ el, getSelected, setSelected }}
 */
function createPresetCards(presets, defaultKey = null) {
    let _selected = defaultKey || (presets.length > 0 ? presets[0].key : null);
    let _changeCb = null;

    const el = document.createElement('div');
    el.className = 'preset-grid';

    function _render() {
        el.innerHTML = '';

        for (const p of presets) {
            const card = document.createElement('div');
            card.className = 'preset-card card-interactive' + (p.key === _selected ? ' selected' : '');
            if (p.badge) card.classList.add('has-badge');

            const iconEl = document.createElement('div');
            iconEl.className = 'preset-icon';
            iconEl.textContent = p.icon || '';
            card.appendChild(iconEl);

            const nameEl = document.createElement('div');
            nameEl.className = 'preset-name';
            nameEl.textContent = p.title;
            card.appendChild(nameEl);

            if (p.description) {
                const descEl = document.createElement('div');
                descEl.className = 'preset-desc';
                descEl.textContent = p.description;
                card.appendChild(descEl);
            }

            // Detail tooltip on hover
            if (p.detail) {
                const detailEl = document.createElement('div');
                detailEl.className = 'preset-detail';
                detailEl.textContent = p.detail;
                card.appendChild(detailEl);
            }

            if (p.badge) {
                const badgeEl = document.createElement('span');
                badgeEl.className = 'badge badge-accent preset-badge';
                badgeEl.textContent = p.badge;
                card.appendChild(badgeEl);
            }

            card.addEventListener('click', () => {
                if (_selected === p.key) return;
                _selected = p.key;
                _render();
                if (_changeCb) _changeCb(_selected);
            });

            el.appendChild(card);
        }
    }

    function getSelected() { return _selected; }

    function setSelected(key) {
        _selected = key;
        _render();
    }

    function onChange(fn) { _changeCb = fn; }

    _render();

    return { el, getSelected, setSelected, onChange };
}


/* ==========================================================================
   SettingsPanel  (collapsible)
   ========================================================================== */

/**
 * @param {Object}  opts
 * @param {string}  opts.title
 * @param {boolean} [opts.open=false]
 * @returns {{ el, addField, getValues, setValues, bodyEl }}
 */
function createSettingsPanel({ title = 'Advanced Settings', open = false } = {}) {
    const _fields = {};  // name -> input element

    const el = document.createElement('div');
    el.className = 'settings-panel' + (open ? ' open' : '');

    // Header (clickable toggle)
    const header = document.createElement('div');
    header.className = 'settings-panel-header';

    const titleEl = document.createElement('div');
    titleEl.className = 'settings-panel-title';
    titleEl.textContent = title;
    header.appendChild(titleEl);

    const chevron = document.createElement('span');
    chevron.className = 'settings-panel-chevron';
    chevron.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5l3 3 3-3"/></svg>';
    header.appendChild(chevron);
    el.appendChild(header);

    // Body
    const bodyEl = document.createElement('div');
    bodyEl.className = 'settings-panel-body';
    if (!open) bodyEl.style.display = 'none';
    el.appendChild(bodyEl);

    // Toggle
    header.addEventListener('click', () => {
        const isOpen = el.classList.toggle('open');
        bodyEl.style.display = isOpen ? '' : 'none';
    });

    /**
     * Add a labelled form field.
     * @param {string}      name   Internal key for getValues/setValues
     * @param {string}      label  Display label
     * @param {HTMLElement}  input  The input/select/checkbox element
     * @param {string}      [helpText]
     */
    function addField(name, label, input, helpText) {
        _fields[name] = input;

        const row = document.createElement('div');
        row.className = 'settings-row';

        const lbl = document.createElement('label');
        lbl.className = 'settings-row-label';
        lbl.textContent = label;
        row.appendChild(lbl);

        const right = document.createElement('div');
        right.style.display = 'flex';
        right.style.alignItems = 'center';
        right.style.gap = 'var(--space-2)';
        right.appendChild(input);

        if (helpText) {
            const help = document.createElement('span');
            help.className = 'form-help';
            help.textContent = helpText;
            right.appendChild(help);
        }

        row.appendChild(right);
        bodyEl.appendChild(row);
    }

    /**
     * Read all field values as a plain object.
     * @returns {Object}
     */
    function getValues() {
        const out = {};
        for (const [name, input] of Object.entries(_fields)) {
            if (input.type === 'checkbox') {
                out[name] = input.checked;
            } else {
                out[name] = input.value;
            }
        }
        return out;
    }

    /**
     * Set field values from a plain object.
     * @param {Object} obj
     */
    function setValues(obj) {
        for (const [name, value] of Object.entries(obj)) {
            const input = _fields[name];
            if (!input) continue;
            if (input.type === 'checkbox') {
                input.checked = !!value;
            } else {
                input.value = value;
            }
            // Fire change event so any listeners react
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    return { el, addField, getValues, setValues, bodyEl };
}
