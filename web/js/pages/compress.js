/* ==========================================================================
   PDF Toolkit - Compress Page (Premium)
   Rich file cards with thumbnails, per-file naming, DPI analysis,
   individual estimates, batch operations, and detailed results.
   ========================================================================== */

"use strict";

class CompressPage {

    constructor() {
        this._el = null;

        // Sub-components
        this._dropZone = null;
        this._presetCards = null;
        this._settingsPanel = null;
        this._progressPanel = null;
        this._resultsPanel = null;

        // Custom DOM containers
        this._fileCardsContainer = null;
        this._fileToolbar = null;
        this._estimatePanel = null;
        this._compressBtn = null;
        this._compressBtnText = null;

        // State
        this._files = [];          // rich file objects (see _addFiles)
        this._presets = [];
        this._presetsLoaded = false;
        this._processing = false;
        this._startTime = null;
        this._completedCount = 0;  // rolling completed count during batch

        // Settings inputs
        this._outputDirInput = null;
        this._useGsCheckbox = null;
        this._suffixInput = null;

        // EventBus listener references
        this._onProgress = null;
        this._onDone = null;
        this._onError = null;
    }

    /* ------------------------------------------------------------------
       Lifecycle
       ------------------------------------------------------------------ */

    onMount(el) {
        this._el = el;
        this._build();
        this._bindEvents();
    }

    onActivated() {
        if (!this._onProgress) this._bindEvents();
        if (!this._presetsLoaded) this._loadPresets();
    }

    onDeactivated() {
        if (!this._processing) this._unbindEvents();
    }

    isBusy() { return this._processing; }

    handleDrop(files) {
        if (files && files.length > 0) this._addFiles(files);
    }

    /* ------------------------------------------------------------------
       DOM Construction
       ------------------------------------------------------------------ */

    _build() {
        const el = this._el;

        // -- Header -------------------------------------------------------
        const header = createPageHeader({
            title: 'Compress PDF',
            subtitle: 'Reduce file size while preserving quality',
            backButton: true,
        });
        el.appendChild(header.el);

        // -- Drop zone (becomes compact once files added) -----------------
        this._dropZone = createDropZone({
            icon: '\uD83D\uDDDC\uFE0F',
            title: 'Drop PDF files here',
            subtitle: 'or click to browse \u2014 add as many as you need',
            accept: 'PDF Files (*.pdf)',
            multiple: true,
            compact: false,
        });
        this._dropZone.onFilesChanged((files) => this._onDropZoneFiles(files));
        el.appendChild(this._dropZone.el);

        // -- File toolbar (hidden until files) ----------------------------
        this._fileToolbar = document.createElement('div');
        this._fileToolbar.className = 'compress-toolbar';
        this._fileToolbar.style.display = 'none';
        el.appendChild(this._fileToolbar);

        // -- File cards container -----------------------------------------
        this._fileCardsContainer = document.createElement('div');
        this._fileCardsContainer.className = 'compress-file-cards';
        this._fileCardsContainer.style.display = 'none';
        el.appendChild(this._fileCardsContainer);

        // -- Preset selection ---------------------------------------------
        const sectionLabel2 = this._sectionLabel('2', 'Choose Quality Preset');
        sectionLabel2.style.marginTop = 'var(--space-8)';
        el.appendChild(sectionLabel2);

        this._presetContainer = document.createElement('div');
        this._presetContainer.className = 'preset-grid';
        const loadingMsg = document.createElement('div');
        loadingMsg.className = 'empty-state';
        loadingMsg.style.padding = 'var(--space-6)';
        const loadingText = document.createElement('div');
        loadingText.className = 'empty-state-text';
        loadingText.textContent = 'Loading presets...';
        loadingMsg.appendChild(loadingText);
        this._presetContainer.appendChild(loadingMsg);
        el.appendChild(this._presetContainer);

        // -- Advanced settings --------------------------------------------
        this._settingsPanel = createSettingsPanel({ title: 'Advanced Settings', open: false });
        this._settingsPanel.el.style.marginTop = 'var(--space-6)';

        // Output folder
        const outputRow = document.createElement('div');
        outputRow.style.cssText = 'display:flex; align-items:center; gap:var(--space-2); flex:1;';
        const outputDirInput = document.createElement('input');
        outputDirInput.type = 'text';
        outputDirInput.className = 'input';
        outputDirInput.placeholder = 'Same as source';
        outputDirInput.readOnly = true;
        outputDirInput.style.flex = '1';
        this._outputDirInput = outputDirInput;
        outputRow.appendChild(outputDirInput);
        const browseBtn = document.createElement('button');
        browseBtn.className = 'btn btn-secondary btn-sm';
        browseBtn.textContent = 'Browse';
        browseBtn.addEventListener('click', async () => {
            try {
                const folder = await BridgeAPI.openFolder();
                if (folder) outputDirInput.value = folder;
            } catch (err) { console.error('[CompressPage] openFolder error:', err); }
        });
        outputRow.appendChild(browseBtn);
        this._settingsPanel.addField('output_dir', 'Output Folder', outputRow);

        // Output naming suffix
        const suffixRow = document.createElement('div');
        suffixRow.style.cssText = 'display:flex; align-items:center; gap:var(--space-2); flex:1;';
        const suffixInput = document.createElement('input');
        suffixInput.type = 'text';
        suffixInput.className = 'input';
        suffixInput.placeholder = '_compressed';
        suffixInput.value = '_compressed';
        suffixInput.style.flex = '1';
        suffixInput.style.maxWidth = '200px';
        this._suffixInput = suffixInput;
        suffixInput.addEventListener('input', () => this._applyBatchSuffix());
        suffixRow.appendChild(suffixInput);
        const applyBtn = document.createElement('button');
        applyBtn.className = 'btn btn-secondary btn-sm';
        applyBtn.textContent = 'Apply to All';
        applyBtn.addEventListener('click', () => this._applyBatchSuffix());
        suffixRow.appendChild(applyBtn);
        this._settingsPanel.addField('suffix', 'Output Suffix', suffixRow,
            'Applied to all file names (e.g. report_compressed.pdf)');

        // Ghostscript toggle
        const gsLabel = document.createElement('label');
        gsLabel.className = 'checkbox';
        const gsCheckbox = document.createElement('input');
        gsCheckbox.type = 'checkbox';
        gsCheckbox.checked = false;
        this._useGsCheckbox = gsCheckbox;
        gsLabel.appendChild(gsCheckbox);
        const gsText = document.createElement('span');
        gsText.textContent = 'Use Ghostscript engine';
        gsLabel.appendChild(gsText);
        this._settingsPanel.addField('use_gs', 'Ghostscript', gsLabel,
            'Advanced compression with font subsetting');

        el.appendChild(this._settingsPanel.el);

        // -- Estimate panel -----------------------------------------------
        this._estimatePanel = document.createElement('div');
        this._estimatePanel.className = 'compress-estimate-panel card';
        this._estimatePanel.style.cssText = 'margin-top:var(--space-6); display:none; padding:var(--space-5);';
        el.appendChild(this._estimatePanel);

        // -- Compress button row ------------------------------------------
        const btnRow = document.createElement('div');
        btnRow.className = 'compress-action-row';
        const compressBtn = document.createElement('button');
        compressBtn.className = 'btn btn-primary btn-lg compress-btn';
        compressBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-right:6px;vertical-align:-2px"><line x1="10" y1="3" x2="10" y2="14"/><polyline points="6,10 10,14 14,10"/><line x1="4" y1="17" x2="16" y2="17"/></svg><span>Compress</span>';
        compressBtn.disabled = true;
        compressBtn.addEventListener('click', () => this._startCompress());
        btnRow.appendChild(compressBtn);
        this._compressBtn = compressBtn;
        this._compressBtnText = compressBtn.querySelector('span');
        el.appendChild(btnRow);

        // -- Progress -----------------------------------------------------
        this._progressPanel = createProgressPanel();
        this._progressPanel.el.style.marginTop = 'var(--space-6)';
        this._progressPanel.onCancel(() => this._cancelCompress());
        el.appendChild(this._progressPanel.el);

        // -- Results ------------------------------------------------------
        this._resultsPanel = createResultsPanel();
        el.appendChild(this._resultsPanel.el);
    }

