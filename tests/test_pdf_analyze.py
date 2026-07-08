"""Tests for pdf_analyze.py — the privacy/security auditor and sanitiser.

Centred on ANL-02: the sanitiser must follow ``/Next`` action chains, or
JavaScript hidden behind a benign ``/URI`` head survives while
``sanitize_pdf`` still reports success. pdf_analyze imports no Qt, so
these run Qt-free (per CLAUDE.md).
"""

import pikepdf

from pdf_analyze import analyze_document, sanitize_pdf

# ── Builders ─────────────────────────────────────────────────────────────


def _uri_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/URI"),
                              URI=pikepdf.String("https://example.com/"))


def _js_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/JavaScript"),
                              JS=pikepdf.String("app.alert('pwned');"))


def _launch_action() -> pikepdf.Dictionary:
    return pikepdf.Dictionary(S=pikepdf.Name("/Launch"),
                              F=pikepdf.String("calc.exe"))


def _make_next_chain_pdf(path, *, head: pikepdf.Dictionary,
                         buried: pikepdf.Dictionary) -> str:
    """One-page PDF with a single Link annotation whose ``/A`` is *head*
    and whose ``/A``'s ``/Next`` is *buried* — the ANL-02 evasion shape."""
    head["/Next"] = buried
    pdf = pikepdf.Pdf.new()
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, 612, 792],
    ))
    pdf.pages.append(page)
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Link"),
        Rect=[0, 0, 100, 100],
        A=head,
    )
    pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
    pdf.save(str(path))
    pdf.close()
    return str(path)


def _finding_ids(report: dict) -> set:
    return {f["id"] for f in report["findings"]}


# ── Tests ────────────────────────────────────────────────────────────────


class TestSanitizeNextChain:
    def test_analyzer_flags_buried_js(self, tmp_path):
        # Baseline: the detector already walks /Next and flags the buried JS
        # alongside the benign link — this is what the sanitiser must match.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        ids = _finding_ids(analyze_document(src))
        assert "scripts.js" in ids
        assert "links.uri" in ids

    def test_sanitize_removes_buried_js_keeps_link(self, tmp_path):
        # The core ANL-02 fix: JS chained after a kept /URI head is excised
        # (and counted), while the URI link itself survives.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out,
                              {"javascript": True, "external_links": False})

        assert result["removed"].get("annot_javascript", 0) >= 1
        assert result["total_removed"] >= 1

        ids = _finding_ids(analyze_document(out))
        assert "scripts.js" not in ids     # JS gone…
        assert "links.uri" in ids          # …link preserved

    def test_js_gate_respected(self, tmp_path):
        # With javascript off, the buried JS is deliberately left alone.
        src = _make_next_chain_pdf(tmp_path / "evasion.pdf",
                                   head=_uri_action(), buried=_js_action())
        out = str(tmp_path / "keep.pdf")
        result = sanitize_pdf(src, out,
                              {"javascript": False, "external_links": False})

        assert result["removed"].get("annot_javascript", 0) == 0
        assert "scripts.js" in _finding_ids(analyze_document(out))

    def test_buried_launch_gated_on_launch_actions(self, tmp_path):
        # The chain walk isn't JS-only: a /Launch behind a /URI head is
        # excised when launch_actions is on, link still preserved.
        src = _make_next_chain_pdf(tmp_path / "launch.pdf",
                                   head=_uri_action(), buried=_launch_action())
        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(src, out, {"launch_actions": True,
                                         "javascript": False,
                                         "external_links": False})

        assert result["removed"].get("launch_action", 0) >= 1
        ids = _finding_ids(analyze_document(out))
        assert "actions.launch" not in ids
        assert "links.uri" in ids

    def test_plain_uri_untouched(self, tmp_path):
        # A /URI with no dangerous /Next must not be disturbed.
        path = str(tmp_path / "plain.pdf")
        pdf = pikepdf.Pdf.new()
        page = pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792]))
        pdf.pages.append(page)
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"), Subtype=pikepdf.Name("/Link"),
            Rect=[0, 0, 100, 100], A=_uri_action())
        pdf.pages[0]["/Annots"] = pikepdf.Array([pdf.make_indirect(annot)])
        pdf.save(path)
        pdf.close()

        out = str(tmp_path / "clean.pdf")
        result = sanitize_pdf(path, out,
                              {"javascript": True, "external_links": False})

        assert "annot_javascript" not in result["removed"]
        assert "links.uri" in _finding_ids(analyze_document(out))
