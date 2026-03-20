"""
bridge.py -- QObject bridge between QWebEngineView (JavaScript) and Python backend.

JavaScript calls @Slot methods via QWebChannel; this object emits Signals
back to the JS layer for progress updates, operation results, file drops,
and theme changes.

All long-running operations are dispatched to background threads so the
UI stays responsive.
"""

import json
import logging
import os
import platform
import subprocess
import sys
import threading
import traceback
from dataclasses import asdict, is_dataclass
from typing import Any

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from engine import (
    PRESET_ORDER,
    PRESETS,
    CancelledError,
    EncryptedPDFError,
    FileTooLargeError,
    InvalidPDFError,
    PDFAnalysis,
    Result,
    analyze_pdf,
    compress_pdf,
    find_ghostscript,
    fmt_size,
)
from epdf_crypto import (
    epdf_encrypt, epdf_decrypt, is_epdf, epdf_read_metadata,
    CIPHERS as EPDF_CIPHERS, KDFS as EPDF_KDFS,
    EPDFError, EPDFPasswordError,
)

from pdf_ops import (
    CompareResult,
    ExtractImagesResult,
    ExtractTextResult,
    ImagesToPdfResult,
    MergeResult,
    PageOpResult,
    PdfToImagesResult,
    PdfToWordResult,
    RedactResult,
    SplitResult,
    add_page_numbers,
    add_watermark,
    apply_page_operations,
    compare_pdfs,
    crop_pages,
    extract_images,
    extract_text,
    flatten_pdf,
    images_to_pdf,
    merge_pdfs,
    nup_layout,
    pdf_to_images,
    pdf_to_word,
    protect_pdf,
    read_metadata,
    redact_text,
    repair_pdf,
    get_toc,
    split_pdf,
    unlock_pdf,
    write_metadata,
)

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Worker thread
# ═══════════════════════════════════════════════════════════════════

class _Worker(QThread):
    """Generic worker that runs a callable in a QThread."""

    progress = Signal(str)   # JSON progress payload
    finished = Signal(str)   # JSON result payload

    def __init__(self, tool_key: str, func, args, kwargs, parent=None):
        super().__init__(parent)
        self.tool_key = tool_key
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._func(*self._args, **self._kwargs)
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, True, result=result),
                ensure_ascii=False,
            ))
        except (CancelledError, InterruptedError):
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, False, message="Cancelled"),
                ensure_ascii=False,
            ))
        except EncryptedPDFError as exc:
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, False,
                              message=f"PDF is password-protected: {exc}"),
                ensure_ascii=False,
            ))
        except InvalidPDFError as exc:
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, False,
                              message=f"Invalid PDF: {exc}"),
                ensure_ascii=False,
            ))
        except FileTooLargeError as exc:
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, False,
                              message=f"File too large: {exc}"),
                ensure_ascii=False,
            ))
        except Exception as exc:
            log.exception("Operation %s failed", self.tool_key)
            self.finished.emit(json.dumps(
                _done_payload(self.tool_key, False,
                              message=str(exc)),
                ensure_ascii=False,
            ))


# ═══════════════════════════════════════════════════════════════════
#  Serialization helpers
# ═══════════════════════════════════════════════════════════════════

def _normalize_params(p: dict) -> dict:
    """Accept both camelCase and snake_case keys from JavaScript.

    Also maps common shorthand keys used by the JS pages to the canonical
    Python parameter names expected by each Slot method.
    """
    import re
    out = dict(p)

    for key, value in list(p.items()):
        # snake_case -> camelCase
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), key)
        if camel not in out:
            out[camel] = value
        # camelCase -> snake_case
        snake = re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), key)
        if snake not in out:
            out[snake] = value

    # Common JS shorthand -> canonical key mappings
    if "file" in out and "inputPath" not in out:
        out["inputPath"] = out["file"]
    if "files" in out and "inputPaths" not in out:
        out["inputPaths"] = out["files"]
    if "files" in out and "imagePaths" not in out:
        out["imagePaths"] = out["files"]
    if "file_a" in out and "pathA" not in out:
        out["pathA"] = out["file_a"]
    if "file_b" in out and "pathB" not in out:
        out["pathB"] = out["file_b"]
    if "fileA" in out and "pathA" not in out:
        out["pathA"] = out["fileA"]
    if "fileB" in out and "pathB" not in out:
        out["pathB"] = out["fileB"]

    return out


