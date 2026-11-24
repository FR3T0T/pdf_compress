"""
translate_runtime — on-demand provisioning of the offline-translation runtime.

WHY THIS EXISTS: the Translate tool's stack (argostranslate, ctranslate2,
sentencepiece, onnxruntime for MiniSBD's sentence splitter, …) is tens of
MB of native wheels for one tool out of 22, kept out of the frozen build
(see pdf_toolkit.spec) and provisioned at run time instead: wheels are
downloaded once into a user-writable directory, verified against the
pinned lockfile, unpacked, and appended to sys.path, so the stack imports
from a real directory exactly like a source checkout. (Until #121 it also
carried stanza, torch and spacy — about 204 MB — and frozen PyTorch does
not survive PyInstaller; LOCK_OVERRIDES drops them: sentences are split
with MiniSBD.)

The download happens only when the user explicitly starts it from the
Translate tool (or setup_translation.py), together with the language packs
and their sentence models (pdf_translate.install_languages). After that,
translation makes no network connection. A source checkout with
argostranslate in the venv never needs any of this — activate() is a no-op
when the stack is already importable.

Lockfile: translate_runtime_lock.json (repo root, bundled beside this
module in the frozen build) pins every wheel by name/version/url/sha256 —
regenerate with  python translate_runtime.py --make-lockfile  after
changing translation dependency versions (needs network + the target
Python; record of what combo was actually tested).

Qt-free on purpose: tests import this module (see CLAUDE.md).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional

RUNTIME_DIR_ENV = "PDFTOOLKIT_TRANSLATE_RUNTIME"
LOCK_FILENAME = "translate_runtime_lock.json"

# Top-level pins the lockfile generator resolves (everything else is their
# dependency closure, constrained to the versions installed in the dev
# venv so the lock always mirrors a combination that was actually run).
# langdetect (source-language auto-detect) is deliberately absent: it
# publishes no wheel (sdist only, incompatible with --only-binary), and
# the tested dev venv doesn't carry it either — pdf_translate already
# degrades gracefully (asks the user to pick the source language).
LOCK_ROOTS = ["argostranslate"]

# Heavy native packages mirrored from the generating venv (the combination
# actually tested from source). Everything else resolves freely from the
# roots' metadata — deliberately NOT the whole venv: a full freeze
# over-constrains (e.g. a venv package newer than argostranslate's own pin
# makes resolution impossible).
LOCK_CONSTRAIN_TO_ENV = [
    "ctranslate2", "onnxruntime", "sentencepiece", "sacremoses", "numpy",
]

# Requirements of the roots changed before resolving (#121): a version
# specifier puts that in place of the root's own; None drops the
# requirement. pip has no way to override a dependency's pin, and every
# argostranslate release pins stanza exactly (1.10.0 and 1.11.0:
# stanza==1.10.1; 1.9.x: ==1.1.1).
#   stanza: 1.10.1 is affected by CVE-2026-54499 (unsafe pickle fallback in
#     model loading, <=1.12.1, fixed in 1.12.2), CVE-2026-59974 (zip slip
#     when stanza unzips its resources, <1.14.0, fixed in 1.14.0) and
#     CVE-2026-115878 (pickle in the lemmatizer, 1.14.0 only, fixed in
#     1.15.0). The app splits sentences with MiniSBD and never builds a
#     stanza pipeline (pdf_translate puts a stand-in in its place, since
#     argostranslate.sbd imports stanza at the top), so it is not shipped,
#     and torch, which only stanza needs, goes with it.
#   spacy: argostranslate imports it only for its spaCy sentence splitter
#     (ChunkType.SPACY), which the app does not use; thinc goes with it.
LOCK_OVERRIDES = {"stanza": None, "spacy": None}
OVERRIDE_REASONS = {
    "stanza": ("argostranslate pins stanza==1.10.1 (CVE-2026-54499, fixed 1.12.2; "
               "CVE-2026-59974, fixed 1.14.0; CVE-2026-115878, fixed 1.15.0); the app "
               "splits sentences with MiniSBD and never builds a stanza pipeline, so "
               "stanza, and torch with it, is not shipped"),
    "spacy": ("argostranslate uses spacy only for its spaCy sentence splitter "
              "(ChunkType.SPACY), which the app does not use"),
}

ProgressFn = Callable[[int, int, str], None]
CancelFn = Callable[[], bool]


class RuntimeInstallError(Exception):
    """A wheel failed to download, verify, or unpack."""


# ════════════════════════════════════════════════════════════════════
#  Paths / status
# ════════════════════════════════════════════════════════════════════

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def runtime_dir() -> Path:
    """User-writable home of the provisioned runtime (env-overridable)."""
    env = os.environ.get(RUNTIME_DIR_ENV)
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "PDFToolkit" / "translate-runtime"
    return Path(os.path.expanduser("~")) / ".local" / "share" / "pdftoolkit" / "translate-runtime"


def _lib_dir() -> Path:
    return runtime_dir() / "lib"


def _cache_dir() -> Path:
    return runtime_dir() / "downloads"


def _marker_path() -> Path:
    return runtime_dir() / "runtime.json"


def lock_path() -> Path:
    # Beside this module: repo root from source; _internal/ when frozen
    # (same relative-resolution trick as assets/fonts in pdf_translate).
    return Path(os.path.dirname(os.path.abspath(__file__))) / LOCK_FILENAME


def load_lock() -> dict:
    with open(lock_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _lock_fingerprint() -> str:
    return hashlib.sha256(lock_path().read_bytes()).hexdigest()


def download_size_mb(lock: Optional[dict] = None) -> int:
    lock = lock or load_lock()
    return round(sum(w["size"] for w in lock["wheels"]) / (1024 * 1024))


def runtime_installed() -> bool:
    """True when the runtime dir holds an install matching the CURRENT lock.

    A lock change (dependency upgrade in a new app release) intentionally
    reads as not-installed so the UI offers a fresh provisioning pass.
    """
    try:
        marker = json.loads(_marker_path().read_text(encoding="utf-8"))
        return marker.get("lock_fingerprint") == _lock_fingerprint()
    except Exception:
        return False


def _native_stack_present() -> bool:
    """Is argostranslate importable WITHOUT the provisioned runtime?

    find_spec is used instead of importing (importing argos pulls in
    ctranslate2 and the rest — seconds of work); an origin under runtime_dir()
    doesn't count as native.
    """
    import importlib.util
    try:
        spec = importlib.util.find_spec("argostranslate")
    except Exception:
        return False
    if spec is None or not getattr(spec, "origin", None):
        return False
    try:
        return not str(Path(spec.origin).resolve()).startswith(str(runtime_dir().resolve()))
    except Exception:
        return True


def runtime_status() -> dict:
    """Everything the UI needs to drive the setup flow. Never raises."""
    try:
        size_mb = download_size_mb()
    except Exception:
        size_mb = 0
    return {
        "needed": not _native_stack_present(),
        "installed": runtime_installed(),
        "downloadSizeMB": size_mb,
        "dir": str(runtime_dir()),
    }


def activate() -> bool:
    """Make a provisioned runtime importable. Idempotent, never raises.

    APPENDS to sys.path so bundled/venv packages always win — the runtime
    only ever supplies what the app doesn't already carry (e.g. numpy is
    frozen into the bundle; the runtime's copy is a dormant duplicate).
    """
    if _native_stack_present():
        return True
    if not runtime_installed():
        return False
    lib = str(_lib_dir())
    if lib not in sys.path:
        sys.path.append(lib)
    return True


# ════════════════════════════════════════════════════════════════════
#  Install
# ════════════════════════════════════════════════════════════════════

def _download(url: str, dest: Path, sha256: str, size: int,
              done_offset: int, total: int,
              progress: Optional[ProgressFn], label: str,
              should_cancel: Optional[CancelFn]) -> None:
    """Stream one wheel to dest, verifying sha256. Resumable via cache:
    an existing file with the right hash is kept as-is."""
    import urllib.request

    if dest.is_file():
        h = hashlib.sha256()
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() == sha256:
            if progress:
                progress((done_offset + size) // (1024 * 1024), total // (1024 * 1024), label)
            return
        dest.unlink()

    part = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    done = 0
    req = urllib.request.Request(url, headers={"User-Agent": "pdf-toolkit-translate-setup"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
        while True:
            if should_cancel and should_cancel():
                out.close()
                part.unlink(missing_ok=True)
                raise InterruptedError("Cancelled")
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress((done_offset + done) // (1024 * 1024), total // (1024 * 1024), label)
    if h.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise RuntimeInstallError(
            f"Checksum mismatch for {dest.name} — download corrupted or altered; try again.")
    part.replace(dest)


def _unpack_wheel(whl: Path, target: Path) -> None:
    """Unzip a wheel into target (a wheel is a zip of importable packages).

    Guards against zip-slip and merges any {name}.data/purelib|platlib
    trees into the root so their contents import normally.
    """
    target_resolved = target.resolve()
    with zipfile.ZipFile(whl) as zf:
        for member in zf.namelist():
            dest = (target / member).resolve()
            if not str(dest).startswith(str(target_resolved)):
                raise RuntimeInstallError(f"Unsafe path in {whl.name}: {member}")
        zf.extractall(target)
    for data_dir in target.glob("*.data"):
        for sub in ("purelib", "platlib"):
            src = data_dir / sub
            if src.is_dir():
                for item in src.iterdir():
                    dst = target / item.name
                    if dst.exists():
                        continue
                    shutil.move(str(item), str(dst))
        shutil.rmtree(data_dir, ignore_errors=True)


def install_runtime(progress: Optional[ProgressFn] = None,
                    should_cancel: Optional[CancelFn] = None) -> dict:
    """Download + verify + unpack every wheel in the lockfile.

    THE one network operation this module performs, and it only runs when
    the user explicitly starts translation setup. Progress is reported in
    two phases (honest scales, the bar restarts between them):
    downloaded MB out of total MB, then unpacked wheel count.
    """
    lock = load_lock()
    wheels = lock["wheels"]
    total_bytes = sum(w["size"] for w in wheels)

    _cache_dir().mkdir(parents=True, exist_ok=True)

    done = 0
    for i, w in enumerate(wheels):
        label = f"Downloading {w['name']} ({i + 1}/{len(wheels)})"
        _download(w["url"], _cache_dir() / w["filename"], w["sha256"], w["size"],
                  done, total_bytes, progress, label, should_cancel)
        done += w["size"]

    # Fresh lib dir per install: never mix package versions across locks.
    lib = _lib_dir()
    if lib.exists():
        shutil.rmtree(lib)
    lib.mkdir(parents=True)

    for i, w in enumerate(wheels):
        if should_cancel and should_cancel():
            raise InterruptedError("Cancelled")
        if progress:
            progress(i, len(wheels), f"Unpacking {w['name']}")
        _unpack_wheel(_cache_dir() / w["filename"], lib)

    _marker_path().write_text(json.dumps({
        "lock_fingerprint": _lock_fingerprint(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": {w["name"]: w["version"] for w in wheels},
    }, indent=2), encoding="utf-8")

    shutil.rmtree(_cache_dir(), ignore_errors=True)  # wheels no longer needed
    if progress:
        progress(len(wheels), len(wheels), "Runtime installed")
    return {"packages": len(wheels), "dir": str(runtime_dir())}


# ════════════════════════════════════════════════════════════════════
#  Lockfile generator (dev-only; needs network + the target Python)
# ════════════════════════════════════════════════════════════════════

# A PEP 508 requirement as Requires-Dist writes it: a name, extras, a
# version specifier (bare, or in parentheses in older metadata) and an
# environment marker after ";".
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
    r"(?P<extras>\[[^\]]*\])?\s*"
    r"(?P<spec>\(?[^;()]*\)?)?\s*"
    r"(?P<marker>;.*)?$")


def normalize_name(name: str) -> str:
    """A project name as PEP 503 compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def rewrite_requirements(requirements: list, overrides: dict) -> tuple:
    """*requirements* (PEP 508 strings, as in Requires-Dist) with every
    requirement on an overridden project changed (#121): an override that is
    a version specifier takes the place of the requirement's own; one that
    is None drops the requirement. Names are compared normalized (PEP 503);
    the requirement's own spelling of the name, its extras and its
    environment marker are kept; every other requirement is returned as it
    was.

    Returns (rewritten requirements, {normalized name: [the requirements that
    were changed, each written plainly: "stanza==1.10.1" for metadata's
    "stanza (==1.10.1)"]}), the second for the lock's record."""
    wanted = {normalize_name(name): spec for name, spec in overrides.items()}
    out, replaced = [], {}
    for text in requirements:
        m = _REQUIREMENT.match(text)
        key = normalize_name(m.group("name")) if m else None
        if key not in wanted:
            out.append(text)
            continue
        marker = m.group("marker")
        tail = f"; {marker[1:].strip()}" if marker else ""
        head = f"{m.group('name')}{m.group('extras') or ''}"
        old = (m.group("spec") or "").strip().strip("()").replace(" ", "")
        replaced.setdefault(key, []).append(f"{head}{old}{tail}")
        if wanted[key] is not None:          # None drops the requirement
            out.append(f"{head}{wanted[key]}{tail}")
    return out, replaced


