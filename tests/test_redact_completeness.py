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
        packet = pdf.make_stream(("\ufeff" + xml).encode(encoding))
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
        (String(("\ufeff" + f"note about {TERM}").encode("utf-16-be")), True),
        (String(("\ufeff" + f"note about {TERM}").encode("utf-16-le")), True),
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
                page.obj["/Stash"] = shared             # held, never painted
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
        """A form field's stale appearance: the field says nothing of the
        term, the appearance still draws it. (An annotation that is not a
        field is taken off the page for this, #119 R3; a field is not.)"""
        def plant(pdf):
            field = pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("who"),
                Rect=[0, 0, 0, 0], F=2, AP=Dictionary(N=_form(pdf, f"typed by {TERM}"))))
            pdf.pages[0].obj["/Annots"] = Array([field])
            pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]))
        src = _with_term_on_page(tmp_path / "src.pdf", plant)
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


# ── R1b: the places real files keep a term ───────────────────────────────
#
#  Each of these was a real file that R1 refused: the term was still in it,
#  somewhere the scrub had no rule for. The fixtures are built by hand to
#  the same shape.


def _annot(pdf, subtype=Name.Text, **entries):
    """An annotation well away from the page's text, so the page search
    never meets it."""
    return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=subtype,
                                        Rect=[300, 300, 320, 320], **entries))


def _on_page(pdf, *annots) -> None:
    page = pdf.pages[0].obj
    if "/Annots" not in page:
        page["/Annots"] = Array()
    for a in annots:
        page["/Annots"].append(a)


def _field_with_kid(pdf, *, hidden: bool = False, **parent_entries):
    """A text field that has no widget of its own, with one child widget on
    page 1. Nothing of the parent is drawn anywhere: it is reached through
    the field tree only. *hidden* makes the widget hidden and zero-size, for
    a field with a value: PyMuPDF 2 draws a visible widget's value where
    1.27 does not, and the page search then finds it and removes the widget
    before the scrub ever sees the field."""
    page = pdf.pages[0].obj
    parent = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("person"), **parent_entries))
    kid = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, T=String("first"),
                                       Rect=[0, 0, 0, 0] if hidden else [300, 500, 400, 520],
                                       P=page, Parent=parent))
    if hidden:
        kid["/F"] = 2
    parent["/Kids"] = Array([kid])
    _on_page(pdf, kid)
    if "/AcroForm" not in pdf.Root:
        pdf.Root["/AcroForm"] = Dictionary(Fields=Array())
    pdf.Root["/AcroForm"]["/Fields"].append(parent)
    return parent, kid


def _fields(pdf) -> dict:
    """{partial name: field dictionary} for the whole field tree of *pdf*."""
    found, stack = {}, list(pdf.Root["/AcroForm"]["/Fields"])
    while stack:
        f = stack.pop()
        found[str(f.get("/T"))] = f
        stack.extend(f.get("/Kids", []))
    return found


def _held_by_structure(pdf, obj) -> None:
    """Reference *obj* from the structure tree and from nowhere else — the
    state an annotation is left in when redaction takes it off its page."""
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Link, P=root))
    elem["/K"] = Array([Dictionary(Type=Name.OBJR, Obj=obj)])
    root["/K"] = Array([elem])
    pdf.Root["/StructTreeRoot"] = root


@pytest.mark.integration
class TestRichText:
    """(a) The rich-text twin of a note's or a field's plain text."""

    @pytest.mark.parametrize("as_stream", [False, True], ids=["a string", "a stream"])
    def test_a_notes_rich_text_goes_with_its_plain_text(self, tmp_path, as_stream):
        def plant(pdf):
            rich = f"<body><p>ask {TERM} first</p></body>"
            _on_page(pdf, _annot(pdf, Contents=String(f"ask {TERM} first"),
                                 RC=pdf.make_stream(rich.encode()) if as_stream else String(rich)))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("rich_text") == 1
        with pikepdf.open(out) as pdf:
            note = pdf.pages[0].obj["/Annots"][0]                # the note itself stays
            assert "/RC" not in note and "/Contents" not in note

    def test_a_fields_rich_value_goes_with_its_value(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _field_with_kid(
            pdf, hidden=True, V=String(f"{TERM} Ltd"),
            RV=String(f"<body><p>{TERM} Ltd</p></body>")))
        assert res.surface_counts.get("rich_text") == 1
        assert res.surface_counts.get("form_fields") == 1
        with pikepdf.open(out) as pdf:
            assert "/RV" not in _fields(pdf)["person"]

    def test_rich_text_without_the_term_is_kept(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _on_page(pdf, _annot(
            pdf, Contents=String("see page 4"), RC=String("<body><p>see page 4</p></body>"))))
        assert "rich_text" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert "/RC" in pdf.pages[0].obj["/Annots"][0]


