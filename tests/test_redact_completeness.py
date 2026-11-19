"""Redaction completeness (#119): content that survived a verified redaction.

Cluster R1. The verifier takes a second, whole-file look for every target
term (``redaction.term_sweep``): every string, and the decoded bytes of
every stream that is not an image or font and colour machinery. The scrub
gains the rules that let ordinary files pass that look — structure-tree
text, layer names, private application data, metadata streams wherever they
hang, scripts, named destinations — and whatever it has no rule for is
refused rather than passed. And ``page_content_blobs`` reads each stream of
a page once.

Every fixture is built here, object by object. Nothing imported here is
new, so this file still collects against the tree before the change and
each test fails there for its own reason.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import os
import subprocess
import sys

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

import pdf_analyze
from pdf_ops import RedactionVerificationError, redact_pdf
from pdf_verify import verify_redaction

fitz = pytest.importorskip("fitz")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERM = "ACME99Z"
SWEEP = "redaction.term_sweep"
MARKER = "[redacted]"


# ── Builders ─────────────────────────────────────────────────────────────


def _font(pdf):
    return pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))


def _show(text: str, y: int = 720) -> bytes:
    return f"BT /F1 14 Tf 72 {y} Td ({text}) Tj ET".encode()


def _add_page(pdf, text: str = "nothing sensitive on this page"):
    """Append a page with *text* drawn on it; returns the page dictionary."""
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[-1].obj
    page["/Resources"] = Dictionary(Font=Dictionary(F1=_font(pdf)))
    page["/Contents"] = pdf.make_stream(_show(text))
    return page


def _pdf(path, plant=None, *, body: str = "nothing sensitive on this page",
         pages: int = 1, **save_kw) -> str:
    """A file of *pages* pages, each showing *body*, with *plant* applied."""
    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        _add_page(pdf, body)
    if plant is not None:
        plant(pdf)
    # fix_metadata_version off: with it on, saving parses a catalog XMP packet
    # and writes back its own, which is not the packet the fixture planted.
    pdf.save(str(path), **{"fix_metadata_version": False, **save_kw})
    pdf.close()
    return str(path)


def _with_term_on_page(path, plant=None, **kw) -> str:
    """The same, with the term in the page text as well — what a redaction
    has to have somewhere to run at all."""
    return _pdf(path, plant, body=f"a line about {TERM} here", **kw)


def _form(pdf, text: str):
    return pdf.make_stream(_show(text, 10), Type=Name.XObject, Subtype=Name.Form,
                           BBox=[0, 0, 300, 40],
                           Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))


# -- the surfaces of #119, each planted on page 1 or on the catalog --------


def _structure(pdf) -> None:
    page = pdf.pages[0].obj
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    figure = pdf.make_indirect(Dictionary(
        Type=Name.StructElem, S=Name.Figure, P=root, Pg=page,
        Alt=String(f"photo of the {TERM} site"), ActualText=String(f"{TERM} logo"),
        T=String("Figure 1")))
    root["/K"] = Array([figure])
    pdf.Root["/StructTreeRoot"] = root
    pdf.Root["/MarkInfo"] = Dictionary(Marked=True)


def _piece_info(pdf) -> None:
    pdf.pages[0].obj["/PieceInfo"] = Dictionary(
        ExampleApp=Dictionary(LastModified=String("D:20250101000000Z"),
                              Private=pdf.make_stream(f"layer notes: {TERM} draft".encode())),
        OtherApp=Dictionary(LastModified=String("D:20250101000000Z"),
                            Private=Dictionary(Note=String("keep me"))))


def _layer(pdf) -> None:
    named = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String(f"{TERM} overlay")))
    other = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("Background")))
    pdf.Root["/OCProperties"] = Dictionary(
        OCGs=Array([named, other]),
        D=Dictionary(Order=Array([named, other]), Name=String("Default")))


def _named_destination(pdf) -> None:
    """Three named destinations, sorted as a name tree has to be, with a
    link, a GoTo action and a bookmark each using the one that holds the
    term."""
    page = pdf.pages[0].obj
    key = f"{TERM}-section"
    entries = Array()
    for name in sorted(["appendix", key, "summary"], key=str.encode):
        entries.append(String(name))
        entries.append(Array([page, Name.Fit]))
    pdf.Root["/Names"] = Dictionary(Dests=pdf.make_indirect(Dictionary(Names=entries)))
    page["/Annots"] = Array([
        pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                     Rect=[72, 300, 200, 320], Dest=String(key))),
        pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                     Rect=[72, 260, 200, 280],
                                     A=Dictionary(S=Name.GoTo, D=String(key)))),
        pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link,
                                     Rect=[72, 220, 200, 240], Dest=String("appendix")))])
    outlines = pdf.make_indirect(Dictionary(Count=1))
    item = pdf.make_indirect(Dictionary(Title=String("Jump"), Parent=outlines, Dest=String(key)))
    outlines["/First"] = outlines["/Last"] = item
    pdf.Root["/Outlines"] = outlines


def _named_script(pdf) -> None:
    keep = pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=String("app.alert('hello');")))
    gone = pdf.make_indirect(Dictionary(S=Name.JavaScript,
                                        JS=String(f"var owner = '{TERM}';")))
    pdf.Root["/Names"] = Dictionary(JavaScript=Dictionary(
        Names=Array([String("a_keep"), keep, String("b_gone"), gone])))


def _page_metadata(pdf) -> None:
    packet = pdf.make_stream(
        f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><creator>{TERM}</creator></x:xmpmeta>".encode())
    packet["/Type"], packet["/Subtype"] = Name.Metadata, Name.XML
    pdf.pages[0].obj["/Metadata"] = packet


def _catalog_xmp(encoding: str):
    def plant(pdf) -> None:
        xml = f"<x:xmpmeta xmlns:x='adobe:ns:meta/'><creator>{TERM}</creator></x:xmpmeta>"
        packet = pdf.make_stream(("﻿" + xml).encode(encoding))
        packet["/Type"], packet["/Subtype"] = Name.Metadata, Name.XML
        pdf.Root["/Metadata"] = packet
    return plant


def _stashed_form(pdf) -> None:
    """A form XObject no page paints, held by a private key of the catalog."""
    pdf.Root["/Stash"] = _form(pdf, f"an earlier draft named {TERM}")


def _hidden_stamp(pdf) -> None:
    """A hidden, zero-size annotation whose appearance draws the term."""
    pdf.pages[0].obj["/Annots"] = Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Stamp, Rect=[0, 0, 0, 0], F=2,
        AP=Dictionary(N=_form(pdf, f"stamped by {TERM}"))))])


#: surface -> (how to plant it, a fragment of the plain-words location, the
#: page a flatten would be offered for or None when it is document-level)
SURFACES = {
    "structure text": (_structure, "structure", None),
    "private application data": (_piece_info, "/Private", 1),
    "layer name": (_layer, "layer", None),
    "named destination": (_named_destination, "named destination", None),
    "named script": (_named_script, "named scripts", None),
    "page metadata stream": (_page_metadata, "on page 1", 1),
    "catalog XMP in UTF-16 BE": (_catalog_xmp("utf-16-be"), "document catalog", None),
    "catalog XMP in UTF-16 LE": (_catalog_xmp("utf-16-le"), "document catalog", None),
    "form no page paints": (_stashed_form, "/Stash", None),
    "hidden annotation appearance": (_hidden_stamp, "on page 1", 1),
}


# ── Reading results ──────────────────────────────────────────────────────


def _check(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c
    raise AssertionError(f"check {cid!r} missing: {[c.id for c in report.checks]}")


def _everything(path) -> bytes:
    """Every string and every decodable stream in the file, inline objects
    included — an independent look, sharing nothing with the verifier."""
    out = []

    def visit(obj, depth=0):
        if depth > 80 or not isinstance(obj, pikepdf.Object):
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


def _holds_term(path, term: str = TERM) -> bool:
    blob = _everything(path)
    return any(needle in haystack
               for needle in (term.encode(), term.lower().encode())
               for haystack in (blob, blob.replace(b"\x00", b""), blob.lower()))


# ── D1: the whole-file term sweep ────────────────────────────────────────


@pytest.mark.integration
class TestTermSweep:
    """verify_redaction on a file that still holds the term somewhere no
    page shows it. None of these is found by the surface checks."""

    @pytest.mark.parametrize("surface", list(SURFACES))
    def test_a_term_left_on_a_surface_fails_the_sweep(self, tmp_path, surface):
        plant, where, page = SURFACES[surface]
        src = _pdf(tmp_path / "planted.pdf", plant)
        assert _holds_term(src)                       # the fixture is what it claims

        report = verify_redaction(src, [TERM])
        sweep = _check(report, SWEEP)
        assert sweep.passed is False and sweep.unevaluated is False
        assert report.verified is False
        line = " | ".join(sweep.evidence)
        assert f"term '{TERM}' found" in line and where in line, line
        assert sweep.object_paths and all(p.startswith("trailer") for p in sweep.object_paths)
        if page is None:
            assert sweep.is_document_level
            assert report.flatten_target_pages() is None
        else:
            assert not sweep.is_document_level and sweep.pages == [page]

    def test_a_clean_file_passes_and_the_check_is_on_the_report(self, tmp_path):
        report = verify_redaction(_pdf(tmp_path / "clean.pdf"), [TERM])
        assert _check(report, SWEEP).passed is True and report.verified is True
        assert SWEEP in {c["id"] for c in report.to_dict()["checks"]}

    def test_boxes_alone_are_not_swept(self, tmp_path):
        src = _pdf(tmp_path / "planted.pdf", _stashed_form)
        report = verify_redaction(src, rects=[{"page": 0, "x0": 300, "y0": 300,
                                               "x1": 320, "y1": 320}])
        assert SWEEP not in {c.id for c in report.checks}

    @pytest.mark.parametrize("value, found", [
        (String(f"note about {TERM}"), True),                               # PDFDocEncoding
        (String(("﻿" + f"note about {TERM}").encode("utf-16-be")), True),
        (String(("﻿" + f"note about {TERM}").encode("utf-16-le")), True),
        (String(f"café {TERM} naïve".encode("utf-8")), True),
        (String(f"{TERM[:4]}\n   {TERM[4:]}"), False),                      # broken mid-word
        (String("nothing here"), False),
    ], ids=["pdfdoc", "utf-16-be", "utf-16-le", "utf-8", "term broken mid-word", "absent"])
    def test_strings_are_read_in_every_encoding_a_pdf_string_has(self, tmp_path, value, found):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__("/Stash", value))
        assert _check(verify_redaction(src, [TERM]), SWEEP).passed is not found

    def test_a_term_of_several_words_is_found_across_any_whitespace(self, tmp_path):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", pdf.make_stream(b"draft for Dana\r\n\t  Whitfield, do not send")))
        assert _check(verify_redaction(src, ["Dana Whitfield"]), SWEEP).passed is False
        assert _check(verify_redaction(src, ["Dana  Whitfield"]), SWEEP).passed is False
        assert _check(verify_redaction(src, ["Dana Whitman"]), SWEEP).passed is True

    def test_case_is_honoured(self, tmp_path):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", String(f"note about {TERM.lower()}")))
        assert _check(verify_redaction(src, [TERM]), SWEEP).passed is False
        assert _check(verify_redaction(src, [TERM], case_sensitive=True), SWEEP).passed is True
        assert _check(verify_redaction(src, [TERM.lower()], case_sensitive=True),
                      SWEEP).passed is False

    def test_a_term_with_typographic_punctuation(self, tmp_path):
        """PDFDocEncoding puts the curly apostrophe at 0x90, where Latin-1
        has a control character."""
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", String(b"signed O\x90Brien")))
        assert _check(verify_redaction(src, ["O’Brien"]), SWEEP).passed is False

    def test_a_stream_shared_by_two_pages_names_both(self, tmp_path):
        def plant(pdf):
            shared = _form(pdf, f"footer for {TERM}")
            for page in pdf.pages:
                page.obj["/Thumb"] = shared             # held, never painted
        src = _pdf(tmp_path / "s.pdf", plant, pages=3)
        report = verify_redaction(src, [TERM])
        assert _check(report, SWEEP).pages == [1, 2, 3]
        assert report.flatten_target_pages() == [1, 2, 3]

    def test_the_evidence_does_not_depend_on_the_hash_seed(self, tmp_path):
        """A file's objects are reachable more than one way; the path a hit
        is reported under must be the same on every run."""
        def plant(pdf):
            _structure(pdf)
            _layer(pdf)
            _named_destination(pdf)
            widget = pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"),
                TU=String(f"name of {TERM}"), Rect=[72, 500, 272, 520], P=pdf.pages[0].obj))
            pdf.pages[0].obj["/Annots"].append(widget)
            pdf.Root["/AcroForm"] = Dictionary(Fields=Array([widget]))
        src = _pdf(tmp_path / "s.pdf", plant)
        code = ("import sys, pdf_verify; r = pdf_verify.verify_redaction(sys.argv[1], [sys.argv[2]]);"
                "c = [c for c in r.checks if c.id == 'redaction.term_sweep'][0];"
                "print(c.evidence, c.object_paths)")
        seen = set()
        for seed in ("0", "1", "2", "3"):
            done = subprocess.run([sys.executable, "-W", "ignore", "-c", code, src, TERM],
                                  cwd=ROOT, capture_output=True, text=True, timeout=120,
                                  env={**os.environ, "PYTHONHASHSEED": seed})
            assert done.returncode == 0, done.stderr[-400:]
            seen.add(done.stdout)
        assert len(seen) == 1 and TERM in seen.pop()


def _image(pdf, data: bytes):
    return pdf.make_stream(data, Type=Name.XObject, Subtype=Name.Image, Width=1, Height=1,
                           ColorSpace=Name.DeviceGray, BitsPerComponent=8)


def _icc_profile(text: bytes) -> bytes:
    """Enough of an ICC profile to be one: the 'acsp' signature at byte 36."""
    return b"\x00" * 36 + b"acsp" + b"\x00" * 88 + text


@pytest.mark.integration
class TestWhatTheSweepLeavesAlone:
    """Images, and the internals of fonts and colour profiles, are not
    document text. A character map says "Adobe" and "Identity" in nearly
    half of real files, and a colour profile says "Copyright": searching
    them would refuse those words in every such file."""

    WORDS = ["Adobe", "Identity", "Copyright", "Helvetica"]

    def _machinery(self, pdf) -> None:
        page = pdf.pages[0].obj
        info = Dictionary(Registry=String("Adobe"), Ordering=String("Identity"), Supplement=0)
        program = pdf.make_stream(b"%!PS-AdobeFont-1.0: Helvetica 001.000\n% Copyright Adobe")
        descriptor = pdf.make_indirect(Dictionary(
            Type=Name.FontDescriptor, FontName=Name.Helvetica, Flags=32,
            FontFamily=String("Helvetica"), FontFile=program))
        cmap = pdf.make_stream(
            b"/CIDInit /ProcSet findresource begin\n/CIDSystemInfo << /Registry (Adobe) "
            b"/Ordering (Identity) >> def\nbegincmap endcmap")
        coded = pdf.make_stream(b"begincmap /CMapName /Adobe-Identity-H def endcmap",
                                Type=Name.CMap, CIDSystemInfo=info)
        descendant = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name.Helvetica,
            CIDSystemInfo=info, FontDescriptor=descriptor))
        page["/Resources"]["/Font"]["/F2"] = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type0, BaseFont=Name.Helvetica, Encoding=coded,
            DescendantFonts=Array([descendant]), ToUnicode=cmap))
        profile = pdf.make_stream(_icc_profile(b"Copyright 1999 Adobe Systems"), N=3)
        page["/Resources"]["/ColorSpace"] = Dictionary(CS0=Array([Name.ICCBased, profile]))
        page["/Resources"]["/XObject"] = Dictionary(
            Im0=_image(pdf, b"Adobe Identity Copyright Helvetica"))

    def test_fonts_colour_profiles_and_images_are_not_searched(self, tmp_path):
        src = _pdf(tmp_path / "s.pdf", self._machinery)
        blob = _everything(src)
        assert all(w.encode() in blob for w in self.WORDS)   # they are all in the file
        report = verify_redaction(src, self.WORDS)
        assert _check(report, SWEEP).passed is True, _check(report, SWEEP).evidence

    @pytest.mark.parametrize("plant, why", [
        (lambda pdf: pdf.Root.__setitem__("/Stash", Dictionary(
            FontFile=pdf.make_stream(f"notes on {TERM}".encode()))),
         "a /FontFile key on something that is not a font descriptor"),
        (lambda pdf: pdf.Root.__setitem__("/Stash", Dictionary(
            ToUnicode=pdf.make_stream(f"notes on {TERM}".encode()))),
         "a /ToUnicode key on something that is not a font"),
        (lambda pdf: pdf.Root.__setitem__("/Stash", pdf.make_stream(
            b"\x00" * 40 + f"notes on {TERM}".encode(), N=3)),
         "a stream with /N that is not a colour profile"),
        (lambda pdf: pdf.Root.__setitem__("/Stash", Dictionary(
            Registry=String(f"notes on {TERM}"))),
         "a /Registry key alone"),
    ], ids=lambda v: v if isinstance(v, str) else None)
    def test_a_key_name_alone_exempts_nothing(self, tmp_path, plant, why):
        src = _pdf(tmp_path / "s.pdf", plant)
        assert _check(verify_redaction(src, [TERM]), SWEEP).passed is False, why

    def test_names_are_not_searched(self, tmp_path):
        """The known limit: a term that is (part of) a PDF name — a font's
        name, a style, a layer's intent — is not text and is not looked at."""
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", Dictionary(Label=Name(f"/{TERM}Sans-Bold"))))
        assert _check(verify_redaction(src, [TERM]), SWEEP).passed is True


