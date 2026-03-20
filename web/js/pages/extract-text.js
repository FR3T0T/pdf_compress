/* ==========================================================================
   PDF Toolkit - Extract Text Page
   Extract text content from a PDF file.
   ========================================================================== */

"use strict";

function ExtractTextPage() {
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

        const pageCount = data.page_count != null ? data.page_count : '?';
        const charCount = data.char_count != null ? data.char_count : '?';

        // Build custom results display
        _showTextResults(data, pageCount, charCount);

        Toast.success('Text extracted: ' + charCount + ' characters from ' + pageCount + ' page(s).');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during text extraction.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#extract-text-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '.txt') : 'extracted.txt';
        const path = await BridgeAPI.saveFile('Text Files (*.txt)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#extract-text-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _showTextResults(data, pageCount, charCount) {
        const container = results.el;
        container.style.display = '';
        container.innerHTML = '';

        const summary = document.createElement('div');
        summary.className = 'results-summary';

        // Page count stat
        const pageStat = document.createElement('div');
        pageStat.className = 'results-stat';
        const pageLabel = document.createElement('div');
        pageLabel.className = 'results-stat-label';
        pageLabel.textContent = 'Pages';
        pageStat.appendChild(pageLabel);
        const pageVal = document.createElement('div');
        pageVal.className = 'results-stat-value';
        pageVal.textContent = String(pageCount);
        pageStat.appendChild(pageVal);
        summary.appendChild(pageStat);

        // Character count stat
        const charStat = document.createElement('div');
        charStat.className = 'results-stat';
        const charLabel = document.createElement('div');
        charLabel.className = 'results-stat-label';
        charLabel.textContent = 'Characters';
        charStat.appendChild(charLabel);
        const charVal = document.createElement('div');
        charVal.className = 'results-stat-value';
        charVal.textContent = String(charCount);
        charStat.appendChild(charVal);
        summary.appendChild(charStat);

        // Time stat
        if (data.elapsed != null) {
            const timeStat = document.createElement('div');
            timeStat.className = 'results-stat';
            const timeLabel = document.createElement('div');
            timeLabel.className = 'results-stat-label';
            timeLabel.textContent = 'Time';
            timeStat.appendChild(timeLabel);
            const timeVal = document.createElement('div');
            timeVal.className = 'results-stat-value';
            timeVal.textContent = data.elapsed < 60
                ? data.elapsed.toFixed(1) + 's'
                : Math.floor(data.elapsed / 60) + 'm ' + Math.round(data.elapsed % 60) + 's';
            timeStat.appendChild(timeVal);
            summary.appendChild(timeStat);
        }

        container.appendChild(summary);

        // Open output file button
        if (_outputPath) {
            const btnRow = document.createElement('div');
            btnRow.style.display = 'flex';
            btnRow.style.justifyContent = 'flex-end';
            btnRow.style.marginTop = 'var(--space-4)';

            const openBtn = document.createElement('button');
            openBtn.className = 'btn btn-primary';
            openBtn.textContent = 'Open Output File';
            openBtn.addEventListener('click', () => BridgeAPI.openFilePath(_outputPath));
            btnRow.appendChild(openBtn);
            container.appendChild(btnRow);
        }
    }

    function _startExtract() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output file.'); return; }

        const pageRange = _el.querySelector('#extract-text-page-range').value.trim();

        _busy = true;
        results.el.style.display = 'none';
        results.el.innerHTML = '';
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startExtractText({
            file: _file,
            output_path: _outputPath,
            page_range: pageRange || null,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Extract Text',
            subtitle: 'Extract text content from a PDF',
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

        const prGroup = document.createElement('div');
        const prLabel = document.createElement('label');
        prLabel.className = 'form-label';
        prLabel.textContent = 'Page range (optional)';
        prLabel.setAttribute('for', 'extract-text-page-range');
        prGroup.appendChild(prLabel);

        const prInput = document.createElement('input');
        prInput.type = 'text';
        prInput.id = 'extract-text-page-range';
        prInput.className = 'form-input';
        prInput.style.width = '100%';
        prInput.placeholder = 'e.g. 1-5, 8, 10-12 (blank = all pages)';
        prGroup.appendChild(prInput);

        settingsCard.appendChild(prGroup);
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
        outputFile.id = 'extract-text-output-label';
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
        btn.id = 'extract-text-btn';
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
            BridgeAPI.cancel('extract_text');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Text extraction cancelled.');
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

Router.register('extract_text', () => ExtractTextPage());
