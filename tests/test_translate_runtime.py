"""Tests for translate_runtime — the on-demand translation-runtime installer.

Fully offline: synthetic wheels are served over file:// URLs (urllib's
FileHandler), so download/verify/unpack/activate run for real without
touching the network. Qt-free per CLAUDE.md.
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

import translate_runtime as tr

# ────────────────────────────────────────────────────────────── helpers

def _make_wheel(path: Path, pkg: str, extra_members: dict | None = None) -> dict:
    """Write a minimal wheel zip; return its lockfile entry (file:// URL)."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{pkg}/__init__.py", f"VALUE = '{pkg}'\n")
        zf.writestr(f"{pkg}-1.0.dist-info/METADATA", f"Name: {pkg}\nVersion: 1.0\n")
        for member, content in (extra_members or {}).items():
            zf.writestr(member, content)
    data = path.read_bytes()
    return {
        "name": pkg,
        "version": "1.0",
        "filename": path.name,
        "url": path.resolve().as_uri(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Isolated runtime dir + a synthetic two-wheel lockfile."""
    rt_dir = tmp_path / "runtime"
    monkeypatch.setenv(tr.RUNTIME_DIR_ENV, str(rt_dir))

    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    entries = [
        _make_wheel(wheels_dir / "alpha_fake-1.0-py3-none-any.whl", "alpha_fake"),
        _make_wheel(
            wheels_dir / "beta_fake-1.0-py3-none-any.whl", "beta_fake",
            extra_members={"beta_fake-1.0.data/purelib/beta_extra.py": "EXTRA = 1\n"},
        ),
    ]
    lock_file = tmp_path / "lock.json"
    lock_file.write_text(json.dumps({
        "schema": 1, "python": "test", "platform": "test",
        "roots": ["alpha_fake"], "wheels": entries,
    }), encoding="utf-8")
    monkeypatch.setattr(tr, "lock_path", lambda: lock_file)
    return {"rt_dir": rt_dir, "lock_file": lock_file, "entries": entries,
            "wheels_dir": wheels_dir}


# ─────────────────────────────────────────────────────── real lockfile

class TestShippedLockfile:
    def test_exists_and_wellformed(self):
        lock = json.loads(
            (Path(__file__).resolve().parent.parent / tr.LOCK_FILENAME)
            .read_text(encoding="utf-8"))
        assert lock["schema"] == 1
        assert lock["wheels"], "lockfile has no wheels"
        names = [w["name"].lower() for w in lock["wheels"]]
        assert len(names) == len(set(names)), "duplicate package in lockfile"
        for w in lock["wheels"]:
            assert w["url"].startswith("https://"), w
            assert w["url"].endswith(w["filename"]), w
            assert len(w["sha256"]) == 64 and int(w["sha256"], 16) >= 0
            assert w["size"] > 0
            assert w["version"] in w["filename"], w

    def test_contains_the_translation_stack_and_nothing_it_does_not_use(self):
        # The packages excluded from the frozen bundle (pdf_toolkit.spec) that
        # translation uses must be in the lock; stanza (and torch with it)
        # and spacy are not: sentences are split with MiniSBD (#121).
        lock = json.loads(
            (Path(__file__).resolve().parent.parent / tr.LOCK_FILENAME)
            .read_text(encoding="utf-8"))
        names = {w["name"].lower() for w in lock["wheels"]}
        for required in ("argostranslate", "ctranslate2", "sentencepiece", "sacremoses",
                         "minisbd", "onnxruntime"):
            assert required in names, f"{required} missing from lockfile"
        for dropped in ("stanza", "torch", "spacy", "thinc"):
            assert dropped not in names, f"{dropped} is still in the lockfile"


def _shipped_lock() -> dict:
    return json.loads((Path(__file__).resolve().parent.parent / tr.LOCK_FILENAME)
                      .read_text(encoding="utf-8"))


class TestStanzaAndSpacyAreDropped:
    """#121: argostranslate pins stanza==1.10.1 exactly (CVE-2026-54499,
    CVE-2026-59974, CVE-2026-115878). The app splits sentences with MiniSBD
    and never builds a stanza pipeline, and spacy is only used for argos'
    spaCy splitter: both are dropped from the runtime, torch with stanza."""

    def test_stanza_torch_and_spacy_are_not_locked(self):
        names = {w["name"].lower() for w in _shipped_lock()["wheels"]}
        assert not names & {"stanza", "torch", "spacy", "thinc"}

    def test_the_lock_records_the_drops(self):
        overrides = _shipped_lock().get("overrides") or {}
        assert overrides["stanza"]["spec"] is None
        assert overrides["stanza"]["replaces"] == "stanza==1.10.1"
        assert "CVE-2026-54499" in overrides["stanza"]["reason"]
        assert overrides["spacy"]["spec"] is None
        assert overrides["spacy"]["replaces"] == "spacy"
        assert "SPACY" in overrides["spacy"]["reason"]

    def test_argostranslate_is_still_locked(self):
        names = {w["name"].lower() for w in _shipped_lock()["wheels"]}
        assert "argostranslate" in names

    def test_the_overrides_drop_both(self):
        assert tr.LOCK_OVERRIDES == {"stanza": None, "spacy": None}

    def test_the_heavy_stack_is_no_longer_held_to_the_venv(self):
        assert not {"torch", "spacy", "thinc"} & set(tr.LOCK_CONSTRAIN_TO_ENV)
        assert {"ctranslate2", "onnxruntime", "sentencepiece", "sacremoses",
                "numpy"} <= set(tr.LOCK_CONSTRAIN_TO_ENV)


class TestRewriteRequirements:
    """The pure function make_lockfile rewrites the roots' requirements with."""

    def test_an_exact_pin_is_replaced(self):
        out, replaced = tr.rewrite_requirements(
            ["ctranslate2<5,>=4.0", "stanza==1.10.1", "spacy"], {"stanza": "==1.15.1"})
        assert out == ["ctranslate2<5,>=4.0", "stanza==1.15.1", "spacy"]
        assert replaced == {"stanza": ["stanza==1.10.1"]}

    def test_a_none_override_drops_the_requirement(self):
        reqs = ["ctranslate2<5,>=4.0", "minisbd", "stanza==1.10.1", "spacy",
                'tomli; python_version < "3.11"']
        out, replaced = tr.rewrite_requirements(reqs, {"stanza": None, "spacy": None})
        assert out == ["ctranslate2<5,>=4.0", "minisbd", 'tomli; python_version < "3.11"']
        assert replaced == {"stanza": ["stanza==1.10.1"], "spacy": ["spacy"]}

    def test_markers_and_extras_are_kept(self):
        out, _ = tr.rewrite_requirements(
            ['stanza[tokenizers] (==1.10.1) ; python_version < "3.15"'], {"stanza": "==1.15.1"})
        assert out == ['stanza[tokenizers]==1.15.1; python_version < "3.15"']

    def test_other_requirements_are_untouched(self):
        reqs = ["minisbd", "sacremoses<0.2,>=0.0.53", 'tomli; python_version < "3.11"',
                "stanzas==2.0"]
        out, replaced = tr.rewrite_requirements(reqs, {"stanza": "==1.15.1"})
        assert out == reqs and replaced == {}

    @pytest.mark.parametrize("written", ["Stanza==1.10.1", "STANZA ==1.10.1", "stanza (==1.10.1)"])
    def test_names_are_compared_as_pep_503_normalizes_them(self, written):
        out, replaced = tr.rewrite_requirements([written], {"stanza": "==1.15.1"})
        assert out[0].lower().replace(" ", "") == "stanza==1.15.1"
        assert list(replaced) == ["stanza"]

    def test_separators_normalize_too(self):
        out, replaced = tr.rewrite_requirements(["Typing.Extensions>=4"],
                                                {"typing_extensions": ">=4.16"})
        assert out == ["Typing.Extensions>=4.16"]
        assert replaced == {"typing-extensions": ["Typing.Extensions>=4"]}


class TestLockConstraints:
    def test_a_package_not_installed_is_held_to_the_current_lock(self, monkeypatch):
        """#121: the dev venv no longer carries the ML stack; without this,
        torch and the rest would float to whatever is newest."""
        from importlib import metadata

        def version(name):
            if name == "numpy":
                return "9.9.9"
            raise metadata.PackageNotFoundError(name)
        monkeypatch.setattr(metadata, "version", version)
        lock = {"wheels": [{"name": "sentencepiece", "version": "0.2.1"},
                           {"name": "ctranslate2", "version": "4.8.0"},
                           {"name": "numpy", "version": "2.5.0"}]}
        constraints = tr.lock_constraints(lock)
        assert "sentencepiece==0.2.1" in constraints and "ctranslate2==4.8.0" in constraints
        assert "numpy==9.9.9" in constraints          # installed: the venv's own
        assert not any(c.startswith("onnxruntime") for c in constraints)   # in neither


# ─────────────────────────────────────────────────────── paths / status

class TestPathsAndStatus:
    def test_runtime_dir_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tr.RUNTIME_DIR_ENV, str(tmp_path / "custom"))
        assert tr.runtime_dir() == tmp_path / "custom"

    def test_not_installed_when_empty(self, fake_env):
        assert tr.runtime_installed() is False

    def test_runtime_status_never_raises_and_has_keys(self, fake_env):
        st = tr.runtime_status()
        assert set(st) == {"needed", "installed", "downloadSizeMB", "dir"}
        assert st["installed"] is False

    def test_activate_without_install(self, fake_env):
        lib = str(fake_env["rt_dir"] / "lib")
        result = tr.activate()
        # On a dev box with argostranslate in the venv the native stack
        # wins (True); on CI there is no stack at all (False). Either way
        # the uninstalled runtime dir must never enter sys.path.
        assert result is tr._native_stack_present()
        assert lib not in sys.path


# ──────────────────────────────────────────────────────────── install

class TestInstall:
    def test_full_install_activate_import(self, fake_env, monkeypatch):
        progress = []
        res = tr.install_runtime(progress=lambda c, t, s: progress.append((c, t, s)))
        assert res["packages"] == 2

        lib = fake_env["rt_dir"] / "lib"
        assert (lib / "alpha_fake" / "__init__.py").is_file()
        # {name}.data/purelib contents merged into the import root:
        assert (lib / "beta_extra.py").is_file()
        assert not list(lib.glob("*.data"))
        # wheel cache cleaned up after success:
        assert not (fake_env["rt_dir"] / "downloads").exists()
        assert tr.runtime_installed() is True
        assert progress and progress[-1][2] == "Runtime installed"

        # Activation must make the packages importable — force the
        # runtime path even if a native stack exists on this machine.
        monkeypatch.setattr(tr, "_native_stack_present", lambda: False)
        try:
            assert tr.activate() is True
            assert str(lib) in sys.path
            import alpha_fake
            assert alpha_fake.VALUE == "alpha_fake"
        finally:
            sys.path.remove(str(lib))
            sys.modules.pop("alpha_fake", None)

    def test_checksum_mismatch_fails_cleanly(self, fake_env):
        fake_env["entries"][0]["sha256"] = "0" * 64
        fake_env["lock_file"].write_text(json.dumps({
            "schema": 1, "wheels": fake_env["entries"],
        }), encoding="utf-8")
        with pytest.raises(tr.RuntimeInstallError, match="Checksum mismatch"):
            tr.install_runtime()
        assert tr.runtime_installed() is False

    def test_zip_slip_rejected(self, fake_env, tmp_path):
        evil = tmp_path / "wheels" / "evil_fake-1.0-py3-none-any.whl"
        entry = _make_wheel(evil, "evil_fake", extra_members={"../escaped.txt": "x"})
        fake_env["lock_file"].write_text(json.dumps({
            "schema": 1, "wheels": [entry],
        }), encoding="utf-8")
        with pytest.raises(tr.RuntimeInstallError, match="Unsafe path"):
            tr.install_runtime()
        assert not (tmp_path / "escaped.txt").exists()

    def test_cancel_interrupts(self, fake_env):
        with pytest.raises(InterruptedError):
            tr.install_runtime(should_cancel=lambda: True)
        assert tr.runtime_installed() is False

    def test_lock_change_invalidates_install(self, fake_env):
        tr.install_runtime()
        assert tr.runtime_installed() is True
        # Simulate an app upgrade shipping a different lock:
        lock = json.loads(fake_env["lock_file"].read_text(encoding="utf-8"))
        lock["schema"] = 1.5
        fake_env["lock_file"].write_text(json.dumps(lock), encoding="utf-8")
        assert tr.runtime_installed() is False


# ─────────────────────────────────────────── pdf_translate integration

class TestStatusIntegration:
    def test_translation_status_reports_runtime(self, fake_env):
        from pdf_translate import translation_status
        st = translation_status()
        assert "runtime" in st
        assert set(st["runtime"]) == {"needed", "installed", "downloadSizeMB", "dir"}