def lock_constraints(current_lock: Optional[dict]) -> list:
    """The constraints make_lockfile resolves with: each LOCK_CONSTRAIN_TO_ENV
    package at the version installed here — the combination tested from
    source — or, when it is not installed, at its version in the current
    lock. The dev venv may no longer carry the ML stack (it is provisioned
    at run time); unconstrained, ctranslate2 and the rest would float to whatever
    is newest on the day the lock is made, untested (#121)."""
    from importlib import metadata

    locked = {normalize_name(w["name"]): w["version"]
              for w in (current_lock or {}).get("wheels", [])}
    constraints = []
    for name in LOCK_CONSTRAIN_TO_ENV:
        try:
            constraints.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            if normalize_name(name) in locked:
                constraints.append(f"{name}=={locked[normalize_name(name)]}")
    return constraints


def _pip_report(requirements: list, constraints_file: str, workdir: str,
                no_deps: bool = False) -> dict:
    """pip's dry-run install report for *requirements* (wheels only)."""
    import subprocess

    report_file = os.path.join(workdir, "report-nodeps.json" if no_deps else "report.json")
    cmd = [sys.executable, "-m", "pip", "install", "--dry-run",
           "--ignore-installed", "--only-binary=:all:",
           "--report", report_file, "-c", constraints_file,
           *(["--no-deps"] if no_deps else []), *requirements]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeInstallError(f"pip resolve failed:\n{res.stderr}")
    with open(report_file, encoding="utf-8") as fh:
        return json.load(fh)


