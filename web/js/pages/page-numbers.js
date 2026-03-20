/* ==========================================================================
   PDF Toolkit - Page Numbers Page
   Add page numbers to PDF files.
   ========================================================================== */

"use strict";

function PageNumbersPage() {
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

        Toast.success('Page numbers added successfully.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred while adding page numbers.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#pagenumbers-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '_numbered.pdf') : 'numbered.pdf';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#pagenumbers-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _startPageNumbers() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output location.'); return; }

        const position    = _el.querySelector('#pagenumbers-position').value;
        const fmt         = _el.querySelector('#pagenumbers-format').value.trim() || '{page}';
        const startNumber = parseInt(_el.querySelector('#pagenumbers-start').value, 10) || 1;
        const fontSize    = parseInt(_el.querySelector('#pagenumbers-font-size').value, 10) || 10;

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startPageNumbers({
            file: _file,
            output_path: _outputPath,
            position: position,
            fmt: fmt,
            start_number: startNumber,
            font_size: fontSize,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Page Numbers',
            subtitle: 'Add page numbers to your PDF',
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
        grid.style.gridTemplateColumns = '1fr 1fr';
        grid.style.gap = 'var(--space-4)';

        // Position
        const posGroup = document.createElement('div');
        const posLabel = document.createElement('label');
        posLabel.className = 'form-label';
        posLabel.textContent = 'Position';
        posLabel.setAttribute('for', 'pagenumbers-position');
        posGroup.appendChild(posLabel);

        const posSelect = document.createElement('select');
        posSelect.id = 'pagenumbers-position';
        posSelect.className = 'form-select';
        posSelect.style.width = '100%';
        const positions = [
            { value: 'bottom-center', label: 'Bottom Center' },
            { value: 'bottom-left',   label: 'Bottom Left' },
            { value: 'bottom-right',  label: 'Bottom Right' },
            { value: 'top-center',    label: 'Top Center' },
            { value: 'top-left',      label: 'Top Left' },
            { value: 'top-right',     label: 'Top Right' },
        ];
        for (const p of positions) {
            const opt = document.createElement('option');
            opt.value = p.value;
            opt.textContent = p.label;
            posSelect.appendChild(opt);
        }
        posGroup.appendChild(posSelect);
        grid.appendChild(posGroup);

        // Format
        const fmtGroup = document.createElement('div');
        const fmtLabel = document.createElement('label');
        fmtLabel.className = 'form-label';
        fmtLabel.textContent = 'Number format';
        fmtLabel.setAttribute('for', 'pagenumbers-format');
        fmtGroup.appendChild(fmtLabel);

        const fmtInput = document.createElement('input');
        fmtInput.type = 'text';
        fmtInput.id = 'pagenumbers-format';
        fmtInput.className = 'form-input';
        fmtInput.style.width = '100%';
        fmtInput.value = '{page}';
        fmtInput.placeholder = '{page} / {total}';
        fmtGroup.appendChild(fmtInput);

        const fmtHelp = document.createElement('div');
        fmtHelp.style.fontSize = 'var(--font-size-xs, 11px)';
        fmtHelp.style.color = 'var(--color-text-3)';
        fmtHelp.style.marginTop = 'var(--space-1)';
        fmtHelp.textContent = 'Use {page} for current page, {total} for total pages';
        fmtGroup.appendChild(fmtHelp);
        grid.appendChild(fmtGroup);

        // Start number
        const startGroup = document.createElement('div');
        const startLabel = document.createElement('label');
        startLabel.className = 'form-label';
        startLabel.textContent = 'Start number';
        startLabel.setAttribute('for', 'pagenumbers-start');
        startGroup.appendChild(startLabel);

        const startInput = document.createElement('input');
        startInput.type = 'number';
        startInput.id = 'pagenumbers-start';
        startInput.className = 'form-input';
        startInput.style.width = '100%';
        startInput.min = 0;
        startInput.value = 1;
        startGroup.appendChild(startInput);
        grid.appendChild(startGroup);

        // Font size
        const fsGroup = document.createElement('div');
        const fsLabel = document.createElement('label');
        fsLabel.className = 'form-label';
        fsLabel.textContent = 'Font size';
        fsLabel.setAttribute('for', 'pagenumbers-font-size');
        fsGroup.appendChild(fsLabel);

        const fsInput = document.createElement('input');
        fsInput.type = 'number';
        fsInput.id = 'pagenumbers-font-size';
        fsInput.className = 'form-input';
        fsInput.style.width = '100%';
        fsInput.min = 4;
        fsInput.max = 72;
        fsInput.value = 10;
        fsGroup.appendChild(fsInput);
        grid.appendChild(fsGroup);

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
        outputFile.id = 'pagenumbers-output-label';
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

        const btn = document.createElement('button');
        btn.id = 'pagenumbers-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Apply';
        btn.addEventListener('click', _startPageNumbers);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('page_numbers');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Page numbering cancelled.');
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

Router.register('page_numbers', () => PageNumbersPage());
