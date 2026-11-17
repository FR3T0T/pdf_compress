"""The #101 gate: sanitize_pdf proves what it wrote before it commits it.

Covers verify-then-commit (a refused run never creates, replaces or deletes
the destination), "could not evaluate" as a failure keyed by finding id,
Analyze reporting everything the whole-file sweep can see (so a user never
gets `clean` from Analyze and then a refusal from Sanitize), pages that live
outside the page tree, diagnostics on a refusal, and the refusal wording.
Then the shapes that got past it or were refused without cause: the hostile
ones found by trying, and the ones real files turned out to have.

New names are reached through the modules (``pdf_analyze.X``) rather than
imported at the top, so this file still collects against a tree that does
not have them yet and each test fails for its own reason.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import hashlib
import io
import os
import subprocess
import sys

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

import pdf_analyze
import pdf_verify
from pdf_analyze import DEFAULT_SANITIZE, analyze_document, sanitize_pdf, strip_file

try:
    import fitz  # noqa: F401  (PyMuPDF — builds the embedded-photo fixture)
    from PIL import Image
    _HAS_FITZ = True
except Exception:
    _HAS_FITZ = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALL_OFF = {k: False for k in DEFAULT_SANITIZE}
ALL_ON = {k: True for k in DEFAULT_SANITIZE}


def _refused():
    """The gate's exception class (RuntimeError stands in where the tree
    under test has no gate, so the test fails with "did not raise")."""
    return getattr(pdf_analyze, "SanitizationVerificationError", RuntimeError)


# ── Builders ─────────────────────────────────────────────────────────────


def _blank(pdf) -> None:
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))


def _pdf(path, build, *, pages: int = 1, **save_kw) -> str:
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        _blank(pdf)
    build(pdf)
    pdf.save(str(path), **save_kw)
    pdf.close()
    return str(path)


def _js(marker: str = "marker-js") -> Dictionary:
    return Dictionary(S=Name.JavaScript, JS=String(f"app.alert('{marker}')"))


def _harmless() -> Dictionary:
    return Dictionary(S=Name.Named, N=Name.NextPage)


def _link(pdf, **entries):
    return pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 50, 50], **entries))


def _attach(pdf, data: bytes = b"PAYLOAD-123") -> None:
    """One embedded file in the name tree, assembled by hand (pdf.attachments
    writes different structures in different pikepdf versions)."""
    payload = pdf.make_stream(data)
    payload["/Type"] = Name.EmbeddedFile
    spec = pdf.make_indirect(Dictionary(
        Type=Name.Filespec, F=String("report-a.bin"), EF=Dictionary(F=payload)))
    pdf.Root["/Names"] = Dictionary(
        EmbeddedFiles=Dictionary(Names=Array([String("report-a.bin"), spec])))


def _dirty(path) -> str:
    """A script that runs on open, and an attachment: both removed by default."""
    def build(pdf):
        pdf.Root["/OpenAction"] = _js()
        _attach(pdf)
    return _pdf(path, build)


def _stashed_script(path) -> str:
    """A script under a key that is no action site: Sanitize has no way to
    remove it, so a sanitize with `javascript` on must be refused."""
    return _pdf(path, lambda pdf: pdf.pages[0].obj.__setitem__(
        "/Annots", Array([_link(pdf, Stash=_js())])))


def _out_of_tree(pdf, tree: str, index: int = -1):
    """Move a page out of the page tree, leaving it reachable only through
    the catalog's /Names /<tree> name tree (Templates or Pages)."""
    page = pdf.pages[index].obj
    del pdf.pages[index]
    names = pdf.Root.get("/Names")
    if names is None:
        names = pdf.Root["/Names"] = Dictionary()
    names[f"/{tree}"] = Dictionary(Names=Array([String("extra"), page]))
    return page


# ── Reading results ──────────────────────────────────────────────────────


def _sha(path) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _ids(path) -> set:
    return {f["id"] for f in analyze_document(path)["findings"]}


def _finding(report: dict, fid: str) -> dict:
    for f in report["findings"]:
        if f["id"] == fid:
            return f
    raise AssertionError(f"no {fid!r} finding in {sorted(x['id'] for x in report['findings'])}")


def _check(report: dict, cid: str) -> dict:
    for c in report["checks"]:
        if c["id"] == cid:
            return c
    raise AssertionError(f"check {cid!r} missing: {[c['id'] for c in report['checks']]}")


def _failed(report: dict) -> set:
    return {c["id"] for c in report["checks"] if not c["passed"]}


def _trailer_ids(path) -> list:
    with pikepdf.open(path) as pdf:
        tid = pdf.trailer.get("/ID")
        return [] if tid is None else [bytes(x).hex() for x in tid]


def _all_strings(path) -> bytes:
    """Every string and stream in the file, inline ones included."""
    out = []

    def visit(obj, depth=0):
        if depth > 60 or not isinstance(obj, pikepdf.Object):
            return
        if isinstance(obj, pikepdf.String):
            out.append(bytes(obj))
        elif isinstance(obj, pikepdf.Array):
            for v in obj:
                if isinstance(v, pikepdf.Object) and not v.is_indirect:
                    visit(v, depth + 1)
        elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            if isinstance(obj, pikepdf.Stream):
                try: out.append(obj.read_bytes())
                except Exception: out.append(obj.read_raw_bytes())
            for k in obj.keys():
                v = obj[k]
                if isinstance(v, pikepdf.Object) and not v.is_indirect:
                    visit(v, depth + 1)

    with pikepdf.open(path) as pdf:
        visit(pdf.trailer)
        for obj in pdf.objects:
            visit(obj)
    return b"\n".join(out)