    _sectionLabel(number, text) {
        const wrap = document.createElement('div');
        wrap.className = 'step';
        wrap.style.marginBottom = 'var(--space-4)';
        const numEl = document.createElement('div');
        numEl.className = 'step-number';
        numEl.textContent = number;
        wrap.appendChild(numEl);
        const content = document.createElement('div');
        content.className = 'step-content';
        const title = document.createElement('div');
        title.className = 'step-title';
        title.textContent = text;
        content.appendChild(title);
        wrap.appendChild(content);
        return wrap;
    }

    /* ------------------------------------------------------------------
       Presets
       ------------------------------------------------------------------ */

    async _loadPresets() {
        try {
            const resp = await BridgeAPI.getPresets();
            const presetList = (resp && resp.presets) ? resp.presets : resp;
            this._presets = Array.isArray(presetList) ? presetList : [];
            this._gsAvailable = resp && resp.ghostscriptAvailable;
            this._presetsLoaded = true;

            const iconMap = { screen: '\uD83D\uDCF1', ebook: '\uD83D\uDCDA', standard: '\u2696\uFE0F', high: '\u2B50', prepress: '\uD83D\uDDA8\uFE0F' };
            const detailMap = {
                screen:   'Best for screen viewing and email attachments. Images resampled to 72 DPI.',
                ebook:    'Optimized for e-readers and tablets. Images at 120 DPI.',
                standard: 'Balanced quality and size. 150 DPI \u2014 ideal for most uses.',
                high:     'High quality output at 200 DPI. Good for detailed documents.',
                prepress: 'Professional print quality at 300 DPI. Maximum fidelity.',
            };
            for (const p of this._presets) {
                if (!p.title && p.name) p.title = p.name;
                if (!p.icon) p.icon = iconMap[p.key] || '';
                if (!p.detail) p.detail = detailMap[p.key] || '';
                if (p.key === 'standard' && !p.badge) p.badge = 'Recommended';
            }

            if (this._useGsCheckbox) this._useGsCheckbox.disabled = !this._gsAvailable;
            this._renderPresets();
        } catch (err) {
            console.error('[CompressPage] Failed to load presets:', err);
            this._presets = [
                { key: 'screen', title: 'Screen', description: '72 DPI \u2014 smallest file size', icon: '\uD83D\uDCF1',
                  detail: 'Best for screen viewing and email attachments. Images resampled to 72 DPI.' },
                { key: 'ebook', title: 'E-book', description: '120 DPI \u2014 digital reading', icon: '\uD83D\uDCDA',
                  detail: 'Optimized for e-readers and tablets. Images at 120 DPI.' },
                { key: 'standard', title: 'Standard', description: '150 DPI \u2014 balanced', icon: '\u2696\uFE0F', badge: 'Recommended',
                  detail: 'Balanced quality and size. 150 DPI \u2014 ideal for most uses.' },
                { key: 'high', title: 'High', description: '200 DPI \u2014 detailed output', icon: '\u2B50',
                  detail: 'High quality output at 200 DPI. Good for detailed documents.' },
                { key: 'prepress', title: 'Prepress', description: '300 DPI \u2014 professional print', icon: '\uD83D\uDDA8\uFE0F',
                  detail: 'Professional print quality at 300 DPI. Maximum fidelity.' },
            ];
            this._presetsLoaded = true;
            this._renderPresets();
        }
    }

