/* ==========================================================================
   PDF Toolkit - Crop Page
   Crop page margins from a PDF file.
   ========================================================================== */

"use strict";

function CropPage() {
    let _el = null;
    let _busy = false;

    /* --- Components --- */
    const dropZone = createDropZone({
        title: 'Drop PDF file here',
        subtitle: 'or click to browse',
        multiple: false,
    });

    const progress = createProgressPanel();
    const results  = createResultsPanel();

    /* --- State --- */
    let _file = null;
    let _outputPath = null;

    /* --- Event handlers --- */
    function _onProgress(data) {
        if (!_busy) return;
        progress.update(data.percent || 0, data.filename || '', data.current || 1, data.total || 1);
    }

    function _onDone(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);

        if (data.error) {
            Toast.error(data.error);
            return;
        }

        results.show({
            files: data.files || [],
            totalTime: data.elapsed || 0,
            outputDir: data.output_dir || null,
        });

        Toast.success('Crop complete.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during cropping.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#crop-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '_cropped.pdf') : 'cropped.pdf';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#crop-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _createNumberInput(id, defaultVal, min, max, step) {
        const input = document.createElement('input');
        input.type = 'number';
        input.id = id;
        input.className = 'form-input';
        input.style.width = '90px';
        input.value = defaultVal;
        if (min != null) input.min = min;
        if (max != null) input.max = max;
        if (step != null) input.step = step;
        return input;
    }

    function _startCrop() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output location.'); return; }

        const left   = parseFloat(_el.querySelector('#crop-left').value)   || 0;
        const right  = parseFloat(_el.querySelector('#crop-right').value)  || 0;
        const top    = parseFloat(_el.querySelector('#crop-top').value)    || 0;
        const bottom = parseFloat(_el.querySelector('#crop-bottom').value) || 0;
        const unit   = _el.querySelector('#crop-unit').value;

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startCrop({
            file: _file,
            output_path: _outputPath,
            margins: { left, right, top, bottom },
            unit: unit,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Crop',
            subtitle: 'Crop page margins from a PDF',
        });
        el.appendChild(header.el);

        // Drop zone
        el.appendChild(dropZone.el);

        dropZone.onFilesChanged((files) => {
            _file = files.length > 0 ? files[0].path : null;
        });

        // Margin settings card
        const settingsCard = document.createElement('div');
        settingsCard.className = 'card';
        settingsCard.style.marginTop = 'var(--space-4)';

        const settingsTitle = document.createElement('div');
        settingsTitle.className = 'form-label';
        settingsTitle.textContent = 'Margins to crop';
        settingsTitle.style.marginBottom = 'var(--space-3)';
        settingsCard.appendChild(settingsTitle);

        // Unit selector
        const unitRow = document.createElement('div');
        unitRow.style.display = 'flex';
        unitRow.style.alignItems = 'center';
        unitRow.style.gap = 'var(--space-3)';
        unitRow.style.marginBottom = 'var(--space-4)';

        const unitLabel = document.createElement('label');
        unitLabel.textContent = 'Unit:';
        unitLabel.className = 'form-label';
        unitLabel.style.marginBottom = '0';
        unitRow.appendChild(unitLabel);

        const unitSelect = document.createElement('select');
        unitSelect.id = 'crop-unit';
        unitSelect.className = 'form-select';
        unitSelect.style.width = '120px';
        const units = [
            { value: 'mm', label: 'Millimeters (mm)' },
            { value: 'pt', label: 'Points (pt)' },
            { value: 'inch', label: 'Inches (in)' },
        ];
        for (const u of units) {
            const opt = document.createElement('option');
            opt.value = u.value;
            opt.textContent = u.label;
            unitSelect.appendChild(opt);
        }
        unitRow.appendChild(unitSelect);
        settingsCard.appendChild(unitRow);

        // Margin grid
        const marginGrid = document.createElement('div');
        marginGrid.style.display = 'grid';
        marginGrid.style.gridTemplateColumns = '1fr 1fr';
        marginGrid.style.gap = 'var(--space-3)';

        const margins = [
            { id: 'crop-left',   label: 'Left' },
            { id: 'crop-right',  label: 'Right' },
            { id: 'crop-top',    label: 'Top' },
            { id: 'crop-bottom', label: 'Bottom' },
        ];

        for (const m of margins) {
            const group = document.createElement('div');
            group.style.display = 'flex';
            group.style.alignItems = 'center';
            group.style.gap = 'var(--space-2)';

            const lbl = document.createElement('label');
            lbl.textContent = m.label + ':';
            lbl.setAttribute('for', m.id);
            lbl.style.width = '60px';
            lbl.style.flexShrink = '0';
            group.appendChild(lbl);

            group.appendChild(_createNumberInput(m.id, 0, 0, null, 1));
            marginGrid.appendChild(group);
        }

        settingsCard.appendChild(marginGrid);
        el.appendChild(settingsCard);

        // Output path picker
        const outputCard = document.createElement('div');
        outputCard.className = 'card';
        outputCard.style.marginTop = 'var(--space-4)';

        const outputRow = document.createElement('div');
        outputRow.style.display = 'flex';
        outputRow.style.alignItems = 'center';
        outputRow.style.gap = 'var(--space-3)';

        const outputLabel = document.createElement('span');
        outputLabel.className = 'form-label';
        outputLabel.textContent = 'Output:';
        outputLabel.style.marginBottom = '0';
        outputRow.appendChild(outputLabel);

        const outputFile = document.createElement('span');
        outputFile.id = 'crop-output-label';
        outputFile.style.flex = '1';
        outputFile.style.color = 'var(--color-text-2)';
        outputFile.textContent = 'No output file selected';
        outputRow.appendChild(outputFile);

        const browseBtn = document.createElement('button');
        browseBtn.className = 'btn btn-secondary btn-sm';
        browseBtn.textContent = 'Browse...';
        browseBtn.addEventListener('click', _pickOutput);
        outputRow.appendChild(browseBtn);

        outputCard.appendChild(outputRow);
        el.appendChild(outputCard);

        // Action button
        const actionRow = document.createElement('div');
        actionRow.style.display = 'flex';
        actionRow.style.justifyContent = 'flex-end';
        actionRow.style.marginTop = 'var(--space-4)';

        const cropBtn = document.createElement('button');
        cropBtn.id = 'crop-btn';
        cropBtn.className = 'btn btn-primary';
        cropBtn.textContent = 'Crop';
        cropBtn.addEventListener('click', _startCrop);
        actionRow.appendChild(cropBtn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('crop');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Crop cancelled.');
        });

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
        EventBus.on('error', _onError);
    }

    function onActivated() {}

    function onDeactivated() {
        EventBus.off('progress', _onProgress);
        EventBus.off('done', _onDone);
        EventBus.off('error', _onError);
    }

    function isBusy() { return _busy; }

    function handleDrop(files) {
        if (files && files.length > 0) {
            dropZone.setFiles([files[0]]);
        }
    }

    return { onMount, onActivated, onDeactivated, isBusy, handleDrop };
}

Router.register('crop', () => CropPage());
