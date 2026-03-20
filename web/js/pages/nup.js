/* ==========================================================================
   PDF Toolkit - N-Up Page
   Arrange multiple pages per sheet (N-up layout).
   ========================================================================== */

"use strict";

function NupPage() {
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

        Toast.success('N-up layout created successfully.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during N-up layout.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#nup-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '_nup.pdf') : 'nup.pdf';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#nup-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _startNup() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output location.'); return; }

        const pagesPerSheet = parseInt(_el.querySelector('#nup-pages-per-sheet').value, 10);
        const pageSize      = _el.querySelector('#nup-page-size').value;
        const orientation   = _el.querySelector('#nup-orientation').value;

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startNup({
            file: _file,
            output_path: _outputPath,
            pages_per_sheet: pagesPerSheet,
            page_size: pageSize,
            orientation: orientation,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'N-Up Layout',
            subtitle: 'Place multiple pages per sheet',
        });
        el.appendChild(header.el);

        // Drop zone
        el.appendChild(dropZone.el);

        dropZone.onFilesChanged((files) => {
            _file = files.length > 0 ? files[0].path : null;
        });

        // Settings card
        const settingsCard = document.createElement('div');
        settingsCard.className = 'card';
        settingsCard.style.marginTop = 'var(--space-4)';

        const grid = document.createElement('div');
        grid.style.display = 'grid';
        grid.style.gridTemplateColumns = '1fr 1fr 1fr';
        grid.style.gap = 'var(--space-4)';

        // Pages per sheet
        const ppsGroup = document.createElement('div');
        const ppsLabel = document.createElement('label');
        ppsLabel.className = 'form-label';
        ppsLabel.textContent = 'Pages per sheet';
        ppsLabel.setAttribute('for', 'nup-pages-per-sheet');
        ppsGroup.appendChild(ppsLabel);

        const ppsSelect = document.createElement('select');
        ppsSelect.id = 'nup-pages-per-sheet';
        ppsSelect.className = 'form-select';
        ppsSelect.style.width = '100%';
        for (const n of [2, 4, 6, 9, 16]) {
            const opt = document.createElement('option');
            opt.value = n;
            opt.textContent = n + ' pages';
            ppsSelect.appendChild(opt);
        }
        ppsSelect.value = '4';
        ppsGroup.appendChild(ppsSelect);
        grid.appendChild(ppsGroup);

        // Page size
        const psGroup = document.createElement('div');
        const psLabel = document.createElement('label');
        psLabel.className = 'form-label';
        psLabel.textContent = 'Page size';
        psLabel.setAttribute('for', 'nup-page-size');
        psGroup.appendChild(psLabel);

        const psSelect = document.createElement('select');
        psSelect.id = 'nup-page-size';
        psSelect.className = 'form-select';
        psSelect.style.width = '100%';
        for (const size of ['A4', 'Letter', 'A3']) {
            const opt = document.createElement('option');
            opt.value = size;
            opt.textContent = size;
            psSelect.appendChild(opt);
        }
        psGroup.appendChild(psSelect);
        grid.appendChild(psGroup);

        // Orientation
        const orGroup = document.createElement('div');
        const orLabel = document.createElement('label');
        orLabel.className = 'form-label';
        orLabel.textContent = 'Orientation';
        orLabel.setAttribute('for', 'nup-orientation');
        orGroup.appendChild(orLabel);

        const orSelect = document.createElement('select');
        orSelect.id = 'nup-orientation';
        orSelect.className = 'form-select';
        orSelect.style.width = '100%';
        for (const o of [{ value: 'portrait', label: 'Portrait' }, { value: 'landscape', label: 'Landscape' }]) {
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.textContent = o.label;
            orSelect.appendChild(opt);
        }
        orGroup.appendChild(orSelect);
        grid.appendChild(orGroup);

        settingsCard.appendChild(grid);
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
        outputFile.id = 'nup-output-label';
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

        const nupBtn = document.createElement('button');
        nupBtn.id = 'nup-btn';
        nupBtn.className = 'btn btn-primary';
        nupBtn.textContent = 'Create';
        nupBtn.addEventListener('click', _startNup);
        actionRow.appendChild(nupBtn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('nup');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('N-up cancelled.');
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

Router.register('nup', () => NupPage());