@pytest.mark.integration
class TestFormFieldText:
    """(b) What a form field says about itself, wherever the field sits in
    the field tree — not only what a page's widget shows."""

    def test_tooltip_mapping_name_default_options_and_caption_become_the_marker(self, tmp_path):
        def plant(pdf):
            _parent, kid = _field_with_kid(
                pdf, TU=String(f"Name of the {TERM} contact"), TM=String(f"{TERM}_export"),
                DV=String(f"{TERM} default"),
                Opt=Array([String("one"), Array([String("x"), String(f"{TERM} two")])]))
            kid["/MK"] = Dictionary(CA=String(f"Ask {TERM}"), R=90)
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("field_text") == 5
        with pikepdf.open(out) as pdf:
            parent, kid = _fields(pdf)["person"], _fields(pdf)["first"]
            assert [str(parent[k]) for k in ("/TU", "/TM", "/DV")] == [MARKER] * 3
            assert str(parent["/Opt"][0]) == "one" and str(parent["/Opt"][1][0]) == "x"
            assert str(parent["/Opt"][1][1]) == MARKER
            assert str(kid["/MK"]["/CA"]) == MARKER and kid["/MK"]["/R"] == 90

    def test_a_field_named_for_the_term_is_renamed_to_something_neutral(self, tmp_path):
        def plant(pdf):
            parent, _kid = _field_with_kid(pdf)
            parent["/T"] = String(f"{TERM}_form")
            pdf.Root["/AcroForm"]["/Fields"].append(pdf.make_indirect(
                Dictionary(FT=Name.Tx, T=String("field-1"))))        # the obvious name is taken
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("field_names") == 1
        with pikepdf.open(out) as pdf:
            names = sorted(_fields(pdf))
            assert len(names) == len(set(names)) == 3
            assert "field-1" in names and "first" in names          # the others keep theirs
            assert not any(TERM.lower() in n.lower() for n in names)

    def test_an_appearance_that_draws_what_was_redacted_goes_too(self, tmp_path):
        """A hidden button: its caption is in /MK and drawn by its /AP. The
        page search never sees it; the caption is redacted, and the stale
        appearance must not keep it."""
        def plant(pdf):
            _on_page(pdf, pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Widget, FT=Name.Btn, Ff=65536, T=String("go"),
                Rect=[0, 0, 0, 0], F=2, MK=Dictionary(CA=String(f"Mail {TERM}")),
                AP=Dictionary(N=_form(pdf, f"Mail {TERM}")))))
            pdf.Root["/AcroForm"] = Dictionary(Fields=Array([pdf.pages[0].obj["/Annots"][0]]))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("field_text") == 1
        with pikepdf.open(out) as pdf:
            button = _fields(pdf)["go"]
            assert str(button["/MK"]["/CA"]) == MARKER and "/AP" not in button

    def test_a_parents_value_takes_its_widgets_appearances_with_it(self, tmp_path):
        def plant(pdf):
            _parent, kid = _field_with_kid(pdf, hidden=True, V=String(f"{TERM} Ltd"))
            kid["/AP"] = Dictionary(N=_form(pdf, f"{TERM} Ltd"))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("form_fields") == 1
        with pikepdf.open(out) as pdf:
            assert "/AP" not in _fields(pdf)["first"]

    def test_field_text_without_the_term_is_left_alone(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _field_with_kid(
            pdf, TU=String("Your full name"), Opt=Array([String("one"), String("two")])))
        assert set(res.surface_counts) == {"page_content"}
        with pikepdf.open(out) as pdf:
            assert str(_fields(pdf)["person"]["/TU"]) == "Your full name"


def _figure(pdf, **ptex):
    """A form XObject painted on page 1, with pdfTeX's record of the file it
    was included from."""
    page = pdf.pages[0].obj
    figure = _form(pdf, "an included figure")
    for key, value in ptex.items():
        figure[f"/PTEX.{key}"] = value
    page["/Resources"]["/XObject"] = Dictionary(Fig=figure)
    page["/Contents"] = pdf.make_stream(
        page["/Contents"].read_bytes() + b"\nq 1 0 0 1 72 400 cm /Fig Do Q")


def _painted_forms(pdf) -> list:
    xobjects = pdf.pages[0].obj["/Resources"].get("/XObject", {})
    return [xobjects[k] for k in xobjects.keys()]


@pytest.mark.integration
class TestPdfTexEntries:
    """(c) pdfTeX records, on every figure it includes, the name of the file
    it came from and that file's own document information."""

    def test_entries_holding_the_term_are_deleted_and_the_figure_stays(self, tmp_path):
        def plant(pdf):
            info = pdf.make_indirect(Dictionary(Title=String(f"{TERM} site plan"),
                                                Producer=String("pdfTeX-1.40")))
            _figure(pdf, FileName=String(f"./figures/{TERM}-plan.pdf"), PageNumber=1,
                    InfoDict=info)
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("pdftex") == 2
        with pikepdf.open(out) as pdf:
            (figure,) = _painted_forms(pdf)
            assert sorted(k for k in figure.keys() if k.startswith("/PTEX.")) == [
                "/PTEX.PageNumber"]
        with fitz.open(out) as doc:
            assert "an included figure" in doc[0].get_text()

    def test_entries_without_the_term_are_kept(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _figure(
            pdf, FileName=String("./figures/plan.pdf"), PageNumber=1))
        assert "pdftex" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert "/PTEX.FileName" in _painted_forms(pdf)[0]


@pytest.mark.integration
class TestOffThePage:
    """(d) Redaction takes an annotation under a box off its page, but a
    tagged file's structure tree still points at it — and at everything it
    says. Such a dictionary is scrubbed like one that is on a page."""

    @pytest.mark.parametrize("what", ["a link", "a note", "a form field", "a scripted link"])
    def test_what_the_structure_tree_still_points_to_is_scrubbed(self, tmp_path, what):
        def plant(pdf):
            if what == "a link":
                stale = _annot(pdf, Name.Link, A=Dictionary(
                    S=Name.URI, URI=String(f"https://{TERM}.example/about")))
            elif what == "a note":
                stale = _annot(pdf, Contents=String(f"ask {TERM}"), Subj=String(f"re {TERM}"),
                               RC=String(f"<body><p>ask {TERM}</p></body>"))
            elif what == "a form field":
                stale = _annot(pdf, Name.Widget, FT=Name.Tx, T=String(f"{TERM}_id"),
                               TU=String(f"id of {TERM}"), V=String(f"{TERM}-0001"))
            else:
                stale = _annot(pdf, Name.Link, A=Dictionary(
                    S=Name.JavaScript, JS=String(f"app.launchURL('https://{TERM}.example');")))
            _held_by_structure(pdf, stale)
        res, out = _redact(tmp_path, plant)
        assert sum(n for key, n in res.surface_counts.items() if key != "page_content") >= 1

    def test_a_link_under_the_redacted_text_itself(self, tmp_path):
        """The whole chain as it happens: the link sits over the text, the
        box takes it off the page, the structure tree keeps it alive."""
        def plant(pdf):
            link = pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.Link, Rect=[60, 705, 420, 745],
                A=Dictionary(S=Name.URI, URI=String(f"https://{TERM}.example/"))))
            _on_page(pdf, link)
            _held_by_structure(pdf, link)
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("links", 0) >= 1


