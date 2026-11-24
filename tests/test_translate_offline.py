"""Translation splits sentences with MiniSBD only, from pinned models, and
never goes online; and it never hands back English silently (#121).

  * argostranslate.sbd imports stanza at the top; the app never builds a
    stanza pipeline, and stanza is no longer shipped. pdf_translate puts a
    stand-in module in its place and tells argostranslate to split with
    MiniSBD (ARGOS_CHUNK_TYPE and settings.chunk_type).
  * MiniSBD's models are pinned in translate_sbd_lock.json and fetched by
    install_languages (size and SHA-256 checked) into argostranslate's
    minisbd folder; a translation whose sentence model is missing fails
    with a plain message before anything could reach for the network.
  * A translation in which no text block could be translated raises and
    writes nothing; one in which some could not reports how many.

The real argostranslate is used only where noted (importorskip); the rest
uses stand-ins, a tmp_path and file:// URLs: no network. Qt-free.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import pdf_translate

fitz = pytest.importorskip("fitz")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("a network connection was attempted")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def clean_modules(monkeypatch):
    """Whatever this test puts in sys.modules (the stanza stand-in, a fresh
    argostranslate) is taken out again afterwards."""
    saved = dict(sys.modules)
    yield
    for name in list(sys.modules):
        if name not in saved:
            del sys.modules[name]
    sys.modules.update(saved)


# ── stanza is not used, nor imported ────────────────────────────────────

def test_the_stanza_stand_in_is_put_in_place_even_over_a_real_one(clean_modules, monkeypatch):
    real = type(sys)("stanza")
    real.Pipeline = object
    monkeypatch.setitem(sys.modules, "stanza", real)
    monkeypatch.setenv("ARGOS_CHUNK_TYPE", "STANZA")        # put back afterwards
    pdf_translate._prepare_argos()
    stand_in = sys.modules["stanza"]
    assert stand_in is not real
    assert stand_in.__spec__ is not None and stand_in.__spec__.name == "stanza"
    import importlib.util
    assert importlib.util.find_spec("stanza") is stand_in.__spec__
    with pytest.raises(AttributeError, match="isn't part of"):
        _ = stand_in.Pipeline
    assert not hasattr(stand_in, "Pipeline")
    pdf_translate._prepare_argos()
    assert sys.modules["stanza"] is stand_in              # once


def test_argos_is_told_to_split_with_minisbd(clean_modules, monkeypatch):
    monkeypatch.setenv("ARGOS_CHUNK_TYPE", "STANZA")        # put back afterwards
    pdf_translate._prepare_argos()
    assert os.environ["ARGOS_CHUNK_TYPE"] == "MINISBD"


_IMPORT_CHECK = """
import json, socket, sys
def refuse(*_a, **_k):
    raise AssertionError("a network connection was attempted")
