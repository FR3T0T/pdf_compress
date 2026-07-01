/* ==========================================================================
   PDF Toolkit - Translate Page
   Offline translation of PDF text and text inside images/photos/scans.
   Translation runs locally via Argos models; image text is read with
   Tesseract OCR. Nothing is uploaded. Provision models with
   setup_translation.py (the one online step).
   ========================================================================== */

"use strict";

function TranslatePage() {
    let _el = null;
    let _busy = false;
    let _file = null;
    let _outputPath = null;
    let _status = null;

    const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'tiff', 'tif', 'bmp', 'gif', 'webp'];

    const dropZone = createDropZone({
        icon: '\uD83C\uDF10',
        title: 'Drop a PDF or an image',
        subtitle: 'PDF, PNG, JPG, TIFF… — files never leave your computer.',
        accept: 'PDF & Images (*.pdf *.png *.jpg *.jpeg *.tiff *.bmp *.gif)',
        multiple: false,
    });

    const progress = createProgressPanel();

    function _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function _ext(path) {
        const m = /\.([a-z0-9]+)$/i.exec(path || '');
        return m ? m[1].toLowerCase() : '';
    }
    function _isImage(path) { return IMAGE_EXT.indexOf(_ext(path)) !== -1; }
    function _isPdf(path) { return _ext(path) === 'pdf'; }

    /* --- Language <select> helpers --- */
    function _sourceOptions() {
        let html = '<option value="auto">Auto-detect</option>';
        (_status?.languages || []).forEach((l) => {
            html += '<option value="' + l.code + '">' +
                _esc(l.name) + (l.native && l.native !== l.name ? ' (' + _esc(l.native) + ')' : '') +
                (l.translateFrom === false ? ' — not installed' : '') +
                '</option>';
        });
        return html;
    }
    function _targetOptions() {
        let html = '';
        (_status?.languages || []).forEach((l) => {
            const sel = l.code === 'en' ? ' selected' : '';
            html += '<option value="' + l.code + '"' + sel + '>' +
                _esc(l.name) + (l.native && l.native !== l.name ? ' (' + _esc(l.native) + ')' : '') +
                (l.translateTo === false ? ' — not installed' : '') +
                '</option>';
        });
        return html;
    }

    function _source() { const s = _el.querySelector('#tr-source'); return s ? s.value : 'auto'; }
    function _target() { const s = _el.querySelector('#tr-target'); return s ? s.value : 'en'; }

    /* --- Status banner --- */
    function _statusBanner() {
        const st = _status || {};
        const ready = st.argosAvailable && (st.argosPairs || []).length > 0;
        const color = ready ? 'var(--color-green)' : 'var(--color-amber)';
        let msg;
        if (ready) {
            const n = (st.languages || []).filter((l) => l.translateTo).length;
            msg = 'Offline translation ready · ' + n + ' language' + (n === 1 ? '' : 's') +
                  ' installed' + (st.ocrAvailable ? ' · OCR available' : ' · OCR not installed');
        } else {
            msg = 'Translation models are not installed yet. Run ' +
                  '<code>python setup_translation.py --install all</code> once ' +
                  '(the only online step), then translation works fully offline.';
        }
        return '<div class="card" style="margin-top:var(--space-4);border-left:4px solid ' + color + '">' +
                 '<div style="font-size:var(--font-size-sm);color:var(--color-text-2)">' + msg + '</div>' +
               '</div>';
    }

    /* --- Result rendering --- */
    function _resultsEl() { return _el ? _el.querySelector('#tr-results') : null; }
    function _clearResults() { const r = _resultsEl(); if (r) r.innerHTML = ''; }

    function _textPanel(label, text, id) {
        return '<div style="flex:1;min-width:240px">' +
                 '<div style="display:flex;align-items:center;margin-bottom:6px">' +
                   '<span class="form-label" style="margin:0">' + _esc(label) + '</span>' +
                   '<button class="btn btn-secondary btn-sm" data-copy="' + id + '" ' +
                     'style="margin-left:auto">Copy</button>' +
                 '</div>' +
                 '<div id="' + id + '" class="card" style="white-space:pre-wrap;' +
                   'max-height:320px;overflow:auto;font-size:var(--font-size-sm)">' +
                   _esc(text || '') + '</div>' +
               '</div>';
    }

    function _showImageResult(res) {
        const r = _resultsEl();
        if (!r) return;
        if (res.note) { Toast.info(res.note); }
        r.innerHTML =
            '<div style="display:flex;gap:var(--space-4);margin-top:var(--space-4);flex-wrap:wrap">' +
              _textPanel('Detected text (' + _esc(res.source || '?') + ')', res.sourceText, 'tr-src-text') +
              _textPanel('Translation (' + _esc(res.target || '?') + ')', res.translatedText, 'tr-out-text') +
            '</div>';
        _wireCopy();
    }

    function _wireCopy() {
        _el.querySelectorAll('[data-copy]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const node = _el.querySelector('#' + btn.getAttribute('data-copy'));
                if (!node) return;
                const range = document.createRange();
                range.selectNodeContents(node);
                const sel = window.getSelection();
                sel.removeAllRanges(); sel.addRange(range);
                try { document.execCommand('copy'); Toast.success('Copied.'); }
                catch (e) { Toast.info('Select and copy manually.'); }
                sel.removeAllRanges();
            });
        });
    }

    /* --- Image flow (synchronous) --- */
    async function _runImage() {
        _busy = true;
        _clearResults();
        const r = _resultsEl();
        if (r) r.innerHTML = '<div class="card" style="margin-top:var(--space-4);' +
            'text-align:center;color:var(--color-text-2)">Reading & translating…</div>';
        try {
            const res = await BridgeAPI.translateImage(_file, _source(), _target());
            _busy = false;
            if (!res.success) { Toast.error(res.error || 'Translation failed.'); _clearResults(); return; }
            _showImageResult(res);
        } catch (err) {
            _busy = false;
            console.error('[TranslatePage] image translate failed:', err);
            Toast.error('Could not translate the image.');
            _clearResults();
        }
    }

    /* --- PDF flow (async via EventBus) --- */
    function _showPdfControls() {
        const r = _resultsEl();
        if (!r) return;
        r.innerHTML =
            '<div class="card" style="margin-top:var(--space-4)">' +
              '<div style="display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap">' +
                '<span class="form-label" style="margin:0">Save translation as:</span>' +
                '<span id="tr-out-label" style="flex:1;color:var(--color-text-2);' +
                  'font-size:var(--font-size-sm)">No output file selected</span>' +
                '<button class="btn btn-secondary btn-sm" id="tr-out-btn">Output…</button>' +
                '<button class="btn btn-primary" id="tr-go-btn">Translate</button>' +
              '</div>' +
              '<div style="margin-top:8px;color:var(--color-text-3);font-size:var(--font-size-sm)">' +
                'Choose a .txt or .docx file. Text output renders every script correctly ' +
                'using your system fonts.</div>' +
            '</div>';
        _el.querySelector('#tr-out-btn').addEventListener('click', _pickOutput);
        _el.querySelector('#tr-go-btn').addEventListener('click', _runPdf);
    }

    async function _pickOutput() {
        const base = _file ? BridgeAPI.basename(_file).replace(/\.[^.]+$/, '') : 'document';
        const path = await BridgeAPI.saveFile(
            'Text (*.txt);;Word Document (*.docx)', base + '_translated.txt');
        if (path) {
            _outputPath = path;
            const lbl = _el.querySelector('#tr-out-label');
            if (lbl) lbl.textContent = BridgeAPI.basename(path);
        }
    }

    function _runPdf() {
        if (!_file) { Toast.warning('Add a file first.'); return; }
        if (!_outputPath) { Toast.warning('Choose an output location.'); return; }

        _busy = true;
        progress.reset();
        progress.show();
        const btn = _el.querySelector('#tr-go-btn');
        if (btn) btn.disabled = true;

        BridgeAPI.startTranslatePdf({
            toolKey: 'translate',
            inputPath: _file,
            outputPath: _outputPath,
            source: _source(),
            target: _target(),
        });
    }

    function _onProgress(data) {
        if (!_busy) return;
        progress.update(data.percent || 0, data.filename || 'Translating…',
                        data.current || 1, data.total || 1);
    }

    function _onDone(data) {
        if (!_busy || data.toolKey !== 'translate') return;
        _busy = false;
        progress.hide();
        const btn = _el.querySelector('#tr-go-btn');
        if (btn) btn.disabled = false;

        if (!data.success) {
            Toast.error(data.message || 'Translation failed.');
            return;
        }
        const res = data.results || {};
        Toast.success('Translated ' + (res.pages || 0) + ' page' +
                      (res.pages === 1 ? '' : 's') + '.');
        const r = _resultsEl();
        if (r && res.output) {
            const done = document.createElement('div');
            done.className = 'card';
            done.style.marginTop = 'var(--space-4)';
            done.innerHTML =
                '<div style="display:flex;align-items:center;gap:var(--space-3)">' +
                  '<span style="flex:1">Saved <strong>' + _esc(BridgeAPI.basename(res.output)) +
                    '</strong> (' + (res.source || '?') + ' → ' + (res.target || '?') + ')</span>' +
                  '<button class="btn btn-secondary btn-sm" id="tr-open">Open</button>' +
                '</div>';
            r.appendChild(done);
            const ob = done.querySelector('#tr-open');
            if (ob) ob.addEventListener('click', () => BridgeAPI.openFilePath(res.output));
        }
    }

    /* --- File dispatch --- */
    function _onFile(path) {
        _file = path;
        _outputPath = null;
        _clearResults();
        if (!path) return;
        if (_isImage(path)) {
            _runImage();
        } else if (_isPdf(path)) {
            _showPdfControls();
        } else {
            Toast.warning('Unsupported file type.');
        }
    }

    /* --- Lifecycle --- */
    async function onMount(el) {
        _el = el;

        const header = createPageHeader({
            title: 'Translate',
            subtitle: 'Offline translation of documents and image text',
        });
        el.appendChild(header.el);

        // Load provisioning status first so the language lists are accurate
        try {
            _status = await BridgeAPI.getTranslationStatus();
        } catch (e) {
            _status = { languages: [], argosAvailable: false, ocrAvailable: false };
        }

        // Status banner
        const banner = document.createElement('div');
        banner.innerHTML = _statusBanner();
        el.appendChild(banner.firstChild);

        // Language controls
        const controls = document.createElement('div');
        controls.className = 'card';
        controls.style.marginTop = 'var(--space-4)';
        controls.innerHTML =
            '<div style="display:flex;gap:var(--space-4);flex-wrap:wrap;align-items:flex-end">' +
              '<div style="flex:1;min-width:180px">' +
                '<label class="form-label" for="tr-source">From</label>' +
                '<select id="tr-source" class="form-input" style="width:100%">' + _sourceOptions() + '</select>' +
              '</div>' +
              '<div style="flex:0 0 auto;padding-bottom:10px;color:var(--color-text-3)">&rarr;</div>' +
              '<div style="flex:1;min-width:180px">' +
                '<label class="form-label" for="tr-target">To</label>' +
                '<select id="tr-target" class="form-input" style="width:100%">' + _targetOptions() + '</select>' +
              '</div>' +
            '</div>';
        el.appendChild(controls);

        // Drop zone
        dropZone.el.style.marginTop = 'var(--space-4)';
        el.appendChild(dropZone.el);
        dropZone.onFilesChanged((files) => {
            _onFile(files.length > 0 ? files[0].path : null);
        });

        // Re-run image translation when languages change (if an image is loaded)
        controls.querySelector('#tr-source').addEventListener('change', _onLangChange);
        controls.querySelector('#tr-target').addEventListener('change', _onLangChange);

        // Progress + results
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);
        const results = document.createElement('div');
        results.id = 'tr-results';
        el.appendChild(results);

        progress.onCancel(() => {
            BridgeAPI.cancel('translate');
            _busy = false;
            progress.hide();
            const btn = _el.querySelector('#tr-go-btn');
            if (btn) btn.disabled = false;
            Toast.info('Translation cancelled.');
        });

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
    }

    function _onLangChange() {
        if (_file && _isImage(_file) && !_busy) _runImage();
    }

    function onActivated() {}
    function onDeactivated() {
        EventBus.off('progress', _onProgress);
        EventBus.off('done', _onDone);
    }
    function isBusy() { return _busy; }
    function handleDrop(files) {
        if (files && files.length > 0) dropZone.setFiles([files[0]]);
    }

    return { onMount, onActivated, onDeactivated, isBusy, handleDrop };
}

Router.register('translate', () => TranslatePage());
