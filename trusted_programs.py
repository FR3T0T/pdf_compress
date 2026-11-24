"""
trusted_programs.py — where the app finds the programs it starts (#120). Qt-free.

A program is taken from an explicit setting or from the places its
installer puts it, never from a search of PATH or the current folder: on
Windows a bare program name is looked up in the current folder first, so a
file there named like the program would run instead. Every function here
returns an absolute path to an existing file, or None.

  * Ghostscript: PDFTOOLKIT_GHOSTSCRIPT, else (Windows)
    %ProgramFiles%\\gs\\gs*\\bin\\gswin64c.exe, then
    %ProgramFiles(x86)%\\gs\\gs*\\bin\\gswin32c.exe, newest version first;
    (macOS) /opt/homebrew/bin/gs, /usr/local/bin/gs, /opt/local/bin/gs;
    (Linux) /usr/bin/gs, /usr/local/bin/gs.
  * Tesseract: PDFTOOLKIT_TESSERACT, else (Windows)
    %ProgramFiles%\\Tesseract-OCR\\tesseract.exe, then the same under
    %ProgramFiles(x86)%; (macOS) /opt/homebrew/bin, /usr/local/bin,
    /opt/local/bin; (Linux) /usr/bin, /usr/local/bin.

A setting that is not an absolute path to an existing file is ignored, with
a warning that does not repeat it.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import sys
from typing import Optional

log = logging.getLogger(__name__)

GHOSTSCRIPT_ENV = "PDFTOOLKIT_GHOSTSCRIPT"
TESSERACT_ENV = "PDFTOOLKIT_TESSERACT"


def _setting(var: str) -> Optional[str]:
    value = os.environ.get(var)
    if not value:
        return None
    if os.path.isabs(value) and os.path.isfile(value):
        return os.path.abspath(value)
    log.warning("%s is set but is not the full path of an existing file; it is ignored", var)
    return None


def _program_files() -> list[tuple[str, bool]]:
    """(folder, is it the 32-bit one) for each Program Files folder there is."""
    seen, out = set(), []
    for var, is_x86 in (("ProgramW6432", False), ("ProgramFiles", False), ("ProgramFiles(x86)", True)):
        base = os.environ.get(var)
        if base and os.path.isabs(base) and os.path.normcase(base) not in seen:
            seen.add(os.path.normcase(base))
            out.append((base, is_x86))
    return out


def _version_key(path: str) -> tuple:
    folder = os.path.basename(os.path.dirname(os.path.dirname(path)))     # gs10.05.1
    return tuple(int(n) for n in re.findall(r"\d+", folder))


def ghostscript_candidates(platform: Optional[str] = None) -> list[str]:
    platform = platform or sys.platform
    if platform == "win32":
        found = []
        for base, is_x86 in _program_files():
            exe = "gswin32c.exe" if is_x86 else "gswin64c.exe"
            hits = glob.glob(os.path.join(glob.escape(base), "gs", "gs*", "bin", exe))
            found += sorted(hits, key=_version_key, reverse=True)
        return found
    if platform == "darwin":
        return ["/opt/homebrew/bin/gs", "/usr/local/bin/gs", "/opt/local/bin/gs"]
    return ["/usr/bin/gs", "/usr/local/bin/gs"]


def tesseract_candidates(platform: Optional[str] = None) -> list[str]:
    platform = platform or sys.platform
    if platform == "win32":
        return [os.path.join(base, "Tesseract-OCR", "tesseract.exe") for base, _x86 in _program_files()]
    if platform == "darwin":
        return ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/opt/local/bin/tesseract"]
    return ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]


def _first(var: str, candidates: list[str]) -> Optional[str]:
    chosen = _setting(var)
    if chosen:
        return chosen
    for path in candidates:
        if os.path.isabs(path) and os.path.isfile(path):
            return path
    return None


def ghostscript() -> Optional[str]:
    return _first(GHOSTSCRIPT_ENV, ghostscript_candidates())


def tesseract() -> Optional[str]:
    return _first(TESSERACT_ENV, tesseract_candidates())


def file_manager() -> Optional[str]:
    """The program that shows a file in its folder: Explorer, Finder's
    open, xdg-open."""
    if sys.platform == "win32":
        root = os.environ.get("SystemRoot") or os.environ.get("windir")
        path = os.path.join(root, "explorer.exe") if root and os.path.isabs(root) else None
    elif sys.platform == "darwin":
        path = "/usr/bin/open"
    else:
        path = next((p for p in ("/usr/bin/xdg-open", "/usr/local/bin/xdg-open") if os.path.isfile(p)), None)
    return path if path and os.path.isfile(path) else None
