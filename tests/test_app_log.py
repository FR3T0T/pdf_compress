"""The activity log names no files by default (#120).

  * Operations run on files in a folder whose path and names hold a made-up
    token: with the detailed log off the log holds neither the token nor
    the folder; with it on, it does (the switch works).
  * The scrubber, on its own and on a traceback whose message holds a path.
  * clear_logs() with the handler attached, as in the app (Windows does not
    delete an open file); the one-time removal of logs older versions wrote.
  * Passwords never reach the log, detailed or not.

Qt-free: app_log and the operations are imported directly.
"""

import logging
import os
from pathlib import Path

import pikepdf
import pytest

import app_log
import engine
import epdf_crypto
import pdf_analyze
import pdf_ops

TOKEN = "zqxwvtoken"
PASSWORD = "Pw-zqxw-7731-secret"


@pytest.fixture
def logdir(tmp_path):
    """The app's log, in a folder of its own; taken down again afterwards."""
    root = logging.getLogger()
    level = root.level
    directory = tmp_path / "logs"
    yield directory
    app_log.shutdown()
    root.setLevel(level)


def _read(directory: Path) -> str:
    for handler in logging.getLogger().handlers:
        handler.flush()
    return "".join(Path(p).read_text(encoding="utf-8")
                   for p in app_log.log_files(str(directory)) if os.path.exists(p))


def _pdf(path: Path, pages: int = 2) -> str:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(200, 200))
    pdf.save(path)
    return str(path)


def _run_operations(tmp_path: Path) -> Path:
    """Merge, split, protect, unlock, watermark and analyze, and one that
    fails on a missing file, in a folder named with the token."""
    work = tmp_path / f"case {TOKEN} files"
    work.mkdir()
    a = _pdf(work / f"{TOKEN} first.pdf")
    b = _pdf(work / f"{TOKEN}_second.pdf")
    pdf_ops.merge_pdfs([a, b], str(work / f"{TOKEN} merged.pdf"))
    split_dir = work / f"split {TOKEN}"
    split_dir.mkdir()
    pdf_ops.split_pdf(a, str(split_dir))
    pdf_ops.protect_pdf(a, str(work / f"{TOKEN}-locked.pdf"), user_password=PASSWORD)
    pdf_ops.unlock_pdf(str(work / f"{TOKEN}-locked.pdf"), str(work / f"{TOKEN}-open.pdf"),
                       password=PASSWORD)
    pdf_ops.add_watermark(a, str(work / f"{TOKEN} marked.pdf"), text="DRAFT")
    pdf_analyze.analyze_file(a)
    engine.analyze_pdf(a)
    # A tool run on a file that is not there: the bridge's worker logs the
    # failure with its traceback (ui/bridge.py _Worker.run).
    try:
        pdf_ops.protect_pdf(str(work / f"{TOKEN} missing.pdf"), str(work / "out.pdf"),
                            user_password=PASSWORD)
    except Exception:
        logging.getLogger("ui.bridge").exception("Operation %s failed", "protect")
    return work


def test_the_default_log_names_no_files(tmp_path, logdir):
    app_log.setup_file_logging(str(logdir))
    work = _run_operations(tmp_path)
    text = _read(logdir)
    assert "Merged 2 files" in text and "Protected a PDF" in text   # it logged
    assert "Operation protect failed" in text and "Traceback" in text
    assert TOKEN not in text
    assert str(tmp_path) not in text and str(work) not in text
    assert PASSWORD not in text


def test_the_detailed_log_names_them(tmp_path, logdir):
    app_log.setup_file_logging(str(logdir), detailed=True)
    work = _run_operations(tmp_path)
    text = _read(logdir)
    assert TOKEN in text and str(work) in text
    assert PASSWORD not in text


def test_the_switch_takes_effect_at_once(tmp_path, logdir):
    app_log.setup_file_logging(str(logdir))
    app_log.set_detailed(True)
    assert app_log.is_detailed()
    logging.getLogger("pdf_ops").debug("detail %s", TOKEN)
    app_log.set_detailed(False)
    logging.getLogger("pdf_ops").debug("detail %s", "afterwards")
    logging.getLogger("pdf_ops").info("Kept %s", tmp_path / f"{TOKEN}.pdf")
    text = _read(logdir)
    assert f"detail {TOKEN}" in text            # while on
    assert "afterwards" not in text             # DEBUG, once off
    assert "Kept <path>" in text


def test_other_libraries_info_lines_stay_out_of_the_default_log(logdir):
    """argostranslate logs the text it translates at INFO."""
    app_log.setup_file_logging(str(logdir))
    logging.getLogger("argostranslate.utils").info("('sentences', ['A private %s'])", TOKEN)
    logging.getLogger("argostranslate.utils").warning("a library warning")
    assert TOKEN not in _read(logdir) and "a library warning" in _read(logdir)
    app_log.set_detailed(True)
    logging.getLogger("argostranslate.utils").info("('sentences', ['A private %s'])", TOKEN)
    assert TOKEN in _read(logdir)