@pytest.mark.integration
class TestLinksEverywhere:
    """(e) A web address holding a term is removed wherever its action
    hangs, the way Sanitize removes that one action."""

    @pytest.mark.parametrize("home", ["a page link", "a bookmark", "the open action",
                                      "after a harmless action", "a form field"])
    def test_the_action_is_removed(self, tmp_path, home):
        def plant(pdf):
            uri = Dictionary(S=Name.URI, URI=String(f"https://{TERM}.example/pricing"))
            if home == "a page link":
                _on_page(pdf, _annot(pdf, Name.Link, A=uri),
                         _annot(pdf, Name.Link, A=Dictionary(
                             S=Name.URI, URI=String("https://other.example/"))))
            elif home == "a bookmark":
                outlines = pdf.make_indirect(Dictionary(Count=1))
                item = pdf.make_indirect(Dictionary(Title=String("Prices"), Parent=outlines,
                                                    A=uri))
                outlines["/First"] = outlines["/Last"] = item
                pdf.Root["/Outlines"] = outlines
            elif home == "the open action":
                pdf.Root["/OpenAction"] = uri
            elif home == "after a harmless action":
                pdf.Root["/OpenAction"] = Dictionary(S=Name.Named, N=Name.NextPage, Next=uri)
            else:
                pdf.Root["/AcroForm"] = Dictionary(Fields=Array([pdf.make_indirect(Dictionary(
                    T=String("site"), FT=Name.Btn, Ff=65536, A=uri))]))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("links") == 1
        with pikepdf.open(out) as pdf:
            if home == "a page link":
                # The link whose action went is off the page, as Sanitize leaves it.
                annots = pdf.pages[0].obj["/Annots"]
                assert [str(a["/A"]["/URI"]) for a in annots] == ["https://other.example/"]
            elif home == "after a harmless action":
                assert str(pdf.Root["/OpenAction"]["/N"]) == "/NextPage"
                assert "/Next" not in pdf.Root["/OpenAction"]
            elif home == "a bookmark":
                assert "/A" not in pdf.Root["/Outlines"]["/First"]


@pytest.mark.integration
class TestPageLabels:
    """(f) A page label's prefix is text a viewer shows beside the page."""

    def test_a_prefix_holding_the_term_becomes_the_marker(self, tmp_path):
        def plant(pdf):
            pdf.Root["/PageLabels"] = Dictionary(Kids=Array([pdf.make_indirect(Dictionary(
                Limits=Array([0, 0]),
                Nums=Array([0, Dictionary(S=Name.D, P=String(f"{TERM}-"), St=1)])))]))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("page_labels") == 1
        with pikepdf.open(out) as pdf:
            label = pdf.Root["/PageLabels"]["/Kids"][0]["/Nums"][1]
            assert str(label["/P"]) == MARKER and str(label["/S"]) == "/D"

    def test_a_prefix_without_the_term_is_kept(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: pdf.Root.__setitem__(
            "/PageLabels", Dictionary(Nums=Array([0, Dictionary(P=String("A-"))]))))
        assert "page_labels" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert str(pdf.Root["/PageLabels"]["/Nums"][1]["/P"]) == "A-"


def _xfa(pdf, *, single: bool = False, data: str = f"<name>{TERM}</name>", fields: int = 1):
    packets = [pdf.make_stream(b"<template>layout</template>"),
               pdf.make_stream(f"<datasets>{data}</datasets>".encode())]
    xfa = packets[1] if single else Array(
        [String("template"), packets[0], String("datasets"), packets[1]])
    pdf.Root["/AcroForm"] = Dictionary(XFA=xfa, Fields=Array([
        pdf.make_indirect(Dictionary(FT=Name.Tx, T=String(f"f{n}"))) for n in range(fields)]))


@pytest.mark.integration
class TestXfa:
    """(g) XFA form data cannot be redacted inside its packets. Where the
    form also exists as ordinary fields, the XFA entry is dropped and the
    form falls back to them."""

    @pytest.mark.parametrize("single", [False, True], ids=["an array of packets", "one stream"])
    def test_a_form_with_fields_loses_its_xfa(self, tmp_path, single):
        res, out = _redact(tmp_path, lambda pdf: _xfa(pdf, single=single))
        assert res.surface_counts.get("xfa") == 1
        with pikepdf.open(out) as pdf:
            form = pdf.Root["/AcroForm"]
            assert "/XFA" not in form and len(form["/Fields"]) == 1

    def test_a_form_that_is_only_xfa_is_refused_in_plain_words(self, tmp_path):
        src = _with_term_on_page(tmp_path / "src.pdf", lambda pdf: _xfa(pdf, fields=0))
        out = str(tmp_path / "out.pdf")
        with pytest.raises(ValueError, match="XFA") as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert "cannot be term-redacted" in str(ei.value)
        assert str(tmp_path) not in str(ei.value)
        assert not os.path.exists(out) and os.listdir(tmp_path) == ["src.pdf"]

    def test_xfa_without_the_term_is_kept(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _xfa(pdf, data="<name>nobody</name>"))
        assert "xfa" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert len(pdf.Root["/AcroForm"]["/XFA"]) == 4


def _filespec(pdf, name: str, data: bytes, desc: str = ""):
    payload = pdf.make_stream(data)
    payload["/Type"] = Name.EmbeddedFile
    spec = Dictionary(Type=Name.Filespec, F=String(name), UF=String(name),
                      EF=Dictionary(F=payload))
    if desc:
        spec["/Desc"] = String(desc)
    return pdf.make_indirect(spec)