@pytest.mark.integration
class TestSweepNeverPassesOnAPartialLook:
    def test_an_object_that_cannot_be_read_fails_it(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: pdf.Root.__setitem__(
            "/Stash", Dictionary(Note=String("harmless"))))
        real = pikepdf.Object.keys

        def keys(self):
            if "/Note" in real(self):
                raise RuntimeError("simulated unreadable object")
            return real(self)

        monkeypatch.setattr(pikepdf.Object, "keys", keys)
        sweep = _check(verify_redaction(src, [TERM]), SWEEP)
        assert sweep.passed is False and sweep.unevaluated is True
        assert sweep.is_document_level
        assert "could not" in " ".join(sweep.evidence)

    def test_a_stream_that_cannot_be_decoded_fails_it(self, tmp_path):
        def plant(pdf):
            broken = pdf.make_stream(b"placeholder")
            broken.write(b"this is not deflate data", filter=Name.FlateDecode)
            pdf.Root["/Stash"] = broken
        src = _pdf(tmp_path / "s.pdf", plant)
        sweep = _check(verify_redaction(src, [TERM]), SWEEP)
        assert sweep.passed is False and sweep.unevaluated is True
        line = " ".join(sweep.evidence)
        assert "could not" in line and str(tmp_path) not in line


# ── D2: the scrub rules ──────────────────────────────────────────────────


