"""
workspace_dir.py — the workspace's temp folder (#120). Qt-free.

The workspace (the working document the tools pass along) keeps its files
in a folder of the user's temp folder, named ``pdfcompress_workspace_*``.

  * create() makes one and writes an owner file into it: this process's id
    and when the process started, so a process id the system has since
    given to another program is not taken for its owner.
  * remove() deletes one, and only one: a folder directly in the temp
    folder whose name starts with the prefix, not a link or junction.
  * sweep_stale() runs at start: it removes our folders whose owner is no
    longer running, and folders with no owner file (older versions made
    those) whose newest entry is more than 24 hours old. It never follows a
    link or a junction, and never touches a folder a running copy of the
    app owns.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
import tempfile
import time
from typing import Optional

log = logging.getLogger(__name__)

PREFIX = "pdfcompress_workspace_"
OWNER_FILE = ".owner.json"
STALE_AFTER_SECONDS = 24 * 60 * 60


def temp_root() -> str:
    return tempfile.gettempdir()


# ════════════════════════════════════════════════════════════════════
#  Processes
# ════════════════════════════════════════════════════════════════════

def _windows_process(pid: int):
    """(running, start time) of *pid* on Windows."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        # Access denied: the process is there but not ours to ask about;
        # count it as running, so its folder is left alone.
        return ctypes.get_last_error() == 5, None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return False, None                               # 259: STILL_ACTIVE
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return True, None
        return True, str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _posix_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _posix_start(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return fields[19]                                    # field 22, starttime
    except (OSError, IndexError):
        pass
    if sys.platform == "darwin" and os.path.isfile("/bin/ps"):
        try:
            out = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            return out or None
        except Exception:
            return None
    return None


def process_state(pid: int) -> tuple[bool, Optional[str]]:
    """(whether *pid* is running, when it started — None if not known)."""
    if pid <= 0:
        return False, None
    if sys.platform == "win32":
        try:
            return _windows_process(pid)
        except Exception:
            return False, None
    running = _posix_running(pid)
    return running, (_posix_start(pid) if running else None)


def _owner_alive(owner: dict) -> bool:
    pid = owner.get("pid")
    if not isinstance(pid, int):
        return False
    running, started = process_state(pid)
    if not running:
        return False
    recorded = owner.get("started")
    return recorded is None or started is None or started == recorded


# ════════════════════════════════════════════════════════════════════
#  Folders
# ════════════════════════════════════════════════════════════════════

def is_link(path: str) -> bool:
    """A symbolic link, a junction or any other reparse point: never followed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(st, "st_file_attributes", 0) & reparse)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)                        # read-only on Windows
        os.unlink(path)


def _remove_tree(path: str) -> None:
    """Delete *path* and what is in it, without following links or
    junctions: those are removed themselves, never what they point at."""
    with os.scandir(path) as entries:
        for entry in entries:
            if is_link(entry.path):
                try:
                    os.unlink(entry.path)
                except OSError:
                    os.rmdir(entry.path)                     # a directory junction
            elif entry.is_dir(follow_symlinks=False):
                _remove_tree(entry.path)
            else:
                _unlink(entry.path)
    os.rmdir(path)


def _ours(path: str, root: str) -> bool:
    parent = os.path.normcase(os.path.abspath(os.path.dirname(os.path.abspath(path))))
    return (parent == os.path.normcase(os.path.abspath(root))
            and os.path.basename(os.path.abspath(path)).startswith(PREFIX))


def create(root: Optional[str] = None) -> str:
    """Make a workspace folder in the temp folder and record this process as its owner."""
    path = tempfile.mkdtemp(prefix=PREFIX, dir=root or temp_root())
    _running, started = process_state(os.getpid())
    with open(os.path.join(path, OWNER_FILE), "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "started": started}, fh)
    return path


def remove(path: str, root: Optional[str] = None) -> bool:
    """Delete the workspace folder *path*. Only a folder directly in the
    temp folder whose name starts with the prefix, and not a link or
    junction, is deleted; any other path raises ValueError. Returns False
    when the folder could not be deleted (the next start's sweep tries
    again)."""
    root = root or temp_root()
    if not path or not _ours(path, root) or is_link(path):
        raise ValueError("not a workspace folder")
    if not os.path.isdir(path):
        return True
    try:
        _remove_tree(path)
        return True
    except OSError as exc:
        log.warning("The workspace folder could not be deleted (%s)", type(exc).__name__)
        return False


def _read_owner(path: str) -> Optional[dict]:
    owner_path = os.path.join(path, OWNER_FILE)
    if is_link(owner_path):
        return None
    try:
        with open(owner_path, encoding="utf-8") as fh:
            owner = json.load(fh)
        return owner if isinstance(owner, dict) else None
    except (OSError, ValueError):
        return None


def _newest_mtime(path: str) -> float:
    newest = os.lstat(path).st_mtime
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
    return newest


def sweep_stale(root: Optional[str] = None, now: Optional[float] = None,
                stale_after: float = STALE_AFTER_SECONDS) -> list[str]:
    """Remove workspace folders left behind: those whose owner is no longer
    running, and ownerless ones whose newest entry is older than
    *stale_after* seconds. Returns the folders removed."""
    root = root or temp_root()
    now = time.time() if now is None else now
    removed = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return removed
    for entry in entries:
        if not entry.name.startswith(PREFIX) or is_link(entry.path):
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        owner = _read_owner(entry.path)
        try:
            if owner is not None:
                if _owner_alive(owner):
                    continue
            elif now - _newest_mtime(entry.path) <= stale_after:
                continue
        except OSError:
            continue
        if remove(entry.path, root):
            removed.append(entry.path)
    return removed