def _attach(pdf, *specs) -> None:
    """List *specs* in the catalog's attachment name tree, sorted."""
    entries = Array()
    for spec in sorted(specs, key=lambda s: bytes(s["/F"])):
        entries.append(String(bytes(spec["/F"])))
        entries.append(spec)
    names = pdf.Root.get("/Names")
    if names is None:
        names = pdf.Root["/Names"] = Dictionary()
    names["/EmbeddedFiles"] = Dictionary(Names=entries)


def _attached(pdf) -> list:
    tree = pdf.Root.get("/Names", {}).get("/EmbeddedFiles")
    return [] if tree is None else [str(k) for k, _v in pdf_analyze.name_tree_items(tree)]


@pytest.mark.integration
class TestAttachments:
    """(h) An attachment whose name, description or bytes hold a term is
    removed whole. The others stay."""

    @pytest.mark.parametrize("where", ["its contents", "its description", "its name"])
    def test_an_attachment_holding_the_term_is_removed_whole(self, tmp_path, where):
        def plant(pdf):
            _attach(pdf, _filespec(pdf, "budget.csv", b"a,b\n1,2\n"), _filespec(
                pdf, f"{TERM}-minutes.txt" if where == "its name" else "minutes.txt",
                f"minutes mention {TERM}".encode() if where == "its contents" else b"minutes",
                f"notes for {TERM}" if where == "its description" else ""))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert _attached(pdf) == ["budget.csv"]

    def test_one_listed_only_as_an_associated_file(self, tmp_path):
        def plant(pdf):
            pdf.Root["/AF"] = Array([_filespec(pdf, f"{TERM}-source.xml", b"<data/>"),
                                     _filespec(pdf, "schema.xsd", b"<xs/>")])
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert [str(s["/F"]) for s in pdf.Root["/AF"]] == ["schema.xsd"]

    def test_one_carried_by_a_file_attachment_annotation(self, tmp_path):
        def plant(pdf):
            _on_page(pdf, _annot(pdf, Name.FileAttachment, FS=_filespec(
                pdf, "scan.txt", f"scan of the {TERM} contract".encode())),
                     _annot(pdf, Contents=String("unrelated note")))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert [str(a["/Subtype"]) for a in pdf.pages[0].obj["/Annots"]] == ["/Text"]

    def test_a_name_tree_with_limits_keeps_no_trace_of_the_name(self, tmp_path):
        def plant(pdf):
            def leaf(*specs):
                entries = Array()
                for spec in specs:
                    entries.append(String(bytes(spec["/F"])))
                    entries.append(spec)
                return pdf.make_indirect(Dictionary(Names=entries, Limits=Array(
                    [String(bytes(specs[0]["/F"])), String(bytes(specs[-1]["/F"]))])))
            first = leaf(_filespec(pdf, f"{TERM}-a.txt", b"x"), _filespec(pdf, "b.txt", b"x"))
            second = leaf(_filespec(pdf, "c.txt", b"x"),
                          _filespec(pdf, "d.txt", f"about {TERM}".encode()))
            pdf.Root["/Names"] = Dictionary(EmbeddedFiles=Dictionary(Kids=Array([first, second])))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 2
        with pikepdf.open(out) as pdf:
            assert _attached(pdf) == ["b.txt", "c.txt"]

    def test_a_compressed_attachment_is_not_opened(self, tmp_path):
        """The limit, on the record: an attachment is searched as its raw
        bytes. A term inside a zip, a docx or an xlsx is compressed there
        and is not found; the attachment stays."""
        import io
        import zipfile
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("notes.txt", f"minutes mention {TERM} " * 40)
        assert TERM.encode() not in packed.getvalue()     # the fixture is what it claims
        src = _with_term_on_page(tmp_path / "src.pdf", lambda pdf: _attach(
            pdf, _filespec(pdf, "archive.zip", packed.getvalue())))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and "embedded_files" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert _attached(pdf) == ["archive.zip"]


def _signed(pdf, *, signer: str = "A. Example", cert: bytes = b"CN=A. Example", draws: str = ""):
    """An invisible signature field on page 1; returns (field, signature)."""
    page = pdf.pages[0].obj
    signature = pdf.make_indirect(Dictionary(
        Type=Name.Sig, Filter=Name("/Adobe.PPKLite"), SubFilter=Name("/adbe.pkcs7.detached"),
        Name=String(signer), Reason=String("approved"), Location=String("head office"),
        M=String("D:20250101000000Z"), ByteRange=Array([0, 10, 20, 30]),
        Contents=String(b"\x30\x82\x01\x00" + cert + b"\x00" * 16)))
    field = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Widget, FT=Name.Sig, T=String("Signature1"),
        Rect=[0, 0, 0, 0], F=2, P=page, V=signature))
    if draws:
        field["/AP"] = Dictionary(N=_form(pdf, draws))
    _on_page(pdf, field)
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array([field]), SigFlags=3)
    return field, signature


@pytest.mark.integration
class TestSignatures:
    """(i) A signature that names the term cannot keep it: its value is
    removed and the field is left unsigned. Redaction invalidates a
    signature in any case."""

    @pytest.mark.parametrize("where", ["the signer's name", "the certificate bytes"])
    def test_a_signature_holding_the_term_is_removed_and_the_field_stays(self, tmp_path, where):
        def plant(pdf):
            if where == "the signer's name":
                _signed(pdf, signer=f"{TERM} Legal", draws=f"Digitally signed by {TERM} Legal")
            else:
                _signed(pdf, cert=f"CN={TERM} Legal, O=Example".encode())
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("signatures") == 1
        with pikepdf.open(out) as pdf:
            field = _fields(pdf)["Signature1"]
            assert "/V" not in field and "/AP" not in field
            assert str(field["/FT"]) == "/Sig"                   # the field is still there

    def test_a_usage_rights_signature(self, tmp_path):
        def plant(pdf):
            pdf.Root["/Perms"] = Dictionary(UR3=pdf.make_indirect(Dictionary(
                Type=Name.Sig, Filter=Name("/Adobe.PPKLite"), Name=String(f"{TERM} Reader Rights"),
                Contents=String(b"\x30\x82" + b"\x00" * 16), ByteRange=Array([0, 1, 2, 3]))))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("signatures") == 1
        with pikepdf.open(out) as pdf:
            assert "/Perms" not in pdf.Root

    def test_a_signature_without_the_term_is_left_alone(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _signed(pdf))
        assert "signatures" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert str(_fields(pdf)["Signature1"]["/V"]["/Name"]) == "A. Example"