socket.socket.connect = refuse
socket.create_connection = refuse
sys.path.insert(0, sys.argv[1])
import pdf_translate
translate, _package = pdf_translate._argos()
import argostranslate.sbd
import argostranslate.settings
stanza = sys.modules["stanza"]
print(json.dumps({
    "sbd_uses_the_stand_in": argostranslate.sbd.stanza is stanza,
    "stand_in": bool(getattr(stanza, "_pdftoolkit_stand_in", False)),
    "chunk_type": argostranslate.settings.chunk_type.name,
    "translate": translate.__name__,
}))
"""


def test_argostranslate_imports_with_the_stand_in(tmp_path):
    """With the real argostranslate (where it is installed, and a real stanza
    with it), in a fresh Python: its compiled parts do not survive being
    imported twice in one process."""
    pytest.importorskip("argostranslate")
    env = dict(os.environ, XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    env.pop("ARGOS_CHUNK_TYPE", None)
    run = subprocess.run([sys.executable, "-c", _IMPORT_CHECK, str(ROOT)], env=env,
                         capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr[-2000:]
    seen = json.loads(run.stdout.strip().splitlines()[-1])
    assert seen == {"sbd_uses_the_stand_in": True, "stand_in": True,
                    "chunk_type": "MINISBD", "translate": "argostranslate.translate"}


# ── The sentence models ─────────────────────────────────────────────────

def _sbd_lock() -> dict:
    return json.loads((ROOT / pdf_translate.SBD_LOCK_FILENAME).read_text(encoding="utf-8"))


def test_the_sentence_model_lock_is_well_formed():
    lock = _sbd_lock()
    assert lock["url"].startswith("https://github.com/LibreTranslate/MiniSBD/releases/download/")
    assert "AGPL-3.0" in lock["licence"]
    for name, model in lock["models"].items():
        assert model["filename"] == f"{name}.onnx"
        assert 100_000 < model["size"] < 2_000_000
        assert len(model["sha256"]) == 64 and int(model["sha256"], 16) >= 0
    assert lock["languages"]["bn"] == "hi" and lock["languages"]["zh"] == "zh-hans"


def test_every_language_the_app_offers_has_a_sentence_model():
    lock = _sbd_lock()
    for language in pdf_translate.SUPPORTED_LANGUAGES:
        model = lock["languages"].get(language.argos)
        assert model in lock["models"], language.code


def test_the_packaged_app_carries_the_sentence_model_lock():
    spec = (ROOT / "pdf_toolkit.spec").read_text(encoding="utf-8")
    assert pdf_translate.SBD_LOCK_FILENAME in spec


def _served(tmp_path, files: dict, wrong: set = frozenset()) -> dict:
    """A lock for *files* ({model: bytes}) served from a file:// folder; the
    hashes of the models in *wrong* do not match what is served."""
    served = tmp_path / "release"
    served.mkdir()
    models = {}
    for name, data in files.items():
        (served / f"{name}.onnx").write_bytes(data)
        sha = hashlib.sha256(b"something else" if name in wrong else data).hexdigest()
        models[name] = {"filename": f"{name}.onnx", "size": len(data), "sha256": sha}
    return {"schema": 1, "url": served.as_uri() + "/", "licence": "test",
            "languages": {"en": "en", "de": "de"}, "models": models}


def test_a_model_is_fetched_and_checked(tmp_path):
    lock = _served(tmp_path, {"en": b"E" * 1000, "de": b"D" * 900})
    target = tmp_path / "minisbd"
    done = pdf_translate._fetch_sbd_models(["de"], target, lock=lock)
    assert sorted(p.name for p in target.iterdir()) == ["de.onnx", "en.onnx"]
    assert done == {"fetched": 2, "present": 0}
    again = pdf_translate._fetch_sbd_models(["de"], target, lock=lock)
    assert again == {"fetched": 0, "present": 2}


def test_a_model_with_a_wrong_hash_fails_and_leaves_nothing(tmp_path):
    lock = _served(tmp_path, {"en": b"E" * 1000, "de": b"D" * 900}, wrong={"de"})
    target = tmp_path / "minisbd"
    with pytest.raises(pdf_translate.TranslationError, match="sentence model for German"):
        pdf_translate._fetch_sbd_models(["de"], target, lock=lock)
    left = sorted(p.name for p in target.iterdir()) if target.exists() else []
    assert "de.onnx" not in left and not any(n.endswith(".part") for n in left)


def test_setup_fetches_the_models_even_when_the_packs_are_installed(monkeypatch):
    """People who set up before this change finish by running setup again."""
    asked = []

    class _Pkg:
        def __init__(self, a, b):
            self.from_code, self.to_code = a, b

        def download(self):
            return "x"

        def install(self):
            pass

    package = type(sys)("package")
    package.update_package_index = lambda: None
    package.get_available_packages = lambda: [_Pkg("en", "de"), _Pkg("de", "en")]
    monkeypatch.setattr(pdf_translate, "_argos", lambda: (None, package))
    monkeypatch.setattr(pdf_translate, "_sbd_dir", lambda: Path("unused"))
    monkeypatch.setattr(pdf_translate, "_fetch_sbd_models",
                        lambda codes, directory, **k: asked.append(sorted(codes)) or
                        {"fetched": 0, "present": 2})
    pdf_translate.install_languages(["de"])
    assert asked == [["de", "en"]]


def test_a_language_is_ready_only_with_its_model(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf_translate, "_installed_argos_pairs", lambda: [("en", "de"), ("de", "en")])
    monkeypatch.setattr(pdf_translate, "_installed_ocr_langs", lambda: [])
    monkeypatch.setattr(pdf_translate, "_sbd_dir", lambda: tmp_path)
    (tmp_path / "en.onnx").write_bytes(b"x")
    langs = {entry["code"]: entry for entry in pdf_translate.translation_status()["languages"]}
    assert langs["en"]["translateTo"] and not langs["de"]["translateTo"]
    assert not langs["de"]["translateFrom"]
    (tmp_path / "de.onnx").write_bytes(b"x")
    langs = {entry["code"]: entry for entry in pdf_translate.translation_status()["languages"]}
    assert langs["de"]["translateTo"] and langs["de"]["translateFrom"]


# ── Translation never goes online ───────────────────────────────────────

def test_a_missing_sentence_model_fails_before_anything_else(monkeypatch, tmp_path, no_network):
    class _Argos:
        @staticmethod
        def translate(*_a):
            raise AssertionError("argostranslate was asked to translate")
    monkeypatch.setattr(pdf_translate, "_argos", lambda: (_Argos, None))
    monkeypatch.setattr(pdf_translate, "_sbd_dir", lambda: tmp_path)
    (tmp_path / "en.onnx").write_bytes(b"x")
    with pytest.raises(pdf_translate.ModelMissingError) as info:
        pdf_translate.translate_text("Guten Morgen, wie geht es dir?", "en", "de")
    assert str(info.value) == ("The sentence model for German isn't installed. "
                               "Run translation setup again.")
    # Through English: both ends' models are needed.
    (tmp_path / "en.onnx").unlink()
    (tmp_path / "de.onnx").write_bytes(b"x")
    with pytest.raises(pdf_translate.ModelMissingError, match="for English isn't installed"):
        pdf_translate.translate_text("Guten Morgen, wie geht es dir?", "ru", "de")


# ── No silent English ───────────────────────────────────────────────────

def _two_block_pdf(path, lines=("The first block of words.", "The second block of words.")):
    doc = fitz.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 100 + 200 * i), line, fontname="helv", fontsize=12)
    page.insert_text((72, 700), "2026", fontname="helv", fontsize=12)    # no letters
    doc.save(str(path))
    return str(path)


def _translator(fail_on=lambda text: True):
    def translate_text(text, target, source="auto", protect_terms=None):
        if fail_on(text):
            raise pdf_translate.ModelMissingError(f"cannot translate: {text.strip()[:9]}")
        return {"translated": text.upper(), "source": "en", "target": target}
    return translate_text


@pytest.mark.parametrize("ext", [".pdf", ".txt"])
def test_when_nothing_could_be_translated_nothing_is_saved(monkeypatch, tmp_path, ext):
    src = _two_block_pdf(tmp_path / "in.pdf")
    monkeypatch.setattr(pdf_translate, "translate_text", _translator())
    out = tmp_path / f"out{ext}"
    with pytest.raises(pdf_translate.TranslationError, match="cannot translate: The first"):
        pdf_translate.translate_pdf(src, str(out), target="de", source="en")
    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["in.pdf"]       # no temp file either


@pytest.mark.parametrize("ext", [".pdf", ".txt"])
def test_blocks_that_could_not_be_translated_are_counted(monkeypatch, tmp_path, ext):
    src = _two_block_pdf(tmp_path / "in.pdf")
    monkeypatch.setattr(pdf_translate, "translate_text",
                        _translator(fail_on=lambda text: "second" in text))
    out = tmp_path / f"out{ext}"
    res = pdf_translate.translate_pdf(src, str(out), target="de", source="en")
    assert out.exists()
    assert res["untranslatedBlocks"] == 1


def test_a_full_translation_reports_none_left(monkeypatch, tmp_path):
    src = _two_block_pdf(tmp_path / "in.pdf")
    monkeypatch.setattr(pdf_translate, "translate_text", _translator(fail_on=lambda text: False))
    res = pdf_translate.translate_pdf(src, str(tmp_path / "out.pdf"), target="de", source="en")
    assert res["untranslatedBlocks"] == 0