# ── C1.1 The gate ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestGate:
    def test_script_and_attachment_verify_clean(self, tmp_path):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out)
        report = res["verification"]
        assert report["verified"] is True
        assert report["tool"] == "sanitization"
        assert report["inputPath"] == src and report["outputPath"] == out
        assert report["sourceSha256"] == _sha(src)
        assert report["outputSha256"] == _sha(out)
        for cid in ("sanitization.readable", "sanitize.scripts_js", "sanitize.files_embedded",
                    "sanitize.sweep_javascript", "sanitize.sweep_embedded_files",
                    "sanitize.file_id"):
            assert _check(report, cid)["passed"] is True
        assert b"marker-js" not in _all_strings(out) and b"PAYLOAD-123" not in _all_strings(out)

    @pytest.mark.skipif(not _HAS_FITZ, reason="PyMuPDF (fitz) not installed")
    def test_embedded_gps_image_is_listed_as_a_residual(self, tmp_path):
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (40.0, 44.0, 54.38), 3: "W", 4: (73.0, 59.0, 8.5)}
        jpg = str(tmp_path / "geo.jpg")
        Image.new("RGB", (64, 48), (10, 120, 200)).save(jpg, quality=90, exif=exif)
        src = str(tmp_path / "geo.pdf")
        doc = fitz.open()
        doc.new_page(width=300, height=300).insert_image(
            fitz.Rect(20, 20, 200, 160), stream=_read(jpg))
        doc.save(src)
        doc.close()
        with pikepdf.open(src, allow_overwriting_input=True) as pdf:
            pdf.Root["/OpenAction"] = _js()
            pdf.save(src)

        res = sanitize_pdf(src, str(tmp_path / "out.pdf"))
        report = res["verification"]
        assert report["verified"] is True
        assert _check(report, "sanitize.scripts_js")["passed"] is True
        assert "location.embedded_image" in {f["id"] for f in report["residualFindings"]}

    def test_promised_category_that_survives_is_refused_and_nothing_is_written(self, tmp_path):
        src, out = _stashed_script(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        assert not os.path.exists(out)
        assert os.listdir(tmp_path) == ["src.pdf"]                 # no stray temp either
        report = ei.value.report
        assert report["verified"] is False and report["outputPath"] == out
        assert "sanitize.sweep_javascript" in _failed(report)

    def test_remover_that_removes_nothing_is_refused(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "_scrub_owner_actions", lambda *a, **k: False)
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        assert {"sanitize.scripts_js", "sanitize.sweep_javascript"} <= _failed(ei.value.report)
        assert not os.path.exists(out)

    def test_in_place_refusal_leaves_the_original_byte_identical(self, tmp_path):
        src = _stashed_script(tmp_path / "only_copy.pdf")
        before = _read(src)
        with pytest.raises(_refused()):
            sanitize_pdf(src, src)
        assert _read(src) == before
        assert os.listdir(tmp_path) == ["only_copy.pdf"]

    def test_refusal_keeps_a_file_already_at_the_destination(self, tmp_path):
        src = _stashed_script(tmp_path / "src.pdf")
        out = tmp_path / "earlier.pdf"
        out.write_bytes(b"an earlier export the user still wants")
        with pytest.raises(_refused()):
            sanitize_pdf(src, str(out))
        assert out.read_bytes() == b"an earlier export the user still wants"
        assert sorted(os.listdir(tmp_path)) == ["earlier.pdf", "src.pdf"]

    def test_destination_is_not_written_until_verified(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        seen = {}
        real = pdf_verify.verify_sanitization

        def spy(path, *a, **k):
            seen["path"], seen["dest_existed"] = path, os.path.exists(out)
            return real(path, *a, **k)

        monkeypatch.setattr(pdf_verify, "verify_sanitization", spy)
        sanitize_pdf(src, out)
        assert seen.get("dest_existed") is False, "nothing verified it before it was committed"
        assert os.path.dirname(seen["path"]) == str(tmp_path) and seen["path"] != out
        assert sorted(os.listdir(tmp_path)) == ["out.pdf", "src.pdf"]

    def test_sanitizing_twice_succeeds_and_changes_no_byte(self, tmp_path):
        first, second = str(tmp_path / "first.pdf"), str(tmp_path / "second.pdf")
        sanitize_pdf(_dirty(tmp_path / "src.pdf"), first)
        res = sanitize_pdf(first, second)
        assert _read(second) == _read(first)
        report = res["verification"]
        # Same bytes is the same file: it keeps its identifier and still verifies.
        assert report["sourceSha256"] == report["outputSha256"]
        assert _check(report, "sanitize.file_id")["passed"] is True
        assert report["verified"] is True

    def test_output_shares_no_identifier_with_its_source(self, tmp_path):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        sanitize_pdf(src, out)
        assert _trailer_ids(src) and _trailer_ids(out)
        assert not set(_trailer_ids(out)) & set(_trailer_ids(src))

    def test_kept_source_identifier_is_refused(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "save_with_content_id",
                            lambda pdf, path, **kw: pdf.save(path, **kw))   # qpdf keeps /ID[0]
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        assert _failed(ei.value.report) == {"sanitize.file_id"}
        assert "identifier" in str(ei.value)
        assert not os.path.exists(out)

    def test_owner_password_source_still_sanitizes(self, tmp_path):
        plain = _dirty(tmp_path / "plain.pdf")
        src = str(tmp_path / "owner.pdf")
        with pikepdf.open(plain) as pdf:
            pdf.save(src, encryption=pikepdf.Encryption(owner="owner-pw", user=""))
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True
        with pikepdf.open(out) as pdf:
            assert not pdf.is_encrypted
        assert b"marker-js" not in _all_strings(out)

    def test_image_inputs_carry_no_verification(self, tmp_path):
        pytest.importorskip("PIL")
        from PIL import Image as _Image
        jpg = str(tmp_path / "p.jpg")
        _Image.new("RGB", (8, 8), (1, 2, 3)).save(jpg)
        assert "verification" not in strip_file(jpg, str(tmp_path / "o.jpg"))


# ── C1.2 Could not evaluate ──────────────────────────────────────────────


def _boom(*_a, **_k):
    raise RuntimeError("simulated scanner failure")


@pytest.mark.integration
class TestCouldNotEvaluate:
    def test_crashed_scanner_marks_its_ids_and_analyze_is_not_clean(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "blank.pdf", lambda pdf: None)
        assert _ids(src) == {"clean"}
        monkeypatch.setattr(pdf_analyze, "_scan_links", _boom)
        report = analyze_document(src)
        assert "links.uri" in report["unevaluated"]
        ids = {f["id"] for f in report["findings"]}
        assert "clean" not in ids
        incomplete = _finding(report, "audit.incomplete")
        assert incomplete["severity"] == "medium"
        assert any("link" in item.lower() for item in incomplete["items"])
        assert report["overallRisk"] == "medium"

    def test_one_unreadable_structure_is_recorded_and_the_rest_still_reported(
            self, tmp_path, monkeypatch):
        def build(pdf):
            pdf.pages[0].obj["/Annots"] = Array([_link(pdf, A=_js())])
            pdf.pages[1].obj["/Annots"] = Array([_link(pdf, A=_js())])

        src = _pdf(tmp_path / "two.pdf", build, pages=2)
        real = pdf_analyze._owner_sites

        def flaky(owner_kind, label, owner, *a, **k):
            if owner_kind == "annot" and label.startswith("Page 2"):
                raise RuntimeError("simulated damaged annotation")
            return real(owner_kind, label, owner, *a, **k)

        monkeypatch.setattr(pdf_analyze, "_owner_sites", flaky)
        report = analyze_document(src)
        assert any("Page 1" in i for i in _finding(report, "scripts.js")["items"])   # still audited
        assert "scripts.js" in report["unevaluated"]                                 # but not fully
        assert "audit.incomplete" in {f["id"] for f in report["findings"]}

    def test_a_very_long_action_chain_is_walked_not_capped(self, tmp_path):
        links = 5000

        def build(pdf):
            node = pdf.make_indirect(_js())
            for _ in range(links):
                node = pdf.make_indirect(Dictionary(S=Name.Named, N=Name.NextPage, Next=node))
            pdf.Root["/OpenAction"] = node

        src = _pdf(tmp_path / "deep.pdf", build)
        report = analyze_document(src)
        assert "scripts.js" in {f["id"] for f in report["findings"]}
        assert report["unevaluated"] == {}
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, {**ALL_OFF, "javascript": True})
        assert res["removed"] == {"annot_javascript": 1}       # the script, not the chain
        assert res["verification"]["verified"] is True
        assert b"marker-js" not in _all_strings(out)

    def test_with_every_option_off_a_long_chain_is_left_whole(self, tmp_path):
        def build(pdf):
            node = pdf.make_indirect(_js())
            for _ in range(40):
                node = pdf.make_indirect(Dictionary(S=Name.Named, N=Name.NextPage, Next=node))
            pdf.Root["/OpenAction"] = node

        src, out = _pdf(tmp_path / "chain.pdf", build), str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, ALL_OFF)["removed"] == {}
        assert b"marker-js" in _all_strings(out), "the tail of the chain was cut off unasked"

    def test_promised_unevaluated_id_is_refused(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "_scan_links", _boom)
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out, {**ALL_OFF, "external_links": True})
        check = _check(ei.value.report, "sanitize.links_uri")
        assert check["passed"] is False and check["unevaluated"] is True
        assert check["isDocumentLevel"] is True
        assert "could not be checked" in str(ei.value)
        assert not os.path.exists(out)

    def test_unpromised_unevaluated_id_is_reported_not_refused(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "_scan_links", _boom)
        res = sanitize_pdf(src, out)                      # external_links is off by default
        report = res["verification"]
        assert report["verified"] is True
        assert [u["id"] for u in report["unevaluated"]] == ["links.uri"]
        assert all(u["reasons"] for u in report["unevaluated"])


# ── C1.3 Analyze reports what the sweep can see ──────────────────────────


