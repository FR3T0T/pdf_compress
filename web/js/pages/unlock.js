/* ==========================================================================
   PDF Toolkit - Unlock Page
   Remove password protection from PDF or EPDF files.
   Auto-detects .epdf files and shows encryption metadata.
   Supports batch processing.
   ========================================================================== */

"use strict";

function UnlockPage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _fileList = null;
    let _progress = null;
    let _results = null;

    // DOM refs
    let _passwordInput = null;
    let _outputDirInput = null;
    let _namingInput = null;
    let _unlockBtn = null;
    let _cancelBtn = null;
    let _summaryLabel = null;
    let _resultLabel = null;
    let _openFolderBtn = null;
    let _epdfInfoPanel = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;

    function _buildUI(container) {
        _el = container;

        // Page header
        var header = createPageHeader({
            title: 'Unlock PDF',
            subtitle: 'Remove password protection from PDF or EPDF files',
        });
        _el.appendChild(header.el);

        // Drop zone (multi-file, accepts .pdf and .epdf)
        _dropZone = createDropZone({
            title: 'Drop protected PDF or EPDF files here',
            subtitle: 'or click to browse',
            accept: 'PDF & EPDF files (*.pdf *.epdf);;PDF files (*.pdf);;EPDF files (*.epdf);;All files (*)',
            multiple: true,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _updateSummary();
            _updateUI();
            _checkForEpdf();
        });
        _el.appendChild(_dropZone.el);

        // File list
        _fileList = createFileList({ showPages: false });
        _fileList.el.style.display = 'none';
        _el.appendChild(_fileList.el);

        // Summary label
        _summaryLabel = document.createElement('div');
        _summaryLabel.style.cssText = 'font-size: var(--font-size-sm); color: var(--color-text-3); margin-top: var(--space-2); margin-bottom: var(--space-2);';
        _el.appendChild(_summaryLabel);

        // EPDF info panel (shown when .epdf files detected)
        _epdfInfoPanel = document.createElement('div');
        _epdfInfoPanel.className = 'card';
        _epdfInfoPanel.style.cssText = 'margin-top: var(--space-3); display: none; border-left: 3px solid var(--color-accent);';
        _el.appendChild(_epdfInfoPanel);

        // Password card
        var pwCard = document.createElement('div');
        pwCard.className = 'card';
        pwCard.style.marginTop = 'var(--space-4)';

        var pwHeader = document.createElement('div');
        pwHeader.style.cssText = 'font-weight: 600; font-size: var(--font-size-sm); text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-2); margin-bottom: var(--space-3);';
        pwHeader.textContent = 'Password';
        pwCard.appendChild(pwHeader);

        var pwRow = document.createElement('div');
        pwRow.style.cssText = 'display: flex; gap: var(--space-2); align-items: center;';

        _passwordInput = document.createElement('input');
        _passwordInput.type = 'password';
        _passwordInput.className = 'form-input';
        _passwordInput.placeholder = 'Enter the file password';
        _passwordInput.style.flex = '1';
        _passwordInput.addEventListener('input', _updateUI);
        pwRow.appendChild(_passwordInput);

        var toggleBtn = document.createElement('button');
        toggleBtn.className = 'btn btn-ghost btn-sm';
        toggleBtn.textContent = 'Show';
        toggleBtn.addEventListener('click', function () {
            if (_passwordInput.type === 'password') {
                _passwordInput.type = 'text';
                toggleBtn.textContent = 'Hide';
            } else {
                _passwordInput.type = 'password';
                toggleBtn.textContent = 'Show';
            }
        });
        pwRow.appendChild(toggleBtn);
        pwCard.appendChild(pwRow);

        var pwHelp = document.createElement('div');
        pwHelp.className = 'form-help';
        pwHelp.textContent = 'Enter the password used to protect the file(s). For batch operations, all files must share the same password.';
        pwCard.appendChild(pwHelp);

        _el.appendChild(pwCard);

        // Output card
        var outCard = document.createElement('div');
        outCard.className = 'card';
        outCard.style.marginTop = 'var(--space-4)';

        var outHeader = document.createElement('div');
        outHeader.style.cssText = 'font-weight: 600; font-size: var(--font-size-sm); text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-2); margin-bottom: var(--space-3);';
        outHeader.textContent = 'Output';
        outCard.appendChild(outHeader);

        // Output directory
        var dirLabel = document.createElement('label');
        dirLabel.className = 'form-label';
        dirLabel.textContent = 'Output folder';
        outCard.appendChild(dirLabel);

        var dirRow = document.createElement('div');
        dirRow.style.cssText = 'display: flex; gap: var(--space-2); align-items: center;';

        _outputDirInput = document.createElement('input');
        _outputDirInput.type = 'text';
        _outputDirInput.className = 'form-input';
        _outputDirInput.placeholder = 'Same folder as input';
        _outputDirInput.readOnly = true;
        _outputDirInput.style.flex = '1';
        dirRow.appendChild(_outputDirInput);

        var dirBrowseBtn = document.createElement('button');
        dirBrowseBtn.className = 'btn btn-secondary';
        dirBrowseBtn.textContent = 'Browse';
        dirBrowseBtn.addEventListener('click', async function () {
            try {
                var path = await BridgeAPI.openFolder();
                if (path) _outputDirInput.value = path;
            } catch (err) {
                console.error('[UnlockPage] openFolder error:', err);
            }
        });
        dirRow.appendChild(dirBrowseBtn);

        var dirResetBtn = document.createElement('button');
        dirResetBtn.className = 'btn btn-ghost btn-sm';
        dirResetBtn.textContent = 'Reset';
        dirResetBtn.addEventListener('click', function () {
            _outputDirInput.value = '';
        });
        dirRow.appendChild(dirResetBtn);
        outCard.appendChild(dirRow);

        // Naming template
        var nameLabel = document.createElement('label');
        nameLabel.className = 'form-label';
        nameLabel.style.marginTop = 'var(--space-3)';
        nameLabel.textContent = 'Naming template';
        outCard.appendChild(nameLabel);

        _namingInput = document.createElement('input');
        _namingInput.type = 'text';
        _namingInput.className = 'form-input';
        _namingInput.value = '{name}_unlocked';
        _namingInput.placeholder = '{name}_unlocked';
        _namingInput.title = 'Variables: {name} = original filename without extension';
        outCard.appendChild(_namingInput);

        var nameHint = document.createElement('div');
        nameHint.style.cssText = 'font-size: var(--font-size-xs); color: var(--color-text-3); margin-top: var(--space-1);';
        nameHint.textContent = 'Variables: {name}';
        outCard.appendChild(nameHint);

        _el.appendChild(outCard);

        // Action bar
        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-top: var(--space-4);';

        _openFolderBtn = document.createElement('button');
        _openFolderBtn.className = 'btn btn-ghost';
        _openFolderBtn.textContent = 'Open output folder';
        _openFolderBtn.style.display = 'none';
        _openFolderBtn.addEventListener('click', function () {
            var dir = _outputDirInput.value;
            if (!dir && _files.length > 0) dir = BridgeAPI.dirname(_files[0].path);
            if (dir) BridgeAPI.openFolderPath(dir);
        });
        actionRow.appendChild(_openFolderBtn);

        var spacer = document.createElement('div');
        spacer.style.flex = '1';
        actionRow.appendChild(spacer);

        _cancelBtn = document.createElement('button');
        _cancelBtn.className = 'btn btn-secondary';
        _cancelBtn.textContent = 'Cancel';
        _cancelBtn.style.display = 'none';
        _cancelBtn.addEventListener('click', function () {
            BridgeAPI.cancel('unlock');
            _cancelBtn.disabled = true;
            _cancelBtn.textContent = 'Cancelling...';
        });
        actionRow.appendChild(_cancelBtn);

        _unlockBtn = document.createElement('button');
        _unlockBtn.className = 'btn btn-primary btn-lg';
        _unlockBtn.textContent = 'Unlock';
        _unlockBtn.disabled = true;
        _unlockBtn.addEventListener('click', _startUnlock);
        actionRow.appendChild(_unlockBtn);
        _el.appendChild(actionRow);

        // Progress
        _progress = createProgressPanel();
        _progress.onCancel(function () { BridgeAPI.cancel('unlock'); });
        _el.appendChild(_progress.el);

        // Result label
        _resultLabel = document.createElement('div');
        _resultLabel.style.cssText = 'text-align: center; font-size: var(--font-size-sm); margin-top: var(--space-3); min-height: 24px;';
        _el.appendChild(_resultLabel);

        // Results panel
        _results = createResultsPanel();
        _el.appendChild(_results.el);
    }

    // ── EPDF detection ──────────────────────────────────────────

    async function _checkForEpdf() {
        var epdfFiles = [];
        for (var i = 0; i < _files.length; i++) {
            var f = _files[i];
            if (f.path && f.path.toLowerCase().endsWith('.epdf')) {
                try {
                    var info = await BridgeAPI.checkEpdf(f.path);
                    if (info.isEpdf) {
                        epdfFiles.push({ name: f.name || BridgeAPI.basename(f.path), info: info });
                    }
                } catch (err) {
                    console.error('[UnlockPage] checkEpdf error:', err);
                }
            }
        }

        if (epdfFiles.length > 0) {
            _epdfInfoPanel.innerHTML = '';
            _epdfInfoPanel.style.display = '';

            var title = document.createElement('div');
            title.style.cssText = 'font-weight: 600; font-size: var(--font-size-sm); color: var(--color-accent); margin-bottom: var(--space-2);';
            title.textContent = 'Enhanced Encryption Detected (' + epdfFiles.length + ' .epdf file' + (epdfFiles.length !== 1 ? 's' : '') + ')';
            _epdfInfoPanel.appendChild(title);

            for (var j = 0; j < epdfFiles.length && j < 5; j++) {
                var ef = epdfFiles[j];
                var row = document.createElement('div');
                row.style.cssText = 'font-size: var(--font-size-sm); color: var(--color-text-3); margin-top: var(--space-1);';
                row.textContent = ef.name + ' \u2014 ' + ef.info.cipher + ' + ' + ef.info.kdf;
                _epdfInfoPanel.appendChild(row);
            }
            if (epdfFiles.length > 5) {
                var more = document.createElement('div');
                more.style.cssText = 'font-size: var(--font-size-xs); color: var(--color-text-3); margin-top: var(--space-1);';
                more.textContent = '...and ' + (epdfFiles.length - 5) + ' more';
                _epdfInfoPanel.appendChild(more);
            }
        } else {
            _epdfInfoPanel.style.display = 'none';
        }
    }

    // ── Summary ─────────────────────────────────────────────────

    function _updateSummary() {
        if (_files.length === 0) {
            _summaryLabel.textContent = '';
            if (_fileList) _fileList.el.style.display = 'none';
            return;
        }

        var nEpdf = 0;
        for (var i = 0; i < _files.length; i++) {
            if (_files[i].path && _files[i].path.toLowerCase().endsWith('.epdf')) nEpdf++;
        }
        var parts = [_files.length + ' file' + (_files.length !== 1 ? 's' : '')];
        if (nEpdf > 0) parts.push(nEpdf + ' .epdf');

        _summaryLabel.textContent = parts.join('  \u00b7  ');

        if (_files.length > 0 && _fileList) {
            _fileList.clear();
            _fileList.addFiles(_files.map(function (f) {
                var isEpdf = f.path && f.path.toLowerCase().endsWith('.epdf');
                return {
                    path: f.path,
                    name: (f.name || BridgeAPI.basename(f.path)) + (isEpdf ? ' [EPDF]' : ''),
                    status: 'pending',
                };
            }));
            _fileList.el.style.display = '';
        }
    }

    function _updateUI() {
        var hasFiles = _files.length > 0;
        var hasPassword = _passwordInput && _passwordInput.value.length > 0;
        _unlockBtn.disabled = _busy || !hasFiles || !hasPassword;
    }

    // ── Run unlock ──────────────────────────────────────────────

    function _startUnlock() {
        if (_files.length === 0) {
            Toast.warning('Please add at least one file.');
            return;
        }
        var password = _passwordInput.value;
        if (!password) {
            Toast.warning('Please enter the password.');
            return;
        }

        _busy = true;
        _updateUI();
        _results.hide();
        _resultLabel.textContent = '';
        _resultLabel.style.color = '';
        _openFolderBtn.style.display = 'none';
        _progress.reset();
        _progress.show();
        _unlockBtn.style.display = 'none';
        _cancelBtn.style.display = '';
        _cancelBtn.disabled = false;
        _cancelBtn.textContent = 'Cancel';

        var filePaths = _files.map(function (f) { return f.path; });
        var naming = _namingInput.value.trim() || '{name}_unlocked';

        BridgeAPI.startUnlock({
            files: filePaths,
            password: password,
            output_dir: _outputDirInput.value || '',
            naming: naming,
        });
    }

    // ── Events ──────────────────────────────────────────────────

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.toolKey !== 'unlock') return;
            _progress.update(
                data.pct || 0,
                data.filename || '',
                data.current || 0,
                data.total || 0
            );
            if (_fileList && data.current != null && data.current < _files.length) {
                _fileList.setStatus(data.current, 'processing');
            }
        };

        _onDone = function (data) {
            if (data.toolKey !== 'unlock') return;
            _busy = false;
            _progress.hide();
            _unlockBtn.style.display = '';
            _cancelBtn.style.display = 'none';
            _updateUI();

            if (data.success) {
                var results = data.results || {};
                var fileResults = results.files || [];
                var nOk = 0, nErr = 0;

                for (var i = 0; i < fileResults.length; i++) {
                    var fr = fileResults[i];
                    if (_fileList) {
                        if (fr.status === 'error') {
                            _fileList.setStatus(i, 'error');
                            nErr++;
                        } else {
                            _fileList.setStatus(i, 'done');
                            nOk++;
                        }
                    }
                }

                if (fileResults.length === 0) nOk = _files.length;

                var elapsed = results.elapsed ? results.elapsed.toFixed(1) + 's' : '';
                var parts = [];
                if (nOk) parts.push(nOk + ' unlocked');
                if (nErr) parts.push(nErr + ' failed');
                if (elapsed) parts.push(elapsed);

                _resultLabel.textContent = parts.join('  \u00b7  ');
                _resultLabel.style.color = nErr ? 'var(--color-red)' : 'var(--color-green)';
                _openFolderBtn.style.display = '';

                if (nOk > 0) {
                    Toast.success(nOk + ' file' + (nOk !== 1 ? 's' : '') + ' unlocked successfully!');
                }
                if (nErr > 0) {
                    var errFiles = fileResults.filter(function(f) { return f.status === 'error'; });
                    for (var e = 0; e < errFiles.length; e++) {
                        Toast.error(errFiles[e].file + ': ' + errFiles[e].details);
                    }
                }

                _results.show({
                    files: fileResults,
                    totalTime: results.elapsed || 0,
                    outputDir: results.output_dir || '',
                });
            } else {
                var errMsg = data.message || 'Unlock failed.';
                _resultLabel.textContent = errMsg;
                _resultLabel.style.color = 'var(--color-red)';
                Toast.error(errMsg);
            }
        };

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
    }

    function _unbindEvents() {
        if (_onProgress) EventBus.off('progress', _onProgress);
        if (_onDone) EventBus.off('done', _onDone);
    }

    return {
        onMount: function (container) {
            _buildUI(container);
            _bindEvents();
        },
        onActivated: function () {},
        onDeactivated: function () {
            _unbindEvents();
        },
        isBusy: function () { return _busy; },
        handleDrop: function (files) {
            if (_dropZone) {
                var paths = files.map(function (f) { return f.path || f; });
                _dropZone.setFiles(paths);
            }
        },
    };
}

Router.register('unlock', function () { return UnlockPage(); });
