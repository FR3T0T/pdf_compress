"""Ghostscript and Tesseract are found only where they are installed or
where a setting points, never by name in the current folder or on PATH
(#120).

Each test puts decoys — a gs / gswin64c / tesseract that leaves a mark when
it runs — in a tmp folder that is both the current folder and first on
PATH, and points the Program Files folders at empty tmp folders. Qt-free.
"""

import logging
import os
import stat
import sys
from pathlib import Path

import pytest

import engine
import pdf_ocr
import trusted_programs

WINDOWS = sys.platform == "win32"


def _decoy(folder: Path, name: str, mark: Path) -> Path:
    """A program called *name* that writes *mark* when it runs."""
    if WINDOWS:
        path = folder / f"{name}.bat"
        path.write_text(f'@echo ran> "{mark}"\r\n@exit /b 0\r\n', encoding="ascii")
    else:
        path = folder / name
        path.write_text(f'#!/bin/sh\necho ran > "{mark}"\nexit 0\n', encoding="ascii")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def decoys(tmp_path, monkeypatch):
    """Decoys in the current folder, first on PATH; no install anywhere."""
    here = tmp_path / "here"
    here.mkdir()
    mark = tmp_path / "decoy-ran.txt"
    for name in ("gs", "gswin64c", "gswin32c", "tesseract"):
        _decoy(here, name, mark)
    (here.parent / "share" / "tessdata").mkdir(parents=True)        # where a found tesseract's data would be
    (here / "tessdata").mkdir()
    empty = tmp_path / "Program Files"
    empty.mkdir()
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(empty))
    for var in (trusted_programs.GHOSTSCRIPT_ENV, trusted_programs.TESSERACT_ENV, "TESSDATA_PREFIX"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", str(here) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(here)
    yield {"here": here, "mark": mark, "empty": empty}
    monkeypatch.undo()
    engine.find_ghostscript(force_refresh=True)                     # no cached decoy for later tests


def _not_in(path, folder: Path) -> bool:
    return path is None or not os.path.abspath(path).startswith(str(folder))


def test_a_ghostscript_in_the_current_folder_is_never_run(decoys):
    found = engine.find_ghostscript(force_refresh=True)
    assert _not_in(found, decoys["here"])
    assert not decoys["mark"].exists(), "a decoy Ghostscript was run"
    if found is not None:
        assert os.path.isabs(found)


def test_a_tesseract_in_the_current_folder_is_never_chosen(decoys):
    assert _not_in(trusted_programs.tesseract(), decoys["here"])
    dirs = [str(d) for d in pdf_ocr._tesseract_dirs()]
    assert not any(d.startswith(str(decoys["here"].parent / "share")) or
                   d.startswith(str(decoys["here"])) for d in dirs)


def test_pytesseract_is_given_the_full_path(decoys, monkeypatch):
    """pytesseract runs whatever its tesseract_cmd names (a bare "tesseract"
    unless set). A stand-in module, so pytesseract need not be installed."""
    import pdf_translate
    pytesseract = type(sys)("pytesseract")
    pytesseract.pytesseract = type(sys)("pytesseract.pytesseract")
    pytesseract.pytesseract.tesseract_cmd = "tesseract"
    monkeypatch.setitem(sys.modules, "pytesseract", pytesseract)
    real = decoys["here"].parent / "Tesseract" / "tesseract.exe"
    real.parent.mkdir()
    real.write_bytes(b"")
    monkeypatch.setenv(trusted_programs.TESSERACT_ENV, str(real))
    pdf_translate._pytesseract()
    assert pytesseract.pytesseract.tesseract_cmd == str(real)
    monkeypatch.delenv(trusted_programs.TESSERACT_ENV)
    if trusted_programs.tesseract() is None:                        # none installed where we look
        with pytest.raises(pdf_translate.ModelMissingError, match="Tesseract program"):
            pdf_translate._pytesseract()


def test_the_setting_wins(decoys, monkeypatch, tmp_path):
    chosen = _decoy(tmp_path, "my-ghostscript", tmp_path / "chosen-ran.txt")
    monkeypatch.setenv(trusted_programs.GHOSTSCRIPT_ENV, str(chosen))
    assert trusted_programs.ghostscript() == str(chosen)
    assert engine.find_ghostscript(force_refresh=True) == str(chosen)
    assert (tmp_path / "chosen-ran.txt").exists()                    # it was asked for --version
    assert not decoys["mark"].exists()


@pytest.mark.parametrize("value", ["gswin64c.bat" if WINDOWS else "gs", "here/gs", "missing-ghostscript"])
def test_a_setting_that_is_not_a_full_path_is_ignored_unprinted(decoys, monkeypatch, caplog, value):
    monkeypatch.setenv(trusted_programs.GHOSTSCRIPT_ENV, value)
    with caplog.at_level(logging.WARNING, logger="trusted_programs"):
        found = trusted_programs.ghostscript()
    assert _not_in(found, decoys["here"])
    assert "PDFTOOLKIT_GHOSTSCRIPT" in caplog.text and value not in caplog.text
    assert not decoys["mark"].exists()


def test_install_folders_are_searched_newest_first(decoys, monkeypatch):
    base = decoys["empty"]
    for version in ("9.56.1", "10.05.1", "10.2.0"):
        exe = base / "gs" / f"gs{version}" / "bin" / "gswin64c.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
    found = trusted_programs.ghostscript_candidates("win32")
    assert [Path(p).parent.parent.name for p in found] == ["gs10.05.1", "gs10.2.0", "gs9.56.1"]
    assert all(os.path.isabs(p) for p in found)


def test_other_systems_look_in_fixed_folders_only():
    for platform in ("darwin", "linux"):
        for path in trusted_programs.ghostscript_candidates(platform) + \
                trusted_programs.tesseract_candidates(platform):
            assert path.startswith("/") and os.path.basename(path) in ("gs", "tesseract")