# ── The scrubber ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text, kept", [
    (r"Failed: C:\Users\Some One\My Files\report 2026.pdf", "Failed: "),
    (r"[Errno 2] No such file or directory: 'D:\\work\\Q3 plan.docx'", "No such file or directory: '"),
    (r"open \\fileserver\team share\budget.xlsx -> done", "open "),
    ("see file:///home/someone/notes.txt for more", "see "),
    ("read /home/someone/Documents/tax return.pdf", "read "),
    ("saved ~/Desktop/scan.png", "saved "),
    ("wrote invoice-778.pdf and photo.JPG", "wrote "),
])
def test_paths_and_file_names_are_scrubbed(text, kept):
    out = app_log.scrub(text)
    assert kept in out and ("<path>" in out or "<file>" in out), out
    assert ":\\" not in out and "\\\\" not in out, out
    for word in ("Some One", "report", "Q3 plan", "fileserver", "budget", "someone",
                 "notes", "Documents", "tax return", "Desktop", "scan", "invoice", "photo"):
        assert word not in out, (word, out)


@pytest.mark.parametrize("text", [
    "Merged 3 files (12 pages, 40960 bytes)",
    "Translating en->de, 2 of 4 blocks; ratio 1/2 at 12:30:05",
    "the .epdf format, pdf_ops.py and https://example.org/a/b",
])
def test_text_without_paths_is_left_alone(text):
    assert app_log.scrub(text) == text


def test_a_traceback_keeps_module_names_and_lines_not_folders(tmp_path, logdir):
    app_log.setup_file_logging(str(logdir))
    missing = tmp_path / f"folder {TOKEN}" / f"{TOKEN}.pdf"
    try:
        open(missing, "rb")
    except OSError:
        logging.getLogger("ui.bridge").exception("Operation %s failed", "merge")
    text = _read(logdir)
    assert 'File "tests/test_app_log.py", line' in text      # the app's own module, relative
    assert "FileNotFoundError" in text and "<path>" in text
    assert TOKEN not in text and str(tmp_path) not in text


# ── Clearing the logs ─────────────────────────────────────────────────

def test_clear_logs_with_the_handler_attached(logdir):
    app_log.setup_file_logging(str(logdir))
    logging.getLogger("pdf_ops").info("a line before clearing")
    for i in (1, 2, 3):
        (logdir / f"{app_log.LOG_NAME}.{i}").write_text("older\n", encoding="utf-8")
    (logdir / "someone-elses.txt").write_text("left alone\n", encoding="utf-8")
    result = app_log.clear_logs()
    assert result == {"success": True, "removed": 4}
    assert not any((logdir / f"{app_log.LOG_NAME}.{i}").exists() for i in (1, 2, 3))
    assert (logdir / "someone-elses.txt").exists()                 # only the log's own files
    logging.getLogger("pdf_ops").info("a line after clearing")
    text = _read(logdir)
    assert "a line before clearing" not in text and "a line after clearing" in text


def test_logs_older_versions_wrote_are_deleted_once(logdir):
    logdir.mkdir()
    (logdir / app_log.LOG_NAME).write_text(r"Protected C:\Users\x\a.pdf" + "\n", encoding="utf-8")
    (logdir / f"{app_log.LOG_NAME}.1").write_text("older\n", encoding="utf-8")
    app_log.setup_file_logging(str(logdir))
    assert (logdir / app_log.CLEARED_MARKER).exists()
    assert not (logdir / f"{app_log.LOG_NAME}.1").exists()
    assert r"C:\Users" not in _read(logdir)
    logging.getLogger("pdf_ops").info("written by this version")
    app_log.shutdown()
    app_log.setup_file_logging(str(logdir))                         # a second start
    assert "written by this version" in _read(logdir)


# ── Passwords ─────────────────────────────────────────────────────────

def test_passwords_never_reach_the_log(tmp_path, logdir):
    app_log.setup_file_logging(str(logdir), detailed=True)
    src = _pdf(tmp_path / "plain.pdf")
    pdf_ops.protect_pdf(src, str(tmp_path / "locked.pdf"), user_password=PASSWORD,
                        owner_password=PASSWORD + "-owner")
    pdf_ops.unlock_pdf(str(tmp_path / "locked.pdf"), str(tmp_path / "open.pdf"), password=PASSWORD)
    epdf_crypto.epdf_encrypt(src, str(tmp_path / "sealed.epdf"), PASSWORD,
                             kdf_params={"time_cost": 1, "memory_cost": 8192, "parallelism": 1})
    epdf_crypto.epdf_decrypt(str(tmp_path / "sealed.epdf"), str(tmp_path / "back.pdf"), PASSWORD)
    with pytest.raises(epdf_crypto.EPDFPasswordError):
        epdf_crypto.epdf_decrypt(str(tmp_path / "sealed.epdf"), str(tmp_path / "x.pdf"), "wrong-" + PASSWORD)
    text = _read(logdir)
    assert "EPDF encrypted" in text and "Unlocked" in text
    assert PASSWORD not in text