@pytest.mark.integration
class TestAnalyzeSeesWhatTheSweepSees:
    def test_script_sanitize_cannot_remove_is_reported_with_that_note(self, tmp_path):
        src = _stashed_script(tmp_path / "src.pdf")
        finding = _finding(analyze_document(src), "scripts.js")
        assert any("page 1" in i.lower() and "cannot remove" in i.lower()
                   for i in finding["items"])
        with pytest.raises(_refused()):                    # ...and Sanitize agrees
            sanitize_pdf(src, str(tmp_path / "out.pdf"))

    def test_3d_instantiation_script_is_reported(self, tmp_path):
        def build(pdf):
            artwork = pdf.make_stream(b"U3D\x00")
            artwork["/Type"] = Name("/3D")
            artwork["/OnInstantiate"] = pdf.make_stream(b"app.alert('marker-3d');")
            annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name("/3D"),
                                                 Rect=[0, 0, 50, 50]))
            annot["/3DD"] = artwork
            pdf.pages[0].obj["/Annots"] = Array([annot])

        src = _pdf(tmp_path / "3d.pdf", build)
        finding = _finding(analyze_document(src), "scripts.js")
        assert any("3d" in i.lower() for i in finding["items"])
        res = sanitize_pdf(src, str(tmp_path / "out.pdf"))
        assert res["removed"] == {"instantiate_script": 1}
        assert res["verification"]["verified"] is True

    def test_script_33_links_down_a_chain_is_reported(self, tmp_path):
        def build(pdf):
            node = _js()
            for _ in range(33):
                node = Dictionary(S=Name.Named, N=Name.NextPage, Next=node)
            pdf.Root["/OpenAction"] = node

        assert "scripts.js" in _ids(_pdf(tmp_path / "33.pdf", build))

    @pytest.mark.parametrize("tree", ["Templates", "Pages"])
    def test_script_on_a_page_outside_the_page_tree_is_reported(self, tmp_path, tree):
        def build(pdf):
            pdf.pages[1].obj["/AA"] = Dictionary(O=_js())
            _out_of_tree(pdf, tree)

        assert "scripts.js" in _ids(_pdf(tmp_path / "t.pdf", build, pages=2))

    def test_clean_file_is_still_clean(self, tmp_path):
        report = analyze_document(_pdf(tmp_path / "blank.pdf", lambda pdf: None))
        assert {f["id"] for f in report["findings"]} == {"clean"}
        assert report["unevaluated"] == {}


# ── C1.4 Pages outside the page tree ─────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize("tree", ["Templates", "Pages"])
class TestPagesOutsideThePageTree:
    def test_actions_there_are_found_removed_and_verified(self, tmp_path, tree):
        def build(pdf):
            page = pdf.pages[1].obj
            page["/AA"] = Dictionary(O=_js("marker-page"))
            page["/Annots"] = Array([_link(pdf, A=_js("marker-annot"))])
            _out_of_tree(pdf, tree)

        src, out = _pdf(tmp_path / "t.pdf", build, pages=2), str(tmp_path / "out.pdf")
        finding = _finding(analyze_document(src), "scripts.js")
        assert finding["count"] == 2
        res = sanitize_pdf(src, out)
        assert res["total_removed"] == 2
        assert res["verification"]["verified"] is True
        left = _all_strings(out)
        assert b"marker-page" not in left and b"marker-annot" not in left

    def test_a_page_in_both_the_tree_and_the_name_tree_is_walked_once(self, tmp_path, tree):
        def build(pdf):
            page = pdf.pages[0].obj
            page["/AA"] = Dictionary(O=_js())
            pdf.Root["/Names"] = Dictionary({f"/{tree}": Dictionary(
                Names=Array([String("same"), page]))})

        src = _pdf(tmp_path / "t.pdf", build)
        assert _finding(analyze_document(src), "scripts.js")["count"] == 1
        assert sanitize_pdf(src, str(tmp_path / "out.pdf"))["total_removed"] == 1


