/* ==========================================================================
   PDF Toolkit - Images to PDF Page
   Convert image files to a single PDF document.
   ========================================================================== */

"use strict";

function ImagesToPdfPage() {
    let _el = null;
    let _busy = false;

    /* --- Components --- */
    const dropZone = createDropZone({
        title: 'Drop image files here',
        subtitle: 'or click to browse',
        accept: 'Images (*.png *.jpg *.jpeg *.tiff *.bmp *.gif)',
        multiple: true,
    });

    const fileList = createFileList({
        showPages: false,
        reorderable: true,
        emptyMessage: 'No images added yet.',
    });

    const progress = createProgressPanel();
    const results  = createResultsPanel();

    /* --- State --- */
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

        Toast.success('PDF created from images successfully.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred while creating PDF from images.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#images-to-pdf-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', 'images.pdf');
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#images-to-pdf-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _startConvert() {
        const files = fileList.getFiles();
        if (files.length === 0) { Toast.warning('Please add at least one image file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output file.'); return; }

        const pageSize = _el.querySelector('#images-to-pdf-page-size').value;
        const margin   = parseInt(_el.querySelector('#images-to-pdf-margin').value, 10) || 10;

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startImagesToPdf({
            imagePaths: files.map(f => f.path),
            outputPath: _outputPath,
            pageSize: pageSize,
            marginMm: margin,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Images to PDF',
            subtitle: 'Convert image files into a PDF document',
        });
        el.appendChild(header.el);

        // Drop zone
        el.appendChild(dropZone.el);

        dropZone.onFilesChanged((files) => {
            fileList.clear();
            if (files.length > 0) {
                fileList.addFiles(files);
            }
        });

        // File list
        fileList.el.style.marginTop = 'var(--space-4)';
        el.appendChild(fileList.el);

        // Settings card
        const settingsCard = document.createElement('div');
        settingsCard.className = 'card';
        settingsCard.style.marginTop = 'var(--space-4)';

        const grid = document.createElement('div');
        grid.style.display = 'grid';
        grid.style.gridTemplateColumns = '1fr 1fr';
        grid.style.gap = 'var(--space-4)';

        // Page size
        const psGroup = document.createElement('div');
        const psLabel = document.createElement('label');
        psLabel.className = 'form-label';
        psLabel.textContent = 'Page size';
        psLabel.setAttribute('for', 'images-to-pdf-page-size');
        psGroup.appendChild(psLabel);

        const psSelect = document.createElement('select');
        psSelect.id = 'images-to-pdf-page-size';
        psSelect.className = 'form-select';
        psSelect.style.width = '100%';
        for (const size of [
            { value: 'auto',   label: 'Auto (fit to image)' },
            { value: 'A4',     label: 'A4' },
            { value: 'Letter', label: 'Letter' },
        ]) {
            const opt = document.createElement('option');
            opt.value = size.value;
            opt.textContent = size.label;
            psSelect.appendChild(opt);
        }
        psGroup.appendChild(psSelect);
        grid.appendChild(psGroup);

        // Margin
        const mGroup = document.createElement('div');
        const mLabel = document.createElement('label');
        mLabel.className = 'form-label';
        mLabel.textContent = 'Margin (mm)';
        mLabel.setAttribute('for', 'images-to-pdf-margin');
        mGroup.appendChild(mLabel);

        const mInput = document.createElement('input');
        mInput.type = 'number';
        mInput.id = 'images-to-pdf-margin';
        mInput.className = 'form-input';
        mInput.style.width = '100%';
        mInput.min = 0;
        mInput.max = 100;
        mInput.value = 10;
        mGroup.appendChild(mInput);
        grid.appendChild(mGroup);

        settingsCard.appendChild(grid);
        el.appendChild(settingsCard);

        // Output file picker
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
        outputFile.id = 'images-to-pdf-output-label';
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
        btn.id = 'images-to-pdf-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Create PDF';
        btn.addEventListener('click', _startConvert);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('images_to_pdf');
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
            dropZone.setFiles(files);
        }
    }

    return { onMount, onActivated, onDeactivated, isBusy, handleDrop };
}

Router.register('images_to_pdf', () => ImagesToPdfPage());
