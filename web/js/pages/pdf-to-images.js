/* ==========================================================================
   PDF Toolkit - PDF to Images Page
   Convert PDF pages to image files.
   ========================================================================== */

"use strict";

function PdfToImagesPage() {
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
    let _outputDir = null;

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
            outputDir: _outputDir || data.output_dir || null,
        });

        Toast.success('PDF converted to images successfully.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during PDF to image conversion.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#pdf-to-images-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutputDir() {
        const dir = await BridgeAPI.openFolder();
        if (dir) {
            _outputDir = dir;
            const label = _el ? _el.querySelector('#pdf-to-images-output-label') : null;
            if (label) label.textContent = dir;
        }
    }

    function _updateQualityVisibility() {
        const fmt = _el.querySelector('#pdf-to-images-format').value;
        const qualityGroup = _el.querySelector('#pdf-to-images-quality-group');
        if (qualityGroup) {
            qualityGroup.style.display = fmt === 'jpeg' ? '' : 'none';
        }
    }

    function _startConvert() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputDir) { Toast.warning('Please choose an output folder.'); return; }

        const format    = _el.querySelector('#pdf-to-images-format').value;
        const dpi       = parseInt(_el.querySelector('#pdf-to-images-dpi').value, 10) || 150;
        const quality   = parseInt(_el.querySelector('#pdf-to-images-quality').value, 10) || 85;
        const pageRange = _el.querySelector('#pdf-to-images-page-range').value.trim();

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startPdfToImages({
            file: _file,
            output_dir: _outputDir,
            format: format,
            dpi: dpi,
            quality: quality,
            page_range: pageRange || null,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'PDF to Images',
            subtitle: 'Convert PDF pages to image files',
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

        // Format
        const fmtGroup = document.createElement('div');
        const fmtLabel = document.createElement('label');
        fmtLabel.className = 'form-label';
        fmtLabel.textContent = 'Output format';
        fmtLabel.setAttribute('for', 'pdf-to-images-format');
        fmtGroup.appendChild(fmtLabel);

        const fmtSelect = document.createElement('select');
        fmtSelect.id = 'pdf-to-images-format';
        fmtSelect.className = 'form-select';
        fmtSelect.style.width = '100%';
        for (const fmt of [{ value: 'png', label: 'PNG' }, { value: 'jpeg', label: 'JPEG' }]) {
            const opt = document.createElement('option');
            opt.value = fmt.value;
            opt.textContent = fmt.label;
            fmtSelect.appendChild(opt);
        }
        fmtSelect.addEventListener('change', _updateQualityVisibility);
        fmtGroup.appendChild(fmtSelect);
        grid.appendChild(fmtGroup);

        // DPI
        const dpiGroup = document.createElement('div');
        const dpiLabel = document.createElement('label');
        dpiLabel.className = 'form-label';
        dpiLabel.textContent = 'DPI';
        dpiLabel.setAttribute('for', 'pdf-to-images-dpi');
        dpiGroup.appendChild(dpiLabel);

        const dpiInput = document.createElement('input');
        dpiInput.type = 'number';
        dpiInput.id = 'pdf-to-images-dpi';
        dpiInput.className = 'form-input';
        dpiInput.style.width = '100%';
        dpiInput.min = 72;
        dpiInput.max = 600;
        dpiInput.value = 150;
        dpiGroup.appendChild(dpiInput);
        grid.appendChild(dpiGroup);

        // Quality (JPEG only)
        const qualityGroup = document.createElement('div');
        qualityGroup.id = 'pdf-to-images-quality-group';
        qualityGroup.style.display = 'none';

        const qualityLabel = document.createElement('label');
        qualityLabel.className = 'form-label';
        qualityLabel.textContent = 'JPEG Quality';
        qualityLabel.setAttribute('for', 'pdf-to-images-quality');
        qualityGroup.appendChild(qualityLabel);

        const qualityRow = document.createElement('div');
        qualityRow.style.display = 'flex';
        qualityRow.style.alignItems = 'center';
        qualityRow.style.gap = 'var(--space-2)';

        const qualitySlider = document.createElement('input');
        qualitySlider.type = 'range';
        qualitySlider.id = 'pdf-to-images-quality';
        qualitySlider.min = 1;
        qualitySlider.max = 100;
        qualitySlider.value = 85;
        qualitySlider.style.flex = '1';

        const qualityValue = document.createElement('span');
        qualityValue.style.width = '36px';
        qualityValue.style.textAlign = 'right';
        qualityValue.style.fontSize = 'var(--font-size-sm)';
        qualityValue.textContent = '85';

        qualitySlider.addEventListener('input', () => {
            qualityValue.textContent = qualitySlider.value;
        });

        qualityRow.appendChild(qualitySlider);
        qualityRow.appendChild(qualityValue);
        qualityGroup.appendChild(qualityRow);
        grid.appendChild(qualityGroup);

        settingsCard.appendChild(grid);

        // Page range
        const prGroup = document.createElement('div');
        prGroup.style.marginTop = 'var(--space-4)';

        const prLabel = document.createElement('label');
        prLabel.className = 'form-label';
        prLabel.textContent = 'Page range (optional)';
        prLabel.setAttribute('for', 'pdf-to-images-page-range');
        prGroup.appendChild(prLabel);

        const prInput = document.createElement('input');
        prInput.type = 'text';
        prInput.id = 'pdf-to-images-page-range';
        prInput.className = 'form-input';
        prInput.style.width = '100%';
        prInput.placeholder = 'e.g. 1-5, 8, 10-12 (blank = all pages)';
        prGroup.appendChild(prInput);

        settingsCard.appendChild(prGroup);
        el.appendChild(settingsCard);

        // Output folder picker
        const outputCard = document.createElement('div');
        outputCard.className = 'card';
        outputCard.style.marginTop = 'var(--space-4)';

        const outputRow = document.createElement('div');
        outputRow.style.display = 'flex';
        outputRow.style.alignItems = 'center';
        outputRow.style.gap = 'var(--space-3)';

        const outputLabel = document.createElement('span');
        outputLabel.className = 'form-label';
        outputLabel.textContent = 'Output folder:';
        outputLabel.style.marginBottom = '0';
        outputRow.appendChild(outputLabel);

        const outputFile = document.createElement('span');
        outputFile.id = 'pdf-to-images-output-label';
        outputFile.style.flex = '1';
        outputFile.style.color = 'var(--color-text-2)';
        outputFile.style.overflow = 'hidden';
        outputFile.style.textOverflow = 'ellipsis';
        outputFile.style.whiteSpace = 'nowrap';
        outputFile.textContent = 'No folder selected';
        outputRow.appendChild(outputFile);

        const browseBtn = document.createElement('button');
        browseBtn.className = 'btn btn-secondary btn-sm';
        browseBtn.textContent = 'Browse...';
        browseBtn.addEventListener('click', _pickOutputDir);
        outputRow.appendChild(browseBtn);

        outputCard.appendChild(outputRow);
        el.appendChild(outputCard);

        // Action button
        const actionRow = document.createElement('div');
        actionRow.style.display = 'flex';
        actionRow.style.justifyContent = 'flex-end';
        actionRow.style.marginTop = 'var(--space-4)';

        const btn = document.createElement('button');
        btn.id = 'pdf-to-images-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Convert';
        btn.addEventListener('click', _startConvert);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('pdf_to_images');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Conversion cancelled.');
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

Router.register('pdf_to_images', () => PdfToImagesPage());
