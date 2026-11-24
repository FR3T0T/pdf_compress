"""
app_log.py — the app's activity log (#120). Qt-free.

One rotating file, ``pdfcompress.log`` (5 MB, three older copies), in the
app's log folder. Two modes:

  * Default: the app's own INFO lines and other libraries' warnings and
    errors, with anything that looks like a folder path, a file:// URL or a
    document's file name scrubbed from the finished line (tracebacks keep
    the app's module names and line numbers, without their folders). The
    app's log calls name no files at INFO; where a file name helps, they
    give it at DEBUG, which only the detailed log writes. Other libraries'
    INFO lines are left out: argostranslate logs the text it translates at
    INFO.
  * Detailed (diagnostics, opt-in; the GUI keeps the choice in QSettings
    ``diagnostics/detailedLog``): everything at DEBUG, unscrubbed.

The first time this version opens the log folder it deletes the logs older
versions wrote there (they hold file names and folders) and leaves a marker
so it does that once. clear_logs() deletes the log and its copies on
request.
"""

from __future__ import annotations

import getpass
import logging
import logging.handlers
import os
import platform
import re
from typing import Optional

LOG_NAME = "pdfcompress.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
# Left in the log folder once the logs older versions wrote are deleted.
CLEARED_MARKER = "old-logs-removed"
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# The app's own loggers (logging.getLogger(__name__) in its modules; app.py
# and compress_pdf.py run as __main__). Below WARNING, only these reach the
# default log.
APP_LOGGERS = frozenset({
    "__main__", "app", "app_log", "compress_paths", "compress_pdf", "engine",
    "epdf_crypto", "pdf_analyze", "pdf_ocr", "pdf_ops", "pdf_translate",
    "pdf_verify", "setup_ocr", "setup_translation", "translate_runtime", "ui",
    "workspace_dir",
})

_APP_ROOT = os.path.dirname(os.path.abspath(__file__))


def default_log_dir() -> str:
    """The app's log folder for this user."""
    if platform.system() == "Windows":
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PDFCompress", "logs")
    if platform.system() == "Darwin":
        return os.path.expanduser("~/Library/Logs/PDFCompress")
    return os.path.expanduser("~/.local/share/PDFCompress/logs")


def log_files(directory: str) -> list[str]:
    """The log and its rotated copies, in *directory* (whether they exist or not)."""
    return [os.path.join(directory, LOG_NAME)] + [
        os.path.join(directory, f"{LOG_NAME}.{i}") for i in range(1, BACKUP_COUNT + 1)]


# ════════════════════════════════════════════════════════════════════
#  Scrubbing
# ════════════════════════════════════════════════════════════════════

_DOC_EXT = r"(?:e?pdf|docx?|txt|rtf|odt|png|jpe?g|tiff?|bmp|gif|webp)"
# Where a path in a log line ends: a quote or a character no file name can
# hold, the end of the line, or an arrow between two paths. A path may hold
# spaces, so it runs on to one of these; scrubbing a little more than the
# path is better than leaving part of it.
_END = r"(?=$|[\"'<>|*?\r\n]| → | -> )"
_TRACEBACK_FILE = re.compile(r'File "([^"\r\n]+)"')
_FILE_URL = re.compile(r"\bfile:/[^\s\"'<>|]*", re.IGNORECASE)
_UNC = re.compile(r"(?<![\w\\])\\\\[^\\\s\"'<>|]+\\[^\"'<>|*?\r\n]*?" + _END, re.MULTILINE)
_DRIVE = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\"'<>|*?\r\n:]*?(?=$|[\"'<>|*?\r\n:]| → | -> )",
                    re.MULTILINE)
_HOME = re.compile(r"(?<![\w.:/\\~-])~[\\/][^\"'<>|*?\r\n:]*?(?=$|[\"'<>|*?\r\n]|: | → | -> )",
                   re.MULTILINE)
_POSIX = re.compile(r"(?<![\w.:/\\~-])/(?:[^/\s\"'<>|:]+/)+[^\"'<>|*?\r\n:]*?"
                    r"(?=$|[\"'<>|*?\r\n]|: | → | -> )", re.MULTILINE)
_DOC_NAME = re.compile(r"[^\s\\/:*?\"'<>|]+\." + _DOC_EXT + r"\b", re.IGNORECASE)


def _user_name() -> Optional[str]:
    try:
        name = getpass.getuser()
    except Exception:
        return None
    return name if name and len(name) >= 3 else None


_USER = _user_name()
_USER_RE = re.compile(rf"(?<![\w]){re.escape(_USER)}(?![\w])", re.IGNORECASE) if _USER else None


def _short_module(path: str) -> str:
    """A traceback's file: relative to the app if it is one of the app's own
    modules, otherwise just its name."""
    try:
        rel = os.path.relpath(path, _APP_ROOT)
        if not rel.startswith("..") and not os.path.isabs(rel):
            return rel.replace("\\", "/")
    except ValueError:                       # another drive
        pass
    return os.path.basename(path.replace("\\", "/"))