    _renderPresets() {
        const mapped = this._presets.map(p => ({
            key: p.key,
            icon: p.icon || '',
            title: p.title || p.key,
            description: p.description || '',
            badge: p.badge || null,
            detail: p.detail || '',
        }));
        this._presetCards = createPresetCards(mapped, 'standard');
        this._presetCards.onChange(() => {
            this._renderFileCards();
            this._updateEstimate();
            this._updateCompressBtn();
        });
        const parent = this._presetContainer.parentNode;
        if (parent) {
            parent.replaceChild(this._presetCards.el, this._presetContainer);
            this._presetContainer = this._presetCards.el;
        }
    }

    /* ------------------------------------------------------------------
       File Management
       ------------------------------------------------------------------ */

    _onDropZoneFiles(dropFiles) {
        this._addFiles(dropFiles.map(f => f.path));
    }

    async _addFiles(paths) {
        const existingSet = new Set(this._files.map(f => f.path));
        const newPaths = paths.filter(p => !existingSet.has(p));
        if (newPaths.length === 0) return;

        const suffix = this._suffixInput ? this._suffixInput.value : '_compressed';

        // Create rich file entries
        const entries = newPaths.map(p => {
            const baseName = BridgeAPI.basename(p);
            const nameNoExt = baseName.replace(/\.pdf$/i, '');
            return {
                path: p,
                name: baseName,
                outputName: nameNoExt + suffix,
                size: null,
                pages: null,
                imageCount: 0,
                status: 'analyzing',
                thumbnail: null,
                _analysis: null,
                errorMsg: null,
            };
        });

        this._files = this._files.concat(entries);
        this._renderFileCards();
        this._renderToolbar();
        this._updateCompressBtn();

        // Make drop zone compact once files are added
        if (this._files.length > 0) {
            this._dropZone.el.classList.add('drop-zone-compact');
            this._dropZone.el.style.minHeight = '80px';
        }

        // Analyze and fetch thumbnails concurrently for each new file
        const promises = entries.map(async (entry) => {
            const [info, thumb] = await Promise.allSettled([
                BridgeAPI.analyzeFile(entry.path),
                BridgeAPI.getThumbnail(entry.path),
            ]);

            // Process analysis
            if (info.status === 'fulfilled' && info.value && info.value.success !== false) {
                const data = info.value;
                entry.size = data.file_size != null ? data.file_size : data.fileSize;
                entry.pages = data.page_count != null ? data.page_count : data.pageCount;
                entry.imageCount = data.image_count || data.imageCount || 0;
                entry.status = 'pending';
                entry._analysis = {
                    estimates: data.estimates || null,
                    imageSummary: data.imageSummary || data.image_summary || null,
                };
            } else if (info.status === 'fulfilled' && info.value) {
                entry.status = 'error';
                entry.errorMsg = info.value.encrypted ? 'Password-protected' : (info.value.error || 'Analysis failed');
            } else {
                entry.status = 'error';
                entry.errorMsg = 'Analysis failed';
            }

            // Process thumbnail
            if (thumb.status === 'fulfilled' && thumb.value && thumb.value.success) {
                entry.thumbnail = thumb.value.dataUrl;
            }

            this._renderFileCards();
            this._renderToolbar();
        });

        await Promise.allSettled(promises);
        this._updateEstimate();
        this._updateCompressBtn();
    }

    _removeFile(index) {
        if (index < 0 || index >= this._files.length) return;
        this._files.splice(index, 1);
        this._renderFileCards();
        this._renderToolbar();
        this._updateCompressBtn();
        this._updateEstimate();
        if (this._files.length === 0) {
            this._dropZone.el.classList.remove('drop-zone-compact');
            this._dropZone.el.style.minHeight = '';
        }
    }

    _clearAllFiles() {
        this._files = [];
        this._renderFileCards();
        this._renderToolbar();
        this._updateCompressBtn();
        this._updateEstimate();
        this._dropZone.el.classList.remove('drop-zone-compact');
        this._dropZone.el.style.minHeight = '';
    }

    _applyBatchSuffix() {
        const suffix = this._suffixInput ? this._suffixInput.value : '_compressed';
        for (const f of this._files) {
            const nameNoExt = f.name.replace(/\.pdf$/i, '');
            f.outputName = nameNoExt + suffix;
        }
        this._renderFileCards();
    }

