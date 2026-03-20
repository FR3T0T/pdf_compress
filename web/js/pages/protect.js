/* ==========================================================================
   PDF Toolkit - Protect Page (Dual-mode: Standard PDF + Enhanced .epdf)

   Modes:
     Standard PDF — AES-256 / AES-128 (opens in any PDF reader)
     Enhanced .epdf — ChaCha20-Poly1305 / AES-256-GCM / Camellia-256-CBC
                      with Argon2id / Argon2d key derivation (toolkit only)

   Features: batch file support, dual mode toggle, cipher & KDF selection,
   password strength, permissions, naming template, output folder, progress,
   cancellation, results summary.
   ========================================================================== */

"use strict";

function ProtectPage() {
    let _el = null;
    let _busy = false;
    let _files = [];
    let _mode = 'standard'; // 'standard' or 'enhanced'

    // Component instances
    let _dropZone = null;
    let _fileList = null;
    let _progress = null;
    let _results = null;

    // DOM refs — mode
    let _modeSelect = null;
    let _stdOptions = null;
    let _enhOptions = null;

    // Standard options
    let _encryptionSelect = null;
    let _permPrint = null;
    let _permModify = null;
    let _permCopy = null;
    let _permAnnotate = null;

    // Enhanced options
    let _cipherSelect = null;
    let _kdfSelect = null;

    // Passwords
    let _userPasswordInput = null;
    let _ownerPasswordInput = null;
    let _ownerPwSection = null;
    let _userStrengthBar = null;
    let _userStrengthLabel = null;
    let _ownerStrengthBar = null;
    let _ownerStrengthLabel = null;

    // Output
    let _outputDirInput = null;
    let _namingInput = null;

    // Action
    let _protectBtn = null;
    let _cancelBtn = null;
    let _summaryLabel = null;
    let _resultLabel = null;
    let _openFolderBtn = null;

    // Event handlers
    let _onProgress = null;
    let _onDone = null;

    // ── Cipher & KDF options ──────────────────────────────────────

    var CIPHERS = [
        { value: 'chacha20-poly1305', text: 'ChaCha20-Poly1305 (256-bit, AEAD)' },
        { value: 'aes-256-gcm',      text: 'AES-256-GCM (AEAD)' },
        { value: 'camellia-256-cbc',  text: 'Camellia-256-CBC + HMAC (encrypt-then-MAC)' },
    ];
    var KDFS = [
        { value: 'argon2id', text: 'Argon2id (recommended — resists side-channel + GPU)' },
        { value: 'argon2d',  text: 'Argon2d (faster — resists GPU attacks)' },
    ];

    // ══════════════════════════════════════════════════════════════
    //  Build UI
    // ══════════════════════════════════════════════════════════════

    function _buildUI(container) {
        _el = container;

        // Page header
        var header = createPageHeader({
            title: 'Protect PDF',
            subtitle: 'Add password protection with standard or enhanced encryption',
        });
        _el.appendChild(header.el);

        // ── Drop zone (multi-file) ──
        _dropZone = createDropZone({
            title: 'Drop PDF files here',
            subtitle: 'or click to browse',
            multiple: true,
        });
        _dropZone.onFilesChanged(function (files) {
            _files = files;
            _updateSummary();
            _updateUI();
        });
        _el.appendChild(_dropZone.el);

        // ── File list ──
        _fileList = createFileList({ showPages: false });
        _fileList.el.style.display = 'none';
        _el.appendChild(_fileList.el);

        // Summary label
        _summaryLabel = document.createElement('div');
        _summaryLabel.style.cssText = 'font-size: var(--font-size-sm); color: var(--color-text-3); margin-top: var(--space-2); margin-bottom: var(--space-2);';
        _el.appendChild(_summaryLabel);

        // ══════════════════════════════════════════════════════════
        //  Encryption Mode Card
        // ══════════════════════════════════════════════════════════

        var modeCard = _createCard('Encryption Mode');

        // Mode selector
        _modeSelect = document.createElement('select');
        _modeSelect.className = 'form-input';
        var modeOptions = [
            { value: 'standard', text: 'Standard PDF (AES) — opens in any PDF reader' },
            { value: 'enhanced', text: 'Enhanced .epdf (advanced ciphers) — this toolkit only' },
        ];
        for (var i = 0; i < modeOptions.length; i++) {
            var opt = document.createElement('option');
            opt.value = modeOptions[i].value;
            opt.textContent = modeOptions[i].text;
            _modeSelect.appendChild(opt);
        }
        _modeSelect.addEventListener('change', function () {
            _mode = _modeSelect.value;
            _updateModeVisibility();
            _updateUI();
            _updateSummary();
            BridgeAPI.saveSetting('protect/mode', _mode);
        });
        modeCard.body.appendChild(_modeSelect);

        // ── Standard options ──
        _stdOptions = document.createElement('div');
        _stdOptions.style.marginTop = 'var(--space-4)';

        var encLabel = _createFormLabel('Encryption');
        _stdOptions.appendChild(encLabel);

        _encryptionSelect = document.createElement('select');
        _encryptionSelect.className = 'form-input';
        var encOptions = [
            { value: 'AES-256', text: 'AES-256 (recommended)' },
            { value: 'AES-128', text: 'AES-128' },
        ];
        for (var j = 0; j < encOptions.length; j++) {
            var opt2 = document.createElement('option');
            opt2.value = encOptions[j].value;
            opt2.textContent = encOptions[j].text;
            _encryptionSelect.appendChild(opt2);
        }
        _stdOptions.appendChild(_encryptionSelect);

        // Permissions
        var permLabel = _createFormLabel('Permissions');
        permLabel.style.marginTop = 'var(--space-4)';
        _stdOptions.appendChild(permLabel);

        var permGrid = document.createElement('div');
        permGrid.style.cssText = 'display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2);';

        _permPrint = _createCheckbox('Allow printing', true);
        permGrid.appendChild(_permPrint.el);
        _permModify = _createCheckbox('Allow modifying', false);
        permGrid.appendChild(_permModify.el);
        _permCopy = _createCheckbox('Allow copying', false);
        permGrid.appendChild(_permCopy.el);
        _permAnnotate = _createCheckbox('Allow annotating', false);
        permGrid.appendChild(_permAnnotate.el);

        _stdOptions.appendChild(permGrid);
        modeCard.body.appendChild(_stdOptions);

        // ── Enhanced options ──
        _enhOptions = document.createElement('div');
        _enhOptions.style.marginTop = 'var(--space-4)';

        var cipherLabel = _createFormLabel('Cipher');
        _enhOptions.appendChild(cipherLabel);

        _cipherSelect = document.createElement('select');
        _cipherSelect.className = 'form-input';
        for (var c = 0; c < CIPHERS.length; c++) {
            var cOpt = document.createElement('option');
            cOpt.value = CIPHERS[c].value;
            cOpt.textContent = CIPHERS[c].text;
            _cipherSelect.appendChild(cOpt);
        }
        _cipherSelect.addEventListener('change', function () {
            BridgeAPI.saveSetting('protect/cipher', _cipherSelect.value);
        });
        _enhOptions.appendChild(_cipherSelect);

        var kdfLabel = _createFormLabel('Key Derivation');
        kdfLabel.style.marginTop = 'var(--space-3)';
        _enhOptions.appendChild(kdfLabel);

        _kdfSelect = document.createElement('select');
        _kdfSelect.className = 'form-input';
        for (var k = 0; k < KDFS.length; k++) {
            var kOpt = document.createElement('option');
            kOpt.value = KDFS[k].value;
            kOpt.textContent = KDFS[k].text;
            _kdfSelect.appendChild(kOpt);
        }
        _kdfSelect.addEventListener('change', function () {
            BridgeAPI.saveSetting('protect/kdf', _kdfSelect.value);
        });
        _enhOptions.appendChild(_kdfSelect);

        // Info note
        var infoNote = document.createElement('div');
        infoNote.style.cssText = 'margin-top: var(--space-3); padding: var(--space-3); background: var(--color-surface-2, var(--color-surface)); border-radius: var(--radius-md, 8px); font-size: var(--font-size-sm); color: var(--color-text-3); line-height: 1.5;';
        infoNote.textContent = 'Enhanced encryption creates .epdf files that can only be opened with this toolkit. Uses military-grade cryptography with memory-hard key derivation for maximum security.';
        _enhOptions.appendChild(infoNote);

        modeCard.body.appendChild(_enhOptions);
        _el.appendChild(modeCard.el);

        // ══════════════════════════════════════════════════════════
        //  Passwords Card
        // ══════════════════════════════════════════════════════════

        var pwCard = _createCard('Passwords');

        // User password
        var userPwLabel = _createFormLabel('User password (required to open)');
        pwCard.body.appendChild(userPwLabel);

        var userPwRow = _createPasswordRow();
        _userPasswordInput = userPwRow.input;
        _userPasswordInput.addEventListener('input', function () {
            _updateStrength(_userPasswordInput.value, _userStrengthBar, _userStrengthLabel);
            _updateUI();
        });
        pwCard.body.appendChild(userPwRow.el);

        // User strength bar
        var userStrengthRow = _createStrengthBar();
        _userStrengthBar = userStrengthRow.bar;
        _userStrengthLabel = userStrengthRow.label;
        pwCard.body.appendChild(userStrengthRow.el);

        // Owner password (standard mode only)
        _ownerPwSection = document.createElement('div');
        var ownerPwLabel = _createFormLabel('Owner password (required to change permissions)');
        ownerPwLabel.style.marginTop = 'var(--space-4)';
        _ownerPwSection.appendChild(ownerPwLabel);

        var ownerPwRow = _createPasswordRow();
        _ownerPasswordInput = ownerPwRow.input;
        _ownerPasswordInput.addEventListener('input', function () {
            _updateStrength(_ownerPasswordInput.value, _ownerStrengthBar, _ownerStrengthLabel);
        });
        _ownerPwSection.appendChild(ownerPwRow.el);

        var ownerStrengthRow = _createStrengthBar();
        _ownerStrengthBar = ownerStrengthRow.bar;
        _ownerStrengthLabel = ownerStrengthRow.label;
        _ownerPwSection.appendChild(ownerStrengthRow.el);

        pwCard.body.appendChild(_ownerPwSection);
        _el.appendChild(pwCard.el);

        // ══════════════════════════════════════════════════════════
        //  Output Card
        // ══════════════════════════════════════════════════════════

        var outCard = _createCard('Output');

        // Output directory
        var dirLabel = _createFormLabel('Output folder');
        outCard.body.appendChild(dirLabel);

        var dirRow = document.createElement('div');
        dirRow.style.cssText = 'display: flex; gap: var(--space-2); align-items: center;';

        _outputDirInput = document.createElement('input');
        _outputDirInput.type = 'text';
        _outputDirInput.className = 'form-input';
        _outputDirInput.placeholder = 'Same folder as input';
        _outputDirInput.readOnly = true;
        _outputDirInput.style.flex = '1';
        dirRow.appendChild(_outputDirInput);

        var dirBrowseBtn = document.createElement('button');
        dirBrowseBtn.className = 'btn btn-secondary';
        dirBrowseBtn.textContent = 'Browse';
        dirBrowseBtn.addEventListener('click', async function () {
            try {
                var path = await BridgeAPI.openFolder();
                if (path) {
                    _outputDirInput.value = path;
                }
            } catch (err) {
                console.error('[ProtectPage] openFolder error:', err);
            }
        });
        dirRow.appendChild(dirBrowseBtn);

        var dirResetBtn = document.createElement('button');
        dirResetBtn.className = 'btn btn-ghost btn-sm';
        dirResetBtn.textContent = 'Reset';
        dirResetBtn.addEventListener('click', function () {
            _outputDirInput.value = '';
        });
        dirRow.appendChild(dirResetBtn);

        outCard.body.appendChild(dirRow);

        // Naming template
        var nameLabel = _createFormLabel('Naming template');
        nameLabel.style.marginTop = 'var(--space-3)';
        outCard.body.appendChild(nameLabel);

        _namingInput = document.createElement('input');
        _namingInput.type = 'text';
        _namingInput.className = 'form-input';
        _namingInput.value = '{name}_protected';
        _namingInput.placeholder = '{name}_protected';
        _namingInput.title = 'Variables: {name} = original filename, {cipher} = cipher used, {mode} = standard/enhanced';
        outCard.body.appendChild(_namingInput);

        var nameHint = document.createElement('div');
        nameHint.style.cssText = 'font-size: var(--font-size-xs); color: var(--color-text-3); margin-top: var(--space-1);';
        nameHint.textContent = 'Variables: {name}, {cipher}, {mode}';
        outCard.body.appendChild(nameHint);

        _el.appendChild(outCard.el);

        // ══════════════════════════════════════════════════════════
        //  Action Bar
        // ══════════════════════════════════════════════════════════

        var actionRow = document.createElement('div');
        actionRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-top: var(--space-4);';

        // Left: open folder button (hidden initially)
        _openFolderBtn = document.createElement('button');
        _openFolderBtn.className = 'btn btn-ghost';
        _openFolderBtn.textContent = 'Open output folder';
        _openFolderBtn.style.display = 'none';
        _openFolderBtn.addEventListener('click', function () {
            var dir = _outputDirInput.value;
            if (!dir && _files.length > 0) {
                dir = BridgeAPI.dirname(_files[0].path);
            }
            if (dir) BridgeAPI.openFolderPath(dir);
        });
        actionRow.appendChild(_openFolderBtn);

        // Spacer
        var spacer = document.createElement('div');
        spacer.style.flex = '1';
        actionRow.appendChild(spacer);

        // Cancel button (hidden initially)
        _cancelBtn = document.createElement('button');
        _cancelBtn.className = 'btn btn-secondary';
        _cancelBtn.textContent = 'Cancel';
        _cancelBtn.style.display = 'none';
        _cancelBtn.addEventListener('click', function () {
            BridgeAPI.cancel('protect');
            _cancelBtn.disabled = true;
            _cancelBtn.textContent = 'Cancelling...';
        });
        actionRow.appendChild(_cancelBtn);

        // Protect button
        _protectBtn = document.createElement('button');
        _protectBtn.className = 'btn btn-primary btn-lg';
        _protectBtn.textContent = 'Protect';
        _protectBtn.disabled = true;
        _protectBtn.addEventListener('click', _startProtect);
        actionRow.appendChild(_protectBtn);

        _el.appendChild(actionRow);

        // ── Progress ──
        _progress = createProgressPanel();
        _progress.onCancel(function () {
            BridgeAPI.cancel('protect');
        });
        _el.appendChild(_progress.el);

        // ── Result label ──
        _resultLabel = document.createElement('div');
        _resultLabel.style.cssText = 'text-align: center; font-size: var(--font-size-sm); margin-top: var(--space-3); min-height: 24px;';
        _el.appendChild(_resultLabel);

        // ── Results panel ──
        _results = createResultsPanel();
        _el.appendChild(_results.el);

        // Set initial mode
        _loadSettings();
        _updateModeVisibility();
    }

    // ══════════════════════════════════════════════════════════════
    //  Helper builders
    // ══════════════════════════════════════════════════════════════

    function _createCard(title) {
        var card = document.createElement('div');
        card.className = 'card';
        card.style.marginTop = 'var(--space-4)';

        if (title) {
            var header = document.createElement('div');
            header.className = 'card-header';
            header.style.cssText = 'font-weight: 600; font-size: var(--font-size-sm); text-transform: uppercase; letter-spacing: 0.05em; color: var(--color-text-2); margin-bottom: var(--space-3);';
            header.textContent = title;
            card.appendChild(header);
        }

        var body = document.createElement('div');
        card.appendChild(body);

        return { el: card, body: body };
    }

    function _createFormLabel(text) {
        var label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = text;
        return label;
    }

    function _createPasswordRow() {
        var row = document.createElement('div');
        row.style.cssText = 'display: flex; gap: var(--space-2); align-items: center;';

        var input = document.createElement('input');
        input.type = 'password';
        input.className = 'form-input';
        input.placeholder = 'Enter password';
        input.style.flex = '1';
        row.appendChild(input);

        var toggleBtn = document.createElement('button');
        toggleBtn.className = 'btn btn-ghost btn-sm';
        toggleBtn.textContent = 'Show';
        toggleBtn.addEventListener('click', function () {
            if (input.type === 'password') {
                input.type = 'text';
                toggleBtn.textContent = 'Hide';
            } else {
                input.type = 'password';
                toggleBtn.textContent = 'Show';
            }
        });
        row.appendChild(toggleBtn);

        return { el: row, input: input };
    }

    function _createStrengthBar() {
        var row = document.createElement('div');
        row.style.cssText = 'margin-top: var(--space-1); display: flex; align-items: center; gap: var(--space-2);';

        var bar = document.createElement('div');
        bar.style.cssText = 'flex: 1; height: 4px; border-radius: 2px; background-color: var(--color-border); overflow: hidden;';

        var fill = document.createElement('div');
        fill.style.cssText = 'height: 100%; width: 0%; transition: width 0.3s, background-color 0.3s;';
        bar.appendChild(fill);
        row.appendChild(bar);

        var label = document.createElement('span');
        label.style.cssText = 'font-size: var(--font-size-xs); color: var(--color-text-3); min-width: 60px;';
        row.appendChild(label);

        return { el: row, bar: bar, label: label };
    }

    function _createCheckbox(labelText, checked) {
        var wrapper = document.createElement('label');
        wrapper.style.cssText = 'display: flex; align-items: center; gap: var(--space-2); cursor: pointer; font-size: var(--font-size-sm);';

        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = checked;
        wrapper.appendChild(cb);

        var span = document.createElement('span');
        span.textContent = labelText;
        wrapper.appendChild(span);

        return { el: wrapper, checkbox: cb };
    }

    // ══════════════════════════════════════════════════════════════
    //  Settings
    // ══════════════════════════════════════════════════════════════

    async function _loadSettings() {
        try {
            var mode = await BridgeAPI.loadSetting('protect/mode');
            if (mode && (mode === '"standard"' || mode === '"enhanced"')) {
                _mode = JSON.parse(mode);
                _modeSelect.value = _mode;
                _updateModeVisibility();
            }
            var cipher = await BridgeAPI.loadSetting('protect/cipher');
            if (cipher) {
                try { _cipherSelect.value = JSON.parse(cipher); } catch(e) {}
            }
            var kdf = await BridgeAPI.loadSetting('protect/kdf');
            if (kdf) {
                try { _kdfSelect.value = JSON.parse(kdf); } catch(e) {}
            }
            var naming = await BridgeAPI.loadSetting('protect/naming');
            if (naming) {
                try {
                    var n = JSON.parse(naming);
                    if (n) _namingInput.value = n;
                } catch(e) {}
            }
        } catch (err) {
            console.error('[ProtectPage] loadSettings error:', err);
        }
    }

    // ══════════════════════════════════════════════════════════════
    //  Mode switching
    // ══════════════════════════════════════════════════════════════

    function _updateModeVisibility() {
        var isStd = _mode === 'standard';
        _stdOptions.style.display = isStd ? '' : 'none';
        _enhOptions.style.display = isStd ? 'none' : '';
        _ownerPwSection.style.display = isStd ? '' : 'none';

        // Update user password placeholder
        _userPasswordInput.placeholder = isStd
            ? 'Required to open the PDF'
            : 'Encryption password';
    }

    // ══════════════════════════════════════════════════════════════
    //  Password strength
    // ══════════════════════════════════════════════════════════════

    function _updateStrength(password, barEl, labelEl) {
        var fill = barEl.firstChild;
        if (!password) {
            fill.style.width = '0%';
            fill.style.backgroundColor = '';
            labelEl.textContent = '';
            return;
        }

        var score = 0;
        if (password.length >= 8) score++;
        if (password.length >= 12) score++;
        if (/[A-Z]/.test(password) && /[a-z]/.test(password)) score++;
        if (/[0-9]/.test(password)) score++;
        if (/[^A-Za-z0-9]/.test(password)) score++;

        var level, color, width;
        if (score <= 1) {
            level = 'Weak'; color = 'var(--color-red, #e53e3e)'; width = '25%';
        } else if (score <= 2) {
            level = 'Fair'; color = 'var(--color-yellow, #d69e2e)'; width = '50%';
        } else if (score <= 3) {
            level = 'Moderate'; color = 'var(--color-yellow, #d69e2e)'; width = '66%';
        } else {
            level = 'Strong'; color = 'var(--color-green, #38a169)'; width = '100%';
        }

        fill.style.width = width;
        fill.style.backgroundColor = color;
        labelEl.textContent = level;
        labelEl.style.color = color;
    }

    // ══════════════════════════════════════════════════════════════
    //  UI updates
    // ══════════════════════════════════════════════════════════════

    function _updateSummary() {
        if (_files.length === 0) {
            _summaryLabel.textContent = '';
            if (_fileList) _fileList.el.style.display = 'none';
            return;
        }

        var modeLabel = _mode === 'standard' ? 'Standard PDF' : 'Enhanced .epdf';
        _summaryLabel.textContent = _files.length + ' file' + (_files.length !== 1 ? 's' : '') + '  \u00b7  ' + modeLabel;

        // Show file list for batch
        if (_files.length > 0 && _fileList) {
            _fileList.clear();
            _fileList.addFiles(_files.map(function (f) {
                return { path: f.path, name: f.name || BridgeAPI.basename(f.path), status: 'pending' };
            }));
            _fileList.el.style.display = '';
        }
    }

    function _updateUI() {
        var hasFiles = _files.length > 0;
        var hasPassword = _userPasswordInput && _userPasswordInput.value.length > 0;
        _protectBtn.disabled = _busy || !hasFiles || !hasPassword;
    }

    // ══════════════════════════════════════════════════════════════
    //  Run protection
    // ══════════════════════════════════════════════════════════════

    function _startProtect() {
        if (_files.length === 0) {
            Toast.warning('Please add at least one PDF file.');
            return;
        }
        var userPw = _userPasswordInput.value;
        if (!userPw) {
            Toast.warning('Please enter a password.');
            return;
        }

        _busy = true;
        _updateUI();
        _results.hide();
        _resultLabel.textContent = '';
        _resultLabel.style.color = '';
        _openFolderBtn.style.display = 'none';
        _progress.reset();
        _progress.show();
        _protectBtn.style.display = 'none';
        _cancelBtn.style.display = '';
        _cancelBtn.disabled = false;
        _cancelBtn.textContent = 'Cancel';

        // Build file paths
        var filePaths = _files.map(function (f) { return f.path; });

        // Build naming template
        var naming = _namingInput.value.trim() || '{name}_protected';

        // Collect params based on mode
        var params = {
            files: filePaths,
            user_password: userPw,
            owner_password: _ownerPasswordInput ? _ownerPasswordInput.value : '',
            mode: _mode,
            output_dir: _outputDirInput.value || '',
            naming: naming,
        };

        if (_mode === 'standard') {
            params.encryption = _encryptionSelect.value;
            var permissions = [];
            if (_permPrint.checkbox.checked) permissions.push('print');
            if (_permModify.checkbox.checked) permissions.push('modify');
            if (_permCopy.checkbox.checked) permissions.push('copy');
            if (_permAnnotate.checkbox.checked) permissions.push('annotate');
            params.permissions = permissions;
        } else {
            params.cipher = _cipherSelect.value;
            params.kdf = _kdfSelect.value;
        }

        // Save settings
        BridgeAPI.saveSetting('protect/mode', _mode);
        BridgeAPI.saveSetting('protect/naming', naming);
        if (_mode === 'enhanced') {
            BridgeAPI.saveSetting('protect/cipher', _cipherSelect.value);
            BridgeAPI.saveSetting('protect/kdf', _kdfSelect.value);
        }

        BridgeAPI.startProtect(params);
    }

    // ══════════════════════════════════════════════════════════════
    //  Events
    // ══════════════════════════════════════════════════════════════

    function _bindEvents() {
        _onProgress = function (data) {
            if (data.toolKey !== 'protect') return;
            _progress.update(
                data.pct || 0,
                data.filename || '',
                data.current || 0,
                data.total || 0
            );
            // Update file list status
            if (_fileList && data.current != null && data.current < _files.length) {
                _fileList.setStatus(data.current, 'processing');
            }
        };

        _onDone = function (data) {
            if (data.toolKey !== 'protect') return;
            _busy = false;
            _progress.hide();
            _protectBtn.style.display = '';
            _cancelBtn.style.display = 'none';
            _updateUI();

            if (data.success) {
                var results = data.results || {};
                var fileResults = results.files || [];
                var nOk = 0, nErr = 0;

                for (var i = 0; i < fileResults.length; i++) {
                    var fr = fileResults[i];
                    if (_fileList) {
                        if (fr.status === 'error') {
                            _fileList.setStatus(i, 'error');
                            nErr++;
                        } else {
                            _fileList.setStatus(i, 'done');
                            nOk++;
                        }
                    }
                }

                // Fallback for single file
                if (fileResults.length === 0) nOk = _files.length;

                var elapsed = results.elapsed ? results.elapsed.toFixed(1) + 's' : '';
                var parts = [];
                if (nOk) parts.push(nOk + ' protected');
                if (nErr) parts.push(nErr + ' failed');
                if (elapsed) parts.push(elapsed);

                _resultLabel.textContent = parts.join('  \u00b7  ');
                _resultLabel.style.color = nErr ? 'var(--color-red)' : 'var(--color-green)';
                _openFolderBtn.style.display = '';

                if (nOk > 0) {
                    Toast.success(nOk + ' file' + (nOk !== 1 ? 's' : '') + ' protected successfully!');
                }

                _results.show({
                    files: fileResults,
                    totalTime: results.elapsed || 0,
                    outputDir: results.output_dir || '',
                });
            } else {
                var errMsg = data.message || 'Protection failed.';
                _resultLabel.textContent = errMsg;
                _resultLabel.style.color = 'var(--color-red)';
                Toast.error(errMsg);
            }
        };

        EventBus.on('progress', _onProgress);
        EventBus.on('done', _onDone);
    }

    function _unbindEvents() {
        if (_onProgress) EventBus.off('progress', _onProgress);
        if (_onDone) EventBus.off('done', _onDone);
    }

    // ══════════════════════════════════════════════════════════════
    //  Page lifecycle
    // ══════════════════════════════════════════════════════════════

    return {
        onMount: function (container) {
            _buildUI(container);
            _bindEvents();
        },
        onActivated: function () {},
        onDeactivated: function () {
            _unbindEvents();
        },
        isBusy: function () { return _busy; },
        handleDrop: function (files) {
            if (_dropZone) {
                var paths = files.map(function (f) { return f.path || f; });
                _dropZone.setFiles(paths);
            }
        },
    };
}

Router.register('protect', function () { return ProtectPage(); });