@pytest.mark.integration
class TestStillRefused:
    """The known limits, on the record: places with no rule. The run is
    refused, never passed."""

    @pytest.mark.parametrize("plant", [
        lambda pdf: pdf.Root.__setitem__("/Names", Dictionary(URLS=Dictionary(Names=Array(
            [String(f"http://{TERM}.example/"), Dictionary(Note=String("captured"))])))),
        lambda pdf: pdf.Root.__setitem__("/OutputIntents", Array([Dictionary(
            Type=Name.OutputIntent, S=Name.GTS_PDFA1,
            OutputConditionIdentifier=String(f"{TERM} press profile"))])),
        lambda pdf: pdf.Root.__setitem__("/Names", Dictionary(EmbeddedFiles=Dictionary(
            Names=Array([String("broken.bin"), _undecodable_attachment(pdf)])))),
    ], ids=["a web-capture name", "an output intent's string", "an attachment that cannot be read"])
    def test_it_is_refused_and_nothing_is_written(self, tmp_path, plant):
        src = _with_term_on_page(tmp_path / "src.pdf", plant)
        out = str(tmp_path / "out.pdf")
        with pytest.raises(RedactionVerificationError) as ei:
            redact_pdf(src, out, search_terms=[TERM])
        assert ei.value.flatten_pages is None and not os.path.exists(out)


@pytest.mark.integration
class TestReplacementTextNeverHoldsTheTerm:
    """What the scrub writes in a term's place must not hold a term itself.
    "Ted" is inside "[redacted]"; and a real file whose fields are called
    "Field 1" and "Field 2" was renamed, for the word "Field", to field-1."""

    def test_a_term_inside_the_marker_gets_another_marker(self, tmp_path):
        def plant(pdf):
            page = pdf.pages[0].obj
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=root, Pg=page, Alt=String("photo of Ted")))])
            pdf.Root["/StructTreeRoot"] = root
            outlines = pdf.make_indirect(Dictionary(Count=1))
            item = pdf.make_indirect(Dictionary(Title=String("Ted's report"), Parent=outlines,
                                                Dest=Array([page, Name.Fit])))
            outlines["/First"] = outlines["/Last"] = item
            pdf.Root["/Outlines"] = outlines
        src = _pdf(tmp_path / "src.pdf", plant, body="a line about Ted here")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Ted"])
        assert res.verification["verified"] is True
        assert res.surface_counts.get("bookmarks") == 1 and res.surface_counts.get("structure") == 1
        with pikepdf.open(out) as pdf:
            assert str(pdf.Root["/Outlines"]["/First"]["/Title"]) == "[removed]"
            assert str(pdf.Root["/StructTreeRoot"]["/K"][0]["/Alt"]) == "[removed]"

    def test_a_field_is_not_renamed_to_a_name_holding_the_term(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", lambda pdf: pdf.Root.__setitem__(
            "/AcroForm", Dictionary(Fields=Array([pdf.make_indirect(Dictionary(
                FT=Name.Tx, T=String(f"Field {n}"))) for n in (1, 2)]))),
            body="a line about the Field here")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Field"])
        assert res.verification["verified"] is True and res.surface_counts.get("field_names") == 2
        with pikepdf.open(out) as pdf:
            names = sorted(_fields(pdf))
            assert len(set(names)) == 2 and not any("field" in n.lower() for n in names)

    def test_a_destination_is_not_renamed_to_a_name_holding_the_term(self, tmp_path):
        def plant(pdf):
            page = pdf.pages[0].obj
            pdf.Root["/Names"] = Dictionary(Dests=pdf.make_indirect(Dictionary(Names=Array(
                [String("redacted-notes"), Array([page, Name.Fit])]))))
            _on_page(pdf, _annot(pdf, Name.Link, Dest=String("redacted-notes")))
        src = _pdf(tmp_path / "src.pdf", plant, body="a redacted line here")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["redacted"])
        assert res.verification["verified"] is True and res.surface_counts.get("destinations") == 1
        with pikepdf.open(out) as pdf:
            key = bytes(pdf.Root["/Names"]["/Dests"]["/Names"][0])
            assert b"redacted" not in key
            assert bytes(pdf.pages[0].obj["/Annots"][0]["/Dest"]) == key


def _undecodable_attachment(pdf):
    payload = pdf.make_stream(b"placeholder")
    payload.write(b"this is not deflate data", filter=Name.FlateDecode)
    payload["/Type"] = Name.EmbeddedFile
    return pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String("broken.bin"),
                                        EF=Dictionary(F=payload)))


# ── The R1b review's small items ─────────────────────────────────────────
#
#  What was left when the rules above met real files: an action that names
#  the attachment it opened, the original of a redacted form kept alive by
#  the structure tree, the appearance of a note that is off its page, and
#  the neutral names themselves.


def _goto_embedded(target, **more):
    """A go-to action into an attachment of this file: *target* is the name
    the attachment list files it under, or a ready-made target dictionary."""
    if not isinstance(target, Dictionary):
        target = Dictionary(R=Name.C, N=String(target))
    return Dictionary(S=Name.GoToE, D=Array([0, Name.Fit]), NewWindow=True, T=target, **more)


def _link_targets(pdf) -> list:
    """Where each link left on page 1 goes: the attachment name or the
    annotation its go-to action names."""
    found = []
    for a in pdf.pages[0].obj.get("/Annots", []):
        if str(a.get("/Subtype")) == "/Link":
            target = a["/A"]["/T"]
            found.append(str(target["/N"] if "/N" in target else target["/A"]))
    return found


