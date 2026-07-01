/* ==========================================================================
   PDF Toolkit - Analyze Document Page
   Offline privacy & security audit: surfaces trackers, JavaScript, auto-run
   and launch actions, embedded files, hidden data and metadata, then offers
   one-click sanitizing. Everything runs locally — nothing is uploaded.
   ========================================================================== */

"use strict";

function AnalyzePage() {
    let _el = null;
    let _busy = false;
    let _file = null;
    let _report = null;
    let _sanitizeDefaults = null;

    const dropZone = createDropZone({
        title: 'Drop a PDF to analyze',
        subtitle: 'or click to browse — the file never leaves your computer',
        multiple: false,
    });

    /* --- Severity styling --- */
    const SEV = {
        high:   { color: 'var(--color-red)',    label: 'High' },
        medium: { color: 'var(--color-amber)',  label: 'Medium' },
        low:    { color: 'var(--color-accent)', label: 'Low' },
        info:   { color: 'var(--color-green)',  label: 'Info' },
    };

    const SANITIZE_FIELDS = [
        ['javascript',     'Embedded JavaScript'],
        ['launch_actions', 'Launch actions (run external programs)'],
        ['auto_actions',   'Auto-run actions (/OpenAction, /AA)'],
        ['embedded_files', 'Embedded files / attachments'],
        ['submit_actions', 'Form submit / import actions'],
        ['external_links', 'External links / URLs (trackers)'],
        ['metadata',       'Document & XMP metadata'],
    ];

    function _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    /* --- Run the audit --- */
    async function _analyze(path) {
        if (!path) return;
        _busy = true;
        _renderLoading();
        try {
            const res = await BridgeAPI.analyzeDocument(path);
            _busy = false;
            if (!res.success) {
                Toast.error(res.error || 'Analysis failed.');
                _renderEmpty();
                return;
            }
            _report = res.report;
            _renderReport();
        } catch (err) {
            _busy = false;
            console.error('[AnalyzePage] analyze failed:', err);
            Toast.error('Could not analyze the file.');
            _renderEmpty();
        }
    }

    /* --- Rendering --- */
    function _resultsEl() {
        return _el ? _el.querySelector('#analyze-results') : null;
    }

    function _renderEmpty() {
        const r = _resultsEl();
        if (r) r.innerHTML = '';
    }

    function _renderLoading() {
        const r = _resultsEl();
        if (!r) return;
        r.innerHTML =
            '<div class="card" style="margin-top:var(--space-4);text-align:center;color:var(--color-text-2)">' +
            'Scanning document locally…</div>';
    }

    function _riskBanner(report) {
        const overall = report.overallRisk || 'info';
        const sev = SEV[overall] || SEV.info;
        const c = report.counts || {};
        const headline = overall === 'info'
            ? 'No privacy or security concerns detected'
            : sev.label + '-risk items found';

        let chips = '';
        ['high', 'medium', 'low', 'info'].forEach((k) => {
            if (!c[k]) return;
            chips += '<span style="display:inline-flex;align-items:center;gap:6px;' +
                'padding:3px 10px;border-radius:999px;font-size:var(--font-size-sm);' +
                'font-weight:600;color:#fff;background:' + SEV[k].color + '">' +
                c[k] + ' ' + SEV[k].label + '</span> ';
        });

        return '' +
            '<div class="card" style="margin-top:var(--space-4);border-left:4px solid ' + sev.color + '">' +
              '<div style="display:flex;align-items:center;gap:var(--space-3);flex-wrap:wrap">' +
                '<div style="width:40px;height:40px;border-radius:10px;flex:0 0 auto;' +
                  'display:flex;align-items:center;justify-content:center;color:#fff;background:' + sev.color + '">' +
                  getIcon('shield', 22, '#fff') +
                '</div>' +
                '<div style="flex:1;min-width:200px">' +
                  '<div style="font-weight:700;font-size:var(--font-size-md)">' + _esc(headline) + '</div>' +
                  '<div style="color:var(--color-text-2);font-size:var(--font-size-sm)">' +
                    _esc(report.fileName) + ' · ' + _esc(report.fileSizeStr) +
                    ' · ' + (report.pages || 0) + ' page' + (report.pages === 1 ? '' : 's') +
                    (report.pdfVersion ? ' · PDF ' + _esc(report.pdfVersion) : '') +
                    (report.encrypted ? ' · encrypted' : '') +
                  '</div>' +
                '</div>' +
              '</div>' +
              (chips ? '<div style="margin-top:var(--space-3);display:flex;gap:8px;flex-wrap:wrap">' + chips + '</div>' : '') +
            '</div>';
    }

    function _findingCard(f) {
        const sev = SEV[f.severity] || SEV.info;
        let items = '';
        if (f.items && f.items.length) {
            items = '<ul style="margin:var(--space-2) 0 0;padding-left:18px;' +
                'color:var(--color-text-2);font-size:var(--font-size-sm);' +
                'word-break:break-word">';
            f.items.forEach((it) => { items += '<li>' + _esc(it) + '</li>'; });
            items += '</ul>';
        }
        const badge = (f.count && f.count > 1)
            ? '<span style="margin-left:auto;font-size:var(--font-size-sm);' +
              'color:var(--color-text-3)">×' + f.count + '</span>' : '';

        return '' +
            '<div class="card" style="margin-top:var(--space-3)">' +
              '<div style="display:flex;align-items:center;gap:var(--space-3)">' +
                '<span style="width:10px;height:10px;border-radius:50%;flex:0 0 auto;background:' + sev.color + '"></span>' +
                '<span style="font-weight:600">' + _esc(f.title) + '</span>' +
                '<span style="font-size:11px;font-weight:700;letter-spacing:.04em;' +
                  'text-transform:uppercase;color:' + sev.color + '">' + sev.label + '</span>' +
                badge +
              '</div>' +
              (f.detail ? '<div style="margin-top:6px;color:var(--color-text-2);' +
                'font-size:var(--font-size-sm)">' + _esc(f.detail) + '</div>' : '') +
              items +
            '</div>';
    }

    function _renderReport() {
        const r = _resultsEl();
        if (!r || !_report) return;

        // Order findings worst-first
        const order = { high: 0, medium: 1, low: 2, info: 3 };
        const findings = (_report.findings || []).slice().sort(
            (a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));

        let html = _riskBanner(_report);
        findings.forEach((f) => { html += _findingCard(f); });

        // Sanitize panel (only when there is something to clean)
        const hasRisk = (_report.overallRisk || 'info') !== 'info';
        html += _sanitizePanel(hasRisk);

        r.innerHTML = html;
        _wireSanitize();
    }

    function _sanitizePanel(open) {
        let boxes = '';
        SANITIZE_FIELDS.forEach(([key, label]) => {
            const def = _sanitizeDefaults ? !!_sanitizeDefaults[key] : false;
            boxes +=
                '<label style="display:flex;align-items:center;gap:10px;padding:6px 0;cursor:pointer">' +
                  '<input type="checkbox" class="san-opt" data-key="' + key + '"' +
                    (def ? ' checked' : '') + '>' +
                  '<span style="font-size:var(--font-size-sm)">' + _esc(label) + '</span>' +
                '</label>';
        });

        return '' +
            '<div class="card" style="margin-top:var(--space-4)">' +
              '<div style="font-weight:600;font-size:var(--font-size-md);margin-bottom:6px">Sanitize</div>' +
              '<div style="color:var(--color-text-2);font-size:var(--font-size-sm);margin-bottom:var(--space-3)">' +
                'Write a cleaned copy with the selected items removed. Your original file is never modified.' +
              '</div>' +
              '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 var(--space-4)">' + boxes + '</div>' +
              '<div style="display:flex;align-items:center;gap:var(--space-3);margin-top:var(--space-4)">' +
                '<span id="analyze-out-label" style="flex:1;color:var(--color-text-2);' +
                  'font-size:var(--font-size-sm)">No output file selected</span>' +
                '<button class="btn btn-secondary btn-sm" id="analyze-out-btn">Output…</button>' +
                '<button class="btn btn-primary" id="analyze-sanitize-btn">Sanitize</button>' +
              '</div>' +
            '</div>';
    }

    let _outPath = null;

    function _wireSanitize() {
        const outBtn = _el.querySelector('#analyze-out-btn');
        const sanBtn = _el.querySelector('#analyze-sanitize-btn');
        if (outBtn) outBtn.addEventListener('click', _pickOutput);
        if (sanBtn) sanBtn.addEventListener('click', _runSanitize);
    }

    async function _pickOutput() {
        const base = _file ? BridgeAPI.basename(_file).replace(/\.pdf$/i, '') : 'document';
        const path = await BridgeAPI.saveFile('PDF Files (*.pdf)', base + '_clean.pdf');
        if (path) {
            _outPath = path;
            const lbl = _el.querySelector('#analyze-out-label');
            if (lbl) lbl.textContent = BridgeAPI.basename(path);
        }
    }

    async function _runSanitize() {
        if (!_file) { Toast.warning('Add a PDF first.'); return; }
        if (!_outPath) { Toast.warning('Choose an output location.'); return; }

        const opts = {};
        _el.querySelectorAll('.san-opt').forEach((cb) => {
            opts[cb.getAttribute('data-key')] = cb.checked;
        });

        const btn = _el.querySelector('#analyze-sanitize-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Sanitizing…'; }

        try {
            const res = await BridgeAPI.sanitizeDocument(_file, _outPath, opts);
            if (!res.success) {
                Toast.error(res.error || 'Sanitize failed.');
            } else if (res.total_removed === 0) {
                Toast.info('Nothing matched the selected options — clean copy written.');
            } else {
                Toast.success('Removed ' + res.total_removed +
                    ' item' + (res.total_removed === 1 ? '' : 's') + '. Clean copy saved.');
            }
        } catch (err) {
            console.error('[AnalyzePage] sanitize failed:', err);
            Toast.error('Could not sanitize the file.');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Sanitize'; }
        }
    }

    /* --- Lifecycle --- */
    async function onMount(el) {
        _el = el;

        const header = createPageHeader({
            title: 'Analyze Document',
            subtitle: 'Offline privacy & security audit',
        });
        el.appendChild(header.el);

        el.appendChild(dropZone.el);

        const results = document.createElement('div');
        results.id = 'analyze-results';
        el.appendChild(results);

        dropZone.onFilesChanged((files) => {
            _file = files.length > 0 ? files[0].path : null;
            _outPath = null;
            if (_file) _analyze(_file);
            else _renderEmpty();
        });

        // Load sanitize defaults once
        try {
            _sanitizeDefaults = await BridgeAPI.getSanitizeDefaults();
        } catch (e) {
            _sanitizeDefaults = {
                javascript: true, launch_actions: true, auto_actions: true,
                embedded_files: true, submit_actions: true,
                external_links: false, metadata: false,
            };
        }
    }

    function onActivated() {}
    function onDeactivated() {}
    function isBusy() { return _busy; }

    function handleDrop(files) {
        if (files && files.length > 0) {
            dropZone.setFiles([files[0]]);
        }
    }

    return { onMount, onActivated, onDeactivated, isBusy, handleDrop };
}

Router.register('analyze', () => AnalyzePage());
