"""
pdf_ocr — reading the words in scanned pages, for Redact's search (#128).

Language data
-------------
PyMuPDF has Tesseract compiled in: with a language data file and nothing
else installed, Pixmap.pdfocr_tobytes reads a picture and gives back a
one-page PDF holding what it read as invisible text. The file used is
tessdata_fast's eng.traineddata, pinned by commit, size and SHA-256 in
ocr_data_lock.json (Apache-2.0). It is not in the repository. The packaged
app carries it (pdf_toolkit.spec); a source checkout fetches it once with
``python setup_ocr.py --install``. Nothing here fetches it on its own:
never during a redaction, never unasked. An installed Tesseract's tessdata
folder is used too, when eng.traineddata is in it. ocr_status() says
whether OCR is ready, and with which file.

Reading a page
--------------
read_page() draws the page as it is shown — turned by its /Rotate, cut to
its crop box — in RGB (a grey picture reads as nothing), at DPI or less for
a large page (PIXEL_BUDGET), and reads it. What it reads is a page of
invisible text, and the term is found on it with pdf_verify.term_boxes, the
matcher the rest of Redact uses (it finds "4455-" / "8870" across a line
end, which page.search_for does not). If what the page reads as shown does
not look like words (looks_like_words), the page is drawn turned a quarter,
a half and three quarters and read again, and the reading that looks most
like words is kept: a scan can be sideways or upside down. Every box found
goes back to the page's own space through the matrix the picture was drawn
with (pdf_verify.page_to_picture_matrix, the one the redact slot puts boxes
drawn on page pictures back with), grown by GROW_PT and by GROW_SHARE of
its height, the share stopping at the edge of any other word OCR read
(_short_of).

Whether OCR can be used, the Redact page is told by ocr_readiness: ready or
not, and one plain line why not, never a path.

Qt-free on purpose: tests import this module (see CLAUDE.md).
"""

from __future__ import annotations

import functools
import glob
import hashlib
import json
import math
import os
import re
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import fitz

import trusted_programs
from pdf_verify import page_to_picture_matrix, term_boxes

LOCK_FILENAME = "ocr_data_lock.json"
#: Where the fetched language file is kept (a folder); overrides the
#: default under the user's local application data.
DATA_DIR_ENV = "PDFTOOLKIT_OCR_DATA"
#: The language read, and the name of its file in a tessdata folder.
LANGUAGE = "eng"

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


class OcrSetupError(Exception):
    """The language file could not be fetched, or is not the pinned one."""


_UNOPENABLE = ("The OCR language file is in {folder}, a folder whose name has letters "
               "Tesseract cannot open on Windows. Set " + DATA_DIR_ENV + " to a folder named "
               "with plain letters and run  python setup_ocr.py --install.")


# ════════════════════════════════════════════════════════════════════
#  Language data
# ════════════════════════════════════════════════════════════════════

def _here() -> Path:
    # Beside this module: the repository root from source, _internal/ in
    # the packaged app (as translate_runtime finds its lockfile).
    return Path(os.path.dirname(os.path.abspath(__file__)))


def lock_path() -> Path:
    return _here() / LOCK_FILENAME


def load_lock() -> dict:
    with open(lock_path(), encoding="utf-8") as fh:
        return json.load(fh)


def bundled_dir() -> Path:
    """Where the packaged app carries the pinned file (pdf_toolkit.spec)."""
    return _here() / "ocr-data"


def data_dir() -> Path:
    """Where ``setup_ocr.py --install`` puts the pinned file: user-writable,
    outside the repository (DATA_DIR_ENV overrides it)."""
    env = os.environ.get(DATA_DIR_ENV)
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "PDFToolkit" / "ocr-data"
    return Path(os.path.expanduser("~")) / ".local" / "share" / "pdftoolkit" / "ocr-data"