@pytest.mark.integration
class TestActionsIntoARemovedAttachment:
    """(a) A go-to action into an attachment names it. When rule (h) removes
    the attachment, the action goes with it — a real file's four links into
    its one attachment went on naming it after it was gone."""

    @pytest.mark.parametrize("home", ["a page link", "a bookmark", "after a harmless action",
                                      "off the page"])
    def test_the_action_goes_with_the_attachment(self, tmp_path, home):
        gone, kept = f"{TERM}-annex.pdf", "other.pdf"

        def plant(pdf):
            _attach(pdf, _filespec(pdf, gone, b"%PDF-1.4 annex"),
                    _filespec(pdf, kept, b"%PDF-1.4 other"))
            _on_page(pdf, _annot(pdf, Name.Link, A=_goto_embedded(kept)))
            if home == "a page link":
                _on_page(pdf, _annot(pdf, Name.Link, A=_goto_embedded(gone)))
            elif home == "a bookmark":
                outlines = pdf.make_indirect(Dictionary(Count=1))
                item = pdf.make_indirect(Dictionary(Title=String("Annex"), Parent=outlines,
                                                    A=_goto_embedded(gone)))
                outlines["/First"] = outlines["/Last"] = item
                pdf.Root["/Outlines"] = outlines
            elif home == "after a harmless action":
                pdf.Root["/OpenAction"] = Dictionary(S=Name.Named, N=Name.NextPage,
                                                     Next=_goto_embedded(gone))
            else:
                _held_by_structure(pdf, _annot(pdf, Name.Link, A=_goto_embedded(gone)))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert _attached(pdf) == [kept]
            assert _link_targets(pdf) == [kept]         # the link into the other one stays
            if home == "a bookmark":
                assert "/A" not in pdf.Root["/Outlines"]["/First"]
            elif home == "after a harmless action":
                assert "/Next" not in pdf.Root["/OpenAction"]

    def test_one_removed_for_what_is_in_it_takes_its_links_too(self, tmp_path):
        """Nothing in the action holds the term here; it would only be left
        pointing at an attachment that is no longer there."""
        def plant(pdf):
            _attach(pdf, _filespec(pdf, "minutes.pdf", f"%PDF-1.4 minutes of {TERM}".encode()),
                    _filespec(pdf, "other.pdf", b"%PDF-1.4 other"))
            _on_page(pdf, _annot(pdf, Name.Link, A=_goto_embedded("minutes.pdf")),
                     _annot(pdf, Name.Link, A=_goto_embedded("other.pdf")),
                     # Into an attachment of some other file: not this one's.
                     _annot(pdf, Name.Link, A=_goto_embedded("minutes.pdf",
                                                             F=String("elsewhere.pdf"))))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert _link_targets(pdf) == ["other.pdf", "minutes.pdf"]
            assert "/F" in pdf.pages[0].obj["/Annots"][1]["/A"]

    def test_a_target_given_as_the_annotation_that_carries_the_file(self, tmp_path):
        def plant(pdf):
            gone = _annot(pdf, Name.FileAttachment, NM=String("scan-1"), FS=_filespec(
                pdf, "scan.txt", f"scan of the {TERM} contract".encode()))
            kept = _annot(pdf, Name.FileAttachment, NM=String("scan-2"),
                          FS=_filespec(pdf, "blank.txt", b"nothing"))
            _on_page(pdf, gone, kept,
                     _annot(pdf, Name.Link, A=_goto_embedded(Dictionary(R=Name.C, P=0, A=0))),
                     _annot(pdf, Name.Link, A=_goto_embedded(
                         Dictionary(R=Name.C, P=0, A=String("scan-1")))),
                     _annot(pdf, Name.Link, A=_goto_embedded(
                         Dictionary(R=Name.C, P=0, A=String("scan-2")))))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("embedded_files") == 1
        with pikepdf.open(out) as pdf:
            assert [str(a["/Subtype"]) for a in pdf.pages[0].obj["/Annots"]] == [
                "/FileAttachment", "/Link"]
            assert _link_targets(pdf) == ["scan-2"]


#: A box drawn over the line the form of _tagged_form paints.
_OVER_THE_FORM = {"page": 0, "x0": 60, "y0": 150, "x1": 400, "y1": 200}


def _tagged_form(pdf, how: str, text: str = f"drawn by {TERM} here", page_no: int = 0):
    """A form XObject painted on a page, its content tagged, with the
    structure tree referring to it the way *how* says: by a marked-content
    reference ("/Stm"), by one among other kids, or by an object reference
    ("/Obj"). Returns the structure element."""
    page = pdf.pages[page_no].obj
    form = pdf.make_stream(
        f"/P <</MCID 0>> BDC BT /F1 14 Tf 10 20 Td ({text}) Tj ET EMC".encode(),
        Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 300, 40], StructParents=0,
        Resources=Dictionary(Font=Dictionary(F1=_font(pdf))))
    page["/Resources"]["/XObject"] = Dictionary(X1=form)
    page["/Contents"] = pdf.make_stream(
        page["/Contents"].read_bytes() + b"\nq 1 0 0 1 72 600 cm /X1 Do Q")
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=root, Pg=page))
    if how == "/Obj":
        elem["/K"] = Dictionary(Type=Name.OBJR, Obj=form)
    elif how == "/Stm":
        elem["/K"] = Dictionary(Type=Name.MCR, MCID=0, Stm=form)
    else:
        elem["/K"] = Array([Dictionary(Type=Name.MCR, MCID=0, Stm=form),
                            Dictionary(Type=Name.MCR, MCID=1, Pg=page), 2])
    root["/K"] = Array([elem])
    root["/ParentTree"] = Dictionary(Nums=Array([0, Array([elem])]))
    pdf.Root["/StructTreeRoot"] = root
    pdf.Root["/MarkInfo"] = Dictionary(Marked=True)
    return elem


def _structure_kids(pdf) -> list:
    """The kids of the one structure element _tagged_form makes."""
    kids = pdf.Root["/StructTreeRoot"]["/K"][0].get("/K")
    if kids is None:
        return []
    return list(kids) if isinstance(kids, pikepdf.Array) else [kids]


