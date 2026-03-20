/* ==========================================================================
   PDF Toolkit - Repair Page
   Repair a corrupted or damaged PDF file.
   ========================================================================== */

"use strict";

function RepairPage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _progress = null;
    let _results = null;

    // DOM refs
    let _outputPathInput = null;
    let _repairBtn = null;
    let _resultCard = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;
    let _onError = null;

    function _buildUI(container) {
        _el = container;

        // Page header
        var header = createPageHeader({
            title: 'Repair PDF',
            subtitle: 'Attempt to repair a corrupted or damaged PDF file',
        });
        _el.appendChild(header.el);

        // Drop zone (single file)
        _dropZone = createDropZone({
            title: 'Drop a PDF file here',
            subtitle: 'or click to browse',
            multiple: false,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _updateUI();
        });
        _el.appendChild(_dropZone.el);

        // Output section
        var outputCard = document.createElement('div');
        outputCard.className = 'card';
        outputCard.style.marginTop = 'var(--space-4)';

        var outputLabel = document.createElement('label');
        outputLabel.className = 'form-label';
        outputLabel.textContent = 'Output file';
        outputCard.appendChild(outputLabel);

        var outputRow = document.createElement('div');
        outputRow.style.display = 'flex';
        outputRow.style.gap = 'var(--space-2)';
        outputRow.style.alignItems = 'center';

        _outputPathInput = document.createElement('input');
        _outputPathInput.type = 'text';
        _outputPathInput.className = 'form-input';
        _outputPathInput.placeholder = 'Choose output file path...';
        _outputPathInput.readOnly = true;
        _outputPathInput.style.flex = '1';
        outputRow.appendChild(_outputPathInput);

        var browseBtn = document.createElement('button');
        browseBtn.className = 'btn btn-secondary';
        browseBtn.textContent = 'Browse';
        browseBtn.addEventListener('click', async function () {
            try {
                var path = await BridgeAPI.saveFile('PDF Files (*.pdf)', 'repaired.pdf');
                if (path) {
                    _outputPathInput.value = path;
                    _updateUI();
                }
            } catch (err) {
                console.error('[RepairPage] saveFile error:', err);
            }
        });
        outputRow.appendChild(browseBtn);
        outputCard.appendChild(outputRow);

        var outputHelp = document.createElement('div');
        outputHelp.className = 'form-help';
        outputHelp.style.marginTop = 'var(--space-2)';
        outputHelp.textContent = 'The repaired PDF will be saved to a new file, leaving the original intact.';
        outputCard.appendChild(outputHelp);

        _el.appendChild(outputCard);

        // Action button
        var actionRow = document.createElement('div');
        actionRow.style.display = 'flex';
        actionRow.style.justifyContent = 'flex-end';
        actionRow.style.marginTop = 'var(--space-4)';

        _repairBtn = document.createElement('button');
        _repairBtn.className = 'btn btn-primary btn-lg';
        _repairBtn.textContent = 'Repair';
        _repairBtn.disabled = true;
        _repairBtn.addEventListener('click', _startRepair);
        actionRow.appendChild(_repairBtn);
        _el.appendChild(actionRow);

        // Progress
        _progress = createProgressPanel();
        _progress.onCancel(function () {
            BridgeAPI.cancel('repair');
        });
        _el.appendChild(_progress.el);

        // Simple result card (success/fail)
        _resultCard = document.createElement('div');
        _resultCard.className = 'card';
        _resultCard.style.display = 'none';
        _resultCard.style.marginTop = 'var(--space-4)';
        _el.appendChild(_resultCard);

        // Results panel (for detailed results if available)
        _results = createResultsPanel();
        _el.appendChild(_results.el);
    }

    function _updateUI() {
        var hasFile = _files.length > 0;
        var hasOutput = _outputPathInput && _outputPathInput.value.length > 0;
        _repairBtn.disabled = _busy || !hasFile || !hasOutput;
    }

    function _startRepair() {
        if (_files.length === 0) {
            Toast.warning('Please add a PDF file first.');
            return;
        }
        var outputPath = _outputPathInput.value;
        if (!outputPath) {
            Toast.warning('Please choose an output file path.');
            return;
        }

        _busy = true;
        _updateUI();
        _results.hide();
        _resultCard.style.display = 'none';
        _progress.reset();
        _progress.show();

        BridgeAPI.startRepair({
            file: _files[0].path,
            output_path: outputPath,
        });
    }

    function _showSimpleResult(success, message) {
        _resultCard.style.display = '';
        _resultCard.textContent = '';

        var iconEl = document.createElement('div');
        iconEl.style.fontSize = '32px';
        iconEl.style.textAlign = 'center';
        iconEl.style.marginBottom = 'var(--space-2)';
        iconEl.textContent = success ? '\u2705' : '\u274C';
        _resultCard.appendChild(iconEl);

        var titleEl = document.createElement('div');
        titleEl.style.textAlign = 'center';
        titleEl.style.fontWeight = 'var(--font-weight-semibold)';
        titleEl.style.fontSize = 'var(--font-size-lg)';
        titleEl.style.marginBottom = 'var(--space-2)';
        titleEl.textContent = success ? 'Repair Successful' : 'Repair Failed';
        _resultCard.appendChild(titleEl);

        var msgEl = document.createElement('div');
        msgEl.style.textAlign = 'center';
        msgEl.style.color = 'var(--color-text-2)';
        msgEl.textContent = message;
        _resultCard.appendChild(msgEl);

        if (success && _outputPathInput.value) {
            var btnRow = document.createElement('div');
            btnRow.style.display = 'flex';
            btnRow.style.justifyContent = 'center';
            btnRow.style.gap = 'var(--space-2)';
            btnRow.style.marginTop = 'var(--space-4)';

            var openFileBtn = document.createElement('button');
            openFileBtn.className = 'btn btn-primary';
            openFileBtn.textContent = 'Open File';
            openFileBtn.addEventListener('click', function () {
                BridgeAPI.openFilePath(_outputPathInput.value);
            });
            btnRow.appendChild(openFileBtn);

            var openFolderBtn = document.createElement('button');
            openFolderBtn.className = 'btn btn-secondary';
            openFolderBtn.textContent = 'Open Folder';
            openFolderBtn.addEventListener('click', function () {
                BridgeAPI.openFolderPath(BridgeAPI.dirname(_outputPathInput.value));
            });
            btnRow.appendChild(openFolderBtn);

            _resultCard.appendChild(btnRow);
        }
    }

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.tool !== 'repair') return;
            _progress.update(
                data.percent || 0,
                data.filename || '',
                data.current || 0,
                data.total || 0
            );
        };

        _onDone = function (data) {
            if (data.tool !== 'repair') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                Toast.success('PDF repaired successfully!');
                _showSimpleResult(true, 'The PDF file has been repaired and saved to the output path.');
                if (data.files && data.files.length > 0) {
                    _results.show({
                        files: data.files,
                        totalTime: data.elapsed || 0,
                        outputDir: data.output_dir || '',
                    });
                }
            } else {
                var errorMsg = data.error || 'The file could not be repaired. It may be too severely damaged.';
                Toast.error(errorMsg);
                _showSimpleResult(false, errorMsg);
            }
        };

        _onError = function (data) {
            if (data.tool && data.tool !== 'repair') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            var errorMsg = data.message || 'An error occurred during repair.';
            Toast.error(errorMsg);
            _showSimpleResult(false, errorMsg);
        };

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
        EventBus.on('error', _onError);
    }

    function _unbindEvents() {
        if (_onProgress) EventBus.off('progress', _onProgress);
        if (_onDone) EventBus.off('done', _onDone);
        if (_onError) EventBus.off('error', _onError);
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
                _dropZone.setFiles([paths[0]]);
            }
        },
    };
}

Router.register('repair', function () { return RepairPage(); });
