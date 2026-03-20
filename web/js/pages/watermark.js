/* ==========================================================================
   PDF Toolkit - Watermark Page (Premium)

   Features: batch file support, watermark presets, live preview text,
   opacity/rotation/font/color/position controls, page range, output folder
   + naming template, settings persistence, keyboard shortcuts, progress,
   cancellation, results summary.
   ========================================================================== */

"use strict";

function WatermarkPage() {
    let _el = null;
    let _busy = false;
    let _files = [];

    // Component instances
    let _dropZone = null;
    let _fileList = null;
    let _progress = null;
    let _results = null;

    // DOM refs — watermark settings
    let _presetSelect = null;
    let _textInput = null;
    let _opacitySlider = null;
    let _opacityValue = null;
    let _rotationInput = null;
    let _fontSizeInput = null;
    let _colorInput = null;
    let _colorPreview = null;
    let _positionSelect = null;
    let _pageRangeInput = null;

    // DOM refs — output
    let _outputDirInput = null;
    let _namingInput = null;

    // DOM refs — action
    let _watermarkBtn = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;
    let _onError = null;
    let _keyHandler = null;

    // ── Preset definitions ─────────────────────────────────────────
    var PRESETS = [
        { key: 'custom',       label: 'Custom',       text: '',              opacity: 30, rotation: -45, fontSize: 48, color: '#808080', position: 'center' },
        { key: 'confidential', label: 'CONFIDENTIAL',  text: 'CONFIDENTIAL',  opacity: 20, rotation: -45, fontSize: 60, color: '#DC2626', position: 'center' },
        { key: 'draft',        label: 'DRAFT',         text: 'DRAFT',         opacity: 25, rotation: -45, fontSize: 72, color: '#2563EB', position: 'center' },
        { key: 'donotcopy',    label: 'DO NOT COPY',   text: 'DO NOT COPY',   opacity: 20, rotation: -45, fontSize: 54, color: '#DC2626', position: 'center' },
        { key: 'sample',       label: 'SAMPLE',        text: 'SAMPLE',        opacity: 25, rotation: -45, fontSize: 64, color: '#7C3AED', position: 'center' },
        { key: 'approved',     label: 'APPROVED',      text: 'APPROVED',      opacity: 20, rotation:   0, fontSize: 48, color: '#059669', position: 'center' },
        { key: 'void',         label: 'VOID',          text: 'VOID',          opacity: 30, rotation: -45, fontSize: 80, color: '#DC2626', position: 'center' },
    ];

    var POSITIONS = [
        { value: 'center',       label: 'Center' },
        { value: 'top-left',     label: 'Top Left' },
        { value: 'top-center',   label: 'Top Center' },
        { value: 'top-right',    label: 'Top Right' },
        { value: 'bottom-left',  label: 'Bottom Left' },
        { value: 'bottom-center', label: 'Bottom Center' },
        { value: 'bottom-right', label: 'Bottom Right' },
    ];

    // ══════════════════════════════════════════════════════════════
    //  Build UI
    // ══════════════════════════════════════════════════════════════

    function _buildUI(container) {
        _el = container;

        // ── Page header ──
        var header = createPageHeader({
            title: 'Watermark',
            subtitle: 'Add text watermarks to PDF pages',
        });
        _el.appendChild(header.el);

        // ── Drop zone (multi-file) ──
        _dropZone = createDropZone({
            title: 'Drop PDF files here',
            subtitle: 'or click to browse — add as many as you need',
            multiple: true,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _fileList.clear();
            _fileList.addFiles(files);
            _updateUI();
        });
        _el.appendChild(_dropZone.el);

        // ── File list ──
        _fileList = createFileList({
            showPages: false,
            reorderable: false,
            emptyMessage: 'No PDF files added yet. Drop files above or click to browse.',
        });
        _el.appendChild(_fileList.el);

        // ── Watermark Settings Card ──
        var settingsCard = document.createElement('div');
        settingsCard.className = 'card';
        settingsCard.style.marginTop = 'var(--space-4)';

        // Section title
        var settingsTitle = document.createElement('div');
        settingsTitle.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); margin-bottom:var(--space-4); color:var(--color-text);';
        settingsTitle.textContent = 'Watermark Settings';
        settingsCard.appendChild(settingsTitle);

        // Preset row
        var presetGroup = _createFormGroup('Preset');
        _presetSelect = document.createElement('select');
        _presetSelect.className = 'form-input';
        _presetSelect.style.width = '100%';
        for (var pi = 0; pi < PRESETS.length; pi++) {
            var opt = document.createElement('option');
            opt.value = PRESETS[pi].key;
            opt.textContent = PRESETS[pi].label;
            _presetSelect.appendChild(opt);
        }
        _presetSelect.addEventListener('change', _onPresetChange);
        presetGroup.appendChild(_presetSelect);
        settingsCard.appendChild(presetGroup);

        // Watermark text
        var textGroup = _createFormGroup('Watermark text');
        _textInput = document.createElement('input');
        _textInput.type = 'text';
        _textInput.className = 'form-input';
        _textInput.style.width = '100%';
        _textInput.placeholder = 'e.g. CONFIDENTIAL, DRAFT, SAMPLE';
        _textInput.addEventListener('input', function () {
            // Switch to Custom preset when text is manually edited
            if (_presetSelect.value !== 'custom') {
                var currentPreset = PRESETS.find(function (p) { return p.key === _presetSelect.value; });
                if (currentPreset && _textInput.value !== currentPreset.text) {
                    _presetSelect.value = 'custom';
                }
            }
        });
        textGroup.appendChild(_textInput);
        settingsCard.appendChild(textGroup);

        // Row 1: Opacity | Rotation | Font size
        var grid1 = document.createElement('div');
        grid1.style.cssText = 'display:grid; grid-template-columns:1fr 1fr 1fr; gap:var(--space-4); margin-top:var(--space-4);';

        // Opacity
        var opGroup = _createFormGroup('Opacity');
        var opRow = document.createElement('div');
        opRow.style.cssText = 'display:flex; align-items:center; gap:var(--space-2);';

        _opacitySlider = document.createElement('input');
        _opacitySlider.type = 'range';
        _opacitySlider.min = 1;
        _opacitySlider.max = 100;
        _opacitySlider.value = 30;
        _opacitySlider.style.flex = '1';

        _opacityValue = document.createElement('span');
        _opacityValue.style.cssText = 'min-width:40px; text-align:right; font-size:var(--font-size-sm); color:var(--color-text-2); font-variant-numeric:tabular-nums;';
        _opacityValue.textContent = '30%';

        _opacitySlider.addEventListener('input', function () {
            _opacityValue.textContent = _opacitySlider.value + '%';
        });

        opRow.appendChild(_opacitySlider);
        opRow.appendChild(_opacityValue);
        opGroup.appendChild(opRow);
        grid1.appendChild(opGroup);

        // Rotation
        var rotGroup = _createFormGroup('Rotation (degrees)');
        _rotationInput = document.createElement('input');
        _rotationInput.type = 'number';
        _rotationInput.className = 'form-input';
        _rotationInput.style.width = '100%';
        _rotationInput.min = -180;
        _rotationInput.max = 180;
        _rotationInput.value = -45;
        rotGroup.appendChild(_rotationInput);
        grid1.appendChild(rotGroup);

        // Font size
        var fsGroup = _createFormGroup('Font size');
        _fontSizeInput = document.createElement('input');
        _fontSizeInput.type = 'number';
        _fontSizeInput.className = 'form-input';
        _fontSizeInput.style.width = '100%';
        _fontSizeInput.min = 6;
        _fontSizeInput.max = 200;
        _fontSizeInput.value = 48;
        fsGroup.appendChild(_fontSizeInput);
        grid1.appendChild(fsGroup);

        settingsCard.appendChild(grid1);

        // Row 2: Color | Position | Page range
        var grid2 = document.createElement('div');
        grid2.style.cssText = 'display:grid; grid-template-columns:1fr 1fr 1fr; gap:var(--space-4); margin-top:var(--space-4);';

        // Color
        var colorGroup = _createFormGroup('Color');
        var colorRow = document.createElement('div');
        colorRow.style.cssText = 'display:flex; align-items:center; gap:var(--space-2);';

        _colorPreview = document.createElement('div');
        _colorPreview.style.cssText = 'width:28px; height:28px; border-radius:var(--radius-sm); border:1px solid var(--color-border); cursor:pointer; flex-shrink:0;';
        _colorPreview.style.backgroundColor = '#808080';

        _colorInput = document.createElement('input');
        _colorInput.type = 'text';
        _colorInput.className = 'form-input';
        _colorInput.style.flex = '1';
        _colorInput.value = '#808080';
        _colorInput.placeholder = '#808080';
        _colorInput.addEventListener('input', function () {
            var val = _colorInput.value.trim();
            if (/^#[0-9A-Fa-f]{6}$/.test(val)) {
                _colorPreview.style.backgroundColor = val;
            }
        });

        // Click color preview to open native color picker
        var _hiddenColorPicker = document.createElement('input');
        _hiddenColorPicker.type = 'color';
        _hiddenColorPicker.value = '#808080';
        _hiddenColorPicker.style.cssText = 'position:absolute; width:0; height:0; visibility:hidden;';
        _hiddenColorPicker.addEventListener('input', function () {
            _colorInput.value = _hiddenColorPicker.value;
            _colorPreview.style.backgroundColor = _hiddenColorPicker.value;
        });
        _colorPreview.addEventListener('click', function () {
            _hiddenColorPicker.click();
        });

        colorRow.appendChild(_colorPreview);
        colorRow.appendChild(_colorInput);
        colorRow.appendChild(_hiddenColorPicker);
        colorGroup.appendChild(colorRow);
        grid2.appendChild(colorGroup);

        // Position
        var posGroup = _createFormGroup('Position');
        _positionSelect = document.createElement('select');
        _positionSelect.className = 'form-input';
        _positionSelect.style.width = '100%';
        for (var pp = 0; pp < POSITIONS.length; pp++) {
            var posOpt = document.createElement('option');
            posOpt.value = POSITIONS[pp].value;
            posOpt.textContent = POSITIONS[pp].label;
            _positionSelect.appendChild(posOpt);
        }
        posGroup.appendChild(_positionSelect);
        grid2.appendChild(posGroup);

        // Page range
        var prGroup = _createFormGroup('Page range');
        _pageRangeInput = document.createElement('input');
        _pageRangeInput.type = 'text';
        _pageRangeInput.className = 'form-input';
        _pageRangeInput.style.width = '100%';
        _pageRangeInput.placeholder = 'All pages (e.g. 1-5, 8, 10-12)';
        prGroup.appendChild(_pageRangeInput);

        var prHelp = document.createElement('div');
        prHelp.className = 'form-help';
        prHelp.textContent = 'Leave empty to watermark all pages.';
        prGroup.appendChild(prHelp);
        grid2.appendChild(prGroup);

        settingsCard.appendChild(grid2);
        _el.appendChild(settingsCard);

        // ── Output Card ──
        var outputCard = document.createElement('div');
        outputCard.className = 'card';
        outputCard.style.marginTop = 'var(--space-4)';

        var outputTitle = document.createElement('div');
        outputTitle.style.cssText = 'font-weight:var(--font-weight-semibold); font-size:var(--font-size-md); margin-bottom:var(--space-4); color:var(--color-text);';
        outputTitle.textContent = 'Output';
        outputCard.appendChild(outputTitle);

        // Output folder row
        var outDirGroup = _createFormGroup('Output folder');
        var outDirRow = document.createElement('div');
        outDirRow.style.cssText = 'display:flex; gap:var(--space-2); align-items:center;';

        _outputDirInput = document.createElement('input');
        _outputDirInput.type = 'text';
        _outputDirInput.className = 'form-input';
        _outputDirInput.placeholder = 'Same as source file';
        _outputDirInput.readOnly = true;
        _outputDirInput.style.flex = '1';
        outDirRow.appendChild(_outputDirInput);

        var browseDirBtn = document.createElement('button');
        browseDirBtn.className = 'btn btn-secondary';
        browseDirBtn.textContent = 'Browse';
        browseDirBtn.addEventListener('click', async function () {
            try {
                var dir = await BridgeAPI.openFolder();
                if (dir) {
                    _outputDirInput.value = dir;
                    _saveSettings();
                }
            } catch (err) {
                console.error('[WatermarkPage] openFolder error:', err);
            }
        });
        outDirRow.appendChild(browseDirBtn);
        outDirGroup.appendChild(outDirRow);
        outputCard.appendChild(outDirGroup);

        // Naming template
        var namingGroup = _createFormGroup('Naming template');
        _namingInput = document.createElement('input');
        _namingInput.type = 'text';
        _namingInput.className = 'form-input';
        _namingInput.style.width = '100%';
        _namingInput.value = '{name}_watermarked';
        _namingInput.placeholder = '{name}_watermarked';
        _namingInput.addEventListener('change', _saveSettings);
        namingGroup.appendChild(_namingInput);

        var namingHelp = document.createElement('div');
        namingHelp.className = 'form-help';
        namingHelp.textContent = 'Use {name} for original filename.';
        namingGroup.appendChild(namingHelp);

        outputCard.appendChild(namingGroup);
        _el.appendChild(outputCard);

        // ── Action row ──
        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display:flex; justify-content:flex-end; align-items:center; gap:var(--space-3); margin-top:var(--space-4);';

        // File count summary
        var fileCountLabel = document.createElement('span');
        fileCountLabel.id = 'watermark-file-count';
        fileCountLabel.style.cssText = 'font-size:var(--font-size-sm); color:var(--color-text-3); margin-right:auto;';
        actionRow.appendChild(fileCountLabel);

        _watermarkBtn = document.createElement('button');
        _watermarkBtn.className = 'btn btn-primary btn-lg';
        _watermarkBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-right:6px;vertical-align:-2px"><path d="M3 17h14"/><path d="M7 3l-4 14"/><path d="M17 3l-4 14"/><path d="M5 10h10"/></svg><span>Apply Watermark</span>';
        _watermarkBtn.disabled = true;
        _watermarkBtn.addEventListener('click', _startWatermark);
        actionRow.appendChild(_watermarkBtn);
        _el.appendChild(actionRow);

        // ── Progress ──
        _progress = createProgressPanel();
        _progress.el.style.marginTop = 'var(--space-4)';
        _progress.onCancel(function () {
            BridgeAPI.cancel('watermark');
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.info('Watermark cancelled.');
        });
        _el.appendChild(_progress.el);

        // ── Results ──
        _results = createResultsPanel();
        _results.el.style.marginTop = 'var(--space-4)';
        _el.appendChild(_results.el);

        // Load saved settings
        _loadSettings();
    }

    // ══════════════════════════════════════════════════════════════
    //  Helpers
    // ══════════════════════════════════════════════════════════════

    function _createFormGroup(labelText) {
        var group = document.createElement('div');
        group.style.marginBottom = 'var(--space-3)';
        var label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = labelText;
        group.appendChild(label);
        return group;
    }

    function _onPresetChange() {
        var key = _presetSelect.value;
        var preset = PRESETS.find(function (p) { return p.key === key; });
        if (!preset || key === 'custom') return;

        _textInput.value = preset.text;
        _opacitySlider.value = preset.opacity;
        _opacityValue.textContent = preset.opacity + '%';
        _rotationInput.value = preset.rotation;
        _fontSizeInput.value = preset.fontSize;
        _colorInput.value = preset.color;
        _colorPreview.style.backgroundColor = preset.color;
        _positionSelect.value = preset.position;
    }

    function _updateUI() {
        var hasFiles = _files.length > 0;
        _watermarkBtn.disabled = _busy || !hasFiles;

        var countLabel = _el ? _el.querySelector('#watermark-file-count') : null;
        if (countLabel) {
            if (hasFiles) {
                countLabel.textContent = _files.length + ' file' + (_files.length === 1 ? '' : 's') + ' selected';
            } else {
                countLabel.textContent = '';
            }
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Settings Persistence
    // ══════════════════════════════════════════════════════════════

    async function _loadSettings() {
        try {
            var naming = await BridgeAPI.loadSetting('watermark/naming');
            if (naming && _namingInput) _namingInput.value = naming;

            var outputDir = await BridgeAPI.loadSetting('watermark/outputDir');
            if (outputDir && _outputDirInput) _outputDirInput.value = outputDir;

            var preset = await BridgeAPI.loadSetting('watermark/preset');
            if (preset && _presetSelect) {
                _presetSelect.value = preset;
                _onPresetChange();
            }

            var opacity = await BridgeAPI.loadSetting('watermark/opacity');
            if (opacity && _opacitySlider) {
                _opacitySlider.value = opacity;
                _opacityValue.textContent = opacity + '%';
            }

            var rotation = await BridgeAPI.loadSetting('watermark/rotation');
            if (rotation && _rotationInput) _rotationInput.value = rotation;

            var fontSize = await BridgeAPI.loadSetting('watermark/fontSize');
            if (fontSize && _fontSizeInput) _fontSizeInput.value = fontSize;

            var color = await BridgeAPI.loadSetting('watermark/color');
            if (color && _colorInput) {
                _colorInput.value = color;
                _colorPreview.style.backgroundColor = color;
            }

            var position = await BridgeAPI.loadSetting('watermark/position');
            if (position && _positionSelect) _positionSelect.value = position;
        } catch (e) {
            console.warn('[WatermarkPage] loadSettings:', e);
        }
    }

    function _saveSettings() {
        try {
            BridgeAPI.saveSetting('watermark/naming', _namingInput.value);
            BridgeAPI.saveSetting('watermark/outputDir', _outputDirInput.value);
            BridgeAPI.saveSetting('watermark/preset', _presetSelect.value);
            BridgeAPI.saveSetting('watermark/opacity', _opacitySlider.value);
            BridgeAPI.saveSetting('watermark/rotation', _rotationInput.value);
            BridgeAPI.saveSetting('watermark/fontSize', _fontSizeInput.value);
            BridgeAPI.saveSetting('watermark/color', _colorInput.value);
            BridgeAPI.saveSetting('watermark/position', _positionSelect.value);
        } catch (e) {
            console.warn('[WatermarkPage] saveSettings:', e);
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Start Operation
    // ══════════════════════════════════════════════════════════════

    function _startWatermark() {
        if (_files.length === 0) {
            Toast.warning('Please add at least one PDF file.');
            return;
        }

        var text = _textInput.value.trim();
        if (!text) {
            Toast.warning('Please enter watermark text.');
            return;
        }

        var opacity  = parseInt(_opacitySlider.value, 10);
        var rotation = parseInt(_rotationInput.value, 10);
        var fontSize = parseInt(_fontSizeInput.value, 10);
        var color    = _colorInput.value.trim() || '#808080';
        var position = _positionSelect.value;
        var pageRange = _pageRangeInput.value.trim() || null;
        var outputDir = _outputDirInput.value || '';
        var naming    = _namingInput.value || '{name}_watermarked';

        _busy = true;
        _updateUI();
        _results.hide();
        _progress.reset();
        _progress.show();
        _saveSettings();

        BridgeAPI.startWatermark({
            files: _files.map(function (f) { return f.path; }),
            text: text,
            opacity: opacity,
            rotation: rotation,
            font_size: fontSize,
            color: color,
            position: position,
            page_range: pageRange,
            output_dir: outputDir,
            naming: naming,
        });
    }

    // ══════════════════════════════════════════════════════════════
    //  Events
    // ══════════════════════════════════════════════════════════════

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.toolKey !== 'watermark') return;
            _progress.update(
                data.pct || 0,
                data.filename || '',
                (data.current || 0) + 1,
                data.total || 1
            );
        };

        _onDone = function (data) {
            if (data.toolKey !== 'watermark') return;
            _busy = false;
            _progress.hide();
            _updateUI();

            if (data.success) {
                var res = data.results || {};
                var fileResults = res.files || [];
                var okCount = 0;
                var errCount = 0;

                for (var i = 0; i < fileResults.length; i++) {
                    if (fileResults[i].status === 'ok') {
                        okCount++;
                        _fileList.setStatus(i, 'done');
                    } else {
                        errCount++;
                        _fileList.setStatus(i, 'error');
                    }
                }

                // Show results panel
                _results.show({
                    files: fileResults.map(function (fr) {
                        return {
                            name: fr.file || '',
                            status: fr.status,
                            details: fr.details || '',
                            outputPath: fr.outputPath || '',
                        };
                    }),
                    totalTime: res.elapsed || 0,
                    outputDir: res.output_dir || '',
                });

                if (errCount === 0) {
                    Toast.success('Watermark applied to ' + okCount + ' file' + (okCount === 1 ? '' : 's') + '.');
                } else if (okCount > 0) {
                    Toast.warning(okCount + ' succeeded, ' + errCount + ' failed.');
                } else {
                    Toast.error('All files failed.');
                }
            } else {
                Toast.error(data.message || 'Watermark operation failed.');
            }
        };

        _onError = function (data) {
            if (data.toolKey && data.toolKey !== 'watermark') return;
            _busy = false;
            _progress.hide();
            _updateUI();
            Toast.error(data.message || 'An error occurred while applying watermark.');
        };

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
        EventBus.on('error', _onError);

        // Keyboard shortcuts
        _keyHandler = function (e) {
            if (_el && _el.offsetParent !== null) {
                if ((e.ctrlKey || e.metaKey) && e.key === 'o') {
                    e.preventDefault();
                    _dropZone.el.click();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                    e.preventDefault();
                    if (!_watermarkBtn.disabled) _startWatermark();
                }
                if (e.key === 'Escape' && _busy) {
                    e.preventDefault();
                    BridgeAPI.cancel('watermark');
                    _busy = false;
                    _progress.hide();
                    _updateUI();
                    Toast.info('Watermark cancelled.');
                }
            }
        };
        document.addEventListener('keydown', _keyHandler);
    }

    function _unbindEvents() {
        if (_onProgress) EventBus.off('progress', _onProgress);
        if (_onDone) EventBus.off('done', _onDone);
        if (_onError) EventBus.off('error', _onError);
        if (_keyHandler) document.removeEventListener('keydown', _keyHandler);
    }

    // ══════════════════════════════════════════════════════════════
    //  Lifecycle
    // ══════════════════════════════════════════════════════════════

    return {
        onMount: function (container) {
            _buildUI(container);
            _bindEvents();
        },
        onActivated: function () {
            if (!_onProgress) _bindEvents();
        },
        onDeactivated: function () {
            if (!_busy) _unbindEvents();
        },
        isBusy: function () { return _busy; },
        handleDrop: function (files) {
            if (_dropZone) {
                _dropZone.setFiles(files.map(function (f) { return f.path || f; }));
            }
        },
    };
}

Router.register('watermark', function () { return WatermarkPage(); });