@pytest.mark.integration
class TestWhatTheStructureTreeKeepsAlive:
    """(b) Redaction does not edit a form XObject in place: the page gets a
    redacted copy and the original is dropped, when nothing refers to it any
    more. A tagged file's structure tree does — and so kept the original,
    unredacted, in the output."""

    HOW = ["/Stm", "/Stm, among others", "/Obj"]

    @pytest.mark.parametrize("how", HOW)
    def test_the_original_of_a_redacted_form_is_not_kept(self, tmp_path, how):
        res, out = _redact(tmp_path, lambda pdf: _tagged_form(pdf, how))
        assert res.surface_counts.get("structure_refs") == 1
        with fitz.open(out) as doc:
            text = doc[0].get_text()
        assert "drawn by" in text and "here" in text       # the rest of the form is still drawn
        with pikepdf.open(out) as pdf:
            kids = _structure_kids(pdf)
            assert not any(isinstance(k, pikepdf.Dictionary) and ("/Stm" in k or "/Obj" in k)
                           for k in kids)
            assert len(kids) == (2 if how == "/Stm, among others" else 0)

    @pytest.mark.parametrize("how", HOW)
    def test_nor_under_a_box_drawn_by_hand(self, tmp_path, how):
        """No term is given, so nothing searches the output for one: the
        original went unnoticed."""
        src = _pdf(tmp_path / "src.pdf", lambda pdf: _tagged_form(pdf, how))
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[_OVER_THE_FORM])
        assert res.verification["verified"] is True
        assert res.surface_counts == {"page_content": 1, "structure_refs": 1}
        assert not _holds_term(out)

    def test_a_reference_to_a_form_a_page_still_paints_is_kept(self, tmp_path):
        """The term is on page 1 only; the tagged form is on page 2, which
        is not touched."""
        def plant(pdf):
            pdf.pages[0].obj["/Contents"] = pdf.make_stream(_show(f"about {TERM}"))
            _tagged_form(pdf, "/Stm", text="an ordinary figure", page_no=1)
        src = _pdf(tmp_path / "src.pdf", plant, pages=2)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=[TERM])
        assert res.verification["verified"] is True and "structure_refs" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            (kid,) = _structure_kids(pdf)
            assert b"an ordinary figure" in kid["/Stm"].read_bytes()

    def test_the_page_s_own_content_stream_named_by_a_reference(self, tmp_path):
        """A page's content is not edited in place either: the page gets
        new streams. A reference that names the old one kept the whole
        unredacted page."""
        def plant(pdf):
            page = pdf.pages[0].obj
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.P, P=root, Pg=page,
                K=Dictionary(Type=Name.MCR, MCID=0, Stm=page["/Contents"])))])
            pdf.Root["/StructTreeRoot"] = root
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("structure_refs") == 1
        with pikepdf.open(out) as pdf:
            assert _structure_kids(pdf) == []

    def test_the_original_of_an_image_under_a_box_is_not_kept(self, tmp_path):
        """An image is not edited in place either: the page gets a copy
        with the pixels under the box blacked out. Eight pixels of the top
        row spell a word here, so the original can be told from the copy."""
        def plant(pdf):
            page = pdf.pages[0].obj
            image = pdf.make_stream(
                b"SECRETPX" + b"\xc8" * 56, Type=Name.XObject, Subtype=Name.Image, Width=8,
                Height=8, ColorSpace=Name.DeviceGray, BitsPerComponent=8, StructParent=0)
            page["/Resources"]["/XObject"] = Dictionary(Im1=image)
            page["/Contents"] = pdf.make_stream(
                page["/Contents"].read_bytes() + b"\nq 200 0 0 200 100 400 cm /Im1 Do Q")
            root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
            root["/K"] = Array([pdf.make_indirect(Dictionary(
                Type=Name.StructElem, S=Name.Figure, P=root, Pg=page,
                K=Dictionary(Type=Name.OBJR, Obj=image)))])
            pdf.Root["/StructTreeRoot"] = root
        src = _pdf(tmp_path / "src.pdf", plant)
        assert b"SECRETPX" in _everything(src)             # the fixture is what it claims
        out = str(tmp_path / "out.pdf")
        # The top half of the image, in the page's top-down coordinates.
        res = redact_pdf(src, out, rects=[{"page": 0, "x0": 90, "y0": 180, "x1": 310, "y1": 290}])
        assert res.verification["verified"] is True
        assert b"SECRETPX" not in _everything(out)
        with fitz.open(out) as doc:
            assert len(doc[0].get_images()) == 1           # the copy is still painted

    def test_a_form_that_is_shared_with_a_page_left_alone_is_kept(self, tmp_path):
        """Page 2 paints the same form and is not redacted: the original is
        still that page's content, and the reference to it is still true."""
        def plant(pdf):
            _tagged_form(pdf, "/Stm")
            second = pdf.pages[1].obj
            second["/Resources"]["/XObject"] = Dictionary(
                X1=pdf.pages[0].obj["/Resources"]["/XObject"]["/X1"])
            second["/Contents"] = pdf.make_stream(b"q 1 0 0 1 72 600 cm /X1 Do Q")
        src = _pdf(tmp_path / "src.pdf", plant, pages=2)
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, rects=[_OVER_THE_FORM])
        assert res.verification["verified"] is True and "structure_refs" not in res.surface_counts
        with pikepdf.open(out) as pdf:
            assert len(_structure_kids(pdf)) == 1


