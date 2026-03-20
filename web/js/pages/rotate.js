/* ==========================================================================
   PDF Toolkit - Rotate / Reorder / Delete Pages
   Maps to the "page_ops" tool key.
   ========================================================================== */

"use strict";

function RotatePage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _progress = null;
    let _results = null;

    // DOM refs
    let _tabBtns = [];
    let _tabPanels = [];
    let _activeTab = 'rotate';

    // Rotate controls
    let _rotateRangeInput = null;
    let _rotateAngleSelect = null;

    // Reorder controls
    let _reorderInput = null;

    // Delete controls
    let _deleteInput = null;

    // Shared controls
    let _outputPathInput = null;
    let _applyBtn = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;
    let _onError = null;

    function _buildUI(container) {
        _el = container;

        // Page header
        var header = createPageHeader({
            title: 'Page Operations',
            subtitle: 'Rotate, reorder, or delete pages in a PDF',
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

        // Operation tabs
        var tabCard = document.createElement('div');
        tabCard.className = 'card';
        tabCard.style.marginTop = 'var(--space-4)';

        var tabBar = document.createElement('div');
        tabBar.style.display = 'flex';
        tabBar.style.gap = 'var(--space-1)';
        tabBar.style.borderBottom = '1px solid var(--color-border)';
        tabBar.style.marginBottom = 'var(--space-4)';

        var tabs = [
            { key: 'rotate', label: 'Rotate' },
            { key: 'reorder', label: 'Reorder' },
            { key: 'delete', label: 'Delete Pages' },
        ];

        _tabBtns = [];
        for (var t = 0; t < tabs.length; t++) {
            var tabBtn = document.createElement('button');
            tabBtn.className = 'btn btn-ghost';
            tabBtn.textContent = tabs[t].label;
            tabBtn.setAttribute('data-tab', tabs[t].key);
            tabBtn.style.borderRadius = '0';
            tabBtn.style.borderBottom = '2px solid transparent';
            tabBtn.style.paddingBottom = 'var(--space-2)';
            if (tabs[t].key === _activeTab) {
                tabBtn.style.borderBottomColor = 'var(--color-accent)';
                tabBtn.style.color = 'var(--color-accent)';
            }
            tabBtn.addEventListener('click', _onTabClick);
            tabBar.appendChild(tabBtn);
            _tabBtns.push(tabBtn);
        }
        tabCard.appendChild(tabBar);

        // Tab panels
        _tabPanels = [];

        // -- Rotate panel --
        var rotatePanel = document.createElement('div');
        rotatePanel.setAttribute('data-panel', 'rotate');

        var rotRangeLabel = document.createElement('label');
        rotRangeLabel.className = 'form-label';
        rotRangeLabel.textContent = 'Page range';
        rotatePanel.appendChild(rotRangeLabel);

        _rotateRangeInput = document.createElement('input');
        _rotateRangeInput.type = 'text';
        _rotateRangeInput.className = 'form-input';
        _rotateRangeInput.placeholder = 'e.g. 1-3, 5 (leave empty for all pages)';
        rotatePanel.appendChild(_rotateRangeInput);

        var angleLabel = document.createElement('label');
        angleLabel.className = 'form-label';
        angleLabel.style.marginTop = 'var(--space-3)';
        angleLabel.textContent = 'Rotation angle';
        rotatePanel.appendChild(angleLabel);

        _rotateAngleSelect = document.createElement('select');
        _rotateAngleSelect.className = 'form-input';

        var angles = [
            { value: '90', text: '90\u00B0 clockwise' },
            { value: '180', text: '180\u00B0' },
            { value: '270', text: '270\u00B0 clockwise (90\u00B0 counter-clockwise)' },
        ];
        for (var a = 0; a < angles.length; a++) {
            var opt = document.createElement('option');
            opt.value = angles[a].value;
            opt.textContent = angles[a].text;
            _rotateAngleSelect.appendChild(opt);
        }
        rotatePanel.appendChild(_rotateAngleSelect);
        tabCard.appendChild(rotatePanel);
        _tabPanels.push(rotatePanel);

        // -- Reorder panel --
        var reorderPanel = document.createElement('div');
        reorderPanel.setAttribute('data-panel', 'reorder');
        reorderPanel.style.display = 'none';

        var reorderLabel = document.createElement('label');
        reorderLabel.className = 'form-label';
        reorderLabel.textContent = 'New page order';
        reorderPanel.appendChild(reorderLabel);

        _reorderInput = document.createElement('input');
        _reorderInput.type = 'text';
        _reorderInput.className = 'form-input';
        _reorderInput.placeholder = 'e.g. 3, 1, 2, 5, 4';
        reorderPanel.appendChild(_reorderInput);

        var reorderHelp = document.createElement('div');
        reorderHelp.className = 'form-help';
        reorderHelp.textContent = 'Enter page numbers in the desired order, separated by commas.';
        reorderPanel.appendChild(reorderHelp);
        tabCard.appendChild(reorderPanel);
        _tabPanels.push(reorderPanel);

        // -- Delete panel --
        var deletePanel = document.createElement('div');
        deletePanel.setAttribute('data-panel', 'delete');
        deletePanel.style.display = 'none';

        var deleteLabel = document.createElement('label');
        deleteLabel.className = 'form-label';
        deleteLabel.textContent = 'Pages to delete';
        deletePanel.appendChild(deleteLabel);

        _deleteInput = document.createElement('input');
        _deleteInput.type = 'text';
        _deleteInput.className = 'form-input';
        _deleteInput.placeholder = 'e.g. 2, 4, 7-9';
        deletePanel.appendChild(_deleteInput);

        var deleteHelp = document.createElement('div');
        deleteHelp.className = 'form-help';
        deleteHelp.textContent = 'Enter page numbers or ranges to remove from the PDF.';
        deletePanel.appendChild(deleteHelp);
        tabCard.appendChild(deletePanel);
        _tabPanels.push(deletePanel);

        _el.appendChild(tabCard);

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
                var path = await BridgeAPI.saveFile('PDF Files (*.pdf)', 'output.pdf');
                if (path) {
                    _outputPathInput.value = path;
                    _updateUI();
                }
            } catch (err) {
                console.error('[RotatePage] saveFile error:', err);
            }
        });
        outputRow.appendChild(browseBtn);
        outputCard.appendChild(outputRow);
        _el.appendChild(outputCard);

        // Action button
        var actionRow = document.createElement('div');
        actionRow.style.display = 'flex';
        actionRow.style.justifyContent = 'flex-end';
        actionRow.style.marginTop = 'var(--space-4)';

        _applyBtn = document.createElement('button');
        _applyBtn.className = 'btn btn-primary btn-lg';
        _applyBtn.textContent = 'Apply';
        _applyBtn.disabled = true;
        _applyBtn.addEventListener('click', _startPageOps);
        actionRow.appendChild(_applyBtn);
        _el.appendChild(actionRow);

        // Progress
        _progress = createProgressPanel();
        _progress.onCancel(function () {
            BridgeAPI.cancel('page_ops');
        });
        _el.appendChild(_progress.el);

        // Results
        _results = createResultsPanel();
        _el.appendChild(_results.el);
    }

    function _onTabClick(e) {
        var key = e.currentTarget.getAttribute('data-tab');
        _activeTab = key;

        for (var i = 0; i < _tabBtns.length; i++) {
            var btn = _tabBtns[i];
            var isActive = btn.getAttribute('data-tab') === key;
            btn.style.borderBottomColor = isActive ? 'var(--color-accent)' : 'transparent';
            btn.style.color = isActive ? 'var(--color-accent)' : '';
        }

        for (var j = 0; j < _tabPanels.length; j++) {
            var panel = _tabPanels[j];
            panel.style.display = panel.getAttribute('data-panel') === key ? '' : 'none';
        }
    }

    function _updateUI() {
        var hasFile = _files.length > 0;
        var hasOutput = _outputPathInput && _outputPathInput.value.length > 0;
        _applyBtn.disabled = _busy || !hasFile || !hasOutput;
    }

    function _startPageOps() {
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
        _progress.reset();
        _progress.show();

        var params = {
            file: _files[0].path,
            output_path: outputPath,
        };

        if (_activeTab === 'rotate') {
            var rangeStr = _rotateRangeInput.value.trim();
            var angle = parseInt(_rotateAngleSelect.value, 10);
            params.rotations = [{ pages: rangeStr || 'all', angle: angle }];
        } else if (_activeTab === 'reorder') {
            var orderStr = _reorderInput.value.trim();
            if (orderStr) {
                params.new_order = orderStr.split(',').map(function (s) {
                    return parseInt(s.trim(), 10);
                }).filter(function (n) { return !isNaN(n); });
            }
        } else if (_activeTab === 'delete') {
            params.delete_pages = _deleteInput.value.trim();
        }

        BridgeAPI.startPageOps(params);
    }

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.tool !== 'page_ops' && data.tool !== 'rotate') return;
            _progress.update(
                data.percent || 0,
                data.filename || '',
                data.current || 0,
                data.total || 0
            );
        };

        _onDone = function (data) {
            if (data.tool !== 'page_ops' && data.tool !== 'rotate') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                Toast.success('Page operations applied successfully!');
                _results.show({
                    files: data.files || [],
                    totalTime: data.elapsed || 0,
                    outputDir: data.output_dir || '',
                });
            } else {
                Toast.error(data.error || 'Page operation failed.');
            }
        };

        _onError = function (data) {
            if (data.tool && data.tool !== 'page_ops' && data.tool !== 'rotate') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.error(data.message || 'An error occurred during page operations.');
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

Router.register('page_ops', function () { return RotatePage(); });
