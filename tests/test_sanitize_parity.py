"""Parity between what Analyze reports, what Sanitize removes, and what the
verifier's whole-file sweep sees (#101).

The cases are generated from ``pdf_analyze.ACTION_SITE_KINDS`` — the one list
of places an action can hang — crossed with the action types the sanitize
options remove. A site kind added to that list without a way to plant an
action there fails ``test_every_site_kind_is_exercised``, so a kind cannot be
taught to the analyzer and forgotten in the sanitizer (or the reverse)
without a test noticing.

For every site x action type, plain and buried one step down a /Next chain:

  * option on  — Analyze finds it, the sweep finds it in the unsanitized
    file, Sanitize removes it and counts it exactly once, and the output
    verifies, sweep included;
  * option off — it survives, nothing is counted, and it is reported as a
    residual rather than a failure.

Qt-free, like the modules it exercises (see CLAUDE.md).
"""

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from pdf_analyze import (
    _AUTORUN_KINDS,
    ACTION_SITE_KINDS,
    DEFAULT_SANITIZE,
    analyze_document,
    sanitize_pdf,
)
from pdf_verify import verify_sanitization

ALL_OFF = {k: False for k in DEFAULT_SANITIZE}


# ── Planting an action at each kind of site ──────────────────────────────


def _add_annot(pdf, page, **entries) -> None:
    annot = pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 50, 50], **entries))
    page["/Annots"] = Array([annot])


def _add_bookmark(pdf, **entries) -> None:
    item = pdf.make_indirect(Dictionary(Title=String("Chapter 1"), **entries))
    outlines = pdf.make_indirect(Dictionary(
        Type=Name.Outlines, First=item, Last=item, Count=1))
    item["/Parent"] = outlines
    pdf.Root["/Outlines"] = outlines


def _add_field(pdf, **entries) -> None:
    """A field listed under /AcroForm that no page shows as an annotation."""
    fld = pdf.make_indirect(Dictionary(FT=Name.Tx, T=String("total"), **entries))
    pdf.Root["/AcroForm"] = Dictionary(Fields=Array([fld]))


#: site kind -> plant(pdf, page, action). One entry per ACTION_SITE_KINDS
#: member. *page* is the page dictionary to use, for the kinds that live on one.
PLANT = {
    "open_action": lambda pdf, page, a: pdf.Root.__setitem__("/OpenAction", a),
    "document_aa": lambda pdf, page, a: pdf.Root.__setitem__("/AA", Dictionary(WC=a)),
    "page_aa": lambda pdf, page, a: page.__setitem__("/AA", Dictionary(O=a)),
    "annot_a": lambda pdf, page, a: _add_annot(pdf, page, A=a),
    "annot_pa": lambda pdf, page, a: _add_annot(
        pdf, page, PA=a, Dest=Array([page, Name.Fit])),
    "annot_aa": lambda pdf, page, a: _add_annot(pdf, page, AA=Dictionary(E=a)),
    "outline_a": lambda pdf, page, a: _add_bookmark(pdf, A=a),
    "field_a": lambda pdf, page, a: _add_field(pdf, A=a),
    "field_aa": lambda pdf, page, a: _add_field(pdf, AA=Dictionary(K=a)),
}

#: Kinds that hang on a page or on one of its annotations.
PAGE_KINDS = ("page_aa", "annot_a", "annot_pa", "annot_aa")

#: Where a page can live: in the page tree, or only in one of the catalog's
#: name trees for pages outside it (templates, named pages).
HOMES = ("tree", "Templates", "Pages")


def _homes(kind: str) -> tuple:
    return HOMES if kind in PAGE_KINDS else ("tree",)