@functools.lru_cache(maxsize=16)
def _sha256_of(path: str, size: int, mtime_ns: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_sha256(path: Path) -> str:
    st = path.stat()
    return _sha256_of(str(path), st.st_size, st.st_mtime_ns)


def _tesseract_dirs() -> list:
    """The folders an installed Tesseract keeps its language data in.

    The places PyMuPDF's get_tessdata looks — TESSDATA_PREFIX, the tessdata
    folder of an installed tesseract, the usual system folders — found by
    looking at folders only. get_tessdata asks the shell (``tesseract
    --list-langs``, ``where``, ``whereis``), and in the windowed app every
    such command flashes a console window. The tesseract is the one
    trusted_programs finds (PDFTOOLKIT_TESSERACT or its install folders),
    not one found on PATH or in the current folder (#120)."""
    found: list[Path] = []
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix:
        found += [Path(prefix), Path(prefix) / "tessdata"]
    exe = trusted_programs.tesseract()
    if exe:
        home = Path(exe).resolve().parent
        found += [home / "tessdata", home.parent / "share" / "tessdata"]
        found += [Path(p) for p in sorted(glob.glob(str(home.parent / "share" / "tesseract-ocr" / "*" / "tessdata")),
                                          reverse=True)]
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                found.append(Path(base) / "Tesseract-OCR" / "tessdata")
    else:
        for pattern in ("/usr/share/tesseract-ocr/*/tessdata", "/usr/share/tessdata",
                        "/usr/share/tesseract/tessdata", "/usr/local/share/tessdata",
                        "/opt/homebrew/share/tessdata"):
            found += [Path(p) for p in sorted(glob.glob(pattern), reverse=True)]
    unique: list[Path] = []
    for folder in found:
        if folder not in unique:
            unique.append(folder)
    return unique


def _short_path(folder: str) -> Optional[str]:
    """The folder's 8.3 short name on Windows (None if it has none)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(folder), buf, len(buf))
        return buf.value if 0 < n < len(buf) else None
    except Exception:
        return None


def _openable(folder) -> Optional[str]:
    """*folder* as Tesseract can open it, or None.

    On Windows, Tesseract opens its language file by a path in the ANSI code
    page: a folder whose name has a letter outside A-Z (a user folder named
    with an accent, say) fails with "Error opening data file". Such a folder
    is given by its 8.3 short name, which is plain; a volume that keeps no
    short names leaves it unusable."""
    folder = str(folder)
    if folder.isascii() or sys.platform != "win32":
        return folder
    short = _short_path(folder)
    return short if short and short.isascii() else None


def ocr_status() -> dict:
    """Whether OCR can run, and with which file. Never raises.

    ``ready``     True when a language file is there to read with.
    ``tessdata``  the folder to hand PyMuPDF (str), or None.
    ``file``      the language file in it (str), or None.
    ``source``    "bundled" (the packaged app's copy), "installed" (fetched
                  by setup_ocr.py), "tesseract" (an installed Tesseract's),
                  or None.
    ``pinned``    True when the file is the one ocr_data_lock.json pins. A
                  bundled or installed file is used only if it is; a
                  Tesseract's is used as it is.
    ``problem``   why OCR is not ready, in words, or None.

    In that order: the bundled file, the installed one, then Tesseract's.
    Never ready without numpy, which the checks of what OCR redacted need
    (pdf_verify._ocr_checks).
    """
    status = {"ready": False, "tessdata": None, "file": None, "source": None,
              "pinned": False, "problem": None}
    # What OCR redacted is checked picture by picture with numpy, which
    # requirements.txt lists as optional.
    import importlib.util
    try:
        has_numpy = importlib.util.find_spec("numpy") is not None
    except Exception:
        has_numpy = False
    if not has_numpy:
        return {**status, "problem": "OCR needs numpy to check what it redacted, and numpy is "
                                     "not installed."}
    problems: list[str] = []
    want: Optional[str] = None
    try:
        lock = load_lock()
        name, want = str(lock["filename"]), str(lock["sha256"])
    except Exception as exc:
        problems.append(f"The OCR lockfile could not be read ({exc}).")
        name = f"{LANGUAGE}.traineddata"
    if want:
        for source, folder in (("bundled", bundled_dir()), ("installed", data_dir())):
            path = folder / name
            try:
                if not path.is_file():
                    continue
                if _file_sha256(path) != want:
                    problems.append(f"The OCR language file {path} does not match the pinned "
                                    f"file. Run  python setup_ocr.py --install  to fetch it again.")
                    continue
            except OSError:
                continue
            tessdata = _openable(folder)
            if tessdata is None:
                problems.append(_UNOPENABLE.format(folder=folder))
                continue
            return {**status, "ready": True, "tessdata": tessdata, "file": str(path),
                    "source": source, "pinned": True}
    try:
        folders = _tesseract_dirs()
    except Exception:
        folders = []
    for folder in folders:
        path = Path(folder) / f"{LANGUAGE}.traineddata"
        try:
            if not path.is_file():
                continue
            pinned = bool(want) and _file_sha256(path) == want
        except OSError:
            continue
        tessdata = _openable(folder)
        if tessdata is None:
            problems.append(_UNOPENABLE.format(folder=folder))
            continue
        return {**status, "ready": True, "tessdata": tessdata, "file": str(path),
                "source": "tesseract", "pinned": pinned}
    status["problem"] = problems[0] if problems else (
        "OCR is not set up: run  python setup_ocr.py --install  to fetch its language file.")
    return status


#: Why OCR cannot be used, as the Redact page says it: one plain line, and
#: never a path (the language file sits in the user's own folder).
_MISSING_FROM_APP = "The OCR language file is missing from this copy of PDF Toolkit: reinstall it."
_NOT_SET_UP = "OCR isn't set up: run python setup_ocr.py --install, then restart the app."
_NO_NUMPY = "OCR isn't set up: it needs numpy (pip install numpy), then restart the app."
_UNOPENABLE_FOLDER = ("OCR can't open its language file in a folder named with letters other "
                      "than A to Z: set " + DATA_DIR_ENV + " to another folder, run python "
                      "setup_ocr.py --install, then restart the app.")


def ocr_readiness(frozen: Optional[bool] = None) -> dict:
    """What the Redact page is told about OCR (#128): {"ready", "reason"},
    the reason a plain line, or None when ready. From ocr_status, without
    its file or folder. *frozen* is whether this is the packaged app (by
    default, sys.frozen), which carries the file: if it is not ready there,
    the copy is broken, and the line says to reinstall."""
    status = ocr_status()
    if status["ready"]:
        return {"ready": True, "reason": None}
    if getattr(sys, "frozen", False) if frozen is None else frozen:
        return {"ready": False, "reason": _MISSING_FROM_APP}
    problem = status.get("problem") or ""
    if "numpy" in problem:
        return {"ready": False, "reason": _NO_NUMPY}
    if "plain letters" in problem:
        return {"ready": False, "reason": _UNOPENABLE_FOLDER}
    return {"ready": False, "reason": _NOT_SET_UP}


def install_language_data(progress: Optional[ProgressFn] = None,
                          should_cancel: Optional[CancelFn] = None) -> dict:
    """Fetch the pinned language file into data_dir(), check its size and
    SHA-256 against ocr_data_lock.json, and put it in place.

    The one network operation of this module, run only when the user asks
    for it (setup_ocr.py). Written beside its final name first and moved
    into place only once it matches: a file that does not match is
    removed and OcrSetupError raised; a cancel (*should_cancel* returning
    True) removes what was written and raises InterruptedError.
    *progress* is called with (bytes so far, bytes in all, label).
    """
    import urllib.request

    lock = load_lock()
    size, want = int(lock["size"]), str(lock["sha256"])
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / str(lock["filename"])
    part = dest.with_name(dest.name + ".part")
    label = f"Fetching the OCR language file ({lock['filename']})"
    h, done = hashlib.sha256(), 0
    try:
        if should_cancel and should_cancel():
            raise InterruptedError("Cancelled")
        req = urllib.request.Request(str(lock["url"]), headers={"User-Agent": "pdf-toolkit-ocr-setup"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            while True:
                if should_cancel and should_cancel():
                    raise InterruptedError("Cancelled")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if done > size:
                    break
                if progress:
                    progress(done, size, label)
        if done != size or h.hexdigest() != want:
            raise OcrSetupError(
                f"The downloaded file does not match the pinned one ({done:,} bytes, SHA-256 "
                f"{h.hexdigest()}; expected {size:,} bytes, {want}). Nothing was installed.")
        os.replace(part, dest)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass
    _sha256_of.cache_clear()
    return {"file": str(dest), "sha256": want, "size": size}


# ════════════════════════════════════════════════════════════════════
#  Reading a page
# ════════════════════════════════════════════════════════════════════

#: The resolution a page is read at, and the least it may be read at.
DPI = 300
MIN_DPI = 150
#: The most pixels a picture of a page may have. An A3 page at 300 dpi has
#: 17.4 million; a larger page is read at less than DPI, and one that would
#: fall under MIN_DPI is too large to read (about A0 and up). A picture this
#: size takes 75 MB, and Tesseract several times that.
PIXEL_BUDGET = 25_000_000
#: Tesseract keeps a page's sizes in 16-bit numbers: no side may be longer.
MAX_SIDE = 32_000
#: The turns a page is read at, clockwise from the page as shown: as it is
#: first, the others only when that does not look like words.
TURNS = (0, 90, 180, 270)
#: A box OCR found is grown on every side by GROW_PT points and GROW_SHARE
#: of its height: OCR boxes a word's letters as it reads them, and a scan's
#: ink runs past them (an accent, a blurred edge, a slanted line). The
#: share goes no further than the edge of another word OCR read (_short_of).
GROW_PT = 2.0
GROW_SHARE = 0.25
#: The resolution a page's fingerprint is kept at (fingerprint()), and the
#: most pixels the page may be drawn with to make it.
FINGERPRINT_DPI = 50
FINGERPRINT_BUDGET = 16_000_000

#: Common English words of three letters or more. A reading that has
#: several of them is words; letters OCR makes of a photo, or of a page
#: read upside down, rarely spell one. (Two-letter words say nothing: such
#: readings are full of "an", "in", "to".)
_COMMON = frozenset("""
the and for that with was are this from have not which but all were they their there been has had
can will would one two more other its also into than these such may only some any each our out
use used new about after before between through under over she his her you your them who what
when where how does did should could must shall made make many most much very well first time
year years page date name number total see per both same during while those then here now just
like upon within without against because being every further however including following part
section report information data system table figure work state people form order case way high
long area based three given general public value results
""".split())
_LETTERS = re.compile(r"[A-Za-z]+")


#: The share of what OCR read that common words must make up, when there are
#: only three to six different ones (looks_like_words).
WORD_SHARE = 0.15


def looks_like_words(text: str) -> bool:
    """Whether what OCR read looks like English words: seven different
    common words (_COMMON), or three that make up WORD_SHARE or more of
    what it read.

    Measured on real pages on PyMuPDF 1.27.2.3 and 2.1 (#128): of 177
    readings of text pages made at a wrong turn, 635 readings of pages that
    are one picture (photos, diagrams, halftones, at every turn) and 640 of
    noise and photo-like pictures, none passes. Where the share decides
    (three to six different common words), what such readings reach is at
    most 6.1% (noise read sideways, on 2.1); English read the right way up
    reaches 21% to 30%, and a short line of title words more. Of 32 English
    pages read the right way up, 31 pass on 1.27.2.3 and all 32 on 2.1, as
    does a book cover with a few words. A page in another language seldom
    passes: it is read at every turn, and is noted as not read unless the
    term is found."""
    words = [w.lower() for w in _LETTERS.findall(text)]
    common = [w for w in words if w in _COMMON]
    distinct = len(set(common))
    return distinct >= 7 or (distinct >= 3 and len(common) >= WORD_SHARE * len(text.split()))


def _score(text: str) -> tuple:
    common = [w for w in (w.lower() for w in _LETTERS.findall(text)) if w in _COMMON]
    return (looks_like_words(text), len(set(common)), len(common))


def reading_dpi(page) -> Optional[int]:
    """The resolution *page* is read at: DPI, or less for a page that would
    be over PIXEL_BUDGET or MAX_SIDE; None when that is under MIN_DPI."""
    width, height = page.rect.width / 72, page.rect.height / 72
    if width <= 0 or height <= 0:
        return None
    dpi = min(DPI, int((PIXEL_BUDGET / (width * height)) ** 0.5), int(MAX_SIDE / max(width, height)))
    return dpi if dpi >= MIN_DPI else None


def _render(page, dpi: int, turn: int):
    """*page* as shown, turned *turn* degrees more, as an RGB picture at
    *dpi*; and the matrix that takes page space to its pixels."""
    zoom = fitz.Matrix(dpi / 72, dpi / 72).prerotate(turn)
    pix = page.get_pixmap(matrix=zoom, colorspace=fitz.csRGB, alpha=False)
    return pix, page_to_picture_matrix(page) * zoom * fitz.Matrix(1, 0, 0, 1, -pix.x, -pix.y)


def _ocr(pix, dpi: int, tessdata: str):
    """What OCR reads on *pix*: a one-page document holding it as invisible
    text, sized in points at *dpi*. (A grey picture reads as nothing.)"""
    pix.set_dpi(dpi, dpi)
    return fitz.open("pdf", pix.pdfocr_tobytes(language=LANGUAGE, tessdata=tessdata))


def _short_of(grown, rect, words, least: float = 0.0) -> "fitz.Rect":
    """*grown* (a box *rect* grown) cut back so that it reaches no further
    than the near edge of any of *words* (the other words OCR read on the
    page, at the same turn), but always *least* past *rect*. A word whose
    centre lies in *rect* is one of its own and is left alone. Which side a
    word is on is told by where its centre lies from *rect*'s.

    OCR's boxes are as tall as the line, and on a tilted scan taller: the
    box of a word on the next line often overlaps the term's own. Cut back
    to *rect* there, the box left part of the term's ink uncovered on 25 of
    64 tilted pages; with *least* (GROW_PT), every term was covered whole,
    and the words harmed beside it — a third or more of the paper round
    their ink blacked — fell from 25 to 4 on 66 straight pages at 300 dpi,
    and from 146 to 56 on 64 tilted ones (measured on real pages, #128)."""
    g = fitz.Rect(grown)
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    for word in words:
        if not g.intersects(word):
            continue
        wx, wy = (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2
        if rect.x0 <= wx <= rect.x1 and rect.y0 <= wy <= rect.y1:
            continue                                 # one of its own
        # How far apart the centres are, in the boxes' half sizes.
        across = abs(wx - cx) / max((rect.width + word.width) / 2, 1e-6)
        down = abs(wy - cy) / max((rect.height + word.height) / 2, 1e-6)
        if down >= across:
            if wy < cy:                              # above it
                g.y0 = max(g.y0, min(word.y1, rect.y0 - least))
            else:                                    # below it
                g.y1 = min(g.y1, max(word.y0, rect.y1 + least))
        elif wx < cx:                                # before it on the line
            g.x0 = max(g.x0, min(word.x1, rect.x0 - least))
        else:                                        # after it
            g.x1 = min(g.x1, max(word.x0, rect.x1 + least))
    return g


def _to_page(rects, ocr_page, pix, to_pixels, dpi: int, words=()) -> list:
    """*rects* found on *ocr_page*, each grown (GROW_PT, GROW_SHARE) but no
    further than the edge of another of *words* (_short_of), in page
    space."""
    if not ocr_page.rect.width:
        return []
    scale = pix.width / ocr_page.rect.width          # pixels to a unit of the OCR page
    per_point = scale / (dpi / 72)                   # OCR units to a point of the page
    back = fitz.Matrix(scale, scale) * ~to_pixels
    words = [fitz.Rect(w) for w in words]
    out = []
    for rect in rects:
        rect = fitz.Rect(rect)
        grow = GROW_PT * per_point + rect.height * GROW_SHARE
        grown = _short_of(fitz.Rect(rect.x0 - grow, rect.y0 - grow, rect.x1 + grow, rect.y1 + grow),
                          rect, words, GROW_PT * per_point)
        out.append((grown * back).normalize())
    return out


@dataclass
class Reading:
    """What OCR read on a page at one turn."""
    turn: int
    dpi: int
    text: str
    #: The terms found, grown, in page space.
    boxes: list = field(default_factory=list)
    seconds: float = 0.0

    @property
    def words(self) -> bool:
        return looks_like_words(self.text)


def read_at(page, terms, case_sensitive: bool, tessdata: str, turn: int, dpi: int) -> Reading:
    """Read *page* at one turn and resolution, and find *terms* on it."""
    start = time.perf_counter()
    pix, to_pixels = _render(page, dpi, turn)
    with _ocr(pix, dpi, tessdata) as doc:
        read = doc[0]
        text = read.get_text()
        found = term_boxes(read, terms, case_sensitive) if terms else []
        words = [w[:4] for w in read.get_text("words")] if found else []
        boxes = _to_page(found, read, pix, to_pixels, dpi, words)
    return Reading(turn, dpi, text, boxes, time.perf_counter() - start)


def read_text(page, tessdata: str, turn: int = 0, dpi: Optional[int] = None) -> str:
    """What OCR reads on *page* at *turn*."""
    return read_at(page, [], False, tessdata, turn, dpi or reading_dpi(page) or MIN_DPI).text


@dataclass
class PageReading:
    """What read_page made of a page.

    ``status`` is "read" (what was read looks like words, or the term was
    found), "unread" (nothing that looks like words at any turn), or
    "too_large" (it would be read under MIN_DPI; it was not drawn)."""
    status: str
    dpi: Optional[int] = None
    readings: list = field(default_factory=list)
    #: The terms found, grown, in page space: the kept reading's, and any
    #: other reading's that lie apart from them.
    boxes: list = field(default_factory=list)

    @property
    def turns(self) -> list:
        return [r.turn for r in self.readings]

    @property
    def hit_turns(self) -> list:
        return [r.turn for r in self.readings if r.boxes]

    @property
    def seconds(self) -> float:
        return sum(r.seconds for r in self.readings)


def read_page(page, terms, case_sensitive: bool, tessdata: str, *, cancel=None) -> PageReading:
    """Read *page* with OCR and find *terms* on what it reads.

    The page as shown first; when that reading does not look like words
    (looks_like_words), the other three turns, keeping the reading that
    looks most like words (the first of equals). The term is looked for in
    every reading made. *cancel*, a threading.Event, is looked at before
    each reading: once it is set, InterruptedError. A reading is one call
    into Tesseract, which holds Python's lock while it reads, so a cancel
    waits for the reading under way."""
    dpi = reading_dpi(page)
    if dpi is None:
        return PageReading("too_large")
    readings: list[Reading] = []
    for turn in TURNS:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Cancelled")
        readings.append(read_at(page, terms, case_sensitive, tessdata, turn, dpi))
        if turn == TURNS[0] and readings[0].words:
            break
    best = max(readings, key=lambda r: _score(r.text))
    boxes = list(best.boxes)
    for reading in readings:
        if reading is not best:
            boxes += [b for b in reading.boxes if not any(b.intersects(o) for o in boxes)]
    status = "read" if best.words or boxes else "unread"
    return PageReading(status, dpi, readings, boxes)


def fingerprint_dpi(page) -> int:
    """The resolution *page* is drawn at for its fingerprint: FINGERPRINT_DPI,
    or half the resolution of the sharpest picture it draws, within
    FINGERPRINT_BUDGET.

    MuPDF decodes a picture drawn at less than half its own resolution at a
    reduced size, and a JPEG decoded that way differs at the edge of every
    letter from the same pixels stored without loss — which is what a
    redacted picture is. Which reduced size it decodes at also depends on
    what it kept from drawing the page before. From half the picture's
    resolution up, both are decoded whole, and draw the same, pixel for
    pixel (measured on PyMuPDF 1.27.2.3 and 2.1, #128)."""
    need = FINGERPRINT_DPI
    try:
        for info in page.get_image_info():
            m, w, h = fitz.Matrix(info["transform"]), info.get("width") or 0, info.get("height") or 0
            # The points one of the picture's pixels takes on the page.
            across, down = math.hypot(m.a, m.b) / w if w else 0, math.hypot(m.c, m.d) / h if h else 0
            if across > 0 and down > 0:
                need = max(need, math.ceil(72 / min(across, down) * 0.51))
    except Exception:
        pass
    area = abs(page.rect.width * page.rect.height) / 72 / 72
    most = int((FINGERPRINT_BUDGET / area) ** 0.5) if area else need
    return max(FINGERPRINT_DPI, min(need, most))


def fingerprint_pixels(page, dpi: int):
    """*page* as shown, without its annotations, in grey, drawn at *dpi* and
    then made FINGERPRINT_DPI or so by averaging blocks of pixels; and the
    matrix that takes page space to those pixels. What MuPDF kept from
    drawing the page before is let go first."""
    import numpy as np
    factor = max(1, dpi // FINGERPRINT_DPI)
    fitz.TOOLS.store_shrink(100)
    zoom = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=zoom, colorspace=fitz.csGRAY, alpha=False, annots=False)
    drawn = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    h, w = pix.height // factor * factor, pix.width // factor * factor
    small = drawn[:h, :w].reshape(h // factor, factor, w // factor, factor).mean(axis=(1, 3))
    to_pixels = (page_to_picture_matrix(page) * zoom * fitz.Matrix(1, 0, 0, 1, -pix.x, -pix.y)
                 * fitz.Matrix(1 / factor, 1 / factor))
    return np.rint(small).astype(np.uint8), to_pixels


def fingerprint(page, dpi: Optional[int] = None) -> dict:
    """A small grey picture of *page* as shown, without its annotations:
    what the check that a redaction changed nothing outside its boxes
    compares the output with (pdf_verify, redaction.ocr_outside), made the
    same way from the output (fingerprint_pixels at the same *dpi*)."""
    dpi = int(dpi or fingerprint_dpi(page))
    small, _to_pixels = fingerprint_pixels(page, dpi)
    return {"dpi": dpi, "width": small.shape[1], "height": small.shape[0],
            "samples": zlib.compress(small.tobytes())}