@pytest.mark.integration
class TestAnAppearanceOffThePage:
    """(c) A note that redaction took off its page, kept alive by the
    structure tree, has its text scrubbed. Its appearance stream drew that
    text, and nothing shows it any more: it goes too."""

    def _note(self, pdf, **entries):
        return _annot(pdf, Name.FreeText, AP=Dictionary(N=_form(pdf, f"ask {TERM} first")),
                      **entries)

    @pytest.mark.parametrize("entry", ["/Contents", "/RC", "/Subj"])
    def test_it_goes_with_the_text_it_drew(self, tmp_path, entry):
        def plant(pdf):
            text = f"<body><p>ask {TERM} first</p></body>" if entry == "/RC" else f"ask {TERM} first"
            _held_by_structure(pdf, self._note(pdf, **{entry.lstrip("/"): String(text)}))
        res, out = _redact(tmp_path, plant)
        with pikepdf.open(out) as pdf:
            note = pdf.Root["/StructTreeRoot"]["/K"][0]["/K"][0]["/Obj"]
            assert "/AP" not in note and entry not in note
            assert str(note["/Subtype"]) == "/FreeText"          # the note itself stays

    def test_a_note_under_the_redacted_text_itself(self, tmp_path):
        """The whole chain as it happens: the note sits over the text, the
        box takes it off the page, the structure tree keeps it alive."""
        def plant(pdf):
            note = pdf.make_indirect(Dictionary(
                Type=Name.Annot, Subtype=Name.FreeText, Rect=[60, 705, 420, 745],
                Contents=String(f"ask {TERM} first"), DA=String("/Helv 12 Tf 0 g"),
                AP=Dictionary(N=_form(pdf, f"ask {TERM} first"))))
            _on_page(pdf, note)
            _held_by_structure(pdf, note)
        _redact(tmp_path, plant)

    def test_a_note_on_its_page_keeps_its_appearance(self, tmp_path):
        """On a page the appearance is what the reader sees. It is not
        dropped for the note's text: if it draws the term, the run is
        refused and the page is offered for flattening, as before."""
        res, out = _redact(tmp_path, lambda pdf: _on_page(pdf, _annot(
            pdf, Name.FreeText, Contents=String(f"ask {TERM} first"),
            AP=Dictionary(N=_form(pdf, "a note drawn here")))))
        assert res.surface_counts.get("annotations") == 1
        with pikepdf.open(out) as pdf:
            note = pdf.pages[0].obj["/Annots"][0]
            assert "/Contents" not in note and "/AP" in note

    def test_text_that_is_kept_keeps_its_appearance(self, tmp_path):
        res, out = _redact(tmp_path, lambda pdf: _held_by_structure(pdf, _annot(
            pdf, Name.FreeText, Contents=String("see page 4"),
            AP=Dictionary(N=_form(pdf, "see page 4")))))
        with pikepdf.open(out) as pdf:
            note = pdf.Root["/StructTreeRoot"]["/K"][0]["/K"][0]["/Obj"]
            assert "/AP" in note and str(note["/Contents"]) == "see page 4"


@pytest.mark.integration
class TestNeutralNamesCountUp:
    """(d) Thirteen sibling fields of a real file came out as field1,
    item-1, item1, entry-1 and so on: unique, and untidy. The names count
    up within one stem, and the stem changes only when it holds a term."""

    def _siblings(self, names):
        def plant(pdf):
            pdf.Root["/AcroForm"] = Dictionary(Fields=Array([pdf.make_indirect(Dictionary(
                FT=Name.Tx, T=String(name))) for name in names]))
        return plant

    def test_sibling_fields_are_numbered_in_one_run(self, tmp_path):
        res, out = _redact(tmp_path, self._siblings([f"{TERM} {n}" for n in range(1, 14)]))
        assert res.surface_counts.get("field_names") == 13
        with pikepdf.open(out) as pdf:
            assert sorted(_fields(pdf)) == sorted(f"field-{n}" for n in range(1, 14))

    def test_a_name_already_in_use_is_passed_over(self, tmp_path):
        res, out = _redact(tmp_path, self._siblings(
            [f"{TERM} a", "field-2", f"{TERM} b", f"{TERM} c"]))
        with pikepdf.open(out) as pdf:
            assert sorted(_fields(pdf)) == ["field-1", "field-2", "field-3", "field-4"]

    def test_the_stem_changes_only_when_it_holds_a_term(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", self._siblings([f"Field {n}" for n in (1, 2, 3)]),
                   body="a line about the Field here")
        out = str(tmp_path / "out.pdf")
        res = redact_pdf(src, out, search_terms=["Field"])
        assert res.verification["verified"] is True
        with pikepdf.open(out) as pdf:
            assert sorted(_fields(pdf)) == ["item-1", "item-2", "item-3"]

    def test_named_destinations_count_up_the_same_way(self, tmp_path):
        def plant(pdf):
            page = pdf.pages[0].obj
            entries = Array()
            for name in sorted(f"{TERM}-{part}" for part in ("a", "b", "c")):
                entries.append(String(name))
                entries.append(Array([page, Name.Fit]))
            pdf.Root["/Names"] = Dictionary(Dests=pdf.make_indirect(Dictionary(Names=entries)))
        res, out = _redact(tmp_path, plant)
        assert res.surface_counts.get("destinations") == 3
        with pikepdf.open(out) as pdf:
            keys = pdf.Root["/Names"]["/Dests"]["/Names"]
            assert [str(keys[i]) for i in (0, 2, 4)] == ["redacted-1", "redacted-2", "redacted-3"]

    @pytest.mark.parametrize("terms, expected", [
        (["-"], [b"field1", b"field2", b"field3"]),          # the separator holds a term
        (["1"], [b"field-2", b"field-3", b"field-4"]),       # a number holds one
        (["d-1"], [b"field-2", b"field-3", b"field-4"]),     # the join of stem and number
        (["field", "item"], [b"entry-1", b"entry-2", b"entry-3"]),
    ], ids=["a hyphen", "a digit", "across the join", "two stems"])
    def test_no_name_holds_a_term(self, terms, expected):
        import pdf_ops
        from pdf_verify import TermFinder
        finder, taken = TermFinder(terms), set()
        names = [pdf_ops._neutral_name(("field", "item", "entry", "x"), taken, finder)
                 for _ in expected]
        assert names == expected and taken == set(expected)


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
