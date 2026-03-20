/* ==========================================================================
   PDF Toolkit - Redact Page
   Redact sensitive text from PDF files.
   ========================================================================== */

"use strict";

function RedactPage() {
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

        const count = data.redaction_count != null ? data.redaction_count : '?';
        const pages = data.pages_affected != null ? data.pages_affected : '?';
        Toast.success('Redaction complete: ' + count + ' matches redacted across ' + pages + ' page(s).');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during redaction.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#redact-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '_redacted.pdf') : 'redacted.pdf';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#redact-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    function _startRedact() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }

        const textarea = _el.querySelector('#redact-terms');
        const raw = textarea ? textarea.value.trim() : '';
        if (!raw) { Toast.warning('Please enter at least one search term.'); return; }

        const searchTerms = raw.split('\n').map(s => s.trim()).filter(Boolean);
        if (searchTerms.length === 0) { Toast.warning('Please enter at least one search term.'); return; }

        if (!_outputPath) { Toast.warning('Please choose an output location.'); return; }

        const caseSensitive = _el.querySelector('#redact-case-sensitive');

        // Show confirmation modal
        const modal = createModal({ title: 'Confirm Redaction' });
        const msg = document.createElement('div');
        msg.style.lineHeight = '1.6';
        const strong = document.createElement('strong');
        strong.textContent = 'This action is irreversible.';
        msg.appendChild(strong);
        msg.appendChild(document.createElement('br'));
        msg.appendChild(document.createTextNode(searchTerms.length + ' search term(s) will be permanently redacted from the document.'));
        msg.appendChild(document.createElement('br'));
        msg.appendChild(document.createElement('br'));
        msg.appendChild(document.createTextNode('Are you sure you want to continue?'));
        modal.setContentEl(msg);

        modal.onConfirm(() => {
            _busy = true;
            results.hide();
            progress.reset();
            progress.show();
            _enableControls(false);

            BridgeAPI.startRedact({
                file: _file,
                output_path: _outputPath,
                search_terms: searchTerms,
                case_sensitive: caseSensitive ? caseSensitive.checked : false,
            });
        });

        modal.show();
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Redact',
            subtitle: 'Permanently remove sensitive text from PDF',
        });
        el.appendChild(header.el);

        // Warning banner
        const warning = document.createElement('div');
        warning.className = 'card';
        warning.style.background = 'var(--color-warning-bg, #fff3cd)';
        warning.style.border = '1px solid var(--color-warning-border, #ffc107)';
        warning.style.color = 'var(--color-warning-text, #856404)';
        warning.style.padding = 'var(--space-4)';
        warning.style.marginBottom = 'var(--space-4)';
        warning.style.borderRadius = 'var(--radius-md, 8px)';
        warning.style.display = 'flex';
        warning.style.alignItems = 'center';
        warning.style.gap = 'var(--space-3)';

        const warnIcon = document.createElement('span');
        warnIcon.style.fontSize = '1.25rem';
        warnIcon.textContent = '\u26A0\uFE0F';
        warning.appendChild(warnIcon);

        const warnText = document.createElement('span');
        warnText.style.fontWeight = '600';
        warnText.textContent = 'Redaction is irreversible. Redacted content cannot be recovered.';
        warning.appendChild(warnText);
        el.appendChild(warning);

        // Drop zone
        el.appendChild(dropZone.el);

        dropZone.onFilesChanged((files) => {
            _file = files.length > 0 ? files[0].path : null;
        });

        // Settings card
        const settingsCard = document.createElement('div');
        settingsCard.className = 'card';
        settingsCard.style.marginTop = 'var(--space-4)';

        // Search terms label
        const termsLabel = document.createElement('label');
        termsLabel.className = 'form-label';
        termsLabel.textContent = 'Search terms (one per line)';
        termsLabel.setAttribute('for', 'redact-terms');
        settingsCard.appendChild(termsLabel);

        // Textarea
        const textarea = document.createElement('textarea');
        textarea.id = 'redact-terms';
        textarea.className = 'form-textarea';
        textarea.rows = 6;
        textarea.placeholder = 'Enter text to redact, one term per line\ne.g.\nJohn Doe\n555-0123\nSSN: 123-45-6789';
        textarea.style.width = '100%';
        textarea.style.resize = 'vertical';
        textarea.style.fontFamily = 'var(--font-mono, monospace)';
        textarea.style.fontSize = 'var(--font-size-sm)';
        textarea.style.padding = 'var(--space-3)';
        textarea.style.borderRadius = 'var(--radius-md)';
        textarea.style.border = '1px solid var(--color-border)';
        textarea.style.background = 'var(--color-surface)';
        textarea.style.color = 'var(--color-text)';
        settingsCard.appendChild(textarea);

        // Case sensitive checkbox row
        const checkRow = document.createElement('div');
        checkRow.style.display = 'flex';
        checkRow.style.alignItems = 'center';
        checkRow.style.gap = 'var(--space-2)';
        checkRow.style.marginTop = 'var(--space-3)';

        const checkBox = document.createElement('input');
        checkBox.type = 'checkbox';
        checkBox.id = 'redact-case-sensitive';

        const checkLabel = document.createElement('label');
        checkLabel.setAttribute('for', 'redact-case-sensitive');
        checkLabel.textContent = 'Case sensitive';
        checkLabel.style.cursor = 'pointer';

        checkRow.appendChild(checkBox);
        checkRow.appendChild(checkLabel);
        settingsCard.appendChild(checkRow);

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
        outputRow.appendChild(outputLabel);

        const outputFile = document.createElement('span');
        outputFile.id = 'redact-output-label';
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

        const redactBtn = document.createElement('button');
        redactBtn.id = 'redact-btn';
        redactBtn.className = 'btn btn-primary';
        redactBtn.textContent = 'Redact';
        redactBtn.addEventListener('click', _startRedact);
        actionRow.appendChild(redactBtn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('redact');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Redaction cancelled.');
        });

        // Wire EventBus
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

Router.register('redact', () => RedactPage());