# ── C1.5 Diagnostics ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestDiagnostics:
    def test_refusal_carries_what_went_wrong_without_naming_files(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "quarterly-figures.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Annots", Array([_link(pdf, A=_js())])))
        out = str(tmp_path / "out.pdf")
        real = pdf_analyze._scrub_owner_actions

        def broken(owner_kind, *a, **k):
            if owner_kind == "annot":
                raise RuntimeError(f"simulated failure while reading {src}")
            return real(owner_kind, *a, **k)

        monkeypatch.setattr(pdf_analyze, "_scrub_owner_actions", broken)
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        diagnostics = ei.value.report["diagnostics"]
        assert diagnostics and any("annotation" in d.lower() for d in diagnostics)
        blob = " ".join(diagnostics)
        assert "quarterly-figures" not in blob and str(tmp_path) not in blob

    def test_clean_run_has_no_diagnostics(self, tmp_path):
        res = sanitize_pdf(_dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf"))
        assert res["verification"]["diagnostics"] == []


# ── C1.6 Refusal wording ─────────────────────────────────────────────────


@pytest.mark.integration
class TestRefusalWording:
    def test_survivor_is_named_in_plain_words_with_where_it_is(self, tmp_path):
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(_stashed_script(tmp_path / "src.pdf"), str(tmp_path / "out.pdf"))
        assert "JavaScript" in str(ei.value) and "still" in str(ei.value)
        sweep = _check(ei.value.report, "sanitize.sweep_javascript")
        assert sweep["evidence"] == ["a JavaScript action on page 1, annotation 1, under /Stash"]
        # The raw object path stays on the report for the exportable version.
        assert sweep["objectPaths"] == ["trailer/Root/Pages/Kids[0]/Annots[0]/Stash"]

    @pytest.mark.parametrize("segments, page, expected", [
        (["trailer", "/Root", "/Pages", "/Kids", "[0]", "/Annots", "[2]", "/A"], 1,
         "on page 1, annotation 3"),
        (["trailer", "/Root", "/Pages", "/Kids", "[4]", "/AA", "/O"], 5, "on page 5"),
        (["trailer", "/Root", "/OpenAction", "/Next", "/Next", "/Next"], None,
         "in the document's open action, 3 steps down a chain of actions"),
        (["trailer", "/Root", "/Outlines", "/First", "/Next", "/A"], None, "in a bookmark"),
        (["trailer", "/Root", "/AcroForm", "/Fields", "[0]", "/Kids", "[1]", "/AA", "/K"], None,
         "in a form field"),
        (["trailer", "/Root", "/Names", "/JavaScript"], None, "in the document's named scripts"),
        (["trailer", "/Root", "/Names", "/Templates", "/Names", "[1]", "/Annots", "[0]", "/A"],
         None, "on a template page, annotation 1"),
        (["trailer", "/Root", "/Custom"], None, "in the document catalog, under /Custom"),
        (["trailer", "/Info"], None, "in the document information"),
    ])
    def test_plain_location(self, segments, page, expected):
        assert pdf_verify.plain_location(segments, page) == expected


# ── Hostile shapes ───────────────────────────────────────────────────────
#
#  Each of these got past the gate, or got a refusal Analyze had not warned
#  of, in the adversarial pass over Cluster C1.


def _only(option: str) -> dict:
    return {**ALL_OFF, option: True}


def _launch() -> Dictionary:
    return Dictionary(S=Name.Launch, F=String("marker-launch.exe"))


def _named_scripts(pdf, *actions) -> None:
    pairs = []
    for n, action in enumerate(actions):
        pairs += [String(f"s{n}"), pdf.make_indirect(action)]
    pdf.Root["/Names"] = Dictionary(JavaScript=Dictionary(Names=Array(pairs)))


def _with_first_id(path, first: bytes) -> str:
    """A file with a script, whose first trailer /ID element is *first*.
    (The second is not ours to choose: qpdf writes a new one on every save.)"""
    def build(pdf):
        pdf.Root["/OpenAction"] = _js()
        pdf.trailer["/ID"] = Array([String(first), String(first)])
    return _pdf(path, build)


def _handmade_with_ids(path, first: str, second: str) -> str:
    """A one-page file with a script that runs on open, written byte by byte
    so that both trailer /ID elements (given in hex) are exactly what the
    test says. No library will write a chosen second element."""
    objects = [b"<</Type/Catalog/Pages 2 0 R/OpenAction 4 0 R>>",
               b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>",
               b"<</S/JavaScript/JS(marker-js)>>"]
    out, offsets = b"%PDF-1.7\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<</Size %d/Root 1 0 R/ID[<%s><%s>]>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, first.encode(), second.encode(), xref)
    with open(path, "wb") as f:
        f.write(out)
    assert _trailer_ids(path) == [first, second]        # the fixture is what it claims
    return str(path)


@pytest.mark.integration
class TestMetadataHeldTwoWays:
    def test_info_dictionary_also_held_under_another_key_is_emptied(self, tmp_path):
        def build(pdf):
            info = pdf.make_indirect(Dictionary(Author=String("marker-author")))
            pdf.trailer["/Info"] = info
            pdf.Root["/Private"] = Dictionary(Copy=info)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _only("metadata"))
        assert res["removed"] == {"doc_info": 1}
        assert b"marker-author" not in _all_strings(out)

    def test_untyped_stream_in_a_page_metadata_slot_is_metadata(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/Metadata", pdf.make_stream(b"<x>marker-meta</x>")))
        out = str(tmp_path / "out.pdf")
        assert _finding(analyze_document(src), "meta.xmp")["items"] == ["Page 1"]
        # The verifier's own look finds it too, so a remover that missed it
        # would be refused rather than believed.
        with pikepdf.open(src) as pdf:
            hits, status = pdf_verify._sweep(pdf, _only("metadata"))
        assert status.complete and [h.where for h in hits["metadata"]] == ["on page 1"]
        res = sanitize_pdf(src, out, _only("metadata"))
        assert res["removed"] == {"xmp_metadata": 1}
        assert b"marker-meta" not in _all_strings(out)

    def test_a_slot_sharing_its_stream_with_the_page_content_keeps_the_page(self, tmp_path):
        def build(pdf):
            st = pdf.make_stream(b"BT ET")
            pdf.pages[0].obj["/Contents"] = st
            pdf.pages[0].obj["/Metadata"] = st
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        sanitize_pdf(src, out, _only("metadata"))
        with pikepdf.open(out) as pdf:
            page = pdf.pages[0].obj
            assert "/Metadata" not in page and page["/Contents"].read_bytes() == b"BT ET"

    def test_an_image_a_resource_dictionary_names_metadata_is_left_alone(self, tmp_path):
        def build(pdf):
            image = pdf.make_stream(b"\x00", Type=Name.XObject, Subtype=Name.Image, Width=1,
                                    Height=1, ColorSpace=Name.DeviceGray, BitsPerComponent=8)
            pdf.pages[0].obj["/Resources"] = Dictionary(XObject=Dictionary(Metadata=image))
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert "meta.xmp" not in _ids(src)
        assert sanitize_pdf(src, out, _only("metadata"))["removed"] == {}
        with pikepdf.open(out) as pdf:
            assert "/Metadata" in pdf.pages[0].obj["/Resources"]["/XObject"]


@pytest.mark.integration
class TestOneOptionAlone:
    """What `auto_actions` or `javascript` removes whole, the other options
    must be able to clean inside — or one of them alone is refused over
    something all of them together remove."""

    def test_aa_entry_holding_an_array_of_actions(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/AA", Dictionary(O=Array([_harmless(), _js()]))))
        out = str(tmp_path / "out.pdf")
        assert {"scripts.js", "actions.autorun"} <= _ids(src)
        res = sanitize_pdf(src, out, _only("javascript"))
        assert res["removed"] == {"page_aa": 1}
        assert b"marker-js" not in _all_strings(out)
        with pikepdf.open(out) as pdf:           # the harmless one is still there
            assert len(pdf.pages[0].obj["/AA"]["/O"]) == 1
        # And with auto_actions both are counted, as two lone entries would be.
        assert sanitize_pdf(src, out, _only("auto_actions"))["removed"] == {"page_aa": 2}

    def test_launch_chained_after_a_named_script(self, tmp_path):
        def build(pdf):
            script = _js()
            script["/Next"] = _launch()
            _named_scripts(pdf, script)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert "actions.launch" in _ids(src)
        res = sanitize_pdf(src, out, _only("launch_actions"))
        assert res["removed"] == {"launch_action": 1}
        left = _all_strings(out)
        assert b"marker-launch" not in left and b"marker-js" in left

    def test_launch_stored_in_place_of_a_named_script(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _named_scripts(pdf, _js(), _launch()))
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _only("launch_actions"))["removed"] == {"launch_action": 1}
        with pikepdf.open(out) as pdf:
            assert len(pdf.Root["/Names"]["/JavaScript"]["/Names"]) == 2    # one pair left
        assert b"marker-launch" not in _all_strings(out)

    def test_with_javascript_on_the_named_scripts_still_go_whole(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _named_scripts(pdf, _js(), _launch()))
        res = sanitize_pdf(src, str(tmp_path / "out.pdf"))
        assert res["removed"] == {"named_javascript": 1}

    def test_analyze_lists_what_only_another_option_removes(self, tmp_path):
        """A script under a private key of a harmless action inside /AA goes
        with the /AA under `auto_actions`, and sits at no place the scanners
        list. Analyze must still say it is there. (`javascript` alone is
        refused for it: a known limit, and Analyze's note says why.)"""
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/AA", Dictionary(O=Dictionary(S=Name.Named, N=Name.NextPage, Weird=_js()))))
        out = str(tmp_path / "out.pdf")
        items = _finding(analyze_document(src), "scripts.js")["items"]
        assert items == ["A JavaScript action on page 1, under /Weird — "
                         "Sanitize removes it only together with what holds it"]
        assert sanitize_pdf(src, out)["verification"]["verified"] is True
        with pytest.raises(_refused()):
            sanitize_pdf(src, out, _only("javascript"))

    def test_an_empty_attachment_list_and_a_bare_info_are_still_clean(self, tmp_path):
        """What the sweep finds by position is not reported from the
        untouched file: the option concerned removes it by position."""
        def build(pdf):
            pdf.Root["/Names"] = Dictionary(EmbeddedFiles=Dictionary(Names=Array([])))
            pdf.trailer["/Info"] = pdf.make_indirect(Dictionary(Trapped=Name.Unknown))
        src = _pdf(tmp_path / "src.pdf", build)
        assert _ids(src) == {"clean"}
        assert sanitize_pdf(src, str(tmp_path / "out.pdf"), ALL_ON)["verification"]["verified"]


@pytest.mark.integration
class TestIdentifierTheSourceAlreadyCarries:
    """The output's identifier is a digest of its content, so a source that
    descends from an earlier sanitized copy can already carry it."""

    def _colliding_source(self, tmp_path) -> str:
        probe = _with_first_id(tmp_path / "probe.pdf", b"\x01" * 16)
        sanitize_pdf(probe, str(tmp_path / "probe-out.pdf"))
        would_get = _trailer_ids(tmp_path / "probe-out.pdf")[0]
        src = _with_first_id(tmp_path / "src.pdf", bytes.fromhex(would_get))
        assert _trailer_ids(src)[0] == would_get        # the fixture is what it claims
        return src

    def test_it_is_written_in_a_second_layout_and_verifies(self, tmp_path):
        src, out = self._colliding_source(tmp_path), str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True
        assert _trailer_ids(out) and not set(_trailer_ids(out)) & set(_trailer_ids(src))
        assert _ids(out) == {"clean"}
        # The result sanitizes again like any other file.
        again = sanitize_pdf(out, str(tmp_path / "again.pdf"))
        assert again["verification"]["verified"] is True

    def test_the_gate_refuses_it_when_the_sanitizer_does_not_step_aside(self, tmp_path,
                                                                         monkeypatch):
        src, out = self._colliding_source(tmp_path), str(tmp_path / "out.pdf")
        monkeypatch.setattr(pdf_analyze, "_shares_identifier", lambda *a: False, raising=False)
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        assert _failed(ei.value.report) == {"sanitize.file_id"}
        assert not os.path.exists(out)

    def test_a_source_carrying_both_layouts_identifiers_is_refused(self, tmp_path):
        """The one case stepping aside cannot help: a source built to carry
        the identifier of the second layout as well. Refused, nothing
        written — and not something Analyze can say beforehand."""
        def clean_copy_id(name, first, second) -> str:
            src = _handmade_with_ids(tmp_path / name, first, second)
            sanitize_pdf(src, str(tmp_path / f"out-{name}"))
            return _trailer_ids(tmp_path / f"out-{name}")[0]

        ordinary = clean_copy_id("plain.pdf", "01" * 16, "02" * 16)
        stepped_aside = clean_copy_id("one.pdf", ordinary, "02" * 16)
        assert stepped_aside != ordinary
        # In the second place it is found and stepped aside from just the same.
        assert clean_copy_id("second.pdf", "01" * 16, ordinary) == stepped_aside

        src = _handmade_with_ids(tmp_path / "both.pdf", ordinary, stepped_aside)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        assert _failed(ei.value.report) == {"sanitize.file_id"}
        assert not os.path.exists(out)
        assert _ids(src) == {"scripts.js", "actions.autorun"}       # no warning of it

    def test_a_resaved_copy_of_a_sanitized_file_sanitizes(self, tmp_path):
        first, resaved = str(tmp_path / "first.pdf"), str(tmp_path / "resaved.pdf")
        sanitize_pdf(_dirty(tmp_path / "src.pdf"), first)
        with pikepdf.open(first) as pdf:
            pdf.save(resaved)
        out = str(tmp_path / "out.pdf")
        assert _ids(resaved) == {"clean"}
        assert sanitize_pdf(resaved, out)["verification"]["verified"] is True
        assert not set(_trailer_ids(out)) & set(_trailer_ids(resaved))


# ── Shapes real files have ───────────────────────────────────────────────
#
#  Each of these came from running the gate over public collections of real
#  PDFs. The fixtures are built by hand to the same shape.


def _page_with_text(pdf) -> dict:
    """Give page 1 text in a font, a note and a bookmark; returns the objects
    a trailer /Info could be pointed at, by what they are."""
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    page = pdf.pages[0].obj
    page["/Contents"] = pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (marker-text on the page) Tj ET")
    page["/Resources"] = Dictionary(Font=Dictionary(F1=font))
    # /Type is optional on an annotation and on the outline root: these two
    # say what they are only through /Subtype and through what they hold.
    note = pdf.make_indirect(Dictionary(
        Subtype=Name.Text, Rect=[20, 20, 40, 40], Contents=String("marker-note")))
    page["/Annots"] = Array([note])
    outlines = pdf.make_indirect(Dictionary(Count=1))
    item = pdf.make_indirect(Dictionary(
        Title=String("marker-bookmark"), Parent=outlines, Dest=Array([page, Name.Fit])))
    outlines["/First"] = outlines["/Last"] = item
    pdf.Root["/Outlines"] = outlines
    return {"page tree root": pdf.Root["/Pages"], "page": page, "catalog": pdf.Root,
            "font": font, "annotation": note, "content stream": page["/Contents"],
            "outline root": outlines}


def _roles(pdf) -> dict:
    """The same objects, found in a file that was read back."""
    page = pdf.pages[0].obj
    return {"page tree root": pdf.Root["/Pages"], "page": page, "catalog": pdf.Root,
            "font": page["/Resources"]["/Font"]["/F1"], "annotation": page["/Annots"][0],
            "content stream": page["/Contents"], "outline root": pdf.Root["/Outlines"]}


def _page_text(path) -> str:
    import fitz
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


@pytest.mark.integration
class TestInfoThatIsAnotherObject:
    """A trailer whose /Info points at an object with another job — a real
    file has /Info and the page tree root as one object. Removing the
    document information must not take the other job with it."""

    @pytest.mark.parametrize("options", [_only("metadata"), ALL_ON], ids=["metadata", "all"])
    @pytest.mark.parametrize("role", ["page tree root", "page", "catalog", "font", "annotation",
                                      "content stream", "outline root"])
    def test_only_the_trailer_entry_goes(self, tmp_path, role, options):
        def build(pdf):
            pdf.trailer["/Info"] = _page_with_text(pdf)[role]
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        with pikepdf.open(src) as pdf:                 # the fixture is what it claims
            assert pdf.trailer["/Info"].objgen == _roles(pdf)[role].objgen
        # Analyze tries the removal on its own copy; that must not break it.
        assert analyze_document(src)["unevaluated"] == {}

        res = sanitize_pdf(src, out, options)
        assert res["verification"]["verified"] is True
        assert res["removed"] == {"doc_info": 1}
        with pikepdf.open(src) as before, pikepdf.open(out) as after:
            assert "/Info" not in after.trailer
            assert len(after.pages) == len(before.pages) == 1
            was, now = _roles(before), _roles(after)
            for name in was:                           # every one of them, not just /Info's
                assert sorted(now[name].keys()) == sorted(was[name].keys()), name
            assert now["content stream"].read_bytes() == was["content stream"].read_bytes()
            assert str(now["annotation"]["/Contents"]) == "marker-note"
            assert str(now["font"]["/BaseFont"]) == "/Helvetica"
            assert str(now["outline root"]["/First"]["/Title"]) == "marker-bookmark"
        assert _page_text(out) == _page_text(src) and "marker-text" in _page_text(out)

    def test_a_plain_one_is_still_emptied_where_it_is_held_twice(self, tmp_path):
        def build(pdf):
            info = pdf.make_indirect(Dictionary(
                Author=String("marker-author"), Trapped=Name.Unknown, Revision=7, Draft=True,
                Company=pdf.make_indirect(String("marker-company"))))
            pdf.trailer["/Info"] = info
            pdf.Root["/Private"] = Dictionary(Copy=info)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _only("metadata"))["removed"] == {"doc_info": 1}
        left = _all_strings(out)
        assert b"marker-author" not in left and b"marker-company" not in left
        with pikepdf.open(out) as pdf:
            assert len(pdf.Root["/Private"]["/Copy"].keys()) == 0

    @pytest.mark.parametrize("entries, plain", [
        (dict(Author=String("a"), Trapped=Name.Unknown, Revision=7, Draft=True), True),
        (dict(), True),
        (dict(Author=String("a"), Type=Name.Info), False),
        (dict(Author=String("a"), Subtype=Name.Text), False),
        (dict(Author=String("a"), S=Name.GoTo), False),
        (dict(Author=String("a"), Kids=Array([])), False),
        (dict(Author=String("a"), First=Dictionary()), False),
    ], ids=["strings names numbers booleans", "empty", "has /Type", "has /Subtype", "has /S",
            "holds an array", "holds a dictionary"])
    def test_what_is_nothing_but_document_information(self, entries, plain):
        assert pdf_analyze._is_plain_info(Dictionary(**entries)) is plain

    def test_a_stream_is_never_plain_document_information(self):
        with pikepdf.Pdf.new() as pdf:
            stream = pdf.make_stream(b"")
            stream["/Author"] = String("a")
            assert pdf_analyze._is_plain_info(stream) is False


