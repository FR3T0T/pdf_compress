/* ==========================================================================
   PDF Toolkit - Split Page (Premium)

   Features: single-file split with multiple modes (all pages, page ranges,
   every N pages, by chapters/bookmarks), file analysis with page count
   display, output folder + naming template, settings persistence, keyboard
   shortcuts, progress with cancellation, results summary.
   ========================================================================== */

"use strict";

function SplitPage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _progress = null;
    let _results = null;

    // DOM refs — file info
    let _fileInfoCard = null;
    let _fileNameEl = null;
    let _filePathEl = null;
    let _fileSizeEl = null;
    let _filePagesEl = null;

    // DOM refs — options
    let _modeSelect = null;
    let _rangeRow = null;
    let _rangeInput = null;
    let _everyNRow = null;
    let _everyNInput = null;

    // DOM refs — chapters
    let _chaptersRow = null;
    let _chaptersList = null;
    let _noChaptersMsg = null;
    let _selectAllBtn = null;
    let _deselectAllBtn = null;

    // Chapter state
    let _tocEntries = [];       // raw TOC from backend
    let _tocFetched = false;    // whether we've already fetched TOC for current file

    // DOM refs — output
    let _outputDirInput = null;
    let _nameTemplateInput = null;

    // DOM refs — action
    let _splitBtn = null;
    let _infoLabel = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;
    let _onError = null;
    let _keyHandler = null;

    // ══════════════════════════════════════════════════════════════
    //  Build UI
    // ══════════════════════════════════════════════════════════════

    function _buildUI(container) {
        _el = container;

        // ── Page header ──
        var header = createPageHeader({
            title: 'Split PDF',
            subtitle: 'Split a PDF into individual pages, page ranges, or chapters',
        });
        _el.appendChild(header.el);

        // ── Drop zone (single file) ──
        _dropZone = createDropZone({
            title: 'Drop a PDF file here',
            subtitle: 'or click to browse',
            multiple: false,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _tocFetched = false;
            _tocEntries = [];
            _updateUI();
            if (files.length > 0) _analyzeFile(files[0]);
        });
        _el.appendChild(_dropZone.el);

        // ── File info card ──
        _fileInfoCard = document.createElement('div');
        _fileInfoCard.className = 'card';
        _fileInfoCard.style.cssText = 'margin-top:var(--space-4); display:none;';

        var infoGrid = document.createElement('div');
        infoGrid.style.cssText = 'display:grid; grid-template-columns:1fr auto auto; gap:var(--space-4); align-items:center;';

        // File name + path
        var nameBlock = document.createElement('div');
        _fileNameEl = document.createElement('div');
        _fileNameEl.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); color:var(--color-text);';
        nameBlock.appendChild(_fileNameEl);

        _filePathEl = document.createElement('div');
        _filePathEl.style.cssText = 'font-size:var(--font-size-xs); color:var(--color-text-3); margin-top:2px; word-break:break-all;';
        nameBlock.appendChild(_filePathEl);
        infoGrid.appendChild(nameBlock);

        // Size
        var sizeBlock = document.createElement('div');
        sizeBlock.style.textAlign = 'center';
        var sizeLabel = document.createElement('div');
        sizeLabel.style.cssText = 'font-size:var(--font-size-xs); color:var(--color-text-3); text-transform:uppercase; letter-spacing:0.05em;';
        sizeLabel.textContent = 'Size';
        sizeBlock.appendChild(sizeLabel);
        _fileSizeEl = document.createElement('div');
        _fileSizeEl.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); color:var(--color-text); font-variant-numeric:tabular-nums;';
        _fileSizeEl.textContent = '--';
        sizeBlock.appendChild(_fileSizeEl);
        infoGrid.appendChild(sizeBlock);

        // Pages
        var pagesBlock = document.createElement('div');
        pagesBlock.style.textAlign = 'center';
        var pagesLabel = document.createElement('div');
        pagesLabel.style.cssText = 'font-size:var(--font-size-xs); color:var(--color-text-3); text-transform:uppercase; letter-spacing:0.05em;';
        pagesLabel.textContent = 'Pages';
        pagesBlock.appendChild(pagesLabel);
        _filePagesEl = document.createElement('div');
        _filePagesEl.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); color:var(--color-accent); font-variant-numeric:tabular-nums;';
        _filePagesEl.textContent = '--';
        pagesBlock.appendChild(_filePagesEl);
        infoGrid.appendChild(pagesBlock);

        _fileInfoCard.appendChild(infoGrid);
        _el.appendChild(_fileInfoCard);

        // ── Split Options Card ──
        var optCard = document.createElement('div');
        optCard.className = 'card';
        optCard.style.marginTop = 'var(--space-4)';

        var optTitle = document.createElement('div');
        optTitle.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); margin-bottom:var(--space-4); color:var(--color-text);';
        optTitle.textContent = 'Split Options';
        optCard.appendChild(optTitle);

        // Split mode
        var modeGroup = _createFormGroup('Split mode');
        _modeSelect = document.createElement('select');
        _modeSelect.className = 'form-input';
        _modeSelect.style.width = '100%';

        var modes = [
            { value: 'all',      text: 'All pages (one file per page)' },
            { value: 'ranges',   text: 'Custom page ranges' },
            { value: 'every_n',  text: 'Every N pages' },
            { value: 'chapters', text: 'By chapters (from bookmarks)' },
        ];
        for (var mi = 0; mi < modes.length; mi++) {
            var opt = document.createElement('option');
            opt.value = modes[mi].value;
            opt.textContent = modes[mi].text;
            _modeSelect.appendChild(opt);
        }
        _modeSelect.addEventListener('change', _onModeChange);
        modeGroup.appendChild(_modeSelect);
        optCard.appendChild(modeGroup);

        // Range input (shown when mode=ranges)
        _rangeRow = document.createElement('div');
        _rangeRow.style.cssText = 'margin-top:var(--space-3); display:none;';

        var rangeGroup = _createFormGroup('Page ranges');
        _rangeInput = document.createElement('input');
        _rangeInput.type = 'text';
        _rangeInput.className = 'form-input';
        _rangeInput.style.width = '100%';
        _rangeInput.placeholder = 'e.g. 1-3, 5, 7-10';
        rangeGroup.appendChild(_rangeInput);

        var rangeHelp = document.createElement('div');
        rangeHelp.className = 'form-help';
        rangeHelp.textContent = 'Separate ranges with commas. Each range becomes a separate file.';
        rangeGroup.appendChild(rangeHelp);

        _rangeRow.appendChild(rangeGroup);
        optCard.appendChild(_rangeRow);

        // Every N input (shown when mode=every_n)
        _everyNRow = document.createElement('div');
        _everyNRow.style.cssText = 'margin-top:var(--space-3); display:none;';

        var everyNGroup = _createFormGroup('Pages per file');
        _everyNInput = document.createElement('input');
        _everyNInput.type = 'number';
        _everyNInput.className = 'form-input';
        _everyNInput.style.width = '100%';
        _everyNInput.min = '1';
        _everyNInput.value = '2';
        _everyNInput.placeholder = 'Number of pages per output file';
        everyNGroup.appendChild(_everyNInput);

        _everyNRow.appendChild(everyNGroup);
        optCard.appendChild(_everyNRow);

        // Chapters list (shown when mode=chapters)
        _chaptersRow = document.createElement('div');
        _chaptersRow.style.cssText = 'margin-top:var(--space-3); display:none;';

        // Select all / Deselect all buttons
        var chBtnRow = document.createElement('div');
        chBtnRow.style.cssText = 'display:flex; gap:var(--space-2); margin-bottom:var(--space-2);';

        _selectAllBtn = document.createElement('button');
        _selectAllBtn.className = 'btn btn-secondary btn-sm';
        _selectAllBtn.textContent = 'Select all';
        _selectAllBtn.addEventListener('click', function () { _toggleAllChapters(true); });
        chBtnRow.appendChild(_selectAllBtn);

        _deselectAllBtn = document.createElement('button');
        _deselectAllBtn.className = 'btn btn-secondary btn-sm';
        _deselectAllBtn.textContent = 'Deselect all';
        _deselectAllBtn.addEventListener('click', function () { _toggleAllChapters(false); });
        chBtnRow.appendChild(_deselectAllBtn);

        _chaptersRow.appendChild(chBtnRow);

        _chaptersList = document.createElement('div');
        _chaptersList.style.cssText = 'max-height:320px; overflow-y:auto; border:1px solid var(--color-border); border-radius:var(--radius-md); padding:var(--space-2);';
        _chaptersRow.appendChild(_chaptersList);

        _noChaptersMsg = document.createElement('div');
        _noChaptersMsg.style.cssText = 'padding:var(--space-4); text-align:center; color:var(--color-text-3); font-size:var(--font-size-sm);';
        _noChaptersMsg.textContent = 'No bookmarks found in this PDF. Use "Custom page ranges" mode instead.';
        _noChaptersMsg.style.display = 'none';
        _chaptersRow.appendChild(_noChaptersMsg);

        optCard.appendChild(_chaptersRow);

        _el.appendChild(optCard);

        // ── Output Card ──
        var outputCard = document.createElement('div');
        outputCard.className = 'card';
        outputCard.style.marginTop = 'var(--space-4)';

        var outputTitle = document.createElement('div');
        outputTitle.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); margin-bottom:var(--space-4); color:var(--color-text);';
        outputTitle.textContent = 'Output';
        outputCard.appendChild(outputTitle);

        // Output folder
        var outDirGroup = _createFormGroup('Output folder');
        var outDirRow = document.createElement('div');
        outDirRow.style.cssText = 'display:flex; gap:var(--space-2); align-items:center;';

        _outputDirInput = document.createElement('input');
        _outputDirInput.type = 'text';
        _outputDirInput.className = 'form-input';
        _outputDirInput.placeholder = 'Same as source file';
        _outputDirInput.readOnly = true;
        _outputDirInput.style.flex = '1';
        outDirRow.appendChild(_outputDirInput);

        var browseDirBtn = document.createElement('button');
        browseDirBtn.className = 'btn btn-secondary';
        browseDirBtn.textContent = 'Browse';
        browseDirBtn.addEventListener('click', async function () {
            try {
                var dir = await BridgeAPI.openFolder();
                if (dir) {
                    _outputDirInput.value = dir;
                    _saveSettings();
                    _updateUI();
                }
            } catch (err) {
                console.error('[SplitPage] openFolder error:', err);
            }
        });
        outDirRow.appendChild(browseDirBtn);
        outDirGroup.appendChild(outDirRow);
        outputCard.appendChild(outDirGroup);

        // Name template
        var tmplGroup = _createFormGroup('Name template');
        _nameTemplateInput = document.createElement('input');
        _nameTemplateInput.type = 'text';
        _nameTemplateInput.className = 'form-input';
        _nameTemplateInput.style.width = '100%';
        _nameTemplateInput.value = '{filename}_page_{n}';
        _nameTemplateInput.placeholder = '{filename}_page_{n}';
        _nameTemplateInput.addEventListener('change', _saveSettings);
        tmplGroup.appendChild(_nameTemplateInput);

        var tmplHelp = document.createElement('div');
        tmplHelp.className = 'form-help';
        tmplHelp.textContent = 'Use {filename} for original name, {n} for page number, {title} for chapter name.';
        tmplGroup.appendChild(tmplHelp);

        outputCard.appendChild(tmplGroup);
        _el.appendChild(outputCard);

        // ── Action row ──
        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display:flex; justify-content:flex-end; align-items:center; gap:var(--space-3); margin-top:var(--space-4);';

        _infoLabel = document.createElement('span');
        _infoLabel.style.cssText = 'font-size:var(--font-size-xs); color:var(--color-text-3); margin-right:auto;';
        _infoLabel.textContent = 'Ctrl+O to browse \u2022 Ctrl+Enter to split';
        actionRow.appendChild(_infoLabel);

        _splitBtn = document.createElement('button');
        _splitBtn.className = 'btn btn-primary btn-lg';
        _splitBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-right:6px;vertical-align:-2px"><path d="M10 3v14"/><path d="M3 10h4"/><path d="M13 10h4"/></svg><span>Split</span>';
        _splitBtn.disabled = true;
        _splitBtn.addEventListener('click', _startSplit);
        actionRow.appendChild(_splitBtn);
        _el.appendChild(actionRow);

        // ── Progress ──
        _progress = createProgressPanel();
        _progress.el.style.marginTop = 'var(--space-4)';
        _progress.onCancel(function () {
            BridgeAPI.cancel('split');
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.info('Split cancelled.');
        });
        _el.appendChild(_progress.el);

        // ── Results ──
        _results = createResultsPanel();
        _results.el.style.marginTop = 'var(--space-4)';
        _el.appendChild(_results.el);

        // Load saved settings
        _loadSettings();
    }

    // ══════════════════════════════════════════════════════════════
    //  Helpers
    // ══════════════════════════════════════════════════════════════

    function _createFormGroup(labelText) {
        var group = document.createElement('div');
        group.style.marginBottom = 'var(--space-3)';
        var label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = labelText;
        group.appendChild(label);
        return group;
    }

    function _onModeChange() {
        var mode = _modeSelect.value;
        _rangeRow.style.display = mode === 'ranges' ? '' : 'none';
        _everyNRow.style.display = mode === 'every_n' ? '' : 'none';
        _chaptersRow.style.display = mode === 'chapters' ? '' : 'none';

        // Auto-fetch TOC when switching to chapters mode
        if (mode === 'chapters' && _files.length > 0 && !_tocFetched) {
            _fetchToc(_files[0].path);
        }

        _saveSettings();
    }

    function _updateUI() {
        var hasFile = _files.length > 0;
        _splitBtn.disabled = _busy || !hasFile;

        if (_fileInfoCard) {
            if (hasFile) {
                _fileInfoCard.style.display = '';
                _fileNameEl.textContent = _files[0].name || BridgeAPI.basename(_files[0].path);
                _filePathEl.textContent = _files[0].path;
            } else {
                _fileInfoCard.style.display = 'none';
            }
        }
    }

    async function _analyzeFile(file) {
        try {
            var info = await BridgeAPI.analyzeFile(file.path);
            if (info) {
                if (info.pages || info.page_count) {
                    _filePagesEl.textContent = info.pages || info.page_count;
                }
                if (info.size || info.file_size) {
                    _fileSizeEl.textContent = BridgeAPI.formatSize(info.size || info.file_size);
                }
            }
        } catch (e) {
            console.warn('[SplitPage] analyzeFile failed:', e);
        }

        // Pre-fetch TOC for this file (useful if user switches to chapters mode)
        _fetchToc(file.path);
    }

    // ══════════════════════════════════════════════════════════════
    //  Chapter / TOC helpers
    // ══════════════════════════════════════════════════════════════

    async function _fetchToc(filePath) {
        try {
            _tocEntries = await BridgeAPI.getToc(filePath);
            _tocFetched = true;
            _renderChaptersList();
        } catch (e) {
            console.warn('[SplitPage] getToc failed:', e);
            _tocEntries = [];
            _tocFetched = true;
            _renderChaptersList();
        }
    }

    function _renderChaptersList() {
        _chaptersList.innerHTML = '';

        if (!_tocEntries || _tocEntries.length === 0) {
            _chaptersList.style.display = 'none';
            _selectAllBtn.style.display = 'none';
            _deselectAllBtn.style.display = 'none';
            _noChaptersMsg.style.display = '';
            return;
        }

        _chaptersList.style.display = '';
        _selectAllBtn.style.display = '';
        _deselectAllBtn.style.display = '';
        _noChaptersMsg.style.display = 'none';

        for (var i = 0; i < _tocEntries.length; i++) {
            var entry = _tocEntries[i];
            var row = document.createElement('label');
            var indent = (entry.level - 1) * 20;
            row.style.cssText = 'display:flex; align-items:center; gap:var(--space-2); padding:6px 8px; cursor:pointer; border-radius:var(--radius-sm); transition:background 0.15s;'
                + 'padding-left:' + (8 + indent) + 'px;';
            row.addEventListener('mouseenter', function () { this.style.background = 'var(--color-bg-hover, rgba(128,128,128,0.08))'; });
            row.addEventListener('mouseleave', function () { this.style.background = ''; });

            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;
            cb.dataset.tocIndex = i;
            cb.style.cssText = 'flex-shrink:0; width:16px; height:16px; cursor:pointer;';
            row.appendChild(cb);

            var titleSpan = document.createElement('span');
            titleSpan.style.cssText = 'flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'
                + (entry.level === 1 ? ' font-weight:var(--font-weight-semibold); color:var(--color-text);' : ' color:var(--color-text-2); font-size:var(--font-size-sm);');
            titleSpan.textContent = entry.title;
            titleSpan.title = entry.title;
            row.appendChild(titleSpan);

            var pagesBadge = document.createElement('span');
            var pageCount = entry.end_page - entry.page + 1;
            pagesBadge.style.cssText = 'flex-shrink:0; font-size:var(--font-size-xs); color:var(--color-text-3); font-variant-numeric:tabular-nums; white-space:nowrap;';
            pagesBadge.textContent = 'pp. ' + entry.page + '\u2013' + entry.end_page + ' (' + pageCount + ')';
            row.appendChild(pagesBadge);

            _chaptersList.appendChild(row);
        }
    }

    function _toggleAllChapters(checked) {
        var checkboxes = _chaptersList.querySelectorAll('input[type="checkbox"]');
        for (var i = 0; i < checkboxes.length; i++) {
            checkboxes[i].checked = checked;
        }
    }

    function _getSelectedChapters() {
        var selected = [];
        var checkboxes = _chaptersList.querySelectorAll('input[type="checkbox"]');
        for (var i = 0; i < checkboxes.length; i++) {
            if (checkboxes[i].checked) {
                var idx = parseInt(checkboxes[i].dataset.tocIndex, 10);
                var entry = _tocEntries[idx];
                selected.push({
                    title: entry.title,
                    start_page: entry.page,
                    end_page: entry.end_page,
                });
            }
        }
        return selected;
    }

    // ══════════════════════════════════════════════════════════════
    //  Settings Persistence
    // ══════════════════════════════════════════════════════════════

    async function _loadSettings() {
        try {
            var outputDir = await BridgeAPI.loadSetting('split/outputDir');
            if (outputDir && _outputDirInput) _outputDirInput.value = outputDir;

            var nameTemplate = await BridgeAPI.loadSetting('split/nameTemplate');
            if (nameTemplate && _nameTemplateInput) _nameTemplateInput.value = nameTemplate;

            var mode = await BridgeAPI.loadSetting('split/mode');
            if (mode && _modeSelect) {
                _modeSelect.value = mode;
                _onModeChange();
            }

            var everyN = await BridgeAPI.loadSetting('split/everyN');
            if (everyN && _everyNInput) _everyNInput.value = everyN;
        } catch (e) {
            console.warn('[SplitPage] loadSettings:', e);
        }
    }

    function _saveSettings() {
        try {
            BridgeAPI.saveSetting('split/outputDir', _outputDirInput.value);
            BridgeAPI.saveSetting('split/nameTemplate', _nameTemplateInput.value);
            BridgeAPI.saveSetting('split/mode', _modeSelect.value);
            BridgeAPI.saveSetting('split/everyN', _everyNInput.value);
        } catch (e) {
            console.warn('[SplitPage] saveSettings:', e);
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Start Operation
    // ══════════════════════════════════════════════════════════════

    function _startSplit() {
        if (_files.length === 0) {
            Toast.warning('Please add a PDF file first.');
            return;
        }

        var outputDir = _outputDirInput.value;
        if (!outputDir) {
            outputDir = BridgeAPI.dirname(_files[0].path);
        }

        var mode = _modeSelect.value;
        var params = {
            file: _files[0].path,
            output_dir: outputDir,
            mode: mode,
            name_template: _nameTemplateInput.value || '{filename}_page_{n}',
        };

        if (mode === 'ranges') {
            var ranges = _rangeInput.value.trim();
            if (!ranges) {
                Toast.warning('Please enter page ranges.');
                return;
            }
            params.ranges = ranges;
        } else if (mode === 'every_n') {
            params.every_n = parseInt(_everyNInput.value, 10) || 1;
        } else if (mode === 'chapters') {
            var chapters = _getSelectedChapters();
            if (chapters.length === 0) {
                Toast.warning('Please select at least one chapter.');
                return;
            }
            params.chapters = chapters;
            // Override name template for chapters if user hasn't customized it
            var tmpl = _nameTemplateInput.value;
            if (!tmpl || tmpl === '{filename}_page_{n}' || tmpl === '{name}_page_{start}') {
                params.name_template = '{filename}_{title}';
            }
        }

        _busy = true;
        _updateUI();
        _results.hide();
        _progress.reset();
        _progress.show();
        _saveSettings();

        BridgeAPI.startSplit(params);
    }

    // ══════════════════════════════════════════════════════════════
    //  Events
    // ══════════════════════════════════════════════════════════════

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.toolKey !== 'split') return;
            _progress.update(
                data.pct || 0,
                data.filename || '',
                (data.current || 0) + 1,
                data.total || 1
            );
        };

        _onDone = function (data) {
            if (data.toolKey !== 'split') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                var res = data.results || {};
                var paths = res.output_paths || [];
                var pageCounts = res.pages_per_output || [];
                var outputDir = '';
                if (paths.length > 0) {
                    outputDir = BridgeAPI.dirname(paths[0]);
                }
                // Map path strings to result objects for the results panel
                var fileList = paths.map(function (p, i) {
                    return {
                        name: BridgeAPI.basename(p),
                        path: p,
                        pages: pageCounts[i] || 0,
                    };
                });
                Toast.success('Split into ' + fileList.length + ' file' + (fileList.length !== 1 ? 's' : '') + '!');
                _results.show({
                    files: fileList,
                    totalTime: res.elapsed || 0,
                    outputDir: outputDir || _outputDirInput.value,
                });
            } else {
                Toast.error(data.message || 'Split failed.');
            }
        };

        _onError = function (data) {
            if (data.toolKey && data.toolKey !== 'split') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.error(data.message || 'An error occurred during split.');
        };

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
        EventBus.on('error', _onError);

        // Keyboard shortcuts
        _keyHandler = function (e) {
            if (_el && _el.offsetParent !== null) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'o') {
                    e.preventDefault();
                    _dropZone.el.click();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    if (!_splitBtn.disabled) _startSplit();
                }
                if (e.key === 'Escape' && _busy) {
                    e.preventDefault();
                    BridgeAPI.cancel('split');
                    _busy = false;
                    _progress.hide();
                    _updateUI();
                    Toast.info('Split cancelled.');
                }
            }
        };
        document.addEventListener('keydown', _keyHandler);
    }

    function _unbindEvents() {
        if (_onProgress) EventBus.off('progress', _onProgress);
        if (_onDone) EventBus.off('done', _onDone);
        if (_onError) EventBus.off('error', _onError);
        if (_keyHandler) document.removeEventListener('keydown', _keyHandler);
    }

    // ══════════════════════════════════════════════════════════════
    //  Lifecycle
    // ══════════════════════════════════════════════════════════════

    return {
        onMount: function (container) {
            _buildUI(container);
            _bindEvents();
        },
        onActivated: function () {
            if (!_onProgress) _bindEvents();
        },
        onDeactivated: function () {
            if (!_busy) _unbindEvents();
        },
        isBusy: function () { return _busy; },
        handleDrop: function (files) {
            if (_dropZone) {
                var paths = files.map(function (f) { return f.path || f; });
                _dropZone.setFiles([paths[0]]);
            }
        },
    };
}

Router.register('split', function () { return SplitPage(); });