def _redact(tmp_path, plant, **kw):
    src = _with_term_on_page(tmp_path / "src.pdf", plant)
    out = str(tmp_path / "out.pdf")
    res = redact_pdf(src, out, search_terms=[TERM], **kw)
    assert res.verification["verified"] is True
    assert not _holds_term(out), "the term is still in the output"
    return res, out


@pytest.mark.integration
class TestScrubRules:
    """redact_pdf end to end: the term is on the page and on one more
    surface. The output verifies, the term is nowhere in it, and the
    surface is counted."""

    def test_structure_text_becomes_the_marker(self, tmp_path):
        res, out = _redact(tmp_path, _structure)
        assert res.surface_counts.get("structure") == 2
        with pikepdf.open(out) as pdf:
            figure = pdf.Root["/StructTreeRoot"]["/K"][0]
            assert str(figure["/Alt"]) == MARKER and str(figure["/ActualText"]) == MARKER
            assert str(figure["/T"]) == "Figure 1"          # what held no term is kept

    def test_a_layer_name_becomes_the_marker(self, tmp_path):
        res, out = _redact(tmp_path, _layer)
        assert res.surface_counts.get("layers") == 1
        with pikepdf.open(out) as pdf:
            names = [str(g["/Name"]) for g in pdf.Root["/OCProperties"]["/OCGs"]]
            assert names == [MARKER, "Background"]

    def test_private_application_data_holding_the_term_is_deleted(self, tmp_path):
        res, out = _redact(tmp_path, _piece_info)
        assert res.surface_counts.get("piece_info") == 1
        with pikepdf.open(out) as pdf:
            assert sorted(pdf.pages[0].obj["/PieceInfo"].keys()) == ["/OtherApp"]

    def test_a_metadata_stream_on_a_page_is_deleted(self, tmp_path):
        res, out = _redact(tmp_path, _page_metadata)
        assert res.surface_counts.get("metadata") == 1
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.pages[0].obj

    @pytest.mark.parametrize("encoding", ["utf-16-be", "utf-16-le", "utf-8"])
    def test_the_catalog_packet_is_deleted_whatever_its_encoding(self, tmp_path, encoding):
        res, out = _redact(tmp_path, _catalog_xmp(encoding))
        assert res.surface_counts.get("xmp") == 1
        with pikepdf.open(out) as pdf:
            assert "/Metadata" not in pdf.Root

    def test_metadata_that_holds_no_term_is_left_alone(self, tmp_path):
        def plant(pdf):
            packet = pdf.make_stream(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>kept</x:xmpmeta>")
            packet["/Type"], packet["/Subtype"] = Name.Metadata, Name.XML
            pdf.pages[0].obj["/Metadata"] = packet
        res, out = _redact(tmp_path, plant)
        assert "metadata" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert b"kept" in pdf.pages[0].obj["/Metadata"].read_bytes()

    def test_a_script_holding_the_term_is_removed_and_the_others_stay(self, tmp_path):
        res, out = _redact(tmp_path, _named_script)
        assert res.surface_counts.get("scripts") == 1
        with pikepdf.open(out) as pdf:
            entries = pdf.Root["/Names"]["/JavaScript"]["/Names"]
            assert [str(entries[i]) for i in range(0, len(entries), 2)] == ["a_keep"]

    @pytest.mark.parametrize("home", ["a script stream", "the open action", "a link",
                                      "a form field's keystroke", "a bookmark"])
    def test_a_script_goes_from_wherever_sanitize_would_take_it(self, tmp_path, home):
        def plant(pdf):
            page = pdf.pages[0].obj
            text = f"console.println('{TERM}');"
            js = Dictionary(S=Name.JavaScript, JS=String(text))
            if home == "a script stream":
                js = Dictionary(S=Name.JavaScript, JS=pdf.make_stream(text.encode()))
                pdf.Root["/Names"] = Dictionary(JavaScript=Dictionary(
                    Names=Array([String("s"), pdf.make_indirect(js)])))
            elif home == "the open action":
                pdf.Root["/OpenAction"] = js
            elif home == "a link":
                page["/Annots"] = Array([pdf.make_indirect(Dictionary(
                    Type=Name.Annot, Subtype=Name.Link, Rect=[72, 300, 200, 320], A=js))])
            elif home == "a form field's keystroke":
                pdf.Root["/AcroForm"] = Dictionary(Fields=Array([pdf.make_indirect(Dictionary(
                    T=String("total"), FT=Name.Tx, AA=Dictionary(K=js)))]))
            else:
                outlines = pdf.make_indirect(Dictionary(Count=1))
                item = pdf.make_indirect(Dictionary(Title=String("Go"), Parent=outlines, A=js))
                outlines["/First"] = outlines["/Last"] = item
                pdf.Root["/Outlines"] = outlines
        res, _out = _redact(tmp_path, plant)
        assert res.surface_counts.get("scripts") == 1

    def test_a_named_destination_is_renamed_and_its_links_follow(self, tmp_path):
        res, out = _redact(tmp_path, _named_destination)
        assert res.surface_counts.get("destinations") == 1
        with pikepdf.open(out) as pdf:
            entries = pdf.Root["/Names"]["/Dests"]["/Names"]
            keys = [bytes(entries[i]) for i in range(0, len(entries), 2)]
            assert keys == sorted(keys) and len(keys) == 3      # still a valid name tree
            assert b"appendix" in keys and b"summary" in keys
            new = (set(keys) - {b"appendix", b"summary"}).pop()
            annots = pdf.pages[0].obj["/Annots"]
            assert bytes(annots[0]["/Dest"]) == new
            assert bytes(annots[1]["/A"]["/D"]) == new
            assert bytes(annots[2]["/Dest"]) == b"appendix"     # the others are untouched
            assert bytes(pdf.Root["/Outlines"]["/First"]["/Dest"]) == new
            page = entries[keys.index(new) * 2 + 1][0]
            assert page.objgen == pdf.pages[0].obj.objgen       # and it still leads there

    @pytest.mark.parametrize("surface", ["structure text", "layer name", "named script",
                                         "named destination", "page metadata stream",
                                         "private application data"])
    def test_a_term_found_only_there_is_still_a_redaction(self, tmp_path, surface):
        """Not "No matching content": the term was in the file, and is gone."""
        src = _pdf(tmp_path / "src.pdf", SURFACES[surface][0])
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.redaction_count == 0 and sum(res.surface_counts.values()) >= 1
        assert res.verification["verified"] is True and not _holds_term(out)

    def test_nothing_is_counted_or_changed_where_the_term_is_not(self, tmp_path):
        def plant(pdf):
            _structure(pdf)
            _layer(pdf)
            _piece_info(pdf)
            _named_destination(pdf)
        src = _pdf(tmp_path / "src.pdf", plant, body="a line about Whitfield here")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Whitfield"])
        assert res.surface_counts == {"page_content": 1}
        with pikepdf.open(out) as pdf:
            assert TERM in str(pdf.Root["/StructTreeRoot"]["/K"][0]["/Alt"])
            assert sorted(pdf.pages[0].obj["/PieceInfo"].keys()) == ["/ExampleApp", "/OtherApp"]


@pytest.mark.integration
class TestWhatHasNoRuleIsRefused:
    """A hit the scrub has no rule for is not passed: the run is refused,
    and nothing is written."""

    def test_a_form_held_by_a_private_key_is_refused_outright(self, tmp_path):
        src = _with_term_on_page(tmp_path / "src.pdf", _stashed_form)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        failed = {c["id"] for c in ei.value.report["checks"] if not c["passed"]}
        assert failed == {SWEEP}
        assert ei.value.document_level is True and ei.value.flatten_pages is None
        assert not os.path.exists(out) and os.listdir(tmp_path) == ["src.pdf"]

    def test_a_hidden_appearance_is_refused_with_the_flatten_offer(self, tmp_path):
        src = _with_term_on_page(tmp_path / "src.pdf", _hidden_stamp)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert ei.value.flatten_pages == [1] and ei.value.document_level is False
        assert not os.path.exists(out)

        res = redact_pdf(src, out, search_terms=[TERM], flatten_pages=[1])
        assert res.verification["verified"] is True and res.flattened_pages == [1]
        assert not _holds_term(out)

    def test_what_could_not_be_read_is_refused_and_said_to_be_unread(self, tmp_path):
        def plant(pdf):
            broken = pdf.make_stream(b"placeholder")
            broken.write(b"this is not deflate data", filter=Name.FlateDecode)
            pdf.Root["/Stash"] = broken
        src = _with_term_on_page(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert "could not be read" in str(ei.value) and "still present" not in str(ei.value)
        assert ei.value.flatten_pages is None and not os.path.exists(out)


# ── D6: each stream of a page is read once ───────────────────────────────


def _shared_forms(pdf, levels: int, width: int) -> None:
    """Forms nested *levels* deep on page 1, *width* to a layer, each layer
    drawing every form of the layer below: width * levels forms, and
    width * width references between each pair of layers."""
    below: dict = {}
    for _ in range(levels):
        draw = b"".join(b"%s Do\n" % k.encode() for k in below) or b"0 0 m\n"
        below = {f"/Fm{n}": pdf.make_stream(
            draw, Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 9, 9],
            Resources=Dictionary(XObject=Dictionary(below))) for n in range(width)}
    page = pdf.pages[0].obj
    page["/Resources"]["/XObject"] = Dictionary(below)
    page["/Contents"] = pdf.make_stream(b"".join(b"%s Do\n" % k.encode() for k in below))


def _counted_loads(monkeypatch, limit: int = 400) -> list:
    """Record every stream fitz is asked to load. Past *limit* it fails
    there and then, so a regression is a failed test and not a slow one."""
    loads = []
    real = fitz.Document.xref_stream

    def xref_stream(self, xref):
        loads.append(xref)
        assert len(loads) <= limit, f"more than {limit} stream loads for one page"
        return real(self, xref)

    monkeypatch.setattr(fitz.Document, "xref_stream", xref_stream)
    return loads


@pytest.mark.integration
class TestContentStreamsAreReadOnce:
    def test_forms_that_share_their_children_are_each_loaded_once(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: _shared_forms(pdf, levels=8, width=4))
        loads = _counted_loads(monkeypatch)
        with fitz.open(src) as doc:
            blobs = pdf_analyze.page_content_blobs(doc, doc[0])
        # The page's own stream and each of the 32 forms. Loaded once per
        # reference, it was 117.
        assert len(loads) == len(set(loads)) == 1 + 8 * 4
        assert len(blobs) == 33 and all(isinstance(b, (bytes, bytearray)) for b in blobs)

    def test_the_same_streams_are_returned_as_before(self, tmp_path):
        def plant(pdf):
            page = pdf.pages[0].obj
            inner = _form(pdf, "inner text")
            outer = _form(pdf, "outer text")
            outer["/Resources"]["/XObject"] = Dictionary(In=inner)
            page["/Resources"]["/XObject"] = Dictionary(A=outer, B=outer, C=inner,
                                                        Im=_image(pdf, b"\x00"))
        src = _pdf(tmp_path / "s.pdf", plant)
        with fitz.open(src) as doc:
            blobs = pdf_analyze.page_content_blobs(doc, doc[0])
        joined = b"\n".join(blobs)
        assert len(blobs) == 3                          # the page, outer, inner; no image
        assert joined.count(b"outer text") == 1 and joined.count(b"inner text") == 1

    def test_a_stream_that_cannot_be_read_is_counted_once_by_the_verifier(self, tmp_path,
                                                                         monkeypatch):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: _shared_forms(pdf, levels=3, width=3))
        with fitz.open(src) as doc:
            bad = max(x[0] for x in doc[0].get_xobjects())
        real = fitz.Document.xref_stream
        monkeypatch.setattr(fitz.Document, "xref_stream",
                            lambda self, xref: None if xref == bad else real(self, xref))
        raw = _check(verify_redaction(src, [TERM]), "redaction.raw_content")
        assert raw.passed is False
        assert raw.evidence == ["1 content stream(s) on page 1 could not be read "
                                "for verification"]

    def test_a_stream_that_raises_is_not_passed_over(self, tmp_path, monkeypatch):
        src = _pdf(tmp_path / "s.pdf", lambda pdf: _shared_forms(pdf, levels=2, width=2))
        with fitz.open(src) as doc:
            bad = max(x[0] for x in doc[0].get_xobjects())
        real = fitz.Document.xref_stream

        def xref_stream(self, xref):
            if xref == bad:
                raise RuntimeError("simulated unreadable stream")
            return real(self, xref)

        monkeypatch.setattr(fitz.Document, "xref_stream", xref_stream)
        raw = _check(verify_redaction(src, [TERM]), "redaction.raw_content")
        assert raw.passed is False and "1 content stream(s) on page 1" in raw.evidence[0]