#: action type -> (make it, the option that removes it, the finding Analyze
#: reports it under, the counter a removal by type is counted under, bytes
#: that are in the file only because of it).
ACTIONS = {
    "javascript": (lambda: Dictionary(S=Name.JavaScript, JS=String("app.alert('marker-js')")),
                   "javascript", "scripts.js", "annot_javascript", b"marker-js"),
    "launch": (lambda: Dictionary(S=Name.Launch, F=String("marker-launch.exe")),
               "launch_actions", "actions.launch", "launch_action", b"marker-launch"),
    "submit": (lambda: Dictionary(S=Name.SubmitForm, F=String("https://submit.example/marker-submit")),
               "submit_actions", "forms.submit", "submit_action", b"marker-submit"),
    "uri": (lambda: Dictionary(S=Name.URI, URI=String("https://link.example/marker-uri")),
            "external_links", "links.uri", "external_link", b"marker-uri"),
}

#: Sites whose removals are counted by where they are, not by action type.
COUNTED_BY_SITE = {"open_action", "document_aa", "page_aa"}


def _harmless() -> Dictionary:
    return Dictionary(S=Name.Named, N=Name.NextPage)


def _build(path, kind: str, action, *, nested: bool, home: str = "tree") -> str:
    pdf = pikepdf.Pdf.new()
    for _ in range(1 if home == "tree" else 2):
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
    page = pdf.pages[-1].obj
    if nested:
        head = _harmless()
        head["/Next"] = pdf.make_indirect(action)
        action = head
    PLANT[kind](pdf, page, action)
    if home != "tree":
        # Take the page out of the page tree; the name tree is now the only
        # thing that holds it.
        del pdf.pages[-1]
        pdf.Root["/Names"] = Dictionary({f"/{home}": Dictionary(
            Names=Array([String("extra"), page]))})
    pdf.save(str(path))
    pdf.close()
    return str(path)


# ── Reading results ──────────────────────────────────────────────────────


def _ids(path) -> set:
    return {f["id"] for f in analyze_document(path)["findings"]}


def _check(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c
    raise AssertionError(f"check {cid!r} missing: {[c.id for c in report.checks]}")


def _all_strings(path) -> bytes:
    """Every string in the file, inline ones included (pdf.objects yields
    indirect objects only, so a check over it alone misses those)."""
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
            for k in obj.keys():
                v = obj[k]
                if isinstance(v, pikepdf.Object) and not v.is_indirect:
                    visit(v, depth + 1)

    with pikepdf.open(path) as pdf:
        visit(pdf.trailer)
        for obj in pdf.objects:
            visit(obj)
    return b"\n".join(out)


# ── The matrix ───────────────────────────────────────────────────────────


def test_every_site_kind_is_exercised():
    assert set(PLANT) == set(ACTION_SITE_KINDS)


MATRIX = [(kind, home, atype, nested)
          for kind in ACTION_SITE_KINDS for home in _homes(kind)
          for atype in ACTIONS for nested in (False, True)]
MATRIX_IDS = [f"{k}-{h}-{a}-{'next' if n else 'head'}" for k, h, a, n in MATRIX]


@pytest.mark.integration
@pytest.mark.parametrize("kind, home, atype, nested", MATRIX, ids=MATRIX_IDS)
class TestSiteByActionType:
    def test_option_on_removes_it_once_and_verifies(self, tmp_path, kind, home, atype, nested):
        make, option, finding, counter, marker = ACTIONS[atype]
        src = _build(tmp_path / "src.pdf", kind, make(), nested=nested, home=home)
        opts = {**ALL_OFF, option: True}

        # Analyze finds it, and so — independently — does the sweep.
        assert finding in _ids(src)
        before = verify_sanitization(src, opts)
        assert _check(before, f"sanitize.sweep_{option}").passed is False
        assert _check(before, f"sanitize.sweep_{option}").evidence

        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, opts)
        expected = kind if (kind in COUNTED_BY_SITE and not nested) else counter
        assert res["removed"] == {expected: 1}
        assert marker not in _all_strings(out)
        assert finding not in _ids(out)

        after = verify_sanitization(out, opts)
        assert after.verified is True, [(c.id, c.evidence) for c in after.failed_checks()]
        assert _check(after, f"sanitize.sweep_{option}").passed is True

    def test_option_off_leaves_it_as_a_residual(self, tmp_path, kind, home, atype, nested):
        make, _option, finding, _counter, marker = ACTIONS[atype]
        src = _build(tmp_path / "src.pdf", kind, make(), nested=nested, home=home)
        out = str(tmp_path / "out.pdf")
        res = sanitize_pdf(src, out, ALL_OFF)
        assert res["removed"] == {}
        assert marker in _all_strings(out)
        report = verify_sanitization(out, ALL_OFF)
        assert finding in {f["id"] for f in report.residual_findings}
        assert report.failed_checks() == []


