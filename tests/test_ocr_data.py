"""OCR language data (#128): the pinned file, where it is looked for, and
the explicit setup command that fetches it.

Offline: the download is served from a file:// URL (urllib's FileHandler),
so fetch, check and install run for real without the network. Qt-free.
"""

import hashlib
import json
import re
import socket
import urllib.request
from pathlib import Path

import pytest

import pdf_ocr
import setup_ocr

ROOT = Path(__file__).resolve().parent.parent
FAKE = b"not a real language model, but bytes to fetch and check\n" * 2000


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to reach the network fails the test."""
    def refuse(*_a, **_k):
        raise AssertionError("the network was used")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def place(tmp_path, monkeypatch):
    """An isolated set of places to look: a data folder, a bundle folder and
    no Tesseract; a lock that pins FAKE, served from a file:// URL."""
    source = tmp_path / "served" / "eng.traineddata"
    source.parent.mkdir()
    source.write_bytes(FAKE)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps({
        "schema": 1, "language": "eng", "filename": "eng.traineddata",
        "source": "test", "commit": "0" * 40, "url": source.resolve().as_uri(),
        "sha256": hashlib.sha256(FAKE).hexdigest(), "size": len(FAKE),
        "licence": "Apache-2.0"}), encoding="utf-8")
    real_dirs = pdf_ocr._tesseract_dirs
    monkeypatch.setattr(pdf_ocr, "lock_path", lambda: lock)
    monkeypatch.setenv(pdf_ocr.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(pdf_ocr, "bundled_dir", lambda: tmp_path / "bundle")
    monkeypatch.setattr(pdf_ocr, "_tesseract_dirs", lambda: [])
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    pdf_ocr._sha256_of.cache_clear()
    return {"tmp": tmp_path, "lock": lock, "source": source,
            "data": tmp_path / "data", "bundle": tmp_path / "bundle", "real_dirs": real_dirs}


class TestShippedLock:
    def test_pins_one_file_by_commit_size_and_sha256(self):
        lock = json.loads((ROOT / pdf_ocr.LOCK_FILENAME).read_text(encoding="utf-8"))
        assert lock["schema"] == 1
        assert lock["language"] == "eng" and lock["filename"] == "eng.traineddata"
        assert re.fullmatch(r"[0-9a-f]{40}", lock["commit"])
        assert lock["url"] == ("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
                               f"{lock['commit']}/eng.traineddata")
        assert re.fullmatch(r"[0-9a-f]{64}", lock["sha256"])
        assert 3_000_000 < lock["size"] < 6_000_000
        assert lock["licence"] == "Apache-2.0"

    def test_the_language_file_is_never_committed(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert "*.traineddata" in ignored

    def test_the_packaged_app_bundles_it(self):
        spec = (ROOT / "pdf_toolkit.spec").read_text(encoding="utf-8")
        assert "pdf_ocr" in spec and pdf_ocr.LOCK_FILENAME in spec


class TestStatus:
    def test_nothing_set_up(self, place, no_network):
        st = pdf_ocr.ocr_status()
        assert st["ready"] is False
        assert st["source"] is None and st["tessdata"] is None and st["file"] is None

    def test_not_ready_without_numpy(self, place, monkeypatch):
        """The checks of what OCR redacted read pictures with numpy, which
        requirements.txt calls optional: without it OCR is not offered."""
        import importlib.util
        place["data"].mkdir()
        (place["data"] / "eng.traineddata").write_bytes(FAKE)
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a: None if name == "numpy" else real(name, *a))
        st = pdf_ocr.ocr_status()
        assert st["ready"] is False and "numpy" in st["problem"]

    def test_never_raises(self, place, monkeypatch):
        place["lock"].write_text("{ not json", encoding="utf-8")
        st = pdf_ocr.ocr_status()
        assert st["ready"] is False and st["problem"]

    def test_an_installed_file_that_matches_the_pin(self, place, no_network):
        place["data"].mkdir()
        (place["data"] / "eng.traineddata").write_bytes(FAKE)
        st = pdf_ocr.ocr_status()
        assert st["ready"] is True and st["source"] == "installed" and st["pinned"] is True
        assert Path(st["tessdata"]) == place["data"]
        assert Path(st["file"]) == place["data"] / "eng.traineddata"

    def test_an_installed_file_that_does_not_match_is_not_used(self, place):
        place["data"].mkdir()
        (place["data"] / "eng.traineddata").write_bytes(FAKE[:-1] + b"?")
        st = pdf_ocr.ocr_status()
        assert st["ready"] is False
        assert "does not match" in st["problem"]

    def test_the_bundled_file_comes_first(self, place):
        for folder in (place["data"], place["bundle"]):
            folder.mkdir()
            (folder / "eng.traineddata").write_bytes(FAKE)
        st = pdf_ocr.ocr_status()
        assert st["source"] == "bundled" and Path(st["tessdata"]) == place["bundle"]

    def test_a_tesseract_folder_only_with_english_in_it(self, place, monkeypatch):
        tess = place["tmp"] / "tesseract" / "tessdata"
        tess.mkdir(parents=True)
        (tess / "deu.traineddata").write_bytes(b"german")
        monkeypatch.setattr(pdf_ocr, "_tesseract_dirs", lambda: [tess])
        assert pdf_ocr.ocr_status()["ready"] is False
        (tess / "eng.traineddata").write_bytes(b"another english model")
        st = pdf_ocr.ocr_status()
        assert st["ready"] is True and st["source"] == "tesseract" and st["pinned"] is False
        assert Path(st["tessdata"]) == tess

    def test_tessdata_prefix_is_a_tesseract_folder(self, place, monkeypatch):
        tess = place["tmp"] / "prefix"
        tess.mkdir()
        (tess / "eng.traineddata").write_bytes(b"english")
        monkeypatch.setenv("TESSDATA_PREFIX", str(tess))
        monkeypatch.setattr(pdf_ocr, "_tesseract_dirs", place["real_dirs"])
        st = pdf_ocr.ocr_status()
        assert st["source"] == "tesseract" and Path(st["tessdata"]) == tess

    def test_a_pinned_file_wins_over_tesseract(self, place, monkeypatch):
        tess = place["tmp"] / "tessdata"
        tess.mkdir()
        (tess / "eng.traineddata").write_bytes(b"english")
        monkeypatch.setattr(pdf_ocr, "_tesseract_dirs", lambda: [tess])
        place["data"].mkdir()
        (place["data"] / "eng.traineddata").write_bytes(FAKE)
        assert pdf_ocr.ocr_status()["source"] == "installed"

    @pytest.mark.parametrize("short, ready", [(None, False), ("D:\\ARCHIV~1\\OCR-DA~1", True),
                                              ("D:\\Archiv-\u00fc\\ocr-data", False)])
    def test_a_folder_named_with_letters_tesseract_cannot_open(self, place, monkeypatch,
                                                                short, ready):
        """On Windows Tesseract opens its file by a path in the ANSI code
        page, so a folder named with any letter outside A-Z fails ("Error
        opening data file"). Such a folder is given by its 8.3 short name;
        without one it is reported, not handed on to fail mid-run."""
        import types
        folder = place["tmp"] / "Archiv-\u00fc" / "ocr-data"
        folder.mkdir(parents=True)
        (folder / "eng.traineddata").write_bytes(FAKE)
        monkeypatch.setenv(pdf_ocr.DATA_DIR_ENV, str(folder))
        monkeypatch.setattr(pdf_ocr, "sys", types.SimpleNamespace(platform="win32"))
        monkeypatch.setattr(pdf_ocr, "_short_path", lambda _f: short)
        st = pdf_ocr.ocr_status()
        assert st["ready"] is ready
        if ready:
            assert st["tessdata"] == short
        else:
            assert "plain letters" in st["problem"]

    def test_a_plain_folder_is_handed_on_as_it_is(self, place):
        place["data"].mkdir()
        (place["data"] / "eng.traineddata").write_bytes(FAKE)
        if str(place["data"]).isascii():
            assert pdf_ocr.ocr_status()["tessdata"] == str(place["data"])

    def test_finding_tesseract_starts_no_program(self, place, monkeypatch):
        """PyMuPDF's get_tessdata runs shell commands to find Tesseract; in
        the windowed app each one flashes a console window. The search here
        only looks at folders."""
        import subprocess

        def refuse(*_a, **_k):
            raise AssertionError("a program was started")
        monkeypatch.setattr(subprocess, "run", refuse)
        monkeypatch.setattr(subprocess, "Popen", refuse)
        monkeypatch.setattr(pdf_ocr, "_tesseract_dirs", place["real_dirs"])
        pdf_ocr._tesseract_dirs()
        pdf_ocr.ocr_status()


class TestInstall:
    def test_fetches_checks_and_installs(self, place):
        res = pdf_ocr.install_language_data()
        target = place["data"] / "eng.traineddata"
        assert target.read_bytes() == FAKE
        assert Path(res["file"]) == target
        st = pdf_ocr.ocr_status()
        assert st["ready"] and st["source"] == "installed" and st["pinned"]

    def test_a_download_that_does_not_match_is_refused(self, place):
        lock = json.loads(place["lock"].read_text(encoding="utf-8"))
        lock["sha256"] = "f" * 64
        place["lock"].write_text(json.dumps(lock), encoding="utf-8")
        with pytest.raises(pdf_ocr.OcrSetupError, match="does not match"):
            pdf_ocr.install_language_data()
        assert not list(place["data"].glob("eng.traineddata*")) if place["data"].exists() else True

    def test_cancelled(self, place):
        with pytest.raises(InterruptedError):
            pdf_ocr.install_language_data(should_cancel=lambda: True)
        assert not (place["data"] / "eng.traineddata").exists()

    def test_progress_is_reported(self, place):
        seen = []
        pdf_ocr.install_language_data(progress=lambda cur, tot, label: seen.append((cur, tot)))
        assert seen and seen[-1][0] == seen[-1][1]


class TestSetupCommand:
    def test_status_when_not_set_up(self, place, capsys, no_network):
        assert setup_ocr.main(["--status"]) == 1
        out = capsys.readouterr().out
        assert "not set up" in out and "setup_ocr.py --install" in out

    def test_install_then_status(self, place, capsys):
        assert setup_ocr.main(["--install"]) == 0
        assert setup_ocr.main(["--status"]) == 0
        out = capsys.readouterr().out
        assert "ready" in out and hashlib.sha256(FAKE).hexdigest() in out

    def test_nothing_is_fetched_unasked(self, place, capsys, no_network):
        assert setup_ocr.main([]) == 0
        assert setup_ocr.main(["--help"]) == 0
        assert not place["data"].exists()