def _serialize(obj: Any) -> Any:
    """Convert a dataclass / result object to a JSON-safe dict."""
    if obj is None:
        return None
    if is_dataclass(obj) and not isinstance(obj, type):
        d = {}
        for k, v in asdict(obj).items():
            d[k] = _serialize(v)
        return d
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (int, float, str, bool)):
        return obj
    return str(obj)


def _done_payload(
    tool_key: str,
    success: bool,
    *,
    message: str = "",
    result: Any = None,
) -> dict:
    """Build the JSON payload for operationDone."""
    payload: dict[str, Any] = {
        "toolKey": tool_key,
        "success": success,
        "message": message,
    }
    if result is not None:
        serialized = _serialize(result)
        # For compress results, add human-readable sizes
        if isinstance(result, Result):
            serialized["originalSizeStr"] = fmt_size(result.original_size)
            serialized["compressedSizeStr"] = fmt_size(result.compressed_size)
            serialized["savedPct"] = round(result.saved_pct, 1)
        elif isinstance(result, list):
            # Batch compress returns a list of Result objects
            for i, item in enumerate(result):
                if isinstance(item, Result) and isinstance(serialized, list) and i < len(serialized):
                    serialized[i]["originalSizeStr"] = fmt_size(item.original_size)
                    serialized[i]["compressedSizeStr"] = fmt_size(item.compressed_size)
                    serialized[i]["savedPct"] = round(item.saved_pct, 1)
        payload["results"] = serialized
    return payload