    /** Validate output name for Windows illegal chars */
    _validateOutputName(name) {
        if (!name || name.trim().length === 0) return 'Name cannot be empty';
        if (/[<>:"/\\|?*]/.test(name)) return 'Contains invalid characters';
        // Check duplicates
        const dupes = this._files.filter(f => f.outputName === name);
        if (dupes.length > 1) return 'Duplicate name';
        return null;
    }

    _updateCompressBtn() {
        if (!this._compressBtn) return;
        const validFiles = this._files.filter(f => f.status !== 'error');
        const hasValidation = validFiles.some(f => this._validateOutputName(f.outputName));
        this._compressBtn.disabled = validFiles.length === 0 || this._processing || hasValidation;

        // Update button text with file count
        if (this._compressBtnText) {
            if (validFiles.length > 1) {
                this._compressBtnText.textContent = 'Compress ' + validFiles.length + ' Files';
            } else {
                this._compressBtnText.textContent = 'Compress';
            }
        }
    }

    /* ------------------------------------------------------------------
       File Toolbar (count, add more, clear all)
       ------------------------------------------------------------------ */

    _renderToolbar() {
        const tb = this._fileToolbar;
        if (this._files.length === 0) {
            tb.style.display = 'none';
            return;
        }

        tb.style.display = '';
        tb.innerHTML = '';

        // Left: Step label + file count + error count
        const left = document.createElement('div');
        left.className = 'compress-toolbar-left';
        const stepBadge = document.createElement('span');
        stepBadge.className = 'step-number';
        stepBadge.style.cssText = 'width:22px; height:22px; font-size:var(--font-size-xs);';
        stepBadge.textContent = '1';
        left.appendChild(stepBadge);

        const countText = document.createElement('span');
        countText.className = 'compress-toolbar-count';
        const totalSize = this._files.reduce((s, f) => s + (f.size || 0), 0);
        const errorCount = this._files.filter(f => f.status === 'error').length;
        const analyzingCount = this._files.filter(f => f.status === 'analyzing').length;

        let countStr = this._files.length + ' file' + (this._files.length !== 1 ? 's' : '');
        if (totalSize > 0) countStr += ' \u00B7 ' + BridgeAPI.formatSize(totalSize);
        countText.textContent = countStr;
        left.appendChild(countText);

        // Error badge
        if (errorCount > 0) {
            const errBadge = document.createElement('span');
            errBadge.className = 'badge badge-red';
            errBadge.textContent = errorCount + ' error' + (errorCount !== 1 ? 's' : '');
            left.appendChild(errBadge);
        }
        // Analyzing indicator
        if (analyzingCount > 0) {
            const anBadge = document.createElement('span');
            anBadge.className = 'badge badge-accent';
            anBadge.innerHTML = '<span class="compress-spinner-sm"></span>Analyzing';
            left.appendChild(anBadge);
        }

        tb.appendChild(left);

        // Right: Add more + Clear all
        const right = document.createElement('div');
        right.className = 'compress-toolbar-right';

        const addBtn = document.createElement('button');
        addBtn.className = 'btn btn-secondary btn-sm';
        addBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" style="margin-right:4px;vertical-align:-1px;"><line x1="6" y1="2" x2="6" y2="10"/><line x1="2" y1="6" x2="10" y2="6"/></svg>Add More';
        addBtn.addEventListener('click', async () => {
            try {
                const files = await BridgeAPI.openFiles('PDF Files (*.pdf)');
                if (files && files.length > 0) this._addFiles(files);
            } catch (err) { console.error('[CompressPage] openFiles error:', err); }
        });
        right.appendChild(addBtn);

        const clearBtn = document.createElement('button');
        clearBtn.className = 'btn btn-ghost btn-sm';
        clearBtn.style.color = 'var(--color-red)';
        clearBtn.textContent = 'Clear All';
        clearBtn.addEventListener('click', () => this._clearAllFiles());
        right.appendChild(clearBtn);

        tb.appendChild(right);
    }

    /* ------------------------------------------------------------------
       Rich File Cards
       ------------------------------------------------------------------ */

    _renderFileCards() {
        const container = this._fileCardsContainer;
        if (this._files.length === 0) {
            container.style.display = 'none';
            return;
        }

        container.style.display = '';
        container.innerHTML = '';

        const presetKey = this._presetCards ? this._presetCards.getSelected() : 'standard';

        for (let i = 0; i < this._files.length; i++) {
            const f = this._files[i];
            const card = document.createElement('div');
            card.className = 'compress-file-card';
            if (f.status === 'error') card.classList.add('has-error');
            if (f.status === 'processing') card.classList.add('is-processing');
            if (f.status === 'done') card.classList.add('is-done');
            if (f.status === 'analyzing') card.classList.add('is-analyzing');

            // -- Thumbnail --
            const thumbWrap = document.createElement('div');
            thumbWrap.className = 'compress-thumb';
            if (f.thumbnail) {
                const img = document.createElement('img');
                img.src = f.thumbnail;
                img.alt = f.name;
                img.draggable = false;
                thumbWrap.appendChild(img);
            } else {
                const placeholder = document.createElement('div');
                placeholder.className = 'compress-thumb-placeholder';
                if (f.status === 'analyzing') {
                    placeholder.innerHTML = '<span class="compress-spinner-sm"></span>';
                } else {
                    placeholder.textContent = '\uD83D\uDCC4';
                }
                thumbWrap.appendChild(placeholder);
            }
            card.appendChild(thumbWrap);

            // -- Info section --
            const info = document.createElement('div');
            info.className = 'compress-file-info';

            // File name (read-only display)
            const nameRow = document.createElement('div');
            nameRow.className = 'compress-file-name';
            nameRow.title = f.path;
            nameRow.textContent = f.name;
            info.appendChild(nameRow);

            // Output name (editable)
            const outputRow = document.createElement('div');
            outputRow.className = 'compress-output-row';
            const outputLabel = document.createElement('span');
            outputLabel.className = 'compress-output-label';
            outputLabel.textContent = 'Save as:';
            outputRow.appendChild(outputLabel);
            const outputInput = document.createElement('input');
            outputInput.type = 'text';
            outputInput.className = 'compress-output-input';
            outputInput.value = f.outputName;
            outputInput.placeholder = 'output filename';
            const idx = i;
            const validationErr = this._validateOutputName(f.outputName);
            if (validationErr) {
                outputInput.classList.add('input-error');
                outputInput.title = validationErr;
            }
            outputInput.addEventListener('input', (e) => {
                if (idx < this._files.length) {
                    this._files[idx].outputName = e.target.value;
                    const err = this._validateOutputName(e.target.value);
                    if (err) {
                        e.target.classList.add('input-error');
                        e.target.title = err;
                    } else {
                        e.target.classList.remove('input-error');
                        e.target.title = '';
                    }
                    this._updateCompressBtn();
                }
            });
            outputRow.appendChild(outputInput);
            const extSpan = document.createElement('span');
            extSpan.className = 'compress-output-ext';
            extSpan.textContent = '.pdf';
            outputRow.appendChild(extSpan);
            info.appendChild(outputRow);

            // Meta chips (size, pages, images, DPI)
            const metaRow = document.createElement('div');
            metaRow.className = 'compress-file-meta';
            if (f.status === 'analyzing') {
                this._addMetaChip(metaRow, 'Analyzing\u2026', 'rgba(var(--color-accent-rgb), 0.1)', 'var(--color-accent)');
            } else if (f.status === 'error') {
                this._addMetaChip(metaRow, f.errorMsg || 'Error', 'rgba(var(--color-red-rgb), 0.1)', 'var(--color-red)');
            } else {
                if (f.size != null) this._addMetaChip(metaRow, BridgeAPI.formatSize(f.size));
                if (f.pages != null) this._addMetaChip(metaRow, f.pages + ' page' + (f.pages !== 1 ? 's' : ''));
                if (f.imageCount > 0) this._addMetaChip(metaRow, f.imageCount + ' image' + (f.imageCount !== 1 ? 's' : ''));
                if (f._analysis && f._analysis.imageSummary) {
                    const summary = f._analysis.imageSummary;
                    if (summary.maxDpi > 0) {
                        // Show DPI with downscale warning
                        const presetEst = f._analysis.estimates && f._analysis.estimates[presetKey];
                        const targetDpi = presetEst ? presetEst.targetDpi : 150;
                        const willDownscale = summary.maxDpi > targetDpi * 1.1;
                        if (willDownscale) {
                            this._addMetaChip(metaRow, summary.maxDpi + ' \u2192 ' + targetDpi + ' DPI',
                                'rgba(var(--color-amber-rgb), 0.1)', 'var(--color-amber)');
                        } else {
                            this._addMetaChip(metaRow, summary.maxDpi + ' DPI');
                        }
                    }
                }
                // Done status chip
                if (f.status === 'done') {
                    this._addMetaChip(metaRow, '\u2713 Compressed', 'rgba(var(--color-green-rgb), 0.1)', 'var(--color-green)');
                }
            }
            info.appendChild(metaRow);

            card.appendChild(info);

            // -- Per-file estimate --
            const estCol = document.createElement('div');
            estCol.className = 'compress-file-est';
            if (f._analysis && f._analysis.estimates && f._analysis.estimates[presetKey] && f.size > 0) {
                const est = f._analysis.estimates[presetKey];
                const pct = est.savedPct;
                const estVal = document.createElement('div');
                estVal.className = 'compress-est-value';
                if (pct >= 30) estVal.style.color = 'var(--color-green)';
                else if (pct >= 10) estVal.style.color = 'var(--color-amber)';
                else estVal.style.color = 'var(--color-text-3)';
                estVal.textContent = pct > 0 ? ('-' + pct.toFixed(0) + '%') : '~0%';
                estCol.appendChild(estVal);
                const estSub = document.createElement('div');
                estSub.className = 'compress-est-sub';
                estSub.textContent = '\u2192 ' + est.estimatedSizeStr;
                estCol.appendChild(estSub);
            } else if (f.status === 'analyzing') {
                const dots = document.createElement('div');
                dots.className = 'compress-est-sub';
                dots.innerHTML = '<span class="compress-spinner-sm"></span>';
                estCol.appendChild(dots);
            }
            card.appendChild(estCol);

            // -- Remove button (always visible but subtle) --
            const removeBtn = document.createElement('button');
            removeBtn.className = 'compress-file-remove';
            removeBtn.title = 'Remove file';
            removeBtn.setAttribute('aria-label', 'Remove ' + f.name);
            removeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="3" y1="3" x2="11" y2="11"/><line x1="11" y1="3" x2="3" y2="11"/></svg>';
            removeBtn.addEventListener('click', () => this._removeFile(idx));
            card.appendChild(removeBtn);

            container.appendChild(card);
        }
    }

    _addMetaChip(parent, text, bg, color) {
        const chip = document.createElement('span');
        chip.className = 'compress-meta-chip';
        if (bg) chip.style.background = bg;
        if (color) chip.style.color = color;
        chip.textContent = text;
        parent.appendChild(chip);
    }

    /* ------------------------------------------------------------------
       Analysis & Estimation Panel
       ------------------------------------------------------------------ */

    _updateEstimate() {
        if (!this._estimatePanel) return;
        if (this._files.length === 0) {
            this._estimatePanel.style.display = 'none';
            return;
        }

        const presetKey = this._presetCards ? this._presetCards.getSelected() : 'standard';

        let totalOriginal = 0, totalEstimated = 0, totalImages = 0, totalImageBytes = 0;
        let hasAnalysis = false, allDpis = [], jpegCount = 0, grayscaleCount = 0, monoCount = 0;
        let totalPages = 0, targetDpi = 0, jpegQuality = 0;

        for (const f of this._files) {
            if (f.size != null) totalOriginal += f.size;
            if (f.pages != null) totalPages += f.pages;

            if (f._analysis && f._analysis.estimates && f._analysis.estimates[presetKey]) {
                hasAnalysis = true;
                const est = f._analysis.estimates[presetKey];
                totalEstimated += est.estimatedSize;
                targetDpi = est.targetDpi;
                jpegQuality = est.jpegQuality;
            } else if (f.size != null) {
                const fb = { screen: 0.20, ebook: 0.40, standard: 0.55, high: 0.70, prepress: 0.85 };
                totalEstimated += Math.round(f.size * (fb[presetKey] || 0.55));
            }

            if (f._analysis && f._analysis.imageSummary) {
                const imgs = f._analysis.imageSummary;
                totalImages += imgs.count;
                totalImageBytes += imgs.totalBytes;
                jpegCount += imgs.jpegCount;
                grayscaleCount += imgs.grayscaleCount;
                monoCount += imgs.monochromeCount;
                if (imgs.maxDpi > 0) allDpis.push(imgs.maxDpi);
                if (imgs.minDpi > 0) allDpis.push(imgs.minDpi);
            }
        }

        if (totalOriginal === 0) { this._estimatePanel.style.display = 'none'; return; }

        const savings = Math.max(0, totalOriginal - totalEstimated);
        const savingsPct = (savings / totalOriginal * 100);
        const maxDpi = allDpis.length > 0 ? Math.max(...allDpis) : 0;
        const imgPct = (totalImageBytes / totalOriginal * 100);

        // Animate panel appearance
        const wasHidden = this._estimatePanel.style.display === 'none';
        this._estimatePanel.style.display = '';
        this._estimatePanel.innerHTML = '';
        if (wasHidden) {
            this._estimatePanel.classList.add('animate-fade-in');
            setTimeout(() => this._estimatePanel.classList.remove('animate-fade-in'), 400);
        }

        // Header
        const header = document.createElement('div');
        header.className = 'compress-estimate-header';
        const headerTitle = document.createElement('span');
        headerTitle.textContent = hasAnalysis ? 'Compression Preview' : 'Estimated Result';
        header.appendChild(headerTitle);
        if (hasAnalysis) {
            const badge = document.createElement('span');
            badge.className = 'badge badge-accent';
            badge.textContent = 'Engine analysis';
            header.appendChild(badge);
        }
        this._estimatePanel.appendChild(header);

        // --- Big savings display ---
        const savingsBlock = document.createElement('div');
        savingsBlock.className = 'compress-estimate-savings';
        const savingsCircle = document.createElement('div');
        savingsCircle.className = 'compress-savings-circle';
        savingsCircle.style.setProperty('--savings-color',
            savingsPct >= 30 ? 'var(--color-green)' : savingsPct >= 10 ? 'var(--color-amber)' : 'var(--color-text-3)');
        const svgNs = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(svgNs, 'svg');
        svg.setAttribute('viewBox', '0 0 80 80');
        svg.setAttribute('width', '80');
        svg.setAttribute('height', '80');
        const bgCircle = document.createElementNS(svgNs, 'circle');
        bgCircle.setAttribute('cx', '40');
        bgCircle.setAttribute('cy', '40');
        bgCircle.setAttribute('r', '34');
        bgCircle.setAttribute('fill', 'none');
        bgCircle.setAttribute('stroke', 'var(--color-border)');
        bgCircle.setAttribute('stroke-width', '6');
        svg.appendChild(bgCircle);
        const fgCircle = document.createElementNS(svgNs, 'circle');
        fgCircle.setAttribute('cx', '40');
        fgCircle.setAttribute('cy', '40');
        fgCircle.setAttribute('r', '34');
        fgCircle.setAttribute('fill', 'none');
        fgCircle.setAttribute('stroke', 'var(--savings-color)');
        fgCircle.setAttribute('stroke-width', '6');
        fgCircle.setAttribute('stroke-linecap', 'round');
        const circumference = 2 * Math.PI * 34;
        const dashOffset = circumference * (1 - Math.min(savingsPct, 100) / 100);
        fgCircle.setAttribute('stroke-dasharray', circumference.toFixed(1));
        fgCircle.setAttribute('stroke-dashoffset', dashOffset.toFixed(1));
        fgCircle.setAttribute('transform', 'rotate(-90 40 40)');
        fgCircle.style.transition = 'stroke-dashoffset 0.6s ease';
        svg.appendChild(fgCircle);
        savingsCircle.appendChild(svg);
        const savingsText = document.createElement('div');
        savingsText.className = 'compress-savings-pct';
        savingsText.style.color = savingsPct >= 30 ? 'var(--color-green)' : savingsPct >= 10 ? 'var(--color-amber)' : 'var(--color-text-3)';
        savingsText.textContent = savingsPct.toFixed(0) + '%';
        savingsCircle.appendChild(savingsText);
        savingsBlock.appendChild(savingsCircle);

        // Size comparison
        const sizeComp = document.createElement('div');
        sizeComp.className = 'compress-estimate-sizes';
        this._addEstimateStat(sizeComp, 'Original', BridgeAPI.formatSize(totalOriginal), 'var(--color-text)');
        const arrow = document.createElement('div');
        arrow.className = 'compress-estimate-arrow';
        arrow.innerHTML = '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="var(--color-text-3)" stroke-width="1.5" stroke-linecap="round"><line x1="4" y1="10" x2="16" y2="10"/><polyline points="12,6 16,10 12,14"/></svg>';
        sizeComp.appendChild(arrow);
        this._addEstimateStat(sizeComp, 'Estimated', BridgeAPI.formatSize(totalEstimated), 'var(--color-accent)');
        this._addEstimateStat(sizeComp, 'You Save', BridgeAPI.formatSize(savings),
            savingsPct >= 30 ? 'var(--color-green)' : savingsPct >= 10 ? 'var(--color-amber)' : 'var(--color-text-3)');
        savingsBlock.appendChild(sizeComp);
        this._estimatePanel.appendChild(savingsBlock);

        // --- Document analysis details ---
        if (hasAnalysis && (totalImages > 0 || totalPages > 0)) {
            const detailsWrap = document.createElement('div');
            detailsWrap.className = 'compress-estimate-details';

            const detailTitle = document.createElement('div');
            detailTitle.className = 'compress-estimate-detail-title';
            detailTitle.textContent = 'Document Analysis';
            detailsWrap.appendChild(detailTitle);

            const chipGrid = document.createElement('div');
            chipGrid.className = 'compress-estimate-chips';
            if (totalPages > 0) this._addDetailChip(chipGrid, '\uD83D\uDCC4', totalPages + ' page' + (totalPages !== 1 ? 's' : ''));
            if (totalImages > 0) this._addDetailChip(chipGrid, '\uD83D\uDDBC\uFE0F', totalImages + ' image' + (totalImages !== 1 ? 's' : ''));
            if (imgPct > 0) this._addDetailChip(chipGrid, '\uD83D\uDCCA', imgPct.toFixed(0) + '% image data');
            if (maxDpi > 0) this._addDetailChip(chipGrid, '\uD83D\uDD0D', 'Max ' + maxDpi + ' DPI');
            if (jpegCount > 0) this._addDetailChip(chipGrid, '\uD83D\uDDBC\uFE0F', jpegCount + ' JPEG');
            if (grayscaleCount > 0) this._addDetailChip(chipGrid, '\u25EF', grayscaleCount + ' grayscale');
            if (monoCount > 0) this._addDetailChip(chipGrid, '\u25A0', monoCount + ' B&W');
            detailsWrap.appendChild(chipGrid);

            // Compression strategy info
            if (targetDpi > 0) {
                const strategyRow = document.createElement('div');
                strategyRow.className = 'compress-estimate-strategy';
                this._addStrategyItem(strategyRow, 'Target DPI', String(targetDpi));
                this._addStrategyItem(strategyRow, 'JPEG Quality', jpegQuality + '%');
                if (maxDpi > 0 && maxDpi > targetDpi * 1.1) {
                    this._addStrategyItem(strategyRow, 'Downscale', 'Yes \u2014 ' + maxDpi + ' \u2192 ' + targetDpi, 'var(--color-amber)');
                } else if (maxDpi > 0) {
                    this._addStrategyItem(strategyRow, 'Downscale', 'Not needed', 'var(--color-green)');
                }
                detailsWrap.appendChild(strategyRow);
            }

            this._estimatePanel.appendChild(detailsWrap);
        }
    }

    _addEstimateStat(parent, label, value, color) {
        const wrap = document.createElement('div');
        wrap.className = 'compress-estimate-stat';
        const lbl = document.createElement('div');
        lbl.className = 'compress-estimate-stat-label';
        lbl.textContent = label;
        wrap.appendChild(lbl);
        const val = document.createElement('div');
        val.className = 'compress-estimate-stat-value';
        val.style.color = color || '';
        val.textContent = value;
        wrap.appendChild(val);
        parent.appendChild(wrap);
    }

    _addDetailChip(parent, icon, text) {
        const chip = document.createElement('div');
        chip.className = 'compress-detail-chip';
        const iconEl = document.createElement('span');
        iconEl.className = 'compress-detail-chip-icon';
        iconEl.textContent = icon;
        chip.appendChild(iconEl);
        const textEl = document.createElement('span');
        textEl.textContent = text;
        chip.appendChild(textEl);
        parent.appendChild(chip);
    }

    _addStrategyItem(parent, label, value, color) {
        const item = document.createElement('div');
        item.className = 'compress-strategy-item';
        const lbl = document.createElement('span');
        lbl.className = 'compress-strategy-label';
        lbl.textContent = label;
        item.appendChild(lbl);
        const val = document.createElement('span');
        val.className = 'compress-strategy-value';
        if (color) val.style.color = color;
        val.textContent = value;
        item.appendChild(val);
        parent.appendChild(item);
    }

    /* ------------------------------------------------------------------
       Compression
       ------------------------------------------------------------------ */

    _startCompress() {
        if (this._files.length === 0 || this._processing) return;

        this._processing = true;
        this._startTime = Date.now();
        this._completedCount = 0;
        this._updateCompressBtn();
        this._resultsPanel.hide();

        this._progressPanel.reset();
        this._progressPanel.show();

        for (const f of this._files) {
            if (f.status !== 'error') f.status = 'processing';
        }
        this._renderFileCards();

        const filePaths = this._files.filter(f => f.status !== 'error').map(f => f.path);
        const presetKey = this._presetCards ? this._presetCards.getSelected() : 'standard';
        const outputDir = this._outputDirInput ? this._outputDirInput.value : '';
        const useGs = this._useGsCheckbox ? this._useGsCheckbox.checked : false;

        BridgeAPI.startCompress({
            files: filePaths,
            preset: presetKey,
            output_dir: outputDir || '',
            use_gs: useGs,
        });
    }

    _cancelCompress() {
        BridgeAPI.cancel('compress');
        this._processing = false;
        this._progressPanel.hide();
        this._updateCompressBtn();
        for (const f of this._files) {
            if (f.status === 'processing') f.status = 'pending';
        }
        this._renderFileCards();
        Toast.warning('Compression cancelled.');
    }

    /* ------------------------------------------------------------------
       EventBus Listeners
       ------------------------------------------------------------------ */

    _bindEvents() {
        this._unbindEvents();

        this._onProgress = (data) => {
            if (!data || data.toolKey !== 'compress') return;
            const pct = data.pct != null ? data.pct : data.percent != null ? data.percent : 0;
            const filename = data.filename || '';
            const current = data.current != null ? data.current : 0;
            const total = data.total != null ? data.total : this._files.length;

            // Estimate time remaining
            let etaStr = '';
            if (this._startTime && pct > 5) {
                const elapsed = (Date.now() - this._startTime) / 1000;
                const remaining = (elapsed / pct) * (100 - pct);
                if (remaining < 60) {
                    etaStr = Math.ceil(remaining) + 's remaining';
                } else {
                    const m = Math.floor(remaining / 60);
                    const s = Math.ceil(remaining % 60);
                    etaStr = m + 'm ' + s + 's remaining';
                }
            }

            this._progressPanel.update(pct, filename, current + 1, total, etaStr);

            const fileIdx = data.fileIndex != null ? data.fileIndex : data.current;
            if (fileIdx != null && fileIdx < this._files.length) {
                this._files[fileIdx].status = 'processing';
                // Mark previous files as done (rolling results)
                for (let i = 0; i < fileIdx; i++) {
                    if (this._files[i].status === 'processing') {
                        this._files[i].status = 'done';
                    }
                }
                this._renderFileCards();
            }
        };

        this._onDone = (data) => {
            if (!data || data.toolKey !== 'compress') return;

            this._processing = false;
            this._progressPanel.hide();
            this._updateCompressBtn();
            const elapsed = this._startTime ? (Date.now() - this._startTime) / 1000 : 0;

            if (!data.success) {
                Toast.error('Compression failed: ' + (data.message || 'Unknown error'));
                for (const f of this._files) {
                    if (f.status === 'processing') f.status = 'error';
                }
                this._renderFileCards();
                return;
            }

            let rawResults = data.results || data.files || [];
            if (!Array.isArray(rawResults)) rawResults = [rawResults];

            let totalSaved = 0;
            const resultFiles = [];

            for (let i = 0; i < rawResults.length; i++) {
                const r = rawResults[i];
                const original = r.original_size != null ? r.original_size : r.originalSize || 0;
                const compressed = r.compressed_size != null ? r.compressed_size
                    : r.compressedSize != null ? r.compressedSize
                    : r.result_size != null ? r.result_size : r.resultSize || 0;
                const outPath = r.output_path || r.outputPath || '';
                const inPath = r.input_path || r.inputPath || r.path || '';
                const saved = original - compressed;
                if (saved > 0) totalSaved += saved;

                resultFiles.push({
                    name: BridgeAPI.basename(inPath || outPath),
                    path: inPath,
                    outputPath: outPath,
                    originalSize: original,
                    resultSize: compressed,
                    status: r.skipped ? 'skipped' : r.error ? 'error' : 'done',
                });

                if (i < this._files.length) {
                    this._files[i].status = r.error ? 'error' : 'done';
                }
            }

            this._renderFileCards();

            const firstOut = resultFiles.length > 0 ? resultFiles[0].outputPath : '';
            const outputDir = data.outputDir || data.output_dir || (firstOut ? BridgeAPI.dirname(firstOut) : '');

            this._resultsPanel.show({
                files: resultFiles,
                totalTime: elapsed,
                totalSaved: totalSaved,
                outputDir: outputDir,
            });

            if (resultFiles.length > 0) {
                Toast.success('Saved ' + BridgeAPI.formatSize(totalSaved) + ' across ' +
                    resultFiles.length + ' file' + (resultFiles.length !== 1 ? 's' : '') + '.');
            }
        };

        this._onError = (data) => {
            if (!data || data.toolKey !== 'compress') return;
            this._processing = false;
            this._progressPanel.hide();
            this._updateCompressBtn();
            Toast.error(data.message || 'An error occurred during compression.');
        };

        EventBus.on('progress', this._onProgress);
        EventBus.on('done', this._onDone);
        EventBus.on('error', this._onError);
    }

    _unbindEvents() {
        if (this._onProgress) EventBus.off('progress', this._onProgress);
        if (this._onDone) EventBus.off('done', this._onDone);
        if (this._onError) EventBus.off('error', this._onError);
        this._onProgress = null;
        this._onDone = null;
        this._onError = null;
    }
}

/* --------------------------------------------------------------------------
   Route registration
   -------------------------------------------------------------------------- */

Router.register('compress', function () {
    return new CompressPage();
});
