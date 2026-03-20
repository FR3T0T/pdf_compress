/* ==========================================================================
   PDF Toolkit - Merge Page (Premium)

   Features: drag-and-drop multi-file, reorderable file list with page counts,
   output folder + naming template, settings persistence, keyboard shortcuts,
   progress with cancellation, results summary with elapsed time.
   ========================================================================== */

"use strict";

function MergePage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _fileList = null;
    let _progress = null;
    let _results = null;

    // DOM refs
    let _outputDirInput = null;
    let _outputNameInput = null;
    let _mergeBtn = null;
    let _fileCountLabel = null;
    let _totalPagesLabel = null;

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
            title: 'Merge PDFs',
            subtitle: 'Combine multiple PDF files into one document',
        });
        _el.appendChild(header.el);

        // ── Drop zone ──
        _dropZone = createDropZone({
            title: 'Drop PDF files here',
            subtitle: 'or click to browse — add as many as you need',
            multiple: true,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _fileList.clear();
            _fileList.addFiles(files);
            _analyzeFiles(files);
            _updateUI();
        });
        _el.appendChild(_dropZone.el);

        // ── File list (reorderable) ──
        _fileList = createFileList({
            showPages: true,
            reorderable: true,
            emptyMessage: 'No PDF files added yet. Drop files above or click to browse.',
        });
        _fileList.onRemove(function () {
            _files = _fileList.getFiles();
            _updateUI();
        });
        _el.appendChild(_fileList.el);

        // ── Info bar (file count + total pages) ──
        var infoBar = document.createElement('div');
        infoBar.className = 'card';
        infoBar.id = 'merge-info-bar';
        infoBar.style.cssText = 'margin-top:var(--space-3); display:none; padding:var(--space-3) var(--space-4); display:flex; align-items:center; gap:var(--space-4);';
        infoBar.style.display = 'none';

        _fileCountLabel = document.createElement('span');
        _fileCountLabel.style.cssText = 'font-size:var(--font-size-sm); color:var(--color-text-2);';
        infoBar.appendChild(_fileCountLabel);

        _totalPagesLabel = document.createElement('span');
        _totalPagesLabel.style.cssText = 'font-size:var(--font-size-sm); color:var(--color-text-2);';
        infoBar.appendChild(_totalPagesLabel);

        _el.appendChild(infoBar);

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
        _outputDirInput.placeholder = 'Same as first source file';
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
                console.error('[MergePage] openFolder error:', err);
            }
        });
        outDirRow.appendChild(browseDirBtn);
        outDirGroup.appendChild(outDirRow);
        outputCard.appendChild(outDirGroup);

        // Output filename
        var nameGroup = _createFormGroup('Output filename');
        _outputNameInput = document.createElement('input');
        _outputNameInput.type = 'text';
        _outputNameInput.className = 'form-input';
        _outputNameInput.style.width = '100%';
        _outputNameInput.value = 'merged';
        _outputNameInput.placeholder = 'merged';
        _outputNameInput.addEventListener('change', _saveSettings);
        nameGroup.appendChild(_outputNameInput);

        var nameHelp = document.createElement('div');
        nameHelp.className = 'form-help';
        nameHelp.textContent = 'The .pdf extension will be added automatically.';
        nameGroup.appendChild(nameHelp);

        outputCard.appendChild(nameGroup);
        _el.appendChild(outputCard);

        // ── Action row ──
        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display:flex; justify-content:flex-end; align-items:center; gap:var(--space-3); margin-top:var(--space-4);';

        var shortcutHint = document.createElement('span');
        shortcutHint.style.cssText = 'font-size:var(--font-size-xs); color:var(--color-text-3); margin-right:auto;';
        shortcutHint.textContent = 'Ctrl+O to add files \u2022 Ctrl+Enter to merge';
        actionRow.appendChild(shortcutHint);

        _mergeBtn = document.createElement('button');
        _mergeBtn.className = 'btn btn-primary btn-lg';
        _mergeBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-right:6px;vertical-align:-2px"><path d="M4 4v12h12"/><path d="M8 4v8h8"/></svg><span>Merge</span>';
        _mergeBtn.disabled = true;
        _mergeBtn.addEventListener('click', _startMerge);
        actionRow.appendChild(_mergeBtn);
        _el.appendChild(actionRow);

        // ── Progress ──
        _progress = createProgressPanel();
        _progress.el.style.marginTop = 'var(--space-4)';
        _progress.onCancel(function () {
            BridgeAPI.cancel('merge');
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.info('Merge cancelled.');
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

    function _updateUI() {
        var files = _fileList.getFiles();
        var hasFiles = files.length >= 2;
        _mergeBtn.disabled = _busy || !hasFiles;

        // Update info bar
        var infoBar = _el ? _el.querySelector('#merge-info-bar') : null;
        if (infoBar) {
            if (files.length > 0) {
                infoBar.style.display = 'flex';
                _fileCountLabel.textContent = files.length + ' file' + (files.length === 1 ? '' : 's');

                var totalPages = 0;
                var hasPages = false;
                for (var i = 0; i < files.length; i++) {
                    if (files[i].pages != null) {
                        totalPages += files[i].pages;
                        hasPages = true;
                    }
                }
                _totalPagesLabel.textContent = hasPages
                    ? totalPages + ' total page' + (totalPages === 1 ? '' : 's')
                    : 'Analyzing...';
            } else {
                infoBar.style.display = 'none';
            }
        }
    }

    async function _analyzeFiles(files) {
        for (var i = 0; i < files.length; i++) {
            try {
                var info = await BridgeAPI.analyzeFile(files[i].path);
                if (info) {
                    var listFiles = _fileList.getFiles();
                    for (var j = 0; j < listFiles.length; j++) {
                        if (listFiles[j].path === files[i].path) {
                            listFiles[j].pages = info.pages || info.page_count || null;
                            listFiles[j].size = info.size || info.file_size || null;
                        }
                    }
                    _fileList.refresh();
                    _updateUI();
                }
            } catch (e) {
                console.warn('[MergePage] analyzeFile failed:', files[i].path, e);
            }
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Settings Persistence
    // ══════════════════════════════════════════════════════════════

    async function _loadSettings() {
        try {
            var outputDir = await BridgeAPI.loadSetting('merge/outputDir');
            if (outputDir && _outputDirInput) _outputDirInput.value = outputDir;

            var outputName = await BridgeAPI.loadSetting('merge/outputName');
            if (outputName && _outputNameInput) _outputNameInput.value = outputName;
        } catch (e) {
            console.warn('[MergePage] loadSettings:', e);
        }
    }

    function _saveSettings() {
        try {
            BridgeAPI.saveSetting('merge/outputDir', _outputDirInput.value);
            BridgeAPI.saveSetting('merge/outputName', _outputNameInput.value);
        } catch (e) {
            console.warn('[MergePage] saveSettings:', e);
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Start Operation
    // ══════════════════════════════════════════════════════════════

    function _startMerge() {
        var files = _fileList.getFiles();
        if (files.length < 2) {
            Toast.warning('Please add at least 2 PDF files to merge.');
            return;
        }

        var outputName = (_outputNameInput.value.trim() || 'merged');
        // Ensure .pdf extension
        if (!/\.pdf$/i.test(outputName)) outputName += '.pdf';

        var outputDir = _outputDirInput.value;
        if (!outputDir && files.length > 0) {
            outputDir = BridgeAPI.dirname(files[0].path);
        }

        var outputPath = outputDir + '\\' + outputName;

        _busy = true;
        _updateUI();
        _results.hide();
        _progress.reset();
        _progress.show();
        _saveSettings();

        BridgeAPI.startMerge({
            files: files.map(function (f) { return f.path; }),
            output_path: outputPath,
        });
    }

    // ══════════════════════════════════════════════════════════════
    //  Events
    // ══════════════════════════════════════════════════════════════

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.toolKey !== 'merge') return;
            _progress.update(
                data.pct || 0,
                data.filename || '',
                (data.current || 0) + 1,
                data.total || 1
            );
        };

        _onDone = function (data) {
            if (data.toolKey !== 'merge') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                var res = data.results || {};
                Toast.success('PDF files merged successfully!');
                _results.show({
                    files: res.files || [],
                    totalTime: res.elapsed || 0,
                    outputDir: res.output_dir || _outputDirInput.value || '',
                });
            } else {
                Toast.error(data.message || 'Merge failed.');
            }
        };

        _onError = function (data) {
            if (data.toolKey && data.toolKey !== 'merge') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.error(data.message || 'An error occurred during merge.');
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
                    if (!_mergeBtn.disabled) _startMerge();
                }
                if (e.key === 'Escape' && _busy) {
                    e.preventDefault();
                    BridgeAPI.cancel('merge');
                    _busy = false;
                    _progress.hide();
                    _updateUI();
                    Toast.info('Merge cancelled.');
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
                _dropZone.setFiles(files.map(function (f) { return f.path || f; }));
            }
        },
    };
}

Router.register('merge', function () { return MergePage(); });
