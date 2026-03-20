/* ==========================================================================
   PDF Toolkit - Extract Images Page
   Extract all images from a PDF file.
   ========================================================================== */

"use strict";

function ExtractImagesPage() {
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

        const imageCount = data.image_count != null ? data.image_count : (data.files ? data.files.length : 0);

        results.show({
            files: data.files || [],
            totalTime: data.elapsed || 0,
            outputDir: _outputDir || data.output_dir || null,
        });

        Toast.success('Extracted ' + imageCount + ' image(s) from PDF.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during image extraction.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#extract-images-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutputDir() {
        const dir = await BridgeAPI.openFolder();
        if (dir) {
            _outputDir = dir;
            const label = _el ? _el.querySelector('#extract-images-output-label') : null;
            if (label) label.textContent = dir;
        }
    }

    function _startExtract() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputDir) { Toast.warning('Please choose an output folder.'); return; }

        const format  = _el.querySelector('#extract-images-format').value;
        const minSize = parseInt(_el.querySelector('#extract-images-min-size').value, 10) || 0;

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startExtractImages({
            file: _file,
            output_dir: _outputDir,
            format: format,
            min_size: minSize,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Extract Images',
            subtitle: 'Extract all images from a PDF file',
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

        // Format
        const fmtGroup = document.createElement('div');
        const fmtLabel = document.createElement('label');
        fmtLabel.className = 'form-label';
        fmtLabel.textContent = 'Output format';
        fmtLabel.setAttribute('for', 'extract-images-format');
        fmtGroup.appendChild(fmtLabel);

        const fmtSelect = document.createElement('select');
        fmtSelect.id = 'extract-images-format';
        fmtSelect.className = 'form-select';
        fmtSelect.style.width = '100%';
        for (const fmt of ['PNG', 'JPEG']) {
            const opt = document.createElement('option');
            opt.value = fmt.toLowerCase();
            opt.textContent = fmt;
            fmtSelect.appendChild(opt);
        }
        fmtGroup.appendChild(fmtSelect);
        grid.appendChild(fmtGroup);

        // Min size
        const minGroup = document.createElement('div');
        const minLabel = document.createElement('label');
        minLabel.className = 'form-label';
        minLabel.textContent = 'Minimum size (pixels)';
        minLabel.setAttribute('for', 'extract-images-min-size');
        minGroup.appendChild(minLabel);

        const minInput = document.createElement('input');
        minInput.type = 'number';
        minInput.id = 'extract-images-min-size';
        minInput.className = 'form-input';
        minInput.style.width = '100%';
        minInput.min = 0;
        minInput.value = 0;
        minInput.placeholder = '0 (no filter)';
        minGroup.appendChild(minInput);

        const minHelp = document.createElement('div');
        minHelp.style.fontSize = 'var(--font-size-xs, 11px)';
        minHelp.style.color = 'var(--color-text-3)';
        minHelp.style.marginTop = 'var(--space-1)';
        minHelp.textContent = 'Skip images smaller than this width or height';
        minGroup.appendChild(minHelp);
        grid.appendChild(minGroup);

        settingsCard.appendChild(grid);
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
        outputFile.id = 'extract-images-output-label';
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
        btn.id = 'extract-images-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Extract';
        btn.addEventListener('click', _startExtract);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('extract_images');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Image extraction cancelled.');
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

Router.register('extract_images', () => ExtractImagesPage());