def scrub(text: str) -> str:
    """*text* with folder paths, file:// URLs, document file names and the
    user's name taken out (<path>, <file>, <user>); a traceback's
    ``File "..."`` keeps the module's name and line, without its folders."""
    text = _TRACEBACK_FILE.sub(lambda m: f'File "{_short_module(m.group(1))}"', text)
    text = _FILE_URL.sub("<path>", text)
    text = _DRIVE.sub("<path>", text)        # before UNC: a repr's C:\\\\ is not a share
    text = _UNC.sub("<path>", text)
    text = _HOME.sub("<path>", text)
    text = _POSIX.sub("<path>", text)
    text = _DOC_NAME.sub("<file>", text)
    if _USER_RE is not None:
        text = _USER_RE.sub("<user>", text)
    return text


class ScrubbingFormatter(logging.Formatter):
    """Formats a record as usual, then scrubs the finished text — message,
    exception and traceback alike. The record itself is left as it was."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


class _DefaultLogFilter(logging.Filter):
    """The default log: the app's own lines, and other libraries' warnings
    and errors only (argostranslate logs the text it translates at INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING or record.name.split(".")[0] in APP_LOGGERS


# ════════════════════════════════════════════════════════════════════
#  The file handler
# ════════════════════════════════════════════════════════════════════

class _LogFileHandler(logging.handlers.RotatingFileHandler):
    """Once retired (clear_logs), it writes nothing: a thread that picked it
    up just before must not open the file again."""

    retired = False

    def emit(self, record):
        if not self.retired:
            super().emit(record)


_handler: Optional[_LogFileHandler] = None
_dir: Optional[str] = None
_detailed = False


def log_dir() -> Optional[str]:
    """The folder the log is written to, once set up."""
    return _dir


def is_detailed() -> bool:
    return _detailed


def _apply_mode() -> None:
    if _handler is None:
        return
    _handler.setLevel(logging.DEBUG if _detailed else logging.INFO)
    for f in list(_handler.filters):
        _handler.removeFilter(f)
    if _detailed:
        _handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    else:
        _handler.setFormatter(ScrubbingFormatter(_FORMAT, datefmt=_DATEFMT))
        _handler.addFilter(_DefaultLogFilter())
    logging.getLogger().setLevel(logging.DEBUG if _detailed else logging.INFO)


def _open_handler() -> None:
    global _handler
    _handler = _LogFileHandler(os.path.join(_dir, LOG_NAME), maxBytes=MAX_BYTES,
                               backupCount=BACKUP_COUNT, encoding="utf-8")
    _apply_mode()
    logging.getLogger().addHandler(_handler)


def _close_handler() -> None:
    global _handler
    if _handler is None:
        return
    logging.getLogger().removeHandler(_handler)
    _handler.retired = True
    _handler.close()
    _handler = None


def _delete(paths: list[str]) -> tuple[int, int]:
    removed = failed = 0
    for path in paths:
        if os.path.isfile(path) and not os.path.islink(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                failed += 1
    return removed, failed


def _remove_old_logs_once(directory: str) -> int:
    """Delete, once, the logs older versions wrote (they name files and
    folders). The marker is left only when all of them went, so a log held
    open by an older copy of the app still running is tried again next time."""
    marker = os.path.join(directory, CLEARED_MARKER)
    if os.path.exists(marker):
        return 0
    removed, failed = _delete(log_files(directory))
    if not failed:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("Logs written before file names were kept out of the log were deleted.\n")
    return removed


def setup_file_logging(log_dir: Optional[str] = None, detailed: bool = False) -> str:
    """Start writing the activity log; returns its path. Called again, it
    replaces the handler it set up before."""
    global _dir, _detailed
    directory = log_dir or default_log_dir()
    os.makedirs(directory, exist_ok=True)
    _close_handler()
    _dir, _detailed = directory, bool(detailed)
    removed = _remove_old_logs_once(directory)
    _open_handler()
    if removed:
        logging.getLogger(__name__).info("Deleted %d log file(s) written by an older version", removed)
    return os.path.join(directory, LOG_NAME)


def set_detailed(on: bool) -> None:
    """Switch between the default log and the detailed one, at once."""
    global _detailed
    _detailed = bool(on)
    _apply_mode()
    logging.getLogger(__name__).info("Detailed log %s", "on" if _detailed else "off")


def clear_logs() -> dict:
    """Delete the log and its rotated copies in the log folder, and go on
    logging to a new file. Returns {"success": bool, "removed": n}.

    The handler is closed first: Windows does not delete a file that is
    open."""
    if _dir is None:
        return {"success": False, "removed": 0, "error": "The log is not set up."}
    was_open = _handler is not None
    _close_handler()
    removed, failed = _delete(log_files(_dir))
    if was_open:
        _open_handler()
        logging.getLogger(__name__).info("Logs cleared (%d file(s))", removed)
    return {"success": not failed, "removed": removed}


def shutdown() -> None:
    """Stop writing the log (tests; the app lets logging close it at exit)."""
    global _dir
    _close_handler()
    _dir = None