def _xfa_form(pdf, *, single: bool = False) -> list:
    """An XFA form whose packets are typed /EmbeddedFile, the way Adobe
    LiveCycle Designer writes them. Returns the packet streams."""
    packets = []
    for body in (b"<template>marker-xfa-template</template>",
                 b"<datasets>marker-xfa-data</datasets>"):
        packet = pdf.make_stream(body)
        packet["/Type"] = Name.EmbeddedFile
        packets.append(packet)
    xfa = packets[0] if single else Array(
        [String("template"), packets[0], String("datasets"), packets[1]])
    pdf.Root["/AcroForm"] = pdf.make_indirect(Dictionary(Fields=Array([]), XFA=xfa))
    return packets


def _typed_stream(pdf, data: bytes = b"marker-stray"):
    stream = pdf.make_stream(data)
    stream["/Type"] = Name.EmbeddedFile
    return stream


@pytest.mark.integration
class TestXfaPackets:
    """An XFA form's packets are the form, whatever /Type they declare."""

    @pytest.mark.parametrize("single", [False, True], ids=["an array of packets", "one stream"])
    def test_packets_typed_embedded_file_are_the_form_not_attachments(self, tmp_path, single):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _xfa_form(pdf, single=single))
        out = str(tmp_path / "out.pdf")
        report = analyze_document(src)
        assert {f["id"] for f in report["findings"]} == {"forms.xfa"}
        assert report["unevaluated"] == {}

        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True and res["removed"] == {}
        assert "forms.xfa" in {f["id"] for f in res["verification"]["residualFindings"]}
        assert b"marker-xfa-template" in _all_strings(out)      # the form is not removed

    def test_a_packet_is_skipped_however_the_walk_reaches_it(self, tmp_path):
        def build(pdf):
            packets = _xfa_form(pdf)
            pdf.Root["/Stash"] = Dictionary(Copy=packets[0], Again=Array([packets[1]]))
        src = _pdf(tmp_path / "src.pdf", build)
        with pikepdf.open(src) as pdf:
            hits, status = pdf_verify._sweep(pdf, _only("embedded_files"))
        assert status.complete and hits["embedded_files"] == []
        assert _ids(src) == {"forms.xfa"}

    @pytest.mark.parametrize("with_form", [False, True], ids=["alone", "beside an XFA form"])
    def test_a_stream_typed_embedded_file_elsewhere_is_still_refused(self, tmp_path, with_form):
        def build(pdf):
            if with_form:
                _xfa_form(pdf)
            pdf.Root["/Stash"] = _typed_stream(pdf)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert _finding(analyze_document(src), "files.embedded")["items"] == [
            "An embedded file's contents in the document catalog, under /Stash — "
            "Sanitize cannot remove it from here"]
        with pytest.raises(_refused()) as ei:
            sanitize_pdf(src, out)
        sweep = _check(ei.value.report, "sanitize.sweep_embedded_files")
        assert sweep["passed"] is False
        assert sweep["evidence"] == [
            "an embedded file's contents in the document catalog, under /Stash"]
        assert not os.path.exists(out)

    def test_a_packet_listed_under_another_forms_key_is_not_one(self, tmp_path):
        """By position means the catalog's /AcroForm /XFA, not any key that
        happens to be called /XFA."""
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", Dictionary(XFA=Array([String("template"), _typed_stream(pdf)]))))
        assert "files.embedded" in _ids(src)
        with pytest.raises(_refused()):
            sanitize_pdf(src, str(tmp_path / "out.pdf"))

    @pytest.mark.parametrize("home, counter", [("the attachment list", "embedded_files"),
                                               ("a private key", "embedded_file")])
    def test_a_packet_that_is_also_an_attachment_loses_the_attachment(self, tmp_path, home,
                                                                      counter):
        def build(pdf):
            packets = _xfa_form(pdf)
            spec = pdf.make_indirect(Dictionary(
                Type=Name.Filespec, F=String("marker-form-data.xml"),
                EF=Dictionary(F=packets[1])))
            if home == "the attachment list":
                pdf.Root["/Names"] = Dictionary(EmbeddedFiles=Dictionary(
                    Names=Array([String("marker-form-data.xml"), spec])))
            else:
                pdf.Root["/Stash"] = spec
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert _ids(src) == {"forms.xfa", "files.embedded"}

        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True and res["removed"] == {counter: 1}
        assert _ids(out) == {"forms.xfa"}
        left = _all_strings(out)
        assert b"marker-form-data.xml" not in left and b"marker-xfa-data" in left
        with pikepdf.open(out) as pdf:
            assert len(pdf.Root["/AcroForm"]["/XFA"]) == 4      # both packets still listed