def make_lockfile() -> dict:
    """Resolve LOCK_ROOTS with pip and write the lockfile.

    (a) The roots alone (--no-deps): their wheels and Requires-Dist.
    (b) Their requirements with LOCK_OVERRIDES applied (rewrite_requirements):
        argostranslate's stanza==1.10.1 and spacy dropped.
    (c) That list resolved, with lock_constraints.
    (d) The two merged.
    (e) The overrides recorded in the lock: what was asked for, what it
        replaced, and why.

    Run on the same Python/platform the frozen app targets — the lock
    records exactly the combination that was tested from source.
    """
    import tempfile
    import urllib.request

    try:
        current = load_lock()
    except Exception:
        current = None
    constraints = lock_constraints(current)

    with tempfile.TemporaryDirectory() as td:
        cfile = os.path.join(td, "constraints.txt")
        with open(cfile, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(constraints)))
        roots = _pip_report(LOCK_ROOTS, cfile, td, no_deps=True)["install"]
        # Requirements behind an extra are not installed with the roots.
        requires = [r for item in roots for r in item["metadata"].get("requires_dist") or []
                    if "extra" not in (r.split(";", 1)[1] if ";" in r else "")]
        rewritten, replaced = rewrite_requirements(requires, LOCK_OVERRIDES)
        deps = _pip_report(rewritten, cfile, td)["install"]

    items = {}
    for item in [*roots, *deps]:
        items.setdefault(normalize_name(item["metadata"]["name"]), item)

    wheels = []
    for item in items.values():
        url = item["download_info"]["url"]
        sha = item["download_info"].get("archive_info", {}).get("hashes", {}).get("sha256")
        meta = item["metadata"]
        if not sha:
            raise RuntimeInstallError(f"No sha256 in pip report for {url}")
        head = urllib.request.Request(url, method="HEAD",
                                      headers={"User-Agent": "pdf-toolkit-lockgen"})
        with urllib.request.urlopen(head, timeout=60) as resp:
            size = int(resp.headers["Content-Length"])
        wheels.append({
            "name": meta["name"],
            "version": meta["version"],
            "filename": url.rsplit("/", 1)[-1],
            "url": url,
            "sha256": sha,
            "size": size,
        })
    wheels.sort(key=lambda w: w["name"].lower())

    import datetime
    lock = {
        "schema": 1,
        "generated": datetime.date.today().isoformat(),
        "python": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "platform": sys.platform,
        "roots": LOCK_ROOTS,
        "overrides": {
            name: {"spec": spec,
                   "replaces": "; ".join(replaced.get(normalize_name(name), [])) or None,
                   "reason": OVERRIDE_REASONS.get(name, "")}
            for name, spec in LOCK_OVERRIDES.items()},
        "wheels": wheels,
    }
    with open(lock_path(), "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2)
        fh.write("\n")
    return lock


# ════════════════════════════════════════════════════════════════════
#  CLI (dev/testing)
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--make-lockfile" in args:
        lock = make_lockfile()
        total = download_size_mb(lock)
        print(f"Wrote {lock_path()} — {len(lock['wheels'])} wheels, {total} MB total:")
        for w in lock["wheels"]:
            print(f"  {w['name']:20} {w['version']:12} {round(w['size'] / (1024 * 1024), 1):>8} MB")
    elif "--install" in args:
        def _p(cur, tot, label):
            print(f"\r  {label:60} {cur}/{tot}", end="", flush=True)
        res = install_runtime(progress=_p)
        print(f"\nInstalled {res['packages']} packages into {res['dir']}")
    elif "--status" in args:
        print(json.dumps(runtime_status(), indent=2))
    else:
        print("usage: python translate_runtime.py --make-lockfile | --install | --status")