AUTORUN = [(k, h) for k in ACTION_SITE_KINDS if k in _AUTORUN_KINDS for h in _homes(k)]


@pytest.mark.integration
@pytest.mark.parametrize("kind, home", AUTORUN, ids=[f"{k}-{h}" for k, h in AUTORUN])
class TestAutoRunSites:
    """`auto_actions` removes an action for where it is, whatever it does:
    here a harmless go-to-next-page that no other option would touch."""

    OPTS = {**ALL_OFF, "auto_actions": True}

    def test_auto_actions_on_removes_it_once_and_verifies(self, tmp_path, kind, home):
        src = _build(tmp_path / "src.pdf", kind, _harmless(), nested=False, home=home)
        assert "actions.autorun" in _ids(src)
        assert _check(verify_sanitization(src, self.OPTS),
                      "sanitize.sweep_auto_actions").passed is False

        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, self.OPTS)["removed"] == {kind: 1}
        assert "actions.autorun" not in _ids(out)
        after = verify_sanitization(out, self.OPTS)
        assert after.verified is True, [(c.id, c.evidence) for c in after.failed_checks()]
        assert _check(after, "sanitize.sweep_auto_actions").passed is True

    def test_auto_actions_off_leaves_it_as_a_residual(self, tmp_path, kind, home):
        src = _build(tmp_path / "src.pdf", kind, _harmless(), nested=False, home=home)
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, ALL_OFF)["removed"] == {}
        report = verify_sanitization(out, ALL_OFF)
        assert "actions.autorun" in {f["id"] for f in report.residual_findings}


def test_sites_that_do_not_fire_by_themselves_survive_auto_actions(tmp_path):
    # A bookmark or a link runs its action when the user clicks it. That is
    # not "auto-run", so `auto_actions` alone leaves a harmless one in place.
    for kind in (k for k in ACTION_SITE_KINDS if k not in _AUTORUN_KINDS):
        src = _build(tmp_path / f"{kind}.pdf", kind, _harmless(), nested=False)
        out = str(tmp_path / f"{kind}_out.pdf")
        assert sanitize_pdf(src, out, {**ALL_OFF, "auto_actions": True})["removed"] == {}


@pytest.mark.integration
class TestNamedJavaScriptTree:
    """Document-level named scripts live in a name tree, not at an action
    site; the tree is removed whole under `javascript`."""

    def _src(self, tmp_path) -> str:
        pdf = pikepdf.Pdf.new()
        pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792])))
        script = pdf.make_indirect(ACTIONS["javascript"][0]())
        pdf.Root["/Names"] = Dictionary(
            JavaScript=Dictionary(Names=Array([String("init"), script])))
        src = str(tmp_path / "src.pdf")
        pdf.save(src)
        pdf.close()
        return src

    def test_javascript_on(self, tmp_path):
        src, opts = self._src(tmp_path), {**ALL_OFF, "javascript": True}
        assert "scripts.js" in _ids(src)
        assert _check(verify_sanitization(src, opts), "sanitize.sweep_javascript").passed is False
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, opts)["removed"] == {"named_javascript": 1}
        assert b"marker-js" not in _all_strings(out)
        assert verify_sanitization(out, opts).verified is True

    def test_javascript_off(self, tmp_path):
        src = self._src(tmp_path)
        out = str(tmp_path / "out.pdf")
        assert sanitize_pdf(src, out, ALL_OFF)["removed"] == {}
        report = verify_sanitization(out, ALL_OFF)
        assert "scripts.js" in {f["id"] for f in report.residual_findings}