def _image(pdf):
    return pdf.make_stream(b"\x00", Type=Name.XObject, Subtype=Name.Image, Width=1, Height=1,
                           ColorSpace=Name.DeviceGray, BitsPerComponent=8)


def _form(pdf, xobjects: dict):
    return pdf.make_stream(b"", Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 9, 9],
                           Resources=Dictionary(XObject=Dictionary(xobjects)))


def _nest_forms(pdf, levels: int, width: int, image=None) -> None:
    """Put an image under *levels* layers of form XObjects on page 1: *width*
    forms to a layer, each holding every form of the layer below. The forms
    of a layer are shared by all the forms above it, so there are
    width ** levels ways down to the image and only width * levels forms."""
    below = {"/Im0": image if image is not None else _image(pdf)}
    for _ in range(levels):
        below = {f"/Fm{n}": _form(pdf, below) for n in range(width)}
    pdf.pages[0].obj["/Resources"] = Dictionary(XObject=Dictionary(below))


def _counted_walk(monkeypatch, limit: int = 2000) -> list:
    """Record every resource dictionary the embedded-image walk opens (one
    per page, one per form it enters). A walk that goes past *limit* fails
    there and then: forms entered once per path instead of once per file
    have no useful ceiling, and the suite must not sit waiting for one."""
    opened = []
    real = pdf_analyze._xobject_entries

    def entries(holder):
        opened.append(holder)
        assert len(opened) <= limit, f"the image walk opened more than {limit} resource dictionaries"
        return real(holder)

    monkeypatch.setattr(pdf_analyze, "_xobject_entries", entries)
    return opened


def _images(pdf) -> list:
    return [(page, str(name)) for page, name, _x in pdf_analyze._iter_image_xobjects(pdf)]


@pytest.mark.integration
class TestEmbeddedImageWalk:
    """Forms nest and share what they nest; the walk that looks for photos
    inside them enters each form once, however many ways lead to it. (A real
    two-page file with 270 forms did not finish otherwise.)"""

    def test_forms_that_share_their_children_are_each_entered_once(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _nest_forms(pdf, levels=10, width=4))
        opened = _counted_walk(monkeypatch)
        with pikepdf.open(src) as pdf:
            assert _images(pdf) == [(1, "/Im0")]
        # The page, then each of the 40 forms. Entered once per path, eight
        # layers deep, it was 87,381.
        assert len(opened) == 1 + 10 * 4

    def test_a_form_shared_between_pages_is_entered_once(self, tmp_path, monkeypatch):
        def build(pdf):
            shared = _form(pdf, {"/Im0": _image(pdf)})
            for page in pdf.pages:
                page.obj["/Resources"] = Dictionary(XObject=Dictionary(Fm0=shared))
        src = _pdf(tmp_path / "src.pdf", build, pages=3)
        opened = _counted_walk(monkeypatch)
        with pikepdf.open(src) as pdf:
            assert _images(pdf) == [(1, "/Im0")]
        assert len(opened) == 3 + 1

    def test_forms_that_hold_each_other_end(self, tmp_path, monkeypatch):
        def build(pdf):
            inner = _form(pdf, {"/Im0": _image(pdf)})
            outer = _form(pdf, {"/Inner": inner})
            inner["/Resources"]["/XObject"]["/Outer"] = outer
            inner["/Resources"]["/XObject"]["/Self"] = inner
            pdf.pages[0].obj["/Resources"] = Dictionary(XObject=Dictionary(Fm0=outer))
        src = _pdf(tmp_path / "src.pdf", build)
        opened = _counted_walk(monkeypatch, limit=50)
        with pikepdf.open(src) as pdf:
            assert _images(pdf) == [(1, "/Im0")]
        assert len(opened) == 3

    def test_an_image_more_than_eight_forms_down_is_found(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _nest_forms(pdf, levels=12, width=1))
        with pikepdf.open(src) as pdf:
            assert _images(pdf) == [(1, "/Im0")]

    def test_images_come_in_the_order_they_are_met(self, tmp_path):
        def build(pdf):
            nested = _form(pdf, {"/Deep": _image(pdf)})
            pdf.pages[0].obj["/Resources"] = Dictionary(XObject=Dictionary(
                A=_image(pdf), B=nested, C=_image(pdf)))
        src = _pdf(tmp_path / "src.pdf", build)
        with pikepdf.open(src) as pdf:
            assert _images(pdf) == [(1, "/A"), (1, "/Deep"), (1, "/C")]

    @pytest.mark.skipif(not _HAS_FITZ, reason="Pillow not installed")
    def test_a_photo_under_shared_forms_is_read_and_sanitize_finishes(self, tmp_path):
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (40.0, 44.0, 54.38), 3: "W", 4: (73.0, 59.0, 8.5)}
        jpeg = io.BytesIO()
        Image.new("RGB", (16, 12), (10, 120, 200)).save(jpeg, "JPEG", quality=90, exif=exif)

        def build(pdf):
            photo = pdf.make_stream(b"", Type=Name.XObject, Subtype=Name.Image, Width=16,
                                    Height=12, ColorSpace=Name.DeviceRGB, BitsPerComponent=8)
            photo.write(jpeg.getvalue(), filter=Name.DCTDecode)
            _nest_forms(pdf, levels=10, width=4, image=photo)
            pdf.Root["/OpenAction"] = _js()
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        assert _finding(analyze_document(src), "location.embedded_image")["count"] == 1
        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True
        assert "location.embedded_image" in {
            f["id"] for f in res["verification"]["residualFindings"]}


