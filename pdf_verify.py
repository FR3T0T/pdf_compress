"""
pdf_verify — Post-operation verification: prove removal instead of trusting it.

The verification loop behind the toolkit's "provable sanitization" promise.
After a destructive transform (``redact_pdf``, ``sanitize_pdf``) saves its
output, these functions re-open the OUTPUT file and independently confirm
the targeted content is actually gone, emitting a structured
:class:`VerificationReport` rather than trusting the transform library:

  - :func:`verify_redaction` — searches every extractable surface of the
    output (page text, raw content streams, annotations, form-field values,
    link URIs, docinfo, XMP, bookmarks, embedded-file names) for each
    redacted term, and checks user-drawn redaction rectangles via clipped
    extraction. Surfaces are read through TWO independent libraries where
    possible: PyMuPDF (the renderer's view) plus a pikepdf pass over the raw
    object graph (all /Info keys, every /V field value, every annotation's
    /Contents//T//Subj — including annotation subtypes PyMuPDF skips), so a
    leak invisible to one library is still caught by the other.
  - :func:`verify_sanitization` — re-runs the ``pdf_analyze`` audit on the
    output and asserts absent exactly the finding categories the enabled
    sanitize options promised to remove (expectations-based — anything else
    the re-scan still flags is reported honestly as a residual finding, not
    a failure).

Fail-closed semantics: an output that cannot be opened, decrypted, or
re-scanned yields a FAILED check; a surface (or single page/stream/rect)
that cannot be read records failure evidence on its check instead of
passing vacuously — "couldn't verify" is never a pass. A report with no
checks does not count as verified.

Every failed check's evidence names the surface AND the page number (or
"document-level" for surfaces that live outside any page). The fail-closed
redaction UX depends on both: surface decides whether flatten-to-image can
even help, and the page number targets the flatten (decision D1).

Qt-free by design, like ``compress_paths.py``: this module imports only the
stdlib, the PDF backends (fitz/pikepdf — both hard dependencies of
``pdf_ops``), and ``pdf_analyze`` — never anything under ``ui/`` — so it
stays unit-testable on a headless CI runner where the PySide6/QtWebEngine
stack cannot load (see CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import fitz  # PyMuPDF
import pikepdf

from pdf_analyze import DEFAULT_SANITIZE, _page_content_blobs, analyze_document

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
#  Result types
# ════════════════════════════════════════════════════════════════════

@dataclass
class VerificationCheck:
    """One asserted expectation about the output file."""
    id: str                # e.g. "redaction.page_text", "sanitize.scripts_js"
    description: str
    passed: bool
    evidence: list[str] = field(default_factory=list)  # each line names surface + page


@dataclass
class VerificationReport:
    input_path: str
    output_path: str
    tool: str              # "redaction" | "sanitization"
    timestamp: str         # ISO 8601
    checks: list[VerificationCheck] = field(default_factory=list)
    residual_findings: list[dict] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """True iff at least one check ran and every check passed.

        An empty check list is NOT verified — a report that asserted nothing
        must never read as proof.
        """
        return bool(self.checks) and all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        """JSON-ready dict, camelCase keys — mirrors AnalysisResult.to_dict()."""
        return {
            "inputPath": self.input_path,
            "outputPath": self.output_path,
            "tool": self.tool,
            "timestamp": self.timestamp,
            "checks": [
                {"id": c.id, "description": c.description,
                 "passed": c.passed, "evidence": list(c.evidence)}
                for c in self.checks
            ],
            "residualFindings": self.residual_findings,
            "verified": self.verified,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ════════════════════════════════════════════════════════════════════
#  Matching helpers
# ════════════════════════════════════════════════════════════════════

def _contains(haystack: str, term: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        return term in haystack
    return term.casefold() in haystack.casefold()


def _bytes_contain(raw: bytes, term: str, case_sensitive: bool) -> bool:
    """Literal byte search in decoded content streams — the paint-over
    backstop. bytes.lower() folds ASCII only; non-ASCII terms fall back to
    exact bytes, which is the safe (never-false-negative for exact matches)
    direction."""
    needle = term.encode("utf-8", "ignore")
    if not needle:
        return False
    if case_sensitive:
        return needle in raw
    return needle.lower() in raw.lower()


def _term_in_page_text(page, text: str, term: str,
                       case_sensitive: bool) -> tuple[bool, Optional[str]]:
    """(found, error) — is *term* extractable from *page*?

    Three views of the extracted text are searched: as-is, whitespace-
    normalized, and dehyphenated-then-normalized — a term wrapped across a
    line break appears with '\\n' (or '-\\n') where the caller's term has a
    space (or nothing), and without normalization an exact-case cross-line
    occurrence would be invisible to case_sensitive matching (the per-rect
    search_for confirm below sees only one line's fragment per hit rect, so
    it can never confirm a multi-line match by itself).

    search_for() (with TEXT_DEHYPHENATE, matching redact_pdf's own search)
    additionally catches kerned text the plain views miss. It is always
    case-insensitive, so for case_sensitive mode each hit is confirmed by
    re-extracting the text under its rect — the RED-01 workaround.

    *error* is set when search_for itself raised, so the caller records a
    failed-verification line instead of passing vacuously (fail-closed).
    """
    norm_term = " ".join(term.split())
    views = (text,
             " ".join(text.split()),
             " ".join(text.replace("-\n", "").split()))
    for v in views:
        if _contains(v, term, case_sensitive) or _contains(v, norm_term, case_sensitive):
            return True, None
    try:
        hits = page.search_for(term, flags=fitz.TEXT_DEHYPHENATE)
    except Exception as exc:
        return False, str(exc)
    for r in hits:
        if not case_sensitive:
            return True, None
        try:
            frag = page.get_textbox(r)
        except Exception:
            frag = ""
        if term in frag or norm_term in frag:
            return True, None
    return False, None


# ════════════════════════════════════════════════════════════════════
#  Redaction verification
# ════════════════════════════════════════════════════════════════════

#: doc.metadata keys that carry identifying content (PyMuPDF's fixed set;
#: the pikepdf cross-check below covers custom /Info keys it can't see).
_DOCINFO_KEYS = ("title", "author", "subject", "keywords",
                 "creator", "producer", "creationDate", "modDate")

#: Annotation text-bearing keys -> the label used in evidence (matches the
#: fitz annot.info key names so both walks produce identical, dedupable
#: evidence lines).
_ANNOT_TEXT_KEYS = (("/Contents", "content"), ("/T", "title"), ("/Subj", "subject"))


def _info_key_label(key: str) -> str:
    """'/CreationDate' -> 'creationDate' — matches fitz doc.metadata naming
    so the two docinfo walks emit identical, dedupable evidence lines."""
    k = key.lstrip("/")
    return k[:1].lower() + k[1:] if k else k


def _pikepdf_scan(path: str, password: Optional[str]):
    """One pass over the raw object graph with pikepdf.

    Returns ``(info_items, field_values, annot_items)`` where info_items is
    ``[(label, value)]`` for every /Info key (custom keys included —
    PyMuPDF's doc.metadata cannot see those), field_values is every /V
    string reachable anywhere in the file (redaction deletes the *widget*,
    but an inherited /V on a parent field survives invisibly to
    page.widgets()), and annot_items is ``[(page_no, subtype, label,
    value)]`` for every annotation's text-bearing keys — including
    annotation subtypes PyMuPDF's page.annots() silently skips.

    On failure returns an error string instead: the caller records it as
    failed evidence on the surfaces this cross-check backs (fail-closed —
    a cross-check that could not run must not read as a pass).
    """
    info_items: list[tuple[str, str]] = []
    field_values: list[str] = []
    annot_items: list[tuple[int, str, str, str]] = []
    kwargs = {"password": password} if password else {}
    try:
        with pikepdf.open(path, **kwargs) as pdf:
            try:
                docinfo = pdf.trailer.get("/Info")
            except Exception:
                docinfo = None
            if docinfo is not None:
                for k in docinfo.keys():
                    try:
                        info_items.append((_info_key_label(str(k)),
                                           str(docinfo[k])))
                    except Exception:
                        continue

            for pi, pg in enumerate(pdf.pages, 1):
                try:
                    annots = pg.get("/Annots")
                except Exception:
                    annots = None
                if annots is None:
                    continue
                for a in annots:
                    try:
                        subtype = str(a.get("/Subtype", "")).lstrip("/") or "unknown"
                        for key, label in _ANNOT_TEXT_KEYS:
                            if key in a:
                                val = str(a[key])
                                if val:
                                    annot_items.append((pi, subtype, label, val))
                    except Exception:
                        continue

            for obj in pdf.objects:
                try:
                    if isinstance(obj, pikepdf.Dictionary) and "/V" in obj:
                        field_values.append(str(obj["/V"]))
                except Exception:
                    continue
    except Exception as exc:
        log.debug("pikepdf cross-check of %s failed: %s", path, exc)
        return f"{exc}"
    return info_items, field_values, annot_items


def _term_surface_checks(doc, output_path: str, terms: list[str],
                         case_sensitive: bool,
                         password: Optional[str]) -> list[VerificationCheck]:
    """One check per extractable surface, aggregated over all *terms*.

    Fail-closed at surface granularity: a page/stream/reader that raises
    records failure evidence on its check (like _rect_region_check does)
    instead of silently narrowing the search to nothing.
    """
    page_text_ev: list[str] = []
    raw_ev: list[str] = []
    annot_ev: list[str] = []
    widget_ev: list[str] = []
    link_ev: list[str] = []
    docinfo_ev: list[str] = []
    xmp_ev: list[str] = []
    toc_ev: list[str] = []
    emb_ev: list[str] = []

    pk = _pikepdf_scan(output_path, password)
    if isinstance(pk, str):
        cross_fail = (f"the pikepdf cross-check could not read the output "
                      f"({pk}); this surface could not be fully verified "
                      f"(document-level)")
        docinfo_ev.append(cross_fail)
        widget_ev.append(cross_fail)
        annot_ev.append(cross_fail)
        info_items, field_values, pk_annots = [], [], []
    else:
        info_items, field_values, pk_annots = pk

    # -- page-attributed surfaces (PyMuPDF walk) -----------------------
    for pno, page in enumerate(doc, 1):
        try:
            text = page.get_text()
        except Exception as exc:
            text = ""
            page_text_ev.append(f"page text on page {pno} could not be read "
                                f"for verification: {exc}")
        blobs = _page_content_blobs(doc, page)
        good = [bytes(b) for b in blobs if isinstance(b, (bytes, bytearray))]
        if len(good) != len(blobs):
            # xref_stream() returns None (no exception) for a non-stream
            # /Contents xref — treat "stream unreadable" as failed, and
            # never let a None reach b"".join (that raised before).
            raw_ev.append(f"{len(blobs) - len(good)} content stream(s) on "
                          f"page {pno} could not be read for verification")
        raw = b"\n".join(good)
        try:
            links = page.get_links()
        except Exception as exc:
            links = []
            link_ev.append(f"links on page {pno} could not be read for "
                           f"verification: {exc}")
        try:
            annots = list(page.annots() or [])
        except Exception as exc:
            annots = []
            annot_ev.append(f"annotations on page {pno} could not be read "
                            f"for verification: {exc}")
        try:
            widgets = list(page.widgets() or [])
        except Exception as exc:
            widgets = []
            widget_ev.append(f"form widgets on page {pno} could not be read "
                             f"for verification: {exc}")

        for term in terms:
            found, err = _term_in_page_text(page, text, term, case_sensitive)
            if err:
                page_text_ev.append(f"page text on page {pno} could not be "
                                    f"fully verified for term '{term}': {err}")
            elif found:
                page_text_ev.append(f"term '{term}' is still extractable "
                                    f"from page text on page {pno}")
            if _bytes_contain(raw, term, case_sensitive):
                raw_ev.append(
                    f"term '{term}' found in a raw content stream on page {pno}")
            for annot in annots:
                info = annot.info or {}
                for fld in ("content", "title", "subject"):
                    val = info.get(fld) or ""
                    if val and _contains(val, term, case_sensitive):
                        annot_ev.append(
                            f"term '{term}' found in {annot.type[1]} "
                            f"annotation {fld} on page {pno}")
            for w in widgets:
                val = "" if w.field_value is None else str(w.field_value)
                if val and _contains(val, term, case_sensitive):
                    widget_ev.append(
                        f"term '{term}' found in form field '{w.field_name}' "
                        f"value on page {pno}")
            for lnk in links:
                for key in ("uri", "file"):
                    val = str(lnk.get(key) or "")
                    if val and _contains(val, term, case_sensitive):
                        link_ev.append(
                            f"term '{term}' found in link {key} on page {pno}")

    # -- pikepdf cross-check surfaces ----------------------------------
    for term in terms:
        for pi, subtype, label, val in pk_annots:
            if _contains(val, term, case_sensitive):
                annot_ev.append(f"term '{term}' found in {subtype} "
                                f"annotation {label} on page {pi}")
        # Only add the document-level /V evidence when the page-widget walk
        # did not already localize this term, so hits aren't double-reported.
        if not any(f"'{term}'" in e for e in widget_ev):
            for val in field_values:
                if val and _contains(val, term, case_sensitive):
                    widget_ev.append(
                        f"term '{term}' found in a form field value not "
                        f"attached to any page widget (document-level)")
                    break

    # -- document-level surfaces ---------------------------------------
    meta = doc.metadata or {}
    fitz_info = [(k, meta.get(k) or "") for k in _DOCINFO_KEYS]
    for term in terms:
        for key, val in fitz_info + info_items:
            if val and _contains(val, term, case_sensitive):
                docinfo_ev.append(
                    f"term '{term}' found in document info field '{key}' "
                    f"(document-level)")

    try:
        xml = doc.get_xml_metadata() or ""
    except Exception as exc:
        xml = ""
        xmp_ev.append(f"the XMP metadata packet could not be read for "
                      f"verification: {exc} (document-level)")
    for term in terms:
        if xml and _contains(xml, term, case_sensitive):
            xmp_ev.append(
                f"term '{term}' found in the XMP metadata packet (document-level)")

    try:
        toc = doc.get_toc(simple=True) or []
    except Exception as exc:
        toc = []
        toc_ev.append(f"bookmark titles could not be read for "
                      f"verification: {exc} (document-level)")
    for term in terms:
        for entry in toc:
            title, tgt = str(entry[1]), entry[2]
            if title and _contains(title, term, case_sensitive):
                toc_ev.append(
                    f"term '{term}' found in a bookmark title targeting "
                    f"page {tgt} (document-level)")

    try:
        emb_names = doc.embfile_names() or []
    except Exception as exc:
        emb_names = []
        emb_ev.append(f"embedded file names could not be read for "
                      f"verification: {exc} (document-level)")
    for term in terms:
        for name in emb_names:
            if _contains(str(name), term, case_sensitive):
                emb_ev.append(
                    f"term '{term}' found in embedded file name '{name}' "
                    f"(document-level)")

    def check(cid: str, desc: str, ev: list[str]) -> VerificationCheck:
        ev = _dedupe(ev)
        return VerificationCheck(id=cid, description=desc,
                                 passed=not ev, evidence=ev)

    return [
        check("redaction.page_text",
              "No target term is extractable from page text", page_text_ev),
        check("redaction.raw_content",
              "No target term appears in raw content-stream bytes", raw_ev),
        check("redaction.annotations",
              "No target term appears in annotation text or titles", annot_ev),
        check("redaction.form_fields",
              "No target term appears in form-field values", widget_ev),
        check("redaction.links",
              "No target term appears in link targets", link_ev),
        check("redaction.docinfo",
              "No target term appears in document info metadata", docinfo_ev),
        check("redaction.xmp",
              "No target term appears in XMP metadata", xmp_ev),
        check("redaction.bookmarks",
              "No target term appears in bookmark titles", toc_ev),
        check("redaction.embedded_files",
              "No target term appears in embedded file names", emb_ev),
    ]


def _rect_region_check(doc, rects: list[dict]) -> VerificationCheck:
    """Clipped extraction over each redaction rectangle must yield no text.

    Rect entries mirror redact_pdf's shape: {"page": int (0-based), "x0",
    "y0", "x1", "y1"} in PDF points. A malformed, out-of-range, inverted,
    or empty entry is a FAILED verification (it could not be checked), not
    a silent skip — redact_pdf skips exactly the invalid/empty rects
    (pdf_ops.py's is_valid/is_empty guard), so an unchecked one here would
    hand an unredacted region a passing proof.
    """
    ev: list[str] = []
    for r in rects:
        try:
            pno = int(r["page"])
            rect = fitz.Rect(float(r["x0"]), float(r["y0"]),
                             float(r["x1"]), float(r["y1"]))
        except Exception:
            ev.append(f"malformed rect entry {r!r} could not be verified")
            continue
        if pno < 0 or pno >= len(doc):
            ev.append(f"rect targets out-of-range page {pno + 1} "
                      f"and could not be verified")
            continue
        if not rect.is_valid or rect.is_empty:
            ev.append(f"invalid or empty rect on page {pno + 1} could not be "
                      f"verified (redaction would have skipped it)")
            continue
        try:
            found = doc[pno].get_text(clip=rect).strip()
        except Exception as exc:
            ev.append(f"rect on page {pno + 1} could not be verified: {exc}")
            continue
        if found:
            preview = " ".join(found.split())[:60]
            ev.append(f"text still extractable inside redaction rect on "
                      f"page {pno + 1}: '{preview}'")
    return VerificationCheck(
        id="redaction.rect_regions",
        description="No text is extractable inside any redaction rectangle",
        passed=not ev, evidence=ev)


def verify_redaction(output_path: str, terms: Optional[list[str]] = None, *,
                     rects: Optional[list[dict]] = None,
                     case_sensitive: bool = False,
                     password: Optional[str] = None,
                     input_path: str = "") -> VerificationReport:
    """Independently confirm a redacted output no longer carries its targets.

    Mirrors redact_pdf's selection model: *terms* (search-term redactions)
    and/or *rects* (user-drawn boxes, same dict shape) — at least one
    required. Term mode searches every extractable surface; rect mode
    verifies via clipped extraction so rect-only redactions aren't a blind
    spot. *input_path* is carried into the report verbatim for the caller
    (#99) — verification itself never reads the input file.

    Never returns a false pass: an output that cannot be opened or
    decrypted yields a report with a failed ``redaction.readable`` check
    (``verified`` False), not an exception and not a success.
    """
    terms = [t for t in (terms or []) if t and t.strip()]
    rects = list(rects or [])
    if not terms and not rects:
        raise ValueError("Provide terms and/or rects to verify.")

    report = VerificationReport(input_path=input_path, output_path=output_path,
                                tool="redaction", timestamp=_now_iso())

    if not os.path.isfile(output_path):
        raise FileNotFoundError(output_path)

    readable = VerificationCheck(
        id="redaction.readable",
        description="Output opens and decrypts for verification", passed=True)
    try:
        doc = fitz.open(output_path)
    except Exception as exc:
        readable.passed = False
        readable.evidence = [f"output could not be opened: {exc} (document-level)"]
        report.checks.append(readable)
        return report
    if doc.needs_pass and not (password and doc.authenticate(password)):
        doc.close()
        readable.passed = False
        readable.evidence = ["output is password-protected and could not be "
                             "decrypted for verification (document-level)"]
        report.checks.append(readable)
        return report
    report.checks.append(readable)

    try:
        if terms:
            report.checks.extend(_term_surface_checks(
                doc, output_path, terms, case_sensitive, password))
        if rects:
            report.checks.append(_rect_region_check(doc, rects))
    finally:
        doc.close()
    return report


# ════════════════════════════════════════════════════════════════════
#  Sanitization verification
# ════════════════════════════════════════════════════════════════════

#: Sanitize option -> analyzer finding id(s) its removal promises to clear.
#: Expectations-based on purpose: the analyzer flags categories the
#: sanitizer has no removal path for (actions.remote, forms.xfa,
#: content.ocg, content.invisible_text, location.embedded_image — the
#: known R1 gaps, tracked as #111/#112). Asserting "zero findings" would
#: fail every legitimate run; those surface as residual_findings instead.
_SANITIZE_PROMISES = {
    "javascript":     ("scripts.js",),
    "launch_actions": ("actions.launch",),
    "auto_actions":   ("actions.autorun",),
    "submit_actions": ("forms.submit",),
    "embedded_files": ("files.embedded",),
    "external_links": ("links.uri",),
    "metadata":       ("meta.docinfo", "meta.xmp"),
}


def verify_sanitization(output_path: str, options: Optional[dict] = None, *,
                        input_path: str = "") -> VerificationReport:
    """Re-audit a sanitized output and assert the promised removals held.

    *options* is the same dict passed to ``sanitize_pdf`` (missing keys
    default via ``DEFAULT_SANITIZE``). For each ENABLED option, the mapped
    finding id(s) must be absent from a fresh ``analyze_document`` run on
    the output. Findings outside the promised set are returned in
    ``residual_findings`` — honest residue, not a failure.

    Fail-closed: a locked or unreadable output yields a failed
    ``sanitization.readable`` check, never a pass (a locked file re-scans
    as a single info finding with nothing inspected — that must not count
    as clean).

    Known limitation (documented, not silently ignored): the re-scan is the
    oracle. ``analyze_document`` deliberately survives a crashing scanner by
    logging a warning and continuing, and a scanner that crashed emits no
    findings for its category — so this function is exactly as strong as
    the analyzer run it consumes.
    """
    opts = {**DEFAULT_SANITIZE, **(options or {})}
    report = VerificationReport(input_path=input_path, output_path=output_path,
                                tool="sanitization", timestamp=_now_iso())

    readable = VerificationCheck(
        id="sanitization.readable",
        description="Output opens and re-scans for verification", passed=True)
    try:
        scan = analyze_document(output_path)
    except FileNotFoundError:
        raise
    except Exception as exc:
        readable.passed = False
        readable.evidence = [f"output could not be re-scanned: {exc} "
                             f"(document-level)"]
        report.checks.append(readable)
        return report

    findings = scan.get("findings", [])
    by_id: dict[str, dict] = {}
    for f in findings:
        by_id.setdefault(f["id"], f)

    if "encryption.locked" in by_id:
        readable.passed = False
        readable.evidence = ["output is password-protected; the re-scan could "
                             "not inspect its contents (document-level)"]
        report.checks.append(readable)
        return report
    report.checks.append(readable)

    promised: set[str] = set()
    for opt, fids in _SANITIZE_PROMISES.items():
        if not opts.get(opt):
            continue
        promised.update(fids)
        for fid in fids:
            found = by_id.get(fid)
            ev: list[str] = []
            if found is not None:
                ev.append(f"'{found['title']}' still detected in the output")
                ev.extend(str(item) for item in found.get("items", [])[:20])
            report.checks.append(VerificationCheck(
                id="sanitize." + fid.replace(".", "_"),
                description=f"Re-scan finds no '{fid}' finding "
                            f"(promised by option '{opt}')",
                passed=found is None, evidence=ev))

    report.residual_findings = [
        f for f in findings if f["id"] not in promised and f["id"] != "clean"
    ]
    return report
