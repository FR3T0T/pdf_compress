/* ==========================================================================
   PDF Toolkit - Compare Page
   Compare two PDF files side by side.
   ========================================================================== */

"use strict";

function ComparePage() {
    let _el = null;
    let _busy = false;

    /* --- Components --- */
    const dropZoneA = createDropZone({
        title: 'Drop PDF A here',
        subtitle: 'or click to browse',
        multiple: false,
        compact: true,
    });

    const dropZoneB = createDropZone({
        title: 'Drop PDF B here',
        subtitle: 'or click to browse',
        multiple: false,
        compact: true,
    });

    const progress = createProgressPanel();

    /* --- State --- */
    let _fileA = null;
    let _fileB = null;

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

        _showResults(data);
        Toast.success('Comparison complete.');
    }

    function _onError(data) {
        if (!_busy) return;
        _busy = false;
        progress.hide();
        _enableControls(true);
        Toast.error(data.message || 'An error occurred during comparison.');
    }

    /* --- Helpers --- */
    function _enableControls(enabled) {
        const btn = _el ? _el.querySelector('#compare-btn') : null;
        if (btn) btn.disabled = !enabled;
    }

    function _showResults(data) {
        const container = _el.querySelector('#compare-results');
        if (!container) return;
        container.style.display = '';
        container.innerHTML = '';

        // Metadata differences table
        const metaDiffs = data.metadata_diff || data.metadata_differences || [];
        const pageDiffs = data.page_diff || data.page_differences || [];
        const identical = data.identical || false;

        // Summary
        const summary = document.createElement('div');
        summary.className = 'results-summary';

        const statusStat = document.createElement('div');
        statusStat.className = 'results-stat';
        const statusLabel = document.createElement('div');
        statusLabel.className = 'results-stat-label';
        statusLabel.textContent = 'Status';
        statusStat.appendChild(statusLabel);
        const statusVal = document.createElement('div');
        statusVal.className = 'results-stat-value';
        statusVal.textContent = identical ? 'Identical' : 'Differences Found';
        statusVal.style.color = identical ? 'var(--color-success, #22c55e)' : 'var(--color-warning, #f59e0b)';
        statusStat.appendChild(statusVal);
        summary.appendChild(statusStat);

        if (data.page_count_a != null) {
            const paStat = document.createElement('div');
            paStat.className = 'results-stat';
            const paLabel = document.createElement('div');
            paLabel.className = 'results-stat-label';
            paLabel.textContent = 'PDF A Pages';
            paStat.appendChild(paLabel);
            const paVal = document.createElement('div');
            paVal.className = 'results-stat-value';
            paVal.textContent = String(data.page_count_a);
            paStat.appendChild(paVal);
            summary.appendChild(paStat);
        }

        if (data.page_count_b != null) {
            const pbStat = document.createElement('div');
            pbStat.className = 'results-stat';
            const pbLabel = document.createElement('div');
            pbLabel.className = 'results-stat-label';
            pbLabel.textContent = 'PDF B Pages';
            pbStat.appendChild(pbLabel);
            const pbVal = document.createElement('div');
            pbVal.className = 'results-stat-value';
            pbVal.textContent = String(data.page_count_b);
            pbStat.appendChild(pbVal);
            summary.appendChild(pbStat);
        }

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

        // Page differences table
        if (pageDiffs.length > 0) {
            const section = document.createElement('div');
            section.style.marginTop = 'var(--space-4)';

            const sTitle = document.createElement('div');
            sTitle.style.fontWeight = '600';
            sTitle.style.marginBottom = 'var(--space-2)';
            sTitle.textContent = 'Page Differences';
            section.appendChild(sTitle);

            const table = _buildTable(
                ['Page', 'Type', 'Details'],
                pageDiffs.map(d => [
                    d.page != null ? String(d.page) : '--',
                    d.type || d.kind || '--',
                    d.detail || d.description || '--',
                ])
            );
            section.appendChild(table);
            container.appendChild(section);
        }

        // Metadata differences table
        if (metaDiffs.length > 0) {
            const section = document.createElement('div');
            section.style.marginTop = 'var(--space-4)';

            const sTitle = document.createElement('div');
            sTitle.style.fontWeight = '600';
            sTitle.style.marginBottom = 'var(--space-2)';
            sTitle.textContent = 'Metadata Differences';
            section.appendChild(sTitle);

            const table = _buildTable(
                ['Field', 'PDF A', 'PDF B'],
                metaDiffs.map(d => [
                    d.field || d.key || '--',
                    d.value_a != null ? String(d.value_a) : '--',
                    d.value_b != null ? String(d.value_b) : '--',
                ])
            );
            section.appendChild(table);
            container.appendChild(section);
        }

        if (identical && pageDiffs.length === 0 && metaDiffs.length === 0) {
            const noChange = document.createElement('div');
            noChange.style.marginTop = 'var(--space-4)';
            noChange.style.color = 'var(--color-text-2)';
            noChange.style.textAlign = 'center';
            noChange.style.padding = 'var(--space-6)';
            noChange.textContent = 'The two PDF files are identical.';
            container.appendChild(noChange);
        }
    }

    function _buildTable(headers, rows) {
        const table = document.createElement('table');
        table.style.width = '100%';
        table.style.borderCollapse = 'collapse';
        table.style.fontSize = 'var(--font-size-sm)';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        for (const h of headers) {
            const th = document.createElement('th');
            th.textContent = h;
            th.style.textAlign = 'left';
            th.style.padding = 'var(--space-2) var(--space-3)';
            th.style.borderBottom = '2px solid var(--color-border)';
            th.style.fontWeight = '600';
            th.style.color = 'var(--color-text-2)';
            headerRow.appendChild(th);
        }
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        for (const row of rows) {
            const tr = document.createElement('tr');
            for (const cell of row) {
                const td = document.createElement('td');
                td.textContent = cell;
                td.style.padding = 'var(--space-2) var(--space-3)';
                td.style.borderBottom = '1px solid var(--color-border)';
                td.style.color = 'var(--color-text)';
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }
        table.appendChild(tbody);

        return table;
    }

    function _startCompare() {
        if (!_fileA) { Toast.warning('Please add PDF A.'); return; }
        if (!_fileB) { Toast.warning('Please add PDF B.'); return; }

        _busy = true;
        const resultsEl = _el.querySelector('#compare-results');
        if (resultsEl) { resultsEl.style.display = 'none'; resultsEl.innerHTML = ''; }
        progress.reset();
        progress.show();
        _enableControls(false);

        BridgeAPI.startCompare({
            file_a: _fileA,
            file_b: _fileB,
        });
    }

    /* --- Lifecycle --- */
    function onMount(el) {
        _el = el;

        // Header
        const header = createPageHeader({
            title: 'Compare PDFs',
            subtitle: 'Compare two PDF files for differences',
        });
        el.appendChild(header.el);

        // Two drop zones side by side
        const dropRow = document.createElement('div');
        dropRow.style.display = 'grid';
        dropRow.style.gridTemplateColumns = '1fr 1fr';
        dropRow.style.gap = 'var(--space-4)';

        // PDF A column
        const colA = document.createElement('div');
        const labelA = document.createElement('div');
        labelA.className = 'form-label';
        labelA.textContent = 'PDF A';
        labelA.style.marginBottom = 'var(--space-2)';
        colA.appendChild(labelA);
        colA.appendChild(dropZoneA.el);
        dropRow.appendChild(colA);

        // PDF B column
        const colB = document.createElement('div');
        const labelB = document.createElement('div');
        labelB.className = 'form-label';
        labelB.textContent = 'PDF B';
        labelB.style.marginBottom = 'var(--space-2)';
        colB.appendChild(labelB);
        colB.appendChild(dropZoneB.el);
        dropRow.appendChild(colB);

        el.appendChild(dropRow);

        dropZoneA.onFilesChanged((files) => {
            _fileA = files.length > 0 ? files[0].path : null;
        });

        dropZoneB.onFilesChanged((files) => {
            _fileB = files.length > 0 ? files[0].path : null;
        });

        // Action button
        const actionRow = document.createElement('div');
        actionRow.style.display = 'flex';
        actionRow.style.justifyContent = 'flex-end';
        actionRow.style.marginTop = 'var(--space-4)';

        const btn = document.createElement('button');
        btn.id = 'compare-btn';
        btn.className = 'btn btn-primary';
        btn.textContent = 'Compare';
        btn.addEventListener('click', _startCompare);
        actionRow.appendChild(btn);
        el.appendChild(actionRow);

        // Progress
        progress.el.style.marginTop = 'var(--space-4)';
        el.appendChild(progress.el);

        // Results container
        const resultsEl = document.createElement('div');
        resultsEl.id = 'compare-results';
        resultsEl.className = 'card';
        resultsEl.style.marginTop = 'var(--space-4)';
        resultsEl.style.display = 'none';
        el.appendChild(resultsEl);

        progress.onCancel(() => {
            BridgeAPI.cancel('compare');
            _busy = false;
            progress.hide();
            _enableControls(true);
            Toast.info('Comparison cancelled.');
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
            if (!_fileA) {
                dropZoneA.setFiles([files[0]]);
            } else if (!_fileB) {
                dropZoneB.setFiles([files[0]]);
            }
        }
    }

    return { onMount, onActivated, onDeactivated, isBusy, handleDrop };
}

Router.register('compare', () => ComparePage());