# ── What the review of the gate left to do ───────────────────────────────


class _NoSuchError(Exception):
    """Stands in for an exception class the tree under test does not have, so
    a test expecting it fails with whatever was raised instead."""


def _not_saved():
    return getattr(pdf_analyze, "SanitizationOutputError", _NoSuchError)


def _unreadable(monkeypatch, key: str) -> None:
    """Make every read of *key* fail, as a structure the library cannot
    parse does."""
    real = pikepdf.Object.get

    def get(self, name, *default):
        if str(name) == key:
            raise RuntimeError(f"simulated unreadable {key}")
        return real(self, name, *default)

    monkeypatch.setattr(pikepdf.Object, "get", get)


def _one_field(pdf) -> None:
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array([pdf.make_indirect(
        Dictionary(T=String("f"), FT=Name.Tx))]))


def _one_layer(pdf) -> None:
    pdf.Root["/OCProperties"] = Dictionary(OCGs=Array([pdf.make_indirect(
        Dictionary(Type=Name.OCG, Name=String("Layer")))]))


@pytest.mark.integration
class TestFormsAndLayersAreNotSilent:
    """The form and layer checks used to pass over what they could not read,
    and report nothing. Not read is not the same as not there."""

    @pytest.mark.parametrize("key", ["/AcroForm", "/Fields"])
    def test_a_form_that_cannot_be_read_is_recorded(self, tmp_path, monkeypatch, key):
        src = _pdf(tmp_path / "src.pdf", _one_field)
        assert _ids(src) == {"forms.acro"}
        _unreadable(monkeypatch, key)
        report = analyze_document(src)
        assert {"forms.xfa", "forms.acro"} <= set(report["unevaluated"])
        ids = {f["id"] for f in report["findings"]}
        assert "audit.incomplete" in ids and "clean" not in ids

    @pytest.mark.parametrize("key", ["/OCProperties", "/OCGs"])
    def test_layers_that_cannot_be_read_are_recorded(self, tmp_path, monkeypatch, key):
        src = _pdf(tmp_path / "src.pdf", _one_layer)
        assert _ids(src) == {"content.ocg"}
        _unreadable(monkeypatch, key)
        report = analyze_document(src)
        assert set(report["unevaluated"]) == {"content.ocg"}
        assert {f["id"] for f in report["findings"]} == {"audit.incomplete"}
        assert _finding(report, "audit.incomplete")["items"] == [
            "Optional-content layers: the layer list could not be read (RuntimeError)"]

    def test_unread_layers_are_listed_on_a_sanitize_report_and_refuse_nothing(self, tmp_path,
                                                                             monkeypatch):
        def build(pdf):
            pdf.Root["/OpenAction"] = _js()
            _one_layer(pdf)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")
        _unreadable(monkeypatch, "/OCProperties")
        report = sanitize_pdf(src, out)["verification"]
        assert report["verified"] is True
        assert [u["id"] for u in report["unevaluated"]] == ["content.ocg"]

    def test_an_xfa_form_is_reported_whatever_its_field_list_is(self, tmp_path):
        """A field list that was not a list used to make the whole form
        disappear from the report, XFA and all."""
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.Root.__setitem__(
            "/AcroForm", Dictionary(XFA=pdf.make_stream(b"<xdp>marker-xfa</xdp>"), Fields=5)))
        report = analyze_document(src)
        assert {f["id"] for f in report["findings"]} == {"forms.xfa"}
        assert report["unevaluated"] == {}

    @pytest.mark.parametrize("build", [
        lambda pdf: pdf.Root.__setitem__("/AcroForm", 5),
        lambda pdf: pdf.Root.__setitem__("/AcroForm", Dictionary(Fields=Name.Nothing)),
        lambda pdf: pdf.Root.__setitem__("/OCProperties", Name.Nothing),
        lambda pdf: pdf.Root.__setitem__("/OCProperties", Dictionary(OCGs=5)),
    ], ids=["form is a number", "field list is a name", "layers are a name",
            "layer list is a number"])
    def test_what_is_not_a_form_or_a_layer_list_is_not_an_unread_one(self, tmp_path, build):
        src = _pdf(tmp_path / "src.pdf", build)
        report = analyze_document(src)
        assert {f["id"] for f in report["findings"]} == {"clean"}
        assert report["unevaluated"] == {}


_AA_HOLDERS = {
    "document": lambda pdf: pdf.Root,
    "page": lambda pdf: pdf.pages[0].obj,
    "annotation": lambda pdf: pdf.pages[0].obj["/Annots"][0],
    "field": lambda pdf: pdf.Root["/AcroForm"]["/Fields"][0],
}


def _odd_value(pdf, shape: str):
    return {"array": lambda: Array([1, 2]), "string": lambda: String("marker-odd"),
            "name": lambda: Name("/MarkerOdd"), "number": lambda: 7,
            "stream": lambda: pdf.make_stream(b"marker-odd")}[shape]()


