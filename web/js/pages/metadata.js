/* ==========================================================================
   PDF Toolkit - Metadata Page
   View and edit PDF metadata fields.
   ========================================================================== */

"use strict";

function MetadataPage() {
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

    /* --- Metadata field definitions --- */
    const FIELDS = [
        { key: 'title',    label: 'Title' },
        { key: 'author',   label: 'Author' },
        { key: 'subject',  label: 'Subject' },
        { key: 'keywords', label: 'Keywords' },
        { key: 'creator',  label: 'Creator' },
        { key: 'producer', label: 'Producer' },
    ];

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

        Toast.success('Metadata saved successfully.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred while saving metadata.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#metadata-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    async function _pickOutput() {
        const defaultName = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '_metadata.pdf') : 'metadata.pdf';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', defaultName);
        if (path) {
            _outputPath = path;
            const label = _el ? _el.querySelector('#metadata-output-label') : null;
            if (label) label.textContent = BridgeAPI.basename(path);
        }
    }

    async function _loadMetadata(filePath) {
        if (!_el) return;
        const fieldsCard = _el.querySelector('#metadata-fields-card');
        if (fieldsCard) fieldsCard.style.display = '';

        try {
            const meta = await BridgeAPI.getMetadata(filePath);
            if (!meta) return;

            for (const f of FIELDS) {
                const input = _el.querySelector('#metadata-' + f.key);
                if (input && meta[f.key] != null) {
                    input.value = meta[f.key];
                }
            }

            Toast.info('Metadata loaded from file.');
        } catch (err) {
            console.error('[MetadataPage] Failed to load metadata:', err);
            Toast.warning('Could not read metadata from file.');
        }
    }

    function _startWriteMetadata() {
        if (!_file) { Toast.warning('Please add a PDF file.'); return; }
        if (!_outputPath) { Toast.warning('Please choose an output location.'); return; }

        const fields = {};
        for (const f of FIELDS) {
            const input = _el.querySelector('#metadata-' + f.key);
            fields[f.key] = input ? input.value : '';
        }

        _busy = true;
        results.hide();
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startWriteMetadata({
            inputPath: _file,
            outputPath: _outputPath,
            fields: fields,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Metadata',
            subtitle: 'View and edit PDF metadata',
        });
        el.appendChild(header.el);

        // Drop zone
        el.appendChild(dropZone.el);

        dropZone.onFilesChanged((files) => {
            _file = files.length > 0 ? files[0].path : null;
            if (_file) {
                _loadMetadata(_file);
            }
        });

        // Metadata fields card
        const fieldsCard = document.createElement('div');
        fieldsCard.className = 'card';
        fieldsCard.id = 'metadata-fields-card';
        fieldsCard.style.marginTop = 'var(--space-4)';
        fieldsCard.style.display = 'none';

        const fieldsTitle = document.createElement('div');
        fieldsTitle.className = 'form-label';
        fieldsTitle.textContent = 'Document Metadata';
        fieldsTitle.style.marginBottom = 'var(--space-4)';
        fieldsTitle.style.fontSize = 'var(--font-size-md)';
        fieldsTitle.style.fontWeight = '600';
        fieldsCard.appendChild(fieldsTitle);

        const fieldsGrid = document.createElement('div');
        fieldsGrid.style.display = 'grid';
        fieldsGrid.style.gridTemplateColumns = '1fr 1fr';
        fieldsGrid.style.gap = 'var(--space-4)';

        for (const f of FIELDS) {
            const group = document.createElement('div');

            const lbl = document.createElement('label');
            lbl.className = 'form-label';
            lbl.textContent = f.label;
            lbl.setAttribute('for', 'metadata-' + f.key);
            group.appendChild(lbl);

            const input = document.createElement('input');
            input.type = 'text';
            input.id = 'metadata-' + f.key;
            input.className = 'form-input';
            input.style.width = '100%';
            input.placeholder = f.label;
            group.appendChild(input);

            fieldsGrid.appendChild(group);
        }

        fieldsCard.appendChild(fieldsGrid);
        el.appendChild(fieldsCard);

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
        outputFile.id = 'metadata-output-label';
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
        btn.id = 'metadata-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Save';
        btn.addEventListener('click', _startWriteMetadata);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        results.el.style.marginTop = 'var(--space-4)';
        el.appendChild(results.el);

        progress.onCancel(() => {
            BridgeAPI.cancel('metadata');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Metadata save cancelled.');
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

Router.register('metadata', () => MetadataPage());
