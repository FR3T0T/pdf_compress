/* ==========================================================================
   PDF Toolkit - Flatten Page
   Flatten PDF annotations and/or form fields.
   ========================================================================== */

"use strict";

function FlattenPage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _progress = null;
    let _results = null;

    // DOM refs
    let _annotationsCb = null;
    let _formsCb = null;
    let _outputPathInput = null;
    let _flattenBtn = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;
    let _onError = null;

    function _buildUI(container) {
        _el = container;

        // Page header
        var header = createPageHeader({
            title: 'Flatten PDF',
            subtitle: 'Remove interactive annotations and form fields',
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

        // Options card
        var optCard = document.createElement('div');
        optCard.className = 'card';
        optCard.style.marginTop = 'var(--space-4)';

        var optLabel = document.createElement('label');
        optLabel.className = 'form-label';
        optLabel.textContent = 'Flatten options';
        optCard.appendChild(optLabel);

        var checkboxGroup = document.createElement('div');
        checkboxGroup.style.display = 'flex';
        checkboxGroup.style.flexDirection = 'column';
        checkboxGroup.style.gap = 'var(--space-3)';

        // Annotations checkbox
        var annoRow = document.createElement('label');
        annoRow.style.display = 'flex';
        annoRow.style.alignItems = 'center';
        annoRow.style.gap = 'var(--space-2)';
        annoRow.style.cursor = 'pointer';

        _annotationsCb = document.createElement('input');
        _annotationsCb.type = 'checkbox';
        _annotationsCb.checked = true;
        annoRow.appendChild(_annotationsCb);

        var annoLabel = document.createElement('div');
        var annoTitle = document.createElement('div');
        annoTitle.style.fontWeight = 'var(--font-weight-medium)';
        annoTitle.textContent = 'Flatten annotations';
        annoLabel.appendChild(annoTitle);
        var annoDesc = document.createElement('div');
        annoDesc.className = 'form-help';
        annoDesc.style.marginTop = '2px';
        annoDesc.textContent = 'Merge comments, highlights, stamps, and other annotations into the page content.';
        annoLabel.appendChild(annoDesc);
        annoRow.appendChild(annoLabel);
        checkboxGroup.appendChild(annoRow);

        // Forms checkbox
        var formsRow = document.createElement('label');
        formsRow.style.display = 'flex';
        formsRow.style.alignItems = 'center';
        formsRow.style.gap = 'var(--space-2)';
        formsRow.style.cursor = 'pointer';

        _formsCb = document.createElement('input');
        _formsCb.type = 'checkbox';
        _formsCb.checked = true;
        formsRow.appendChild(_formsCb);

        var formsLabel = document.createElement('div');
        var formsTitle = document.createElement('div');
        formsTitle.style.fontWeight = 'var(--font-weight-medium)';
        formsTitle.textContent = 'Flatten form fields';
        formsLabel.appendChild(formsTitle);
        var formsDesc = document.createElement('div');
        formsDesc.className = 'form-help';
        formsDesc.style.marginTop = '2px';
        formsDesc.textContent = 'Convert interactive form fields into static text on the page.';
        formsLabel.appendChild(formsDesc);
        formsRow.appendChild(formsLabel);
        checkboxGroup.appendChild(formsRow);

        optCard.appendChild(checkboxGroup);
        _el.appendChild(optCard);

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
                var path = await BridgeAPI.saveFile('PDF Files (*.pdf)', 'flattened.pdf');
                if (path) {
                    _outputPathInput.value = path;
                    _updateUI();
                }
            } catch (err) {
                console.error('[FlattenPage] saveFile error:', err);
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

        _flattenBtn = document.createElement('button');
        _flattenBtn.className = 'btn btn-primary btn-lg';
        _flattenBtn.textContent = 'Flatten';
        _flattenBtn.disabled = true;
        _flattenBtn.addEventListener('click', _startFlatten);
        actionRow.appendChild(_flattenBtn);
        _el.appendChild(actionRow);

        // Progress
        _progress = createProgressPanel();
        _progress.onCancel(function () {
            BridgeAPI.cancel('flatten');
        });
        _el.appendChild(_progress.el);

        // Results
        _results = createResultsPanel();
        _el.appendChild(_results.el);
    }

    function _updateUI() {
        var hasFile = _files.length > 0;
        var hasOutput = _outputPathInput && _outputPathInput.value.length > 0;
        _flattenBtn.disabled = _busy || !hasFile || !hasOutput;
    }

    function _startFlatten() {
        if (_files.length === 0) {
            Toast.warning('Please add a PDF file first.');
            return;
        }
        var outputPath = _outputPathInput.value;
        if (!outputPath) {
            Toast.warning('Please choose an output file path.');
            return;
        }

        if (!_annotationsCb.checked && !_formsCb.checked) {
            Toast.warning('Please select at least one flatten option.');
            return;
        }

        _busy = true;
        _updateUI();
        _results.hide();
        _progress.reset();
        _progress.show();

        BridgeAPI.startFlatten({
            file: _files[0].path,
            output_path: outputPath,
            annotations: _annotationsCb.checked,
            forms: _formsCb.checked,
        });
    }

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.tool !== 'flatten') return;
            _progress.update(
                data.percent || 0,
                data.filename || '',
                data.current || 0,
                data.total || 0
            );
        };

        _onDone = function (data) {
            if (data.tool !== 'flatten') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                Toast.success('PDF flattened successfully!');
                _results.show({
                    files: data.files || [],
                    totalTime: data.elapsed || 0,
                    outputDir: data.output_dir || '',
                });
            } else {
                Toast.error(data.error || 'Flatten failed.');
            }
        };

        _onError = function (data) {
            if (data.tool && data.tool !== 'flatten') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.error(data.message || 'An error occurred during flatten.');
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

Router.register('flatten', function () { return FlattenPage(); });