@pytest.mark.integration
class TestCounters:
    """What Sanitize removes, it counts — whatever shape it had."""

    @pytest.mark.parametrize("shape", ["array", "string", "number", "stream"])
    @pytest.mark.parametrize("owner, counter", [
        ("document", "document_aa"), ("page", "page_aa"),
        ("annotation", "annot_aa"), ("field", "field_aa")])
    def test_an_aa_that_is_not_a_dictionary_is_counted_when_it_goes(self, tmp_path, owner,
                                                                    counter, shape):
        def build(pdf):
            pdf.pages[0].obj["/Annots"] = Array([_link(pdf)])
            _one_field(pdf)
            _AA_HOLDERS[owner](pdf)["/AA"] = _odd_value(pdf, shape)
        src, out = _pdf(tmp_path / "src.pdf", build), str(tmp_path / "out.pdf")

        res = sanitize_pdf(src, out, _only("auto_actions"))
        assert res["removed"] == {counter: 1}
        assert res["verification"]["verified"] is True
        with pikepdf.open(out) as pdf:
            assert "/AA" not in _AA_HOLDERS[owner](pdf)

        # With the option off it is not Sanitize's to remove, or to count.
        kept = str(tmp_path / "kept.pdf")
        assert sanitize_pdf(src, kept, _only("javascript"))["removed"] == {}
        with pikepdf.open(kept) as pdf:
            assert "/AA" in _AA_HOLDERS[owner](pdf)

    def test_an_aa_dictionary_is_still_counted_by_its_actions(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.pages[0].obj.__setitem__(
            "/AA", Dictionary(O=_js(), C=_harmless(), Junk=5)))
        res = sanitize_pdf(src, str(tmp_path / "out.pdf"), _only("auto_actions"))
        assert res["removed"] == {"page_aa": 2}

    @pytest.mark.parametrize("shape", ["string", "array", "name", "number"])
    def test_an_info_that_is_not_a_dictionary_is_counted_when_it_goes(self, tmp_path, shape):
        src = _pdf(tmp_path / "src.pdf",
                   lambda pdf: pdf.trailer.__setitem__("/Info", _odd_value(pdf, shape)),
                   fix_metadata_version=False)
        with pikepdf.open(src) as pdf:
            assert "/Info" in pdf.trailer                  # the fixture is what it claims
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, _only("metadata"))
        assert res["removed"] == {"doc_info": 1}
        assert res["verification"]["verified"] is True
        with pikepdf.open(out) as pdf:
            assert "/Info" not in pdf.trailer

    def test_an_empty_info_dictionary_is_removed_without_a_count(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf",
                   lambda pdf: pdf.trailer.__setitem__("/Info", pdf.make_indirect(Dictionary())),
                   fix_metadata_version=False)
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, _only("metadata"))["removed"] == {}
        with pikepdf.open(out) as pdf:
            assert "/Info" not in pdf.trailer


def _objects_only(path, *, tail: bytes = b"%%EOF\n") -> str:
    """A damaged file, written byte by byte: four objects and no
    cross-reference table (and, unless *tail* supplies one, no trailer). A
    reader has to rebuild both, and the trailer it rebuilds has no /Size.
    (Something has to follow the last object: qpdf drops an object the file
    ends on.)"""
    body = (b"%PDF-1.7\n"
            b"1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n"
            b"2 0 obj\n<</Type/Pages/Kids[3 0 R]/Count 1>>\nendobj\n"
            b"3 0 obj\n<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/AA<</O 4 0 R>>>>\nendobj\n"
            b"4 0 obj\n<</S/JavaScript/JS(marker-js)>>\nendobj\n")
    with open(path, "wb") as f:
        f.write(body + tail)
    return str(path)


@pytest.mark.integration
class TestDamagedSource:
    """A file that opens only by being repaired has a rebuilt trailer, and a
    rebuilt trailer can lack /Size. Written out with object streams and no
    /Size, the cleaned copy could not be opened again — by anything."""

    @pytest.mark.parametrize("tail", [
        b"%%EOF\n", b"trailer\n<</Root 1 0 R>>\n",
        b"trailer\n<</Root 1 0 R/Size 5/ID[<904e5a16"],
        ids=["no trailer", "a trailer with no /Size", "a trailer cut off mid-way"])
    def test_it_sanitizes_and_the_output_opens_cleanly(self, tmp_path, tail):
        src, out = _objects_only(tmp_path / "src.pdf", tail=tail), str(tmp_path / "out.pdf")
        with pikepdf.open(src) as pdf:
            assert pdf.get_warnings()                      # the fixture needs repairing
        assert "scripts.js" in _ids(src)

        res = sanitize_pdf(src, out)
        assert res["verification"]["verified"] is True
        assert res["removed"] == {"page_aa": 1}
        with pikepdf.open(out) as pdf:
            assert pdf.get_warnings() == []
            assert len(pdf.pages) == 1 and "/Size" in pdf.trailer
        assert b"marker-js" not in _all_strings(out)
        assert sorted(os.listdir(tmp_path)) == ["out.pdf", "src.pdf"]

    def test_in_place(self, tmp_path):
        src = _objects_only(tmp_path / "only_copy.pdf")
        assert sanitize_pdf(src, src)["verification"]["verified"] is True
        assert _ids(src) == {"clean"} and os.listdir(tmp_path) == ["only_copy.pdf"]


@pytest.mark.integration
class TestCopyThatCannotBeSaved:
    """Writing the cleaned copy, reading it back and moving it into place can
    each fail for reasons that have nothing to do with what is in the
    document. The error then says, in plain words, that nothing was saved —
    and names no file: the library's own message quotes the temp file."""

    def _plain(self, exc, tmp_path) -> str:
        import traceback
        text = str(exc)
        assert "nothing was saved" in text
        for leak in (str(tmp_path), os.sep, "/", ".pdf"):
            assert leak not in text, text
        # Nor may the original error ride along where a log would print it.
        assert str(tmp_path) not in "".join(traceback.format_exception(exc))
        return text

    def test_a_destination_folder_that_does_not_exist(self, tmp_path):
        src = _dirty(tmp_path / "src.pdf")
        before = _read(src)
        with pytest.raises(_not_saved()) as ei:
            sanitize_pdf(src, str(tmp_path / "no-such-folder" / "out.pdf"))
        assert "could not be written" in self._plain(ei.value, tmp_path)
        assert ei.value.cause == "FileNotFoundError"
        assert _read(src) == before and os.listdir(tmp_path) == ["src.pdf"]

    def test_a_copy_that_cannot_be_read_back(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")

        def garbage(pdf, path, **kw):
            with open(path, "wb") as f:
                f.write(b"%PDF-1.7\nnot a document\n")

        monkeypatch.setattr(pdf_analyze, "save_with_content_id", garbage)
        with pytest.raises(_not_saved()) as ei:
            sanitize_pdf(src, out)
        assert "could not be read back" in self._plain(ei.value, tmp_path)
        assert ei.value.cause == "PdfError"
        assert os.listdir(tmp_path) == ["src.pdf"]

    def test_a_failed_write_keeps_its_type_and_drops_its_text(self, tmp_path, monkeypatch):
        src, out = _dirty(tmp_path / "src.pdf"), str(tmp_path / "out.pdf")

        def boom(self, filename, *a, **k):
            raise RuntimeError(f"simulated failure writing {filename}")

        monkeypatch.setattr(pikepdf.Pdf, "save", boom)
        with pytest.raises(_not_saved()) as ei:
            sanitize_pdf(src, out)
        assert "could not be written" in self._plain(ei.value, tmp_path)
        assert ei.value.cause == "RuntimeError"
        assert os.listdir(tmp_path) == ["src.pdf"]

    def test_a_destination_that_cannot_be_replaced(self, tmp_path):
        src = _dirty(tmp_path / "src.pdf")
        taken = tmp_path / "taken"
        taken.mkdir()
        (taken / "keep.txt").write_text("still here")
        with pytest.raises(_not_saved()) as ei:
            sanitize_pdf(src, str(taken))                  # a folder is in the way
        assert "could not be moved into place" in self._plain(ei.value, tmp_path)
        assert ei.value.cause.endswith("Error")
        assert sorted(os.listdir(tmp_path)) == ["src.pdf", "taken"]
        assert os.listdir(taken) == ["keep.txt"]

    def test_it_is_not_a_refusal(self, tmp_path):
        """Nothing was found wrong with the output: there is no report."""
        with pytest.raises(_not_saved()) as ei:
            sanitize_pdf(_dirty(tmp_path / "src.pdf"), str(tmp_path / "gone" / "out.pdf"))
        assert not isinstance(ei.value, _refused())
        assert not hasattr(ei.value, "report")


# ── No import cycle ──────────────────────────────────────────────────────


@pytest.mark.parametrize("statement", [
    "import pdf_analyze", "import pdf_verify",
    "import pdf_analyze, pdf_verify", "import pdf_verify, pdf_analyze", "import pdf_ops"])
def test_modules_import_in_a_fresh_interpreter(statement):
    done = subprocess.run([sys.executable, "-W", "ignore", "-c", statement],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-600:]
