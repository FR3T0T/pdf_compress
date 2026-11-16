"""
pdf_verify — Post-operation verification: prove removal instead of trusting it.

The verification loop behind the toolkit's "provable sanitization" promise.
After a destructive transform (``redact_pdf``, ``sanitize_pdf``) writes its
output, these functions re-open the OUTPUT file and independently confirm
the targeted content is actually gone, emitting a structured
:class:`VerificationReport` rather than trusting the transform library.
``redact_pdf`` verifies before it commits: the file it hands over is a temp
candidate beside the destination, renamed into place only if it passes
(:meth:`VerificationReport.retarget` then names the real destination):

  - :func:`verify_redaction` — searches every extractable surface of the
    output (page text, raw content streams, annotations, form-field values,
    link URIs, docinfo, XMP, bookmarks, embedded-file names) for each
    redacted term, and checks user-drawn redaction rectangles via clipped
    extraction. Surfaces are read through TWO independent libraries where
    possible: PyMuPDF (the renderer's view) plus a pikepdf pass over the raw
    object graph (all /Info keys, every /V field value, every annotation's
    /Contents//T//Subj — including annotation subtypes PyMuPDF skips), so a
    leak invisible to one library is still caught by the other. When the
    caller supplies the source's trailer /ID elements it also asserts the
    output carries none of them (``redaction.file_id``): a cleaned copy that
    keeps its source's identifier is still linkable to it.
  - :func:`verify_sanitization` — re-runs the ``pdf_analyze`` audit on the
    output and asserts absent exactly the finding categories the enabled
    sanitize options promised to remove (expectations-based — anything else
    the re-scan still flags is reported honestly as a residual finding, not
    a failure). It then sweeps every object in the output independently of
    the analyzer (:func:`walk_objects`, :func:`_sweep`), so a promised thing
    sitting where the analyzer has no site is still found, and a sweep that
    could not cover the whole file fails rather than passes.

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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import fitz  # PyMuPDF
import pikepdf

from pdf_analyze import (
    DEFAULT_SANITIZE,
    analyze_document,
    file_sha256,
    page_content_blobs,
    trailer_ids,
)

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
#  Result types
# ════════════════════════════════════════════════════════════════════

#: Extracts the page number from a page-attributed evidence line. Only
#: matches the "on page N" form the module's own surface checks emit; the
#: bookmark surface's "targeting page N (document-level)" is deliberately
#: NOT this form, so a document-level residual never looks page-attributed
#: (which would wrongly make it flatten-eligible under D1).
_ON_PAGE_RE = re.compile(r"\bon page (\d+)\b")


@dataclass
class VerificationCheck:
    """One asserted expectation about the output file."""
    id: str                # e.g. "redaction.page_text", "sanitize.scripts_js"
    description: str
    passed: bool
    evidence: list[str] = field(default_factory=list)  # each line names surface + page
    #: Explicit page numbers (1-based) this check failed on, derived from
    #: the page-attributed evidence lines. Empty for a passing check or a
    #: purely document-level residual. Structured so callers (the #99
    #: fail-closed flatten targeting) never parse evidence strings.
    pages: list[int] = field(default_factory=list)

    def __post_init__(self):
        if not self.pages:
            self.pages = self._derive_pages(self.evidence)

    @staticmethod
    def _derive_pages(evidence: list[str]) -> list[int]:
        pages: list[int] = []
        for line in evidence:
            if "(document-level)" in line:
                continue          # doc-level residual — no page, no flatten
            m = _ON_PAGE_RE.search(line)
            if m:
                p = int(m.group(1))
                if p not in pages:
                    pages.append(p)
        return sorted(pages)

    @property
    def is_document_level(self) -> bool:
        """True if any failure evidence is a document-level residual — the
        D1 signal that flatten-to-image cannot fix this check."""
        return any("(document-level)" in e for e in self.evidence)


@dataclass
class VerificationReport:
    input_path: str
    output_path: str
    tool: str              # "redaction" | "sanitization"
    timestamp: str         # ISO 8601
    checks: list[VerificationCheck] = field(default_factory=list)
    residual_findings: list[dict] = field(default_factory=list)
    #: SHA-256 of the source file, as supplied by the caller (None when the
    #: caller supplied none — the verifier never opens the input to find out).
    source_sha256: Optional[str] = None
    #: SHA-256 of the file the checks ran on (None if it could not be read).
    output_sha256: Optional[str] = None

    @property
    def verified(self) -> bool:
        """True iff at least one check ran and every check passed.

        An empty check list is NOT verified — a report that asserted nothing
        must never read as proof.
        """
        return bool(self.checks) and all(c.passed for c in self.checks)

    def failed_checks(self) -> list[VerificationCheck]:
        return [c for c in self.checks if not c.passed]

    def flatten_target_pages(self) -> Optional[list[int]]:
        """The pages a flatten-to-image retry should rasterize, or None when
        flatten is not offerable (D1).

        Returns None if there are no failures, or if ANY failed check is
        document-level (docinfo/XMP/bookmark/embedded-name/orphan-/V or an
        unreadable-surface gate) — flatten physically cannot fix those, so
        the caller must hard-raise instead of offering it. Otherwise every
        failure is page-attributed and the union of their pages is the
        rasterization target.
        """
        failed = self.failed_checks()
        if not failed:
            return None
        if any(c.is_document_level for c in failed):
            return None
        pages: set[int] = set()
        for c in failed:
            if not c.pages:
                return None       # a page-content failure we can't localize
            pages.update(c.pages)
        return sorted(pages)

    def retarget(self, real_path: str) -> None:
        """Name *real_path* as the output instead of the temp the checks ran on.

        Verify-then-commit verifies a candidate beside the destination and
        renames it into place only if it passed, so the path this report was
        built with is a temp that is gone either way. Evidence text is
        rewritten too — library error messages quote the path they were
        given. Only the basename is swapped: the candidate sits in the
        destination's own directory, so that covers every spelling of it.
        """
        old, new = os.path.basename(self.output_path), os.path.basename(real_path)
        if old and old != new:
            for c in self.checks:
                c.evidence = [e.replace(old, new) for e in c.evidence]
        self.output_path = real_path

    def to_dict(self) -> dict:
        """JSON-ready dict, camelCase keys — mirrors AnalysisResult.to_dict()."""
        return {
            "inputPath": self.input_path,
            "outputPath": self.output_path,
            "tool": self.tool,
            "timestamp": self.timestamp,
            "checks": [
                {"id": c.id, "description": c.description,
                 "passed": c.passed, "evidence": list(c.evidence),
                 "pages": list(c.pages),
                 "isDocumentLevel": c.is_document_level}
                for c in self.checks
            ],
            "residualFindings": self.residual_findings,
            "verified": self.verified,
            "flattenTargetPages": self.flatten_target_pages(),
            "sourceSha256": self.source_sha256,
            "outputSha256": self.output_sha256,
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


def _output_sha256(path: str) -> Optional[str]:
    try:
        return file_sha256(path)
    except OSError:
        return None


def _file_id_check(cid: str, output_path: str, source_ids,
                   source_sha256: Optional[str], output_sha256: Optional[str],
                   password: Optional[str] = None) -> VerificationCheck:
    """The output must not carry any of its source's trailer /ID elements.

    A shared element links the cleaned copy back to the original no matter
    what else was removed. *source_ids* are hex strings the CALLER captured
    while it had the input open: the verifier is handed values, never a path
    to read, so it needs neither the input nor the input's password and has
    no "input could not be read" case.

    One exemption: an output whose SHA-256 equals the source's IS the source
    (cleaning an already-clean file changes no byte), not a second file that
    can be linked to it.

    Always document-level — a trailer entry is on no page, so flatten is
    never offered for this failure (D1).
    """
    desc = "Output does not carry the source file's identifier (/ID)"
    kwargs = {"password": password} if password else {}
    try:
        with pikepdf.open(output_path, **kwargs) as pdf:
            out_ids = trailer_ids(pdf)
    except Exception as exc:
        return VerificationCheck(id=cid, description=desc, passed=False, evidence=[
            f"the output's file identifier could not be read for "
            f"verification: {exc} (document-level)"])
    ev: list[str] = []
    if not (source_sha256 and output_sha256 == source_sha256):
        src = {str(s).lower() for s in source_ids if s}
        ev = [f"the output's trailer /ID still carries the source file's "
              f"identifier {i}, which links it to the original (document-level)"
              for i in _dedupe(out_ids) if i and i in src]
    return VerificationCheck(id=cid, description=desc, passed=not ev, evidence=ev)


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
        blobs = page_content_blobs(doc, page)
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
                     input_path: str = "",
                     source_ids: Optional[list[str]] = None,
                     source_sha256: Optional[str] = None) -> VerificationReport:
    """Independently confirm a redacted output no longer carries its targets.

    Mirrors redact_pdf's selection model: *terms* (search-term redactions)
    and/or *rects* (user-drawn boxes, same dict shape) — at least one
    required. Term mode searches every extractable surface; rect mode
    verifies via clipped extraction so rect-only redactions aren't a blind
    spot. *input_path* is carried into the report verbatim for the caller
    (#99) — verification itself never reads the input file.

    *source_ids* (the source's trailer /ID elements, hex) and
    *source_sha256* are plain values the caller captured while it had the
    input open. When *source_ids* is given — an empty list means "the source
    had no /ID", None means "not supplied" — a ``redaction.file_id`` check
    asserts the output shares none of them (see :func:`_file_id_check`).
    Both hashes are recorded on the report.

    Never returns a false pass: an output that cannot be opened or
    decrypted yields a report with a failed ``redaction.readable`` check
    (``verified`` False), not an exception and not a success.
    """
    terms = [t for t in (terms or []) if t and t.strip()]
    rects = list(rects or [])
    if not terms and not rects:
        raise ValueError("Provide terms and/or rects to verify.")

    report = VerificationReport(input_path=input_path, output_path=output_path,
                                tool="redaction", timestamp=_now_iso(),
                                source_sha256=source_sha256)

    if not os.path.isfile(output_path):
        raise FileNotFoundError(output_path)
    report.output_sha256 = _output_sha256(output_path)

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
    if source_ids is not None:
        report.checks.append(_file_id_check(
            "redaction.file_id", output_path, source_ids, source_sha256,
            report.output_sha256, password))
    return report


# ════════════════════════════════════════════════════════════════════
#  Sanitization verification
# ════════════════════════════════════════════════════════════════════

#: Sanitize option -> analyzer finding id(s) its removal promises to clear.
#: Expectations-based on purpose: the analyzer flags categories the
#: sanitizer has no removal path for (actions.remote, forms.xfa,
#: content.ocg, content.invisible_text, location.embedded_image — the
#: known R1 gaps, tracked as #111/#112 — and files.external, a reference to
#: a file outside the PDF, which is nothing to remove). Asserting "zero findings" would
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


# ── Whole-file sweep ─────────────────────────────────────────────────
#
#  The promise checks above re-run the analyzer, so on their own they are
#  only as good as the list of places the analyzer knows to look. The sweep
#  is the second, independent look: it visits every object the output
#  contains and asks of each one what it is, sharing no code with the
#  analyzer's site walk. Something the analyzer has no site for is still an
#  object in the file, and is found here.

@dataclass
class WalkStatus:
    """What a walk covered. Anything that kept it from covering the whole
    file is recorded in ``problems``; an empty list means it saw everything."""
    visited: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems


def render_path(path) -> str:
    """Text for a path node from :func:`walk_objects`, e.g.
    ``trailer/Root/Pages/Kids[0]/Annots[2]/A``. A run of one repeated
    segment — a long /Next chain — is folded: ``/Next(x4000)``."""
    segs = []
    while path is not None:
        segs.append(path[0])
        path = path[1]
    segs.reverse()
    out, i = [], 0
    while i < len(segs):
        j = i
        while j < len(segs) and segs[j] == segs[i]:
            j += 1
        out.append(segs[i] if j - i == 1 else f"{segs[i]}(x{j - i})")
        i = j
    return "".join(out)


def walk_objects(pdf, status: WalkStatus, *, strings: bool = False,
                 max_nodes: Optional[int] = None):
    """Yield ``(path, obj)`` for every dictionary and stream reachable from
    the trailer — and, with *strings*, every string — each indirect object
    once.

    Built for the sanitization sweep, and meant for verify_redaction to
    reuse for a term search over every string and stream in a file: pass
    ``strings=True`` and read the streams it yields.

    Iterative, not recursive. PDF structures are linked lists as often as
    trees — a /Next chain of actions, a run of sibling bookmarks — and a
    file can make one as long as it likes; a recursive walk dies on it.
    Descends into direct dictionaries and arrays and into stream
    dictionaries, and is cycle-safe.

    Never silently partial. Whatever stops the walk short of the whole file
    — an object that raises when read, or the optional *max_nodes* limit —
    is recorded on *status*. A caller for whom "nothing found" has to mean
    something must check ``status.complete``.

    *path* is an opaque node; :func:`render_path` turns it into text.
    """
    containers = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)
    seen: set = set()
    stack = [(pdf.trailer, ("trailer", None))]
    while stack:
        obj, path = stack.pop()
        try:
            if not isinstance(obj, containers):
                continue
            if obj.is_indirect:
                key = tuple(obj.objgen)
                if key in seen:
                    continue
                seen.add(key)
            status.visited += 1
            if max_nodes is not None and status.visited > max_nodes:
                status.problems.append(
                    f"stopped after {max_nodes} objects; the rest of the file "
                    f"was not inspected")
                return
            if isinstance(obj, pikepdf.Array):
                children = [(f"[{i}]", v) for i, v in enumerate(obj)]
            else:
                yield path, obj
                children = [(str(k), obj[k]) for k in obj.keys()]
            for seg, child in reversed(children):
                if isinstance(child, containers):
                    stack.append((child, (seg, path)))
                elif strings and isinstance(child, pikepdf.String):
                    yield (seg, path), child
        except Exception as exc:
            status.problems.append(f"could not read {render_path(path)}: {exc}")


#: Sanitize options the sweep has a predicate for (all of them, today).
_SWEEP_OPTIONS = ("javascript", "launch_actions", "auto_actions",
                  "submit_actions", "embedded_files", "external_links",
                  "metadata")

#: Action /S value -> (option that removes it, what to call it in evidence).
_SWEEP_ACTION_TYPES = {
    "/JavaScript": ("javascript", "a JavaScript action"),
    "/Launch": ("launch_actions", "a launch action"),
    "/SubmitForm": ("submit_actions", "a submit-form action"),
    "/ImportData": ("submit_actions", "an import-data action"),
    "/URI": ("external_links", "a URI action"),
}

#: The trigger names PDF 32000 defines for additional-actions dictionaries
#: (catalog, page, annotation, form field).
_AA_TRIGGERS = frozenset({
    "/WC", "/WS", "/DS", "/WP", "/DP", "/O", "/C", "/E", "/X", "/D", "/U",
    "/Fo", "/Bl", "/PO", "/PC", "/PV", "/PI", "/K", "/F", "/V"})

_EF_KEYS = ("/F", "/UF", "/DOS", "/Mac", "/Unix")


def _sweep(pdf, opts: dict) -> tuple[dict[str, list[str]], WalkStatus]:
    """Look at every object in *pdf* for anything an enabled option removes.

    Returns ``({option: [evidence, ...]}, status)`` for the enabled options.

    Every predicate asks what an object IS, never what it is called. A key
    name proves nothing: resource dictionaries map arbitrary names to fonts,
    images and graphics states, and a producer that names them A, B, ... will
    get to /AA, /EF and /JS; an image may be named /Metadata. So nothing here
    fires on a key alone.

    What each option's predicate covers, and what it knowingly does not:

    javascript
        A dictionary whose /S is /JavaScript, anywhere. A /JS entry that is
        a string; a /JS entry that is a stream on a dictionary with a
        name-valued /S (a rendition action carries its script that way). The
        catalog's /Names /JavaScript tree, by position. A stream typed /3D
        with an /OnInstantiate stream — a script run when the 3D artwork
        loads, which is not an action at all (Sanitize deletes it by the
        same test).
        Not covered: scripts inside an XFA form packet (#112); a /JS stream
        on a dictionary with no /S, which is not an action and is not run;
        scripts inside embedded files (that is `embedded_files`).
    launch_actions
        A dictionary whose /S is /Launch.
        Not covered: /GoToR and /GoToE, which open other documents (#112).
    submit_actions
        A dictionary whose /S is /SubmitForm or /ImportData.
        Not covered: submission configured inside an XFA packet (#112).
    external_links
        A dictionary whose /S is /URI.
        Not covered: URLs that are not URI actions — text on the page, the
        catalog's /URI base, a URL file specification, a submit target.
    auto_actions
        The catalog's /OpenAction when it is a dictionary (an action). An
        /AA dictionary holding, under one of the standard trigger names, a
        dictionary with a name-valued /S.
        Not covered: an /OpenAction that is a destination (an array or a
        name) — it sets where the document opens and runs nothing; an /AA
        entry under a trigger name the standard does not define.
    embedded_files
        A dictionary whose /EF is a dictionary holding a stream under /F,
        /UF, /DOS, /Mac or /Unix. A stream typed /EmbeddedFile. The
        catalog's /Names /EmbeddedFiles tree, by position.
        Not covered: a file specification with no payload, which only names
        a file elsewhere (reported by the analyzer as `files.external`).
    metadata
        A stream typed /Metadata or /XML, on any object. Whatever stream is
        in the catalog's /Metadata slot, typed or not. A trailer /Info with
        any entry.
        Not covered: EXIF inside embedded images (#111); private application
        data such as /PieceInfo; the file identifier (checked separately).

    A hit at a place the analyzer has no site for is still a hit: the option
    promised the thing would be gone from the file, not from a list of
    places.
    """
    hits: dict[str, list[str]] = {o: [] for o in _SWEEP_OPTIONS if opts.get(o)}
    status = WalkStatus()

    def hit(option: str, what: str, path) -> None:
        if option in hits:
            hits[option].append(f"{what} at {render_path(path)}")

    def typed(obj, key: str) -> str:
        v = obj.get(key)
        return str(v) if isinstance(v, pikepdf.Name) else ""

    # -- the few things that are defined by where they are --------------
    trailer_path = ("trailer", None)
    root_path = ("/Root", trailer_path)
    try:
        root = pdf.Root
        if isinstance(root.get("/OpenAction"), pikepdf.Dictionary):
            hit("auto_actions", "an action", ("/OpenAction", root_path))
        names = root.get("/Names")
        if isinstance(names, pikepdf.Dictionary):
            if "/JavaScript" in names:
                hit("javascript", "a named-JavaScript tree",
                    ("/JavaScript", ("/Names", root_path)))
            if "/EmbeddedFiles" in names:
                hit("embedded_files", "an embedded-files tree",
                    ("/EmbeddedFiles", ("/Names", root_path)))
        if isinstance(root.get("/Metadata"), pikepdf.Stream):
            hit("metadata", "a metadata stream", ("/Metadata", root_path))
        info = pdf.trailer.get("/Info")
        if isinstance(info, pikepdf.Dictionary) and len(info.keys()):
            hit("metadata", f"a document-info dictionary with "
                            f"{len(info.keys())} entries", ("/Info", trailer_path))
    except Exception as exc:
        status.problems.append(f"could not read the document catalog: {exc}")

    # -- everything else, by what the object is -------------------------
    for path, obj in walk_objects(pdf, status):
        try:
            keys = obj.keys()
            stype = typed(obj, "/S") if "/S" in keys else ""
            if stype in _SWEEP_ACTION_TYPES:
                hit(*_SWEEP_ACTION_TYPES[stype], path)
            if "/JS" in keys and stype != "/JavaScript":
                js = obj.get("/JS")
                if isinstance(js, pikepdf.String) or (
                        isinstance(js, pikepdf.Stream) and stype):
                    hit("javascript", "a script (/JS)", path)
            if "/AA" in keys and "auto_actions" in hits:
                aa = obj.get("/AA")
                if isinstance(aa, pikepdf.Dictionary):
                    for trig in aa.keys():
                        entry = aa[trig] if str(trig) in _AA_TRIGGERS else None
                        if isinstance(entry, pikepdf.Dictionary) and typed(entry, "/S"):
                            hit("auto_actions", f"an additional action ({trig})",
                                ("/AA", path))
            if "/EF" in keys and "embedded_files" in hits:
                ef = obj.get("/EF")
                if isinstance(ef, pikepdf.Dictionary) and any(
                        isinstance(ef.get(k), pikepdf.Stream) for k in _EF_KEYS):
                    hit("embedded_files", "an embedded file", path)
            if isinstance(obj, pikepdf.Stream):
                kind, subtype = typed(obj, "/Type"), typed(obj, "/Subtype")
                if kind == "/Metadata" or subtype == "/XML":
                    hit("metadata", "a metadata stream", path)
                if kind == "/EmbeddedFile":
                    hit("embedded_files", "an embedded file stream", path)
                if kind == "/3D" and isinstance(obj.get("/OnInstantiate"), pikepdf.Stream):
                    hit("javascript", "a 3D stream's /OnInstantiate script", path)
        except Exception as exc:
            status.problems.append(f"could not inspect {render_path(path)}: {exc}")
    return hits, status


def _sweep_checks(output_path: str, opts: dict) -> list[VerificationCheck]:
    """One check per enabled option: the sweep found nothing it removes.

    Fail-closed twice over. A hit fails the option's check, with the object
    path as evidence. And a sweep that did not cover the whole file — the
    output would not open, an object could not be read, the walk was cut
    short — fails EVERY check: what it did not look at cannot be reported
    absent, so a partial walk never passes.
    """
    enabled = [o for o in _SWEEP_OPTIONS if opts.get(o)]
    if not enabled:
        return []
    try:
        with pikepdf.open(output_path) as pdf:
            hits, status = _sweep(pdf, opts)
        problems = list(status.problems)
    except Exception as exc:
        hits, problems = {}, [f"the output could not be opened: {exc}"]
    incomplete = [f"the sweep did not cover the whole file ({p}); what it "
                  f"did not inspect cannot be reported absent (document-level)"
                  for p in problems[:5]]
    checks = []
    for opt in enabled:
        ev = _dedupe(hits.get(opt, []))
        if len(ev) > 20:
            ev = ev[:20] + [f"... and {len(ev) - 20} more"]
        ev += incomplete
        checks.append(VerificationCheck(
            id=f"sanitize.sweep_{opt}",
            description=f"Whole-file sweep finds nothing option '{opt}' removes",
            passed=not ev, evidence=ev))
    return checks


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

    # The second, independent look: every object in the output, judged by
    # what it is rather than by where the analyzer expects to find it.
    report.checks.extend(_sweep_checks(output_path, opts))

    report.residual_findings = [
        f for f in findings if f["id"] not in promised and f["id"] != "clean"
    ]
    return report