def _progress_payload(
    tool_key: str,
    current: int,
    total: int,
    filename: str = "",
) -> str:
    pct = round(current / total * 100, 1) if total > 0 else 0
    return json.dumps({
        "toolKey": tool_key,
        "current": current,
        "total": total,
        "pct": pct,
        "filename": filename,
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
#  Bridge
# ═══════════════════════════════════════════════════════════════════

class Bridge(QObject):
    """
    QWebChannel bridge between JavaScript and Python backend.

    Signals are emitted to the JS side; Slots are called from JS.
    """

    # ── Signals (Python -> JS) ────────────────────────────────────
    progressUpdate = Signal(str)
    operationDone = Signal(str)
    filesDropped = Signal(str)
    themeChanged = Signal(str)
    themeToggleRequested = Signal()  # JS requests toggle -> shell handles

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_events: dict[str, threading.Event] = {}
        self._workers: dict[str, _Worker] = {}
        self._settings = QSettings("PDFCompress", "PDFCompress")
        self._qwebchannel_js_path = ""

    def set_qwebchannel_js_path(self, path: str):
        """Store the path to qwebchannel.js for potential JS queries."""
        self._qwebchannel_js_path = path

    # ── Internal helpers ──────────────────────────────────────────

    def _make_progress_callback(self, tool_key: str, filename: str = ""):
        """Return a progress callback that emits progressUpdate."""
        def _on_progress(current: int, total: int):
            self.progressUpdate.emit(
                _progress_payload(tool_key, current, total, filename)
            )
        return _on_progress

    def _make_cancel_event(self, tool_key: str) -> threading.Event:
        """Create (or reset) a cancellation event for the given tool.

        If a previous cancel event exists for this tool, it is signalled
        first so any in-flight worker knows to stop.
        """
        old = self._cancel_events.get(tool_key)
        if old is not None:
            old.set()
        evt = threading.Event()
        self._cancel_events[tool_key] = evt
        return evt

    def _run_in_thread(self, tool_key: str, func, *args, **kwargs):
        """
        Dispatch *func* to a background QThread.

        Connects the worker's signals to this bridge's signals and
        cleans up on completion.
        """
        # NOTE: cancellation of any previous run is handled by
        # _make_cancel_event (called before this method), so we
        # must NOT set the current cancel event here.

        worker = _Worker(tool_key, func, args, kwargs, parent=self)

        def _on_progress(payload: str):
            self.progressUpdate.emit(payload)

        def _on_finished(payload: str):
            self.operationDone.emit(payload)
            # Cleanup
            self._workers.pop(tool_key, None)
            self._cancel_events.pop(tool_key, None)
            worker.deleteLater()

        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        self._workers[tool_key] = worker
        worker.start()

    # ── File / folder dialogs (synchronous) ───────────────────────

    @Slot(str, result=str)
    def openFileDialog(self, filter_str: str) -> str:
        """Open a native file picker. Returns JSON array of selected paths."""
        paths, _ = QFileDialog.getOpenFileNames(
            None, "Select File(s)", "", filter_str
        )
        return json.dumps(paths, ensure_ascii=False)

    @Slot(result=str)
    def openFolderDialog(self) -> str:
        """Open a native folder picker. Returns JSON string (path or null)."""
        path = QFileDialog.getExistingDirectory(None, "Select Folder", "")
        return json.dumps(path if path else None, ensure_ascii=False)

    @Slot(str, str, result=str)
    def saveFileDialog(self, filter_str: str, default_name: str) -> str:
        """Open a native save-file dialog. Returns JSON string (path or null)."""
        path, _ = QFileDialog.getSaveFileName(
            None, "Save As", default_name, filter_str
        )
        return json.dumps(path if path else None, ensure_ascii=False)

    # ── Synchronous queries ───────────────────────────────────────

    @Slot(str, result=str)
    def getPresets(self, _unused: str = "") -> str:
        """Return compression presets as JSON."""
        presets = []
        for key in PRESET_ORDER:
            p = PRESETS[key]
            presets.append({
                "key": key,
                "name": p.name,
                "description": p.description,
                "targetDpi": p.target_dpi,
                "jpegQuality": p.jpeg_quality,
            })
        return json.dumps({
            "presets": presets,
            "defaultPreset": "standard",
            "ghostscriptAvailable": find_ghostscript() is not None,
        }, ensure_ascii=False)

    @Slot(str, result=str)
    def analyzeFile(self, path: str) -> str:
        """Quick synchronous analysis of a PDF. Returns JSON.

        Includes per-preset size estimates and per-image DPI/type details
        so the JS UI can show an accurate pre-compression preview.
        """
        try:
            analysis = analyze_pdf(path)
            data = _serialize(analysis)
            data["fileSizeStr"] = fmt_size(analysis.file_size)
            data["imageBytesStr"] = fmt_size(analysis.image_bytes)

            # ── Per-preset size estimates using the engine's model ──
            estimates = {}
            for key in PRESET_ORDER:
                preset = PRESETS[key]
                est = analysis.estimate_output(preset)
                saved = max(0, analysis.file_size - est)
                saved_pct = (saved / analysis.file_size * 100) if analysis.file_size > 0 else 0
                estimates[key] = {
                    "estimatedSize": est,
                    "estimatedSizeStr": fmt_size(est),
                    "savedBytes": saved,
                    "savedBytesStr": fmt_size(saved),
                    "savedPct": round(saved_pct, 1),
                    "targetDpi": preset.target_dpi,
                    "jpegQuality": preset.jpeg_quality,
                }
            data["estimates"] = estimates

            # ── Image summary for the UI ──
            if analysis.images:
                dpis = [img.effective_dpi for img in analysis.images if img.effective_dpi > 0]
                img_summary = {
                    "count": analysis.image_count,
                    "totalBytes": analysis.image_bytes,
                    "totalBytesStr": fmt_size(analysis.image_bytes),
                    "avgDpi": round(sum(dpis) / len(dpis), 0) if dpis else 0,
                    "maxDpi": round(max(dpis), 0) if dpis else 0,
                    "minDpi": round(min(dpis), 0) if dpis else 0,
                    "jpegCount": sum(1 for img in analysis.images if img.is_jpeg),
                    "grayscaleCount": sum(1 for img in analysis.images if img.is_grayscale),
                    "monochromeCount": sum(1 for img in analysis.images if img.is_monochrome),
                    "pctOfFile": round(analysis.image_bytes / analysis.file_size * 100, 1) if analysis.file_size > 0 else 0,
                }
                data["imageSummary"] = img_summary
            else:
                data["imageSummary"] = {
                    "count": 0, "totalBytes": 0, "totalBytesStr": "0 B",
                    "avgDpi": 0, "maxDpi": 0, "minDpi": 0,
                    "jpegCount": 0, "grayscaleCount": 0, "monochromeCount": 0,
                    "pctOfFile": 0,
                }

            return json.dumps({"success": True, **data}, ensure_ascii=False)
        except EncryptedPDFError as exc:
            return json.dumps({
                "success": False,
                "error": f"Password-protected: {exc}",
                "encrypted": True,
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({
                "success": False,
                "error": str(exc),
            }, ensure_ascii=False)

    @Slot(str, result=str)
    def getThumbnail(self, path: str) -> str:
        """Render page 1 of a PDF as a small JPEG thumbnail (base64 data URL).

        Returns JSON: { success, dataUrl, width, height }
        """
        try:
            import base64
            import fitz  # PyMuPDF

            doc = fitz.open(path)
            if doc.page_count == 0:
                doc.close()
                return json.dumps({"success": False, "error": "Empty PDF"})
            page = doc[0]

            # Render at low DPI for a small thumbnail (max ~200px wide)
            zoom = min(200.0 / max(page.rect.width, 1), 2.0)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            # Encode as JPEG for smaller payload
            img_bytes = pix.tobytes("jpeg", jpg_quality=70)
            b64 = base64.b64encode(img_bytes).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

            w, h = pix.width, pix.height
            doc.close()

            return json.dumps({
                "success": True,
                "dataUrl": data_url,
                "width": w,
                "height": h,
            }, ensure_ascii=False)
        except Exception as exc:
            log.exception("getThumbnail failed for %s", path)
            return json.dumps({"success": False, "error": str(exc)})

    @Slot(str, result=str)
    def getMetadata(self, path: str) -> str:
        """Read PDF metadata. Returns JSON."""
        try:
            meta = read_metadata(path)
            return json.dumps({"success": True, "metadata": meta},
                              ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"success": False, "error": str(exc)},
                              ensure_ascii=False)

    @Slot(str, result=str)
    def getToc(self, path: str) -> str:
        """Extract PDF table of contents / bookmarks. Returns JSON list."""
        try:
            entries = get_toc(path)
            return json.dumps(entries, ensure_ascii=False)
        except Exception as exc:
            log.warning("getToc failed for %s: %s", path, exc)
            return json.dumps([])

    @Slot(result=str)
    def getToolRegistry(self) -> str:
        """Return JSON tool definitions for the JS side."""
        from ui.tool_registry import CATEGORIES, get_tools

        tools = []
        for t in get_tools():
            tools.append({
                "key": t.key,
                "title": t.title,
                "description": t.description,
                "icon": t.icon,
                "category": t.category,
                "acceptedExtensions": t.accepted_extensions,
            })

        cats = [{"key": k, "label": v} for k, v in CATEGORIES.items()]

        return json.dumps({
            "tools": tools,
            "categories": cats,
        }, ensure_ascii=False)

    # ── Settings ──────────────────────────────────────────────────

    @Slot(str, str)
    def saveSetting(self, key: str, value: str):
        self._settings.setValue(key, value)

    @Slot(str, result=str)
    def loadSetting(self, key: str) -> str:
        val = self._settings.value(key, None)
        return json.dumps(val, ensure_ascii=False)

    # ── OS integration ────────────────────────────────────────────

    @Slot(str)
    def openFolder(self, path: str):
        """Open a folder in the OS file manager."""
        path = os.path.normpath(path)
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            log.exception("Failed to open folder: %s", path)

    @Slot(str)
    def openFile(self, path: str):
        """Open a file with the default application."""
        path = os.path.normpath(path)
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            log.exception("Failed to open file: %s", path)

    # ── Theme toggle (JS -> Python shell) ─────────────────────────

    @Slot()
    def requestThemeToggle(self):
        """Called from JS when the user clicks the theme toggle button."""
        self.themeToggleRequested.emit()

    # ── Cancellation ──────────────────────────────────────────────

    @Slot(str)
    def cancelOperation(self, tool_key: str):
        """Signal cancellation for a running operation."""
        evt = self._cancel_events.get(tool_key)
        if evt is not None:
            evt.set()
            log.info("Cancellation requested for %s", tool_key)

    # ── Compress ──────────────────────────────────────────────────

    @Slot(str)
    def startCompress(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "compress")
        cancel = self._make_cancel_event(tool_key)

        # Support both single file (inputPath) and batch (files/inputPaths)
        files = p.get("inputPaths") or p.get("files") or []
        if not files and p.get("inputPath"):
            files = [p["inputPath"]]

        preset_key = p.get("preset", "standard")
        use_gs = p.get("useGhostscript") or p.get("useGs", False)

        def _work():
            results = []
            for i, fpath in enumerate(files):
                if cancel.is_set():
                    raise CancelledError("Cancelled")
                self.progressUpdate.emit(
                    _progress_payload(tool_key, i, len(files),
                                      os.path.basename(fpath))
                )
                result = compress_pdf(
                    input_path=fpath,
                    output_path=p.get("outputPath"),
                    preset_key=preset_key,
                    linearize=p.get("linearize", False),
                    cancel=cancel,
                    password=p.get("password"),
                    use_ghostscript=use_gs,
                    backup_on_overwrite=p.get("backupOnOverwrite", True),
                )
                results.append(result)
            return results

        self._run_in_thread(tool_key, _work)

    # ── Merge ─────────────────────────────────────────────────────

    @Slot(str)
    def startMerge(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "merge")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return merge_pdfs(
                input_paths=p["inputPaths"],
                output_path=p["outputPath"],
                on_progress=progress_cb,
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Split ─────────────────────────────────────────────────────

    @Slot(str)
    def startSplit(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "split")
        cancel = self._make_cancel_event(tool_key)

        input_path = p["inputPath"]
        output_dir = p.get("outputDir") or os.path.dirname(os.path.abspath(input_path))

        def _work():
            return split_pdf(
                input_path=input_path,
                output_dir=output_dir,
                mode=p.get("mode", "all"),
                ranges=p.get("ranges"),
                every_n=p.get("everyN", 1),
                chapters=p.get("chapters"),
                name_template=p.get("nameTemplate", "{name}_page_{start}"),
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Page Operations ───────────────────────────────────────────

    @Slot(str)
    def startPageOps(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "page_ops")
        cancel = self._make_cancel_event(tool_key)

        def _work():
            return apply_page_operations(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                rotations=p.get("rotations"),
                delete_pages=p.get("deletePages"),
                new_order=p.get("newOrder"),
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Protect ───────────────────────────────────────────────────

    @Slot(str)
    def startProtect(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "protect")
        cancel = self._make_cancel_event(tool_key)

        # Support batch (files) or single (file/inputPath)
        files = p.get("files") or p.get("inputPaths") or []
        if not files and p.get("inputPath"):
            files = [p["inputPath"]]

        mode = p.get("mode", "standard")
        output_dir = p.get("outputDir") or p.get("output_dir", "")
        naming = p.get("naming", "{name}_protected")
        user_pw = p.get("userPassword") or p.get("user_password", "")
        owner_pw = p.get("ownerPassword") or p.get("owner_password", "")

        # Standard mode params
        encryption = p.get("encryption", "AES-256")
        permissions = p.get("permissions")

        # Enhanced mode params
        cipher = p.get("cipher", "chacha20-poly1305")
        kdf = p.get("kdf", "argon2id")

        def _work():
            import time
            t0 = time.time()
            file_results = []

            for i, fpath in enumerate(files):
                if cancel.is_set():
                    raise CancelledError("Cancelled")

                fname = os.path.basename(fpath)
                name_no_ext = os.path.splitext(fname)[0]

                self.progressUpdate.emit(
                    _progress_payload(tool_key, i, len(files), fname)
                )

                try:
                    # Build output path
                    if mode == "enhanced":
                        cipher_label = cipher.split("-")[0]
                        ext = ".epdf"
                    else:
                        cipher_label = encryption.lower()
                        ext = ".pdf"

                    try:
                        out_name = naming.format(
                            name=name_no_ext,
                            cipher=cipher_label,
                            mode=mode,
                        )
                    except (KeyError, IndexError):
                        out_name = f"{name_no_ext}_protected"

                    out_folder = output_dir or os.path.dirname(fpath)
                    out_path = os.path.join(out_folder, out_name + ext)

                    if mode == "enhanced":
                        epdf_encrypt(
                            fpath, out_path, user_pw,
                            cipher=cipher, kdf=kdf,
                        )
                        detail = f"{cipher} / {kdf}"
                    else:
                        protect_pdf(
                            fpath, out_path,
                            user_pw, owner_pw,
                            permissions, encryption,
                        )
                        detail = encryption

                    file_results.append({
                        "file": fname,
                        "status": "ok",
                        "details": detail,
                        "outputPath": out_path,
                    })
                except Exception as exc:
                    log.error("Protection failed for %s: %s", fpath, exc)
                    file_results.append({
                        "file": fname,
                        "status": "error",
                        "details": str(exc)[:100],
                    })

            elapsed = time.time() - t0
            return {
                "files": file_results,
                "elapsed": round(elapsed, 2),
                "output_dir": output_dir or (
                    os.path.dirname(files[0]) if files else ""),
            }

        self._run_in_thread(tool_key, _work)

    # ── Unlock ────────────────────────────────────────────────────

    @Slot(str)
    def startUnlock(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "unlock")
        cancel = self._make_cancel_event(tool_key)

        # Support batch
        files = p.get("files") or p.get("inputPaths") or []
        if not files and p.get("inputPath"):
            files = [p["inputPath"]]

        password = p.get("password", "")
        output_dir = p.get("outputDir") or p.get("output_dir", "")
        naming = p.get("naming", "{name}_unlocked")

        def _work():
            import time
            t0 = time.time()
            file_results = []

            for i, fpath in enumerate(files):
                if cancel.is_set():
                    raise CancelledError("Cancelled")

                fname = os.path.basename(fpath)
                name_no_ext = os.path.splitext(fname)[0]

                self.progressUpdate.emit(
                    _progress_payload(tool_key, i, len(files), fname)
                )

                try:
                    try:
                        out_name = naming.format(name=name_no_ext)
                    except (KeyError, IndexError):
                        out_name = f"{name_no_ext}_unlocked"

                    out_folder = output_dir or os.path.dirname(fpath)
                    out_path = os.path.join(out_folder, out_name + ".pdf")

                    if is_epdf(fpath):
                        epdf_decrypt(fpath, out_path, password)
                        detail = "EPDF decrypted"
                    else:
                        unlock_pdf(
                            input_path=fpath,
                            output_path=out_path,
                            password=password,
                        )
                        detail = "PDF unlocked"

                    file_results.append({
                        "file": fname,
                        "status": "ok",
                        "details": detail,
                        "outputPath": out_path,
                    })
                except EPDFPasswordError as exc:
                    file_results.append({
                        "file": fname,
                        "status": "error",
                        "details": "Wrong password",
                    })
                except Exception as exc:
                    log.error("Unlock failed for %s: %s", fpath, exc)
                    file_results.append({
                        "file": fname,
                        "status": "error",
                        "details": str(exc)[:100],
                    })

            elapsed = time.time() - t0
            return {
                "files": file_results,
                "elapsed": round(elapsed, 2),
                "output_dir": output_dir or (
                    os.path.dirname(files[0]) if files else ""),
            }

        self._run_in_thread(tool_key, _work)

    @Slot(str, result=str)
    def checkEpdf(self, path: str) -> str:
        """Check if a file is .epdf and return metadata if so."""
        try:
            if is_epdf(path):
                meta = epdf_read_metadata(path)
                cipher_label = EPDF_CIPHERS.get(
                    meta.get("cipher", ""), meta.get("cipher", ""))
                kdf_label = EPDF_KDFS.get(
                    meta.get("kdf", ""), meta.get("kdf", ""))
                return json.dumps({
                    "isEpdf": True,
                    "cipher": cipher_label,
                    "kdf": kdf_label,
                    "originalFilename": meta.get("original_filename", ""),
                    "created": meta.get("created", ""),
                }, ensure_ascii=False)
            else:
                return json.dumps({"isEpdf": False})
        except Exception as exc:
            return json.dumps({"isEpdf": False, "error": str(exc)})

    # ── PDF to Images ─────────────────────────────────────────────

    @Slot(str)
    def startPdfToImages(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "pdf_to_images")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return pdf_to_images(
                input_path=p["inputPath"],
                output_dir=p["outputDir"],
                fmt=p.get("format", "png"),
                dpi=p.get("dpi", 150),
                quality=p.get("quality", 85),
                page_range=p.get("pageRange"),
                on_progress=progress_cb,
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Images to PDF ─────────────────────────────────────────────

    @Slot(str)
    def startImagesToPdf(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "images_to_pdf")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return images_to_pdf(
                image_paths=p["imagePaths"],
                output_path=p["outputPath"],
                page_size=p.get("pageSize", "auto"),
                margin_mm=p.get("marginMm", 10),
                on_progress=progress_cb,
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── PDF to Word ───────────────────────────────────────────────

    @Slot(str)
    def startPdfToWord(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "pdf_to_word")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return pdf_to_word(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                on_progress=progress_cb,
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Watermark ─────────────────────────────────────────────────

    @Slot(str)
    def startWatermark(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "watermark")
        cancel = self._make_cancel_event(tool_key)

        # Support batch (files) or single (file/inputPath)
        files = p.get("files") or p.get("inputPaths") or []
        if not files and p.get("inputPath"):
            files = [p["inputPath"]]

        # Single-file legacy: output_path directly specified
        single_output = p.get("outputPath")

        text      = p.get("text", "WATERMARK")
        opacity   = p.get("opacity", 0.3)
        rotation  = p.get("rotation", 45)
        font_size = p.get("fontSize", 48)
        color     = p.get("color", "#888888")
        position  = p.get("position", "center")
        page_range = p.get("pageRange")
        output_dir = p.get("outputDir") or p.get("output_dir", "")
        naming    = p.get("naming", "{name}_watermarked")

        def _work():
            import time
            t0 = time.time()
            file_results = []

            for i, fpath in enumerate(files):
                if cancel.is_set():
                    raise CancelledError("Cancelled")

                fname = os.path.basename(fpath)
                name_no_ext = os.path.splitext(fname)[0]

                self.progressUpdate.emit(
                    _progress_payload(tool_key, i, len(files), fname)
                )

                try:
                    # Build output path
                    if single_output and len(files) == 1:
                        out_path = single_output
                    else:
                        try:
                            out_name = naming.format(name=name_no_ext)
                        except (KeyError, IndexError):
                            out_name = f"{name_no_ext}_watermarked"
                        out_folder = output_dir or os.path.dirname(fpath)
                        out_path = os.path.join(out_folder, out_name + ".pdf")

                    add_watermark(
                        input_path=fpath,
                        output_path=out_path,
                        text=text,
                        opacity=opacity,
                        rotation=rotation,
                        font_size=font_size,
                        color=color,
                        position=position,
                        page_range=page_range,
                        cancel=cancel,
                    )

                    file_results.append({
                        "file": fname,
                        "status": "ok",
                        "details": f"{text} ({position})",
                        "outputPath": out_path,
                    })
                except Exception as exc:
                    log.error("Watermark failed for %s: %s", fpath, exc)
                    file_results.append({
                        "file": fname,
                        "status": "error",
                        "details": str(exc)[:100],
                    })

            elapsed = time.time() - t0
            return {
                "files": file_results,
                "elapsed": round(elapsed, 2),
                "output_dir": output_dir or (
                    os.path.dirname(files[0]) if files else ""),
            }

        self._run_in_thread(tool_key, _work)

    # ── Page Numbers ──────────────────────────────────────────────

    @Slot(str)
    def startPageNumbers(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "page_numbers")
        cancel = self._make_cancel_event(tool_key)

        def _work():
            add_page_numbers(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                position=p.get("position", "bottom-center"),
                font_size=p.get("fontSize", 10),
                fmt=p.get("format", "{page}"),
                start_number=p.get("startNumber", 1),
                margin_pt=p.get("marginPt", 36),
                cancel=cancel,
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── Write Metadata ────────────────────────────────────────────

    @Slot(str)
    def startWriteMetadata(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "metadata")

        def _work():
            write_metadata(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                fields=p["fields"],
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── Extract Images ────────────────────────────────────────────

    @Slot(str)
    def startExtractImages(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "extract_images")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return extract_images(
                input_path=p["inputPath"],
                output_dir=p["outputDir"],
                fmt=p.get("format", "png"),
                min_size=p.get("minSize", 0),
                on_progress=progress_cb,
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Extract Text ──────────────────────────────────────────────

    @Slot(str)
    def startExtractText(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "extract_text")
        cancel = self._make_cancel_event(tool_key)

        def _work():
            return extract_text(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                page_range=p.get("pageRange"),
                cancel=cancel,
            )

        self._run_in_thread(tool_key, _work)

    # ── Crop ──────────────────────────────────────────────────────

    @Slot(str)
    def startCrop(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "crop")

        def _work():
            crop_pages(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                margins=p["margins"],
                unit=p.get("unit", "mm"),
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── Flatten ───────────────────────────────────────────────────

    @Slot(str)
    def startFlatten(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "flatten")

        def _work():
            flatten_pdf(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                annotations=p.get("annotations", True),
                forms=p.get("forms", True),
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── N-up Layout ───────────────────────────────────────────────

    @Slot(str)
    def startNup(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "nup")

        def _work():
            nup_layout(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                pages_per_sheet=p.get("pagesPerSheet", 4),
                page_size=p.get("pageSize", "A4"),
                orientation=p.get("orientation", "landscape"),
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── Repair ────────────────────────────────────────────────────

    @Slot(str)
    def startRepair(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "repair")

        def _work():
            repair_pdf(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
            )
            return {"outputPath": p["outputPath"]}

        self._run_in_thread(tool_key, _work)

    # ── Compare ───────────────────────────────────────────────────

    @Slot(str)
    def startCompare(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "compare")

        def _work():
            return compare_pdfs(
                path_a=p["pathA"],
                path_b=p["pathB"],
            )

        self._run_in_thread(tool_key, _work)

    # ── Redact ────────────────────────────────────────────────────

    @Slot(str)
    def startRedact(self, json_params: str):
        p = _normalize_params(json.loads(json_params))
        tool_key = p.get("toolKey", "redact")
        cancel = self._make_cancel_event(tool_key)
        progress_cb = self._make_progress_callback(tool_key)

        def _work():
            return redact_text(
                input_path=p["inputPath"],
                output_path=p["outputPath"],
                search_terms=p["searchTerms"],
                case_sensitive=p.get("caseSensitive", False),
                pages=p.get("pages"),
                on_progress=progress_cb,
                cancel=cancel,
                password=p.get("password"),
            )

        self._run_in_thread(tool_key, _work)
