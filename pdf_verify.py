"""
pdf_verify — Post-operation verification: prove removal instead of trusting it.

The verification loop behind the toolkit's "provable sanitization" promise.
After a destructive transform (``redact_pdf``, ``sanitize_pdf``) writes its
output, these functions re-open the OUTPUT file and independently confirm
the targeted content is actually gone, emitting a structured
:class:`VerificationReport` rather than trusting the transform library.
``redact_pdf`` and ``sanitize_pdf`` both verify before they commit: the file
they hand over is a temp candidate beside the destination, renamed into
place only if it passes (:meth:`VerificationReport.retarget` then names the
real destination). A candidate that does not pass is deleted and the
transform raises, with the report, having touched nothing at the destination:

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
    keeps its source's identifier is still linkable to it. And it takes a
    second, whole-file look for the terms (``redaction.term_sweep``, #119):
    every string in the output and the decoded bytes of every stream,
    wherever it hangs, read in every encoding text has in a PDF
    (:class:`TermFinder`). That is what finds a term in a place the surface
    checks have no name for — structure-tree text, an application's private
    data, a layer's name, a form no page paints. It does not read images,
    the internals of fonts and colour profiles, or PDF names (see the note
    above :func:`_term_sweep_check` for why, and for what that leaves).
    Page text is searched by :class:`PageText`, the one matcher
    ``redact_pdf`` also boxes by (:func:`term_boxes`): the page's
    characters with their boxes, looked at in four views so that a term is
    found across a line break, a line-end hyphen, letter-spacing and a
    ligature, where PyMuPDF's search_for is not. It reads text a clipping
    path hides. It cannot read text outside the page's visible box, or
    remove what an annotation's appearance draws: those are left to the
    raw-content check and the sweep, which refuse the run. For boxes drawn
    by hand, no character may be left under a box
    (``redaction.rect_regions``; :func:`chars_under` is the rule, the one
    ``redact_pdf`` removes by: the character's centre is in the box, or a
    fifth or more of its text box is). And there are two checks of what a
    box leaves that is not text: no annotation lies over it
    (``redaction.rect_annotations``; an annotation is painted above the
    page) and no drawing has an edge inside it
    (``redaction.rect_line_art``; a straight line that runs right through
    the box hides nothing under it and is not counted,
    :func:`has_edge_inside`). And every redaction, by term or by box,
    is checked for a stream the structure tree alone keeps in the file
    (``redaction.structure_streams``) and for page thumbnails
    (``redaction.thumbnails``).
  - :func:`verify_sanitization` — re-runs the ``pdf_analyze`` audit on the
    output and asserts absent exactly the finding categories the enabled
    sanitize options promised to remove (expectations-based — anything else
    the re-scan still flags is reported honestly as a residual finding, not
    a failure). A promised finding the audit could not fully evaluate fails
    its check, marked ``unevaluated``; an unpromised one is listed in the
    report's ``unevaluated`` field. It then sweeps every object in the
    output independently of the analyzer (:func:`walk_objects`,
    :func:`_sweep`), so a promised thing sitting where the analyzer has no
    site is still found, and a sweep that could not cover the whole file
    fails rather than passes. Sweep evidence is written for a reader who has
    never seen a PDF's object graph (:func:`plain_location`); the raw object
    path stays on the check as ``object_paths``. Given the source's trailer
    /ID elements it asserts, like the redaction side, that the output
    carries none of them (``sanitize.file_id``).
    :func:`sanitize_refusal_message` turns a failed report into the one
    sentence a refusal shows, and :func:`sanitize_refusal_detail` into the
    short lists a screen shows under it (what is still present, what could
    not be checked, the identifier case); ``pdf_analyze.sanitize_payload``
    hands both to the app. ``pdf_analyze`` runs the same sweep from
    Analyze (imported there lazily; this module imports that one), so what
    would be refused here is reported there first.

Known limits of the sanitize gate, each needing a file built for the purpose
(the README lists them for users):

  - Sanitize empties the trailer's /Info dictionary in place when it is
    nothing but document information, so a second reference to it keeps
    nothing. A dictionary of only strings, names, numbers and booleans, with
    no /Type, /Subtype or /S, that the trailer calls /Info *and* something
    else uses (a graphics state written without /Type) is emptied with it.
  - The other way round, an /Info also held under a second key that is not
    plain (it declares a /Type, or holds an array or a dictionary) is only
    unhooked from the trailer. Its entries stay in the output under the
    other key, and the output verifies: nothing there marks them as document
    information any more. Narrowing either rule widens the other.
  - ``sanitize.file_id`` and ``redaction.file_id`` compare the ELEMENTS of
    the two trailer /ID values (:func:`_file_id_check`). An identifier the
    source carries inside a nested array is not an element, and is not seen.

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

import contextlib
import logging
import math
import os
import re
import unicodedata
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple, Optional

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
    #: True when the check failed because it could not be carried out — the
    #: output would not open, a scanner did not finish, the sweep did not
    #: cover the whole file — rather than because it found something.
    #: "Could not look" is a failure, but a different one to report.
    unevaluated: bool = False
    #: Raw object paths (``trailer/Root/Pages/Kids[0]/...``) of what a sweep
    #: check found, in the order of its evidence lines. The evidence says the
    #: same thing in plain words; the paths are kept for an exported report.
    object_paths: list[str] = field(default_factory=list)
    #: False when turning the pages it failed on into pictures would not
    #: fix it: a term OCR still reads in a page's picture is in the picture
    #: of that picture too (#128). Such a failure makes the flatten offer
    #: None, as a document-level one does.
    flattenable: bool = True

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
    #: Findings that could not be fully evaluated and that no enabled option
    #: promised to remove: ``[{"id", "reasons"}]``. They fail nothing, but
    #: they are listed so the report never implies they were checked.
    unevaluated: list[dict] = field(default_factory=list)
    #: What went wrong inside the transform itself, if anything (it is
    #: tolerant, and the checks judge its result, so this is the "why" a
    #: refusal would otherwise lack). Names no file.
    diagnostics: list[str] = field(default_factory=list)

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
        if any(c.is_document_level or not c.flattenable for c in failed):
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
                 "isDocumentLevel": c.is_document_level,
                 "unevaluated": c.unevaluated,
                 "objectPaths": list(c.object_paths)}
                for c in self.checks
            ],
            "residualFindings": self.residual_findings,
            "verified": self.verified,
            "flattenTargetPages": self.flatten_target_pages(),
            "sourceSha256": self.source_sha256,
            "outputSha256": self.output_sha256,
            "unevaluated": [dict(u) for u in self.unevaluated],
            "diagnostics": list(self.diagnostics),
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


def _pdfdoc_table() -> dict:
    """Latin-1 code point -> what that byte is in PDFDocEncoding, for the
    bytes where the two differ (typographic quotes and dashes, the bullet,
    the euro, the fi and fl ligatures). From pikepdf's codec; empty if this
    pikepdf has none, which leaves the Latin-1 reading alone."""
    table = {}
    for i in range(256):
        try:
            ch = bytes([i]).decode("pdfdoc")
        except Exception:
            continue
        if ch != chr(i):
            table[i] = ch
    return table


_PDFDOC = _pdfdoc_table()


class TermFinder:
    """Finds target terms in raw bytes: a string object's, or a stream's
    decoded data.

    A PDF has no one text encoding, so the bytes are read every way text is
    written in one: Latin-1 and PDFDocEncoding (the same for ASCII), Windows
    code page 1252 (what most page content uses), UTF-8, and UTF-16 in both
    byte orders and at either alignment. Whitespace in a term matches any
    run of whitespace, a line break or a no-break space included; nothing
    else is bridged, so a term broken in the middle of a word is not found
    here (that is the page-text matcher's work). Case is folded unless
    *case_sensitive*.

    Shared by the verifier's sweep and by the scrub in pdf_ops, so the scrub
    removes by the same test it will be checked with.
    """

    def __init__(self, terms, case_sensitive: bool = False):
        self.terms = [t for t in (terms or []) if t and t.strip()]
        self._case = case_sensitive
        self._tests = []
        for term in self.terms:
            words = self._fold(term).split()
            if len(words) == 1:
                self._tests.append((term, words[0], None))
            else:
                self._tests.append((term, None, re.compile(r"\s+".join(map(re.escape, words)))))
        joined = "".join(self.terms)
        self._ascii = joined.isascii()
        self._latin1 = all(ord(c) < 256 for c in joined)
        # An ASCII word is the same bytes in UTF-8 as in Latin-1; only a term
        # of several words needs the UTF-8 reading, for the whitespace.
        self._utf8 = not (self._ascii and all(rx is None for _t, _w, rx in self._tests))

    def _fold(self, text: str) -> str:
        return text if self._case else text.casefold()

    def _readings(self, raw: bytes):
        latin = raw.decode("latin-1")
        yield latin
        if not self._ascii:
            if _PDFDOC:
                yield latin.translate(_PDFDOC)
            yield raw.decode("cp1252", "replace")
        if self._utf8:
            yield raw.decode("utf-8", "replace")
        if self._latin1:
            # UTF-16 of Latin-1 text is that text with a zero byte beside
            # every character: dropping the zeros reads it in either byte
            # order and at either alignment.
            if b"\x00" in raw:
                yield raw.replace(b"\x00", b"").decode("latin-1")
        else:
            for encoding in ("utf-16-be", "utf-16-le"):
                yield raw.decode(encoding, "replace")
                yield raw[1:].decode(encoding, "replace")

    def find(self, raw) -> list[str]:
        """The terms found in *raw*, in the order they were given."""
        raw = bytes(raw)
        if not raw or not self._tests:
            return []
        pending, found = list(self._tests), set()
        for reading in self._readings(raw):
            text = self._fold(reading)
            left = []
            for term, word, rx in pending:
                if (word in text) if rx is None else (rx.search(text) is not None):
                    found.add(term)
                else:
                    left.append((term, word, rx))
            pending = left
            if not pending:
                break
        return [t for t in self.terms if t in found]

    def __call__(self, raw) -> bool:
        return bool(self.find(raw))


# ════════════════════════════════════════════════════════════════════
#  The page-text matcher (#119, D3)
# ════════════════════════════════════════════════════════════════════
#
#  One matcher for the text of a page, used on both sides: redact_pdf boxes
#  what it finds, and verify_redaction confirms it finds nothing. It reads
#  the page's characters with their boxes (PyMuPDF's "rawdict") and does
#  its own matching, because search_for cannot be relied on for it. On
#  PyMuPDF 1.27 search_for finds no term that is broken across a line after
#  a hyphen, in any mode, and no letter-spaced one. On 2.1 it finds the
#  typesetter's hyphenation only, and its hit can stop one letter short of
#  the word.
#
#  A term is looked for in four views of the page's text:
#
#    spaced        every run of whitespace, a line break included, is one
#                  space — "Dana" at the end of a line and "Whitfield" at
#                  the start of the next;
#    dehyphenated  as spaced, but a hyphen that ends a line goes, with the
#                  break — "Dana Whit-" / "field";
#    joined        as spaced, but the break after a hyphen that ends a line
#                  goes and the hyphen stays — "4455-" / "887";
#    solid         no whitespace at all — "C O N F I D E N T I A L", a
#                  number printed in groups, an address broken at the end
#                  of a line with no hyphen. Accepted only where the match
#                  is whole words run together (PageText._solid_ok).
#
#  Both sides are folded the same way first: NFKC (a ligature is its
#  letters, a full-width letter the plain one, a no-break space a space),
#  the hyphen characters U+2010 and U+2011 become "-", and characters that
#  take no room — the soft hyphen, zero-width spaces and joiners, direction
#  marks — are dropped. Case is folded unless the search is case-sensitive.

#: Characters with no width of their own, dropped before matching.
_INVISIBLE = frozenset(map(chr, (
    0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF,                    # soft hyphen, zero width
    0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,            # direction marks
    0x2066, 0x2067, 0x2068, 0x2069)))
_SOFT_HYPHEN = chr(0x00AD)
_FOLD_TABLE = {0x2010: "-", 0x2011: "-", **{ord(c): None for c in _INVISIBLE}}

#: PageText views.
_SPACED, _DEHYPHENATED, _JOINED, _SOLID = range(4)

#: The straight apostrophe and the curly one (U+2019): see _solid_ok.
_APOSTROPHES = "'" + chr(0x2019)

#: Text flags for the matcher. What page.get_text() reads by default, less
#: one: TEXT_MEDIABOX_CLIP, which also drops text a clipping path hides. A
#: stamp cut off at "APPRO" by its clip still has "APPROVED" in the content
#: stream, where any other extractor reads it — and search_for, as redact_pdf
#: always called it, reads it too. Ligatures are kept whole (one character,
#: one box) and folded here.
_MATCH_FLAGS = (fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
                | getattr(fitz, "TEXT_CID_FOR_UNKNOWN_UNICODE", 0))    # not in old PyMuPDF


#: What the matcher needs of one character, worked out once per character
#: and not once per occurrence: (is whitespace, joins a word together). And
#: the folded form of each character or accented letter, case-sensitively
#: (True) and not. A page is some thousands of characters from an alphabet
#: of a hundred.
_CHAR_KINDS: dict = {}
_FOLDS: dict = {False: {}, True: {}}


def _fold_text(text: str, case_sensitive: bool) -> str:
    """*text* as the matcher compares it (see the note above)."""
    text = unicodedata.normalize("NFKC", text).translate(_FOLD_TABLE)
    if not case_sensitive:
        text = unicodedata.normalize("NFKC", text.casefold())
    return text


def _unspaced_script(ch: str) -> bool:
    """True for a character of a script written without spaces between
    words — Chinese, Japanese, Thai, Lao, Khmer, Burmese, Tibetan. A hit
    there is not widened to "the whole word": the nearest spaces are the
    ends of the sentence."""
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0xFF66 <= o <= 0xFF9F
            or 0x20000 <= o <= 0x3FFFF or 0x0E00 <= o <= 0x0FFF
            or 0x1000 <= o <= 0x109F or 0x1780 <= o <= 0x17FF)


class PageText:
    """The characters of one page with their boxes, and the views of them a
    term is searched in. Built once per page; any number of terms can then
    be looked for.

    ``spans(term)`` gives the hits as (first, last) character positions;
    ``boxes(spans)`` turns hits into rectangles, each hit widened to the
    whole words it touches and cut into one rectangle per line;
    ``find(term)`` is the verifier's question. ``placed(quad, term)`` puts
    a hit found some other way (search_for) on the same characters.
    """

    def __init__(self, page, case_sensitive: bool = False):
        self._case = case_sensitive
        self._chars: list[str] = []
        self._boxes: list[tuple] = []
        self._line: list[int] = []          # which line each character is on
        self._space: list[bool] = []
        self._joins: list[bool] = []        # part of a word that runs to its neighbours
        self._lines: list[tuple] = []       # (first, end, bbox, runs across) of each line
        #: (kind, text, first character, last character). Kinds: "c" a
        #: character or a base with its accents, folded; "s" whitespace; "h"
        #: a soft hyphen; "b" a line break — its text says how the line
        #: ended: "hard" on a hyphen, "soft" on a soft hyphen, or "".
        self._toks: list[tuple] = []
        self._views: dict = {}
        self._cuts: list[tuple] = []        # see _spaced
        self._broken = False                # some line ends on a hyphen
        chars, boxes, line_of = self._chars, self._boxes, self._line
        space, joins, kinds = self._space, self._joins, _CHAR_KINDS
        raw = page.get_text("rawdict", flags=_MATCH_FLAGS)
        for block in raw.get("blocks", ()):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", ()):
                first, number = len(chars), len(self._lines)
                for span in line.get("spans", ()):
                    for ch in span.get("chars", ()):
                        c = ch.get("c")
                        if not c:
                            continue
                        kind = kinds.get(c)
                        if kind is None:
                            white = unicodedata.normalize("NFKC", c).isspace()
                            kind = kinds[c] = (white, not white and not _unspaced_script(c))
                        chars.append(c)
                        boxes.append(ch.get("bbox") or (0, 0, 0, 0))
                        line_of.append(number)
                        space.append(kind[0])
                        joins.append(kind[1])
                if len(chars) > first:
                    across = line.get("dir") or (1.0, 0.0)
                    self._add_line(first, len(chars), abs(across[1]) < 1e-3)

    def _add_line(self, first: int, end: int, across: bool = True) -> None:
        boxes = [b for b in self._boxes[first:end] if b[2] > b[0] and b[3] > b[1]]
        bbox = ((min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else None)
        self._lines.append((first, end, bbox, across))
        chars, folds, combining = self._chars, _FOLDS[self._case], unicodedata.combining
        if len(folds) > 100000:             # a long-lived process, many documents
            folds.clear()
        toks, start, i = self._toks, len(self._toks), first
        while i < end:
            c = chars[i]
            if c == _SOFT_HYPHEN:
                toks.append(("h", "", i, i))
                i += 1
                continue
            j = i + 1
            while j < end and combining(chars[j]):
                j += 1
            if j > i + 1:
                c = "".join(chars[i:j])
            folded = folds.get(c)
            if folded is None:
                folded = _fold_text(c, self._case)
                # " " for whitespace, "" for what folds away to nothing.
                folded = folds[c] = " " if folded.isspace() else "".join(folded.split())
            if folded == " ":
                toks.append(("s", "", i, j - 1))
            elif folded:
                toks.append(("c", folded, i, j - 1))
            i = j
        # How the line ends decides what its break means in two of the views.
        ending, k = "", len(toks) - 1
        while k >= start and toks[k][0] == "s":
            k -= 1
        if k >= start and toks[k][0] == "h":
            ending = "soft"
        elif (k > start and toks[k][:2] == ("c", "-") and toks[k - 1][0] == "c"
              and toks[k - 1][1][-1].isalnum()):
            ending = "hard"
        self._broken = self._broken or bool(ending)
        toks.append(("b", ending, -1, -1))

    # -- views ---------------------------------------------------------

    def _spaced(self) -> tuple[str, list]:
        """The spaced view, built from the tokens; the other three are cut
        out of it. Notes in ``_cuts``, for each line that ends on a hyphen
        and runs straight on into the next: where that hyphen is in the
        text (-1 for a soft hyphen, which is not in it) and where the space
        that stands for the line break is."""
        text: list[str] = []
        owner: list[int] = []
        pending, hyphen = False, None       # hyphen: of a broken line, until the next character
        for k, (kind, value, _a0, _a1) in enumerate(self._toks):
            if kind == "c":
                if pending and text:
                    if hyphen is not None:
                        self._cuts.append((hyphen, len(text)))
                    text.append(" ")
                    owner.append(-1)
                pending, hyphen = False, None
                if len(value) == 1:
                    text.append(value)
                    owner.append(k)
                else:
                    text.extend(value)
                    owner.extend([k] * len(value))
            elif kind == "s":
                pending = True      # and a hyphen stays noted: the next line may be indented
            elif kind == "b":
                pending = True
                if value and text:
                    hyphen = len(text) - 1 if value == "hard" else -1
        return "".join(text), owner

    def _view(self, mode: int) -> tuple[str, list]:
        """(text, owner): the page's text in view *mode*, and for each of its
        characters the token it came from (-1 for a space)."""
        if mode not in self._views:
            if _SPACED not in self._views:
                self._views[_SPACED] = self._spaced()
            text, owner = self._views[_SPACED]
            if mode == _SOLID:
                self._views[mode] = (text.replace(" ", ""), [k for k in owner if k >= 0])
            elif mode != _SPACED:
                # Without the break's space; dehyphenated, without the hyphen too.
                gone = sorted(i for hyphen, gap in self._cuts
                              for i in ((hyphen, gap) if mode == _DEHYPHENATED else (gap,))
                              if i >= 0)
                parts, owners, prev = [], [], 0
                for i in gone:
                    parts.append(text[prev:i])
                    owners.extend(owner[prev:i])
                    prev = i + 1
                parts.append(text[prev:])
                owners.extend(owner[prev:])
                self._views[mode] = ("".join(parts), owners)
        return self._views[mode]

    def _apart(self, k0: int, k1: int) -> bool:
        """True if whitespace or a line break lies between tokens *k0* <
        *k1*: they belong to different words."""
        return any(self._toks[k][0] in "sb" for k in range(k0 + 1, k1))

    def _solid_ok(self, at: int, end: int) -> bool:
        """Is the match text[at:end] of the solid view to be believed?

        With every space gone, any neighbours read as one word, so the view
        is kept to whole words run together: the match has to start at the
        first letter or digit of a word and end at the last one of a word,
        a word being what stands between two spaces. Punctuation at the
        edge of the word may be left outside the match ("(4111 1111)."),
        letters and digits may not: the term "00000" is not in "1.0000
        0.2000", though the digits are there in a row once the space is
        gone. (Taking a full stop for the edge of a word found exactly that
        in a real file's table of numbers, and nothing true.) One thing may
        follow the last letter: an apostrophe and what it brings, so that
        "W H I T F I E L D's file" is found — its last word is "D's".
        """
        text, owner = self._view(_SOLID)
        i = at
        while i > 0 and (owner[i - 1] == owner[i] or not self._apart(owner[i - 1], owner[i])):
            i -= 1
            if text[i].isalnum():
                return False
        i = end - 1
        while i + 1 < len(text) and (owner[i + 1] == owner[i]
                                     or not self._apart(owner[i], owner[i + 1])):
            i += 1
            if i == end and text[i] in _APOSTROPHES:
                break
            if text[i].isalnum():
                return False
        return True

    # -- matching ------------------------------------------------------

    def spans(self, term: str) -> list[tuple[int, int]]:
        """Every hit for *term*, as (first, last) character positions, in
        reading order. A hit found in more than one view is one hit."""
        folded = _fold_text(term, self._case)
        spaced, solid = " ".join(folded.split()), "".join(folded.split())
        if not solid:
            return []
        found: set = set()
        modes = (_SPACED, _DEHYPHENATED, _JOINED) if self._broken else (_SPACED,)
        for mode in modes + (_SOLID,):
            text, owner = self._view(mode)
            needle = solid if mode == _SOLID else spaced
            at = text.find(needle)
            while at >= 0:
                end = at + len(needle)
                if mode != _SOLID or self._solid_ok(at, end):
                    found.add((self._toks[owner[at]][2], self._toks[owner[end - 1]][3]))
                at = text.find(needle, at + 1)
        return sorted(found)

    def find(self, term: str) -> bool:
        """True if *term* is on the page, in any view."""
        return bool(self.spans(term))

    def text_of(self, span: tuple[int, int]) -> str:
        """The page's own characters from *span*'s first to its last."""
        return "".join(self._chars[span[0]:span[1] + 1])

    def spans_under(self, quad) -> list[tuple[int, int]]:
        """The characters whose centre is inside *quad* — a hit from
        search_for(quads=True) — as runs: one span for each stretch of a
        line that is inside without a break. The quad and not its bounding
        rectangle: the rectangle round a hit in slanted text takes in
        whatever else is printed near it."""
        corners = [(p.x, p.y) for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
        x0, x1 = min(c[0] for c in corners), max(c[0] for c in corners)
        y0, y1 = min(c[1] for c in corners), max(c[1] for c in corners)

        def inside(i: int) -> bool:
            if self._space[i]:
                return False
            b = self._boxes[i]
            px, py = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                return False
            sides = [(bx - ax) * (py - ay) - (by - ay) * (px - ax)
                     for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1],
                                                   strict=True)]
            return all(s >= -1e-6 for s in sides) or all(s <= 1e-6 for s in sides)

        found = []
        for first, end, bbox, _across in self._lines:
            if bbox is None or bbox[2] < x0 or bbox[0] > x1 or bbox[3] < y0 or bbox[1] > y1:
                continue
            start = last = None
            for i in range(first, end):
                if inside(i):
                    start, last = (i if start is None else start), i
                elif start is not None and not self._space[i]:
                    found.append((start, last))
                    start = None
            if start is not None:
                found.append((start, last))
        return found

    def placed(self, quad, term: str) -> list[tuple[int, int]]:
        """Where a search_for hit for *term* lies on the page's characters:
        the spans under *quad* that read as the term, or [] if none does.

        A quad takes in whatever else is printed over the hit, and its edge
        can catch a neighbour's last letter, so the characters are asked
        what they say. A run that holds the whole term is cut down to the
        term. Failing that, one hit can be a piece of the term — search_for
        cuts a hit at each line, and into single glyphs in right-to-left
        text — and the longest run that is a piece of it is taken, and only
        that one.
        """
        key = "".join(_fold_text(term, self._case).split())
        whole, pieces = [], []
        for span in self.spans_under(quad):
            text, owner = [], []
            for i in range(span[0], span[1] + 1):
                if not self._space[i]:
                    folded = "".join(_fold_text(self._chars[i], self._case).split())
                    text.extend(folded)
                    owner.extend([i] * len(folded))
            text = "".join(text)
            at = text.find(key) if key else -1
            if at >= 0:
                whole.append((owner[at], owner[at + len(key) - 1]))
            elif any(t and t in key for t in (text, text.rstrip("-"))):
                pieces.append(span)
        return whole or sorted(pieces, key=lambda s: s[0] - s[1])[:1]

    # -- boxes ---------------------------------------------------------

    def widened(self, span: tuple[int, int]) -> tuple[int, int]:
        """*span* grown to the whole words it touches: out to the nearest
        whitespace on the same line, on each side. Not through a script
        written without spaces (_unspaced_script)."""
        a0, a1 = span
        line, joins = self._line, self._joins
        while a0 > 0 and line[a0 - 1] == line[a0] and joins[a0 - 1] and joins[a0]:
            a0 -= 1
        last = len(line) - 1
        while a1 < last and line[a1 + 1] == line[a1] and joins[a1 + 1] and joins[a1]:
            a1 += 1
        return a0, a1

    def _trimmed(self, rect: tuple, line_no: int, a0: int, a1: int) -> tuple:
        """*rect* — one line of the hit *a0*..*a1* — cut back at the top and
        at the bottom to where characters that are not part of the hit stop
        reaching into it; never past the middle half of its height, which
        every character of the hit crosses.

        A character goes if its box overlaps a redaction rectangle at all,
        and a character's box is as tall as its line: with lines set close,
        the full box of a word takes the words above and below it. Text is
        removed with this box; the fill and the pixels under it are the full
        box's (pdf_ops._redact_term_boxes). Only for a line that runs across
        the page: on any other, top and bottom are not where the neighbours
        are, and the rectangle is returned as it is.
        """
        x0, y0, x1, y1 = rect
        if not self._lines[line_no][3]:
            return rect
        middle, quarter = (y0 + y1) / 2, (y1 - y0) / 4
        top, bottom = y0, y1
        for number, (first, end, bbox, _across) in enumerate(self._lines):
            if (number == line_no or bbox is None or bbox[2] <= x0 or bbox[0] >= x1
                    or bbox[3] <= y0 or bbox[1] >= y1):
                continue
            for i in range(first, end):
                if a0 <= i <= a1 or self._space[i]:
                    continue
                b = self._boxes[i]
                if b[2] <= x0 or b[0] >= x1 or b[3] <= top or b[1] >= bottom or b[3] <= b[1]:
                    continue
                centre = (b[1] + b[3]) / 2
                if centre < middle:
                    top = max(top, min(b[3] + 0.05, middle - quarter))
                elif centre > middle:
                    bottom = min(bottom, max(b[1] - 0.05, middle + quarter))
        return (x0, top, x1, bottom)

    def boxes(self, spans, trimmed: bool = False) -> list:
        """One rectangle per line of each hit in *spans*, each hit widened
        to whole words first. Hits that come to the same rectangle — two
        views of one occurrence, a search_for hit and the matcher's own —
        are one rectangle. With *trimmed*, (rectangle, trimmed rectangle)
        pairs (_trimmed)."""
        rects, seen = [], set()
        for span in sorted(set(spans)):
            a0, a1 = self.widened(span)
            i = a0
            while i <= a1:
                j = i
                while j < a1 and self._line[j + 1] == self._line[i]:
                    j += 1
                part = [self._boxes[n] for n in range(i, j + 1) if not self._space[n]
                        and self._boxes[n][2] > self._boxes[n][0]
                        and self._boxes[n][3] > self._boxes[n][1]]
                if part:
                    rect = (min(b[0] for b in part), min(b[1] for b in part),
                            max(b[2] for b in part), max(b[3] for b in part))
                    key = tuple(round(v, 2) for v in rect)
                    if key not in seen:
                        seen.add(key)
                        rects.append((fitz.Rect(rect), fitz.Rect(self._trimmed(
                            rect, self._line[i], a0, a1))) if trimmed else fitz.Rect(rect))
                i = j + 1
        return rects


def term_boxes(page, terms, case_sensitive: bool = False) -> list:
    """The rectangles to redact on *page* for *terms* (term_hits, without
    the trimmed boxes)."""
    return [whole for whole, _trimmed in term_hits(page, terms, case_sensitive)]


def term_hits(page, terms, case_sensitive: bool = False) -> list:
    """The rectangles to redact on *page* for *terms*, each with the
    trimmed rectangle to remove its text by (PageText._trimmed): the matcher's hits
    and search_for's, together, each widened to the whole words it touches
    (PageText.boxes).

    search_for is kept beside the matcher because it is MuPDF's own reading
    of the page and may see what the views do not. Each of its hits is put
    back on the page's characters — those under the hit's quad that read as
    the term or a piece of it (PageText.placed) — so that a hit that stops
    short of the word (PyMuPDF 2.1 on a hyphenated word) is widened like
    any other, and so that an occurrence found both ways is one rectangle.
    A hit whose characters the matcher has already found adds nothing and
    is dropped. One that cannot be placed on characters is passed on as the
    rectangle search_for gave, unless every character under it is already
    in a rectangle.

    search_for has no case-sensitive mode. In a case-sensitive search the
    characters are compared in their own case, so a hit in another case is
    not placed, and is dropped; one with no character under it at all is
    kept only if the text in its rectangle holds the term exactly. (That
    last test alone, on every hit, is what redact_pdf used to do. A hit's
    rectangle is as tall as the line and reaches into the next one, so the
    term in the right case on the line below made a hit in the wrong case
    pass.)
    """
    terms = [t for t in (terms or []) if t and t.strip()]
    if not terms:
        return []
    text = PageText(page, case_sensitive)
    spans: list = []
    found: list = []        # search_for's hits, placed on characters
    loose: list = []        # ... and the ones that could not be
    for term in terms:
        spans.extend(text.spans(term))
        for quad in page.search_for(term, flags=fitz.TEXT_DEHYPHENATE, quads=True):
            placed = text.placed(quad, term)
            if placed:
                found.extend(placed)
            elif not case_sensitive:
                loose.append(quad)
            elif not text.spans_under(quad) and term in page.get_textbox(quad.rect):
                # Nothing under it to ask. (With characters under it that
                # do not read as the term, it is the term in another case.)
                loose.append(quad)
    covered: set = set()         # every character some rectangle will take

    def cover(span) -> None:
        a0, a1 = text.widened(span)
        covered.update(i for i in range(a0, a1 + 1) if not text._space[i])

    for span in spans:
        cover(span)
    for span in found:
        if not all(i in covered or text._space[i] for i in range(span[0], span[1] + 1)):
            spans.append(span)
            cover(span)
    rects = text.boxes(spans, trimmed=True)
    seen = {tuple(round(v, 2) for v in r) for r, _trimmed in rects}
    for quad in loose:
        under = [i for a0, a1 in text.spans_under(quad) for i in range(a0, a1 + 1)
                 if not text._space[i]]
        key = tuple(round(v, 2) for v in quad.rect)
        if key in seen or (under and all(i in covered for i in under)):
            continue
        seen.add(key)
        rects.append((fitz.Rect(quad.rect), fitz.Rect(quad.rect)))    # nothing known to trim by
    return rects


def _term_in_page_text(page, text: str, term: str, case_sensitive: bool,
                       chars: Optional[PageText] = None) -> tuple[bool, Optional[str]]:
    """(found, error) — is *term* extractable from *page*?

    The matcher is asked first (*chars*, the page's PageText): the same
    views redact_pdf boxed by, so what it would have boxed and did not
    remove is found here.

    Behind it, the older checks. Three views of the extracted text are
    searched: as-is, whitespace-normalized, and dehyphenated-then-
    normalized — a term wrapped across a line break appears with '\\n' (or
    '-\\n') where the caller's term has a space (or nothing), and without
    normalization an exact-case cross-line occurrence would be invisible to
    case_sensitive matching (the per-rect search_for confirm below sees only
    one line's fragment per hit rect, so it can never confirm a multi-line
    match by itself).

    search_for() (with TEXT_DEHYPHENATE, matching redact_pdf's own search)
    additionally catches kerned text the plain views miss. It is always
    case-insensitive, so for case_sensitive mode each hit is confirmed by
    re-extracting the text under its rect — the RED-01 workaround.

    *error* is set when search_for itself raised, so the caller records a
    failed-verification line instead of passing vacuously (fail-closed).
    """
    if chars is not None and chars.find(term):
        return True, None
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
        try:
            chars = PageText(page, case_sensitive)
        except Exception as exc:
            # Not a pass: the matcher did not look. Page-attributed, like
            # the line above, so the page can be offered for flattening.
            chars = None
            page_text_ev.append(f"page text on page {pno} could not be read character "
                                f"by character for verification ({type(exc).__name__})")
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
            found, err = _term_in_page_text(page, text, term, case_sensitive, chars)
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


#: A character is under a box drawn by hand when its centre is inside the
#: box, or this share of its text box is.
UNDER_SHARE = 0.2


def _page_characters(page) -> list[tuple]:
    """(character, text box) for every character of *page*, read the way
    the matcher reads the page. White space is given as (" ", None): it is
    under nothing, and keeps words apart in what is quoted."""
    found = []
    for block in page.get_text("rawdict", flags=_MATCH_FLAGS).get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for ch in span.get("chars", ()):
                    c, box = ch.get("c"), ch.get("bbox")
                    if not c or not box or unicodedata.normalize("NFKC", c).isspace():
                        found.append((" ", None))
                    else:
                        found.append((c, tuple(box)))
            found.append((" ", None))
    return found


def _is_under(b: tuple, box) -> bool:
    """Is the character with text box *b* under *box*? Its centre is in the
    box, or a fifth or more of its text box is. (A text box with no width
    — a font whose widths are nought — is the upright line the letter is
    written from, and a fifth of that is a fifth of its height; likewise
    one with no height.)"""
    if box.x0 <= (b[0] + b[2]) / 2 <= box.x1 and box.y0 <= (b[1] + b[3]) / 2 <= box.y1:
        return True
    share = 1.0
    for low, high, start, end in ((b[0], b[2], box.x0, box.x1), (b[1], b[3], box.y0, box.y1)):
        if high > low:
            share *= max(0.0, min(high, end) - max(low, start)) / (high - low)
        elif not start <= low <= end:
            return False
    return share >= UNDER_SHARE


def chars_under(page, boxes: list) -> list[tuple]:
    """The characters of *page* that are under one of *boxes* — rectangles
    the user drew, as fitz.Rect — each as (character, text box).

    The one rule redact_pdf removes by and the verifier checks by: a
    character is under a box when the centre of its text box is inside the
    box, or a fifth or more of the text box is. MuPDF's own measure for
    removing a character is another (the box must reach a tenth of the way
    into the letter, from any side), and what page.get_text(clip=...)
    returns is a third (the box touches the letter's drawn shape). A box
    drawn a point too tall reaches the tails of the line above and the tops
    of the line below; MuPDF leaves those letters, rightly, and a check
    that asked for no trace of any letter in the box refused the run for
    them. By this rule they are not under the box, and a letter that is —
    half of it, or its middle — is never passed.
    """
    return [(c, b) for c, b in _page_characters(page)
            if b is not None and any(_is_under(b, box) for box in boxes)]


def _rect_region_check(doc, rects: list[dict]) -> VerificationCheck:
    """No character is left under a redaction rectangle (chars_under: its
    centre in the rectangle, or a fifth or more of its text box).

    Rect entries mirror redact_pdf's shape: {"page": int (0-based), "x0",
    "y0", "x1", "y1"} in PDF points. A malformed, out-of-range, inverted,
    or empty entry is a FAILED verification (it could not be checked), not
    a silent skip — redact_pdf skips exactly the invalid/empty rects
    (pdf_ops.py's is_valid/is_empty guard), so an unchecked one here would
    hand an unredacted region a passing proof.
    """
    ev: list[str] = []
    read: dict[int, list] = {}          # the characters of each page, read once
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
            if pno not in read:
                read[pno] = _page_characters(doc[pno])
            # What is under it, words kept apart as they are on the page.
            found = "".join(c if b is not None and _is_under(b, rect) else " "
                            for c, b in read[pno]).strip()
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


#: Keys that lead out of the structure tree: to a page, to an annotation or
#: an XObject, to a content stream.
STRUCTURE_WAYS_OUT = ("/Pg", "/Obj", "/Stm", "/StmOwn")
#: /Type of the dictionaries that make up the structure tree.
_STRUCTURE_TYPES = ("/StructTreeRoot", "/StructElem", "/MCR", "/OBJR")


def structure_stream(kid):
    """The stream a kid of a structure element names — the content stream
    of a marked-content reference (/Stm), the XObject of an object
    reference (/Obj) — or None."""
    if isinstance(kid, pikepdf.Dictionary):
        for key in ("/Stm", "/Obj"):
            target = kid.get(key)
            if isinstance(target, pikepdf.Stream):
                return target
    return None


def structure_kids(elem) -> list:
    """The kids of structure element *elem*, as a list."""
    kids = elem.get("/K")
    return list(kids) if isinstance(kids, pikepdf.Array) else [kids]


def annotation_or_field(obj) -> bool:
    """An annotation or a form field, by what it is: it says it is an
    annotation, it has a field type, it has a /Subtype and a /Rect, or it
    is a parent field (a name and kids). Not by where it is listed."""
    if not isinstance(obj, pikepdf.Dictionary) or isinstance(obj, pikepdf.Stream):
        return False
    try:
        if _type_name(obj) == "/Annot" or "/FT" in obj:
            return True
        if "/Subtype" in obj and "/Rect" in obj:
            return True
        return "/T" in obj and "/Kids" in obj and _type_name(obj) != "/Pages"
    except Exception:
        return False


def structure_object(kid):
    """The annotation or form field a kid of a structure element names —
    the target of an object reference (/Obj) — or None."""
    if isinstance(kid, pikepdf.Dictionary):
        target = kid.get("/Obj")
        if annotation_or_field(target):
            return target
    return None


def structure_refs(pdf) -> tuple[list, set, set, set]:
    """(holders, named, alive, objects) for the structure tree of *pdf*:
    the dictionaries of the tree that have a kid naming a stream, an
    annotation or a form field; the (num, gen) of everything so named; those
    of them something outside the tree leads to as well; and which of the
    named are annotations or fields (the rest are streams).

    What is named and not alive is in the file for the structure tree
    alone. After a redaction a stream like that is the unredacted original
    of a form, an image or a page's content, which the page has a redacted
    copy of; an annotation or field like that is one that was taken off its
    page — a stamp under a box, a link or a field MuPDF removed — with
    everything it held. redact_pdf removes such references
    (pdf_ops._drop_stale_structure_refs) and verify_redaction checks that
    none is left. "Outside the tree" is tested by walking the file from the
    trailer without entering the tree, not through the catalog and not
    through a bookmark's /SE.
    """
    holders: list = []
    named: set = set()
    objects: set = set()
    try:
        root = pdf.Root.get("/StructTreeRoot")
    except Exception:
        root = None
    if not isinstance(root, pikepdf.Dictionary):
        return holders, named, set(), objects

    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        try:
            if not isinstance(cur, (pikepdf.Dictionary, pikepdf.Array)):
                continue
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
            if isinstance(cur, pikepdf.Array):
                stack.extend(cur)
                continue
            if _type_name(cur) in ("/Page", "/Pages", "/Catalog"):
                continue
            if "/K" in cur:
                kids = structure_kids(cur)
                streams = [s for s in map(structure_stream, kids) if s is not None]
                things = [o for o in map(structure_object, kids) if o is not None]
                if streams or things:
                    holders.append(cur)
                    named.update(tuple(s.objgen) for s in streams)
                    for o in things:
                        # One written into the reference itself has no number
                        # to be reached by, and nothing outside the tree has it.
                        key = tuple(o.objgen) if o.is_indirect else ("direct", len(objects))
                        named.add(key)
                        objects.add(key)
            stack.extend(cur[k] for k in cur.keys() if k not in STRUCTURE_WAYS_OUT)
        except Exception:
            continue
    alive: set = set()
    if not named:
        return holders, named, alive, objects

    containers = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)
    seen, stack = set(), [pdf.trailer]
    while stack and len(alive) < len(named):
        cur = stack.pop()
        try:
            if not isinstance(cur, containers):
                continue
            if cur.is_indirect:
                ident = tuple(cur.objgen)
                if ident in seen:
                    continue
                seen.add(ident)
                if ident in named:
                    alive.add(ident)
            if isinstance(cur, pikepdf.Array):
                stack.extend(cur)
                continue
            if _type_name(cur) in _STRUCTURE_TYPES:
                continue
            stack.extend(cur[k] for k in cur.keys() if k not in ("/StructTreeRoot", "/SE"))
        except Exception:
            continue
    return holders, named, alive, objects


def _whole_file_checks(output_path: str, password: Optional[str] = None) -> list[VerificationCheck]:
    """Two checks every redaction gets, by term or by box, read with pikepdf
    from the output as a whole (#119, R3).

    ``redaction.structure_streams``: nothing is in the file for the
    structure tree alone (structure_refs) — no stream, and no annotation or
    form field, which is then on no page. ``redaction.thumbnails``:
    no page carries a /Thumb, a small picture of the page as it was. Both
    are document-level: making a page a picture does not mend either. An
    output that cannot be read fails both, marked unevaluated.
    """
    kept: list[str] = []
    thumbs: list[str] = []
    problem = None
    kwargs = {"password": password} if password else {}
    try:
        with pikepdf.open(output_path, **kwargs) as pdf:
            _holders, named, alive, objects = structure_refs(pdf)
            streams = len(named - alive - objects)
            things = len((objects & named) - alive)
            if streams:
                kept.append(f"{streams} stream{'' if streams == 1 else 's'} in the file can be "
                            f"reached only through the structure tree: the original of content "
                            f"a page now has a redacted copy of (document-level)")
            if things:
                kept.append(f"{things} annotation{'' if things == 1 else 's'} or form "
                            f"field{'' if things == 1 else 's'} in the file can be reached only "
                            f"through the structure tree: {'it is' if things == 1 else 'they are'}"
                            f" on no page (document-level)")
            in_tree = set()
            for number, page in enumerate(pdf.pages, 1):
                in_tree.add(tuple(page.obj.objgen))
                if "/Thumb" in page.obj:
                    thumbs.append(f"page {number} still carries a thumbnail of the page as "
                                  f"it was (document-level)")
            for obj in pdf.objects:
                try:
                    if (isinstance(obj, pikepdf.Dictionary) and "/Thumb" in obj
                            and _type_name(obj) == "/Page" and tuple(obj.objgen) not in in_tree):
                        thumbs.append("a page outside the page tree still carries a thumbnail "
                                      "of the page as it was (document-level)")
                except Exception:
                    continue
    except Exception as exc:
        problem = (f"the output could not be read for this check "
                   f"({type(exc).__name__}) (document-level)")
    return [
        VerificationCheck(
            id="redaction.structure_streams",
            description="Nothing is kept in the file by the structure tree alone",
            passed=not (kept or problem), evidence=kept or ([problem] if problem else []),
            unevaluated=bool(problem)),
        VerificationCheck(
            id="redaction.thumbnails",
            description="No page carries a thumbnail of itself",
            passed=not (thumbs or problem),
            evidence=_dedupe(thumbs) or ([problem] if problem else []),
            unevaluated=bool(problem)),
    ]


@contextlib.contextmanager
def upright(page):
    """*page* with its /Rotate set to 0 for the time being, and put back
    afterwards. A page with a /Rotate of its own gets that value back. A page
    that takes its turn from the page tree is left with "/Rotate null" in its
    dictionary in memory (xref_set_key sets a key to null; it does not delete
    it), which reads as no entry: the page is shown as before, and the saved
    output carries the turn on the page itself.

    PyMuPDF does two things on a turned page with a matrix it builds by
    hand, Matrix(1, 0, 0, -1, 0, cropbox height), which leaves out where
    the page box starts (page.transformation_matrix): it puts a rectangle
    from the PDF into page space, and it draws a redaction's black fill
    (apply_redactions, through a Shape). On a page whose crop box or media
    box does not start at 0 0 both land in the wrong place. Upright, it
    asks MuPDF for the matrix, and MuPDF has it right. The change is made
    in memory, on the document's own page dictionary; nothing is saved
    here, and it is undone on the way out whatever happens.
    """
    if not page.rotation:
        yield page
        return
    doc, xref = page.parent, page.xref
    kind, value = doc.xref_get_key(xref, "Rotate")
    doc.xref_set_key(xref, "Rotate", "0")
    try:
        yield page
    finally:
        doc.xref_set_key(xref, "Rotate", "null" if kind == "null" else value)


def page_to_picture_matrix(page):
    """The matrix that takes a point in PyMuPDF's page space to the page as
    it is shown — turned by its /Rotate, as get_pixmap draws it and as the
    app's page pictures show it. Its inverse takes a box drawn on such a
    picture back to page space.

    page.rotation_matrix is the same turn, made from the size of the crop
    box as it is written. MuPDF draws the page at another size when the
    page has a user unit (it is drawn that many times larger) or when its
    crop box is not inside its media box (it is cut to the media box), and
    there a box drawn on the picture went somewhere else and the run
    verified with the word under it. The turn here is made from the page
    as MuPDF bounds it (page.rect, the size of the page as shown); on every
    other page the two are the same.
    """
    size = page.rect
    return {90: fitz.Matrix(0, 1, -1, 0, size.width, 0),
            180: fitz.Matrix(-1, 0, 0, -1, size.width, size.height),
            270: fitz.Matrix(0, -1, 1, 0, 0, size.height)}.get(page.rotation % 360, fitz.Matrix(1, 0, 0, 1, 0, 0))


def pdf_to_page_matrix(page):
    """The matrix that takes a point in the PDF's own coordinates (an
    annotation's /Rect, say) to PyMuPDF's page space — the page before any
    /Rotate, from the top left of its crop box, where get_text and
    search_for put things. page.transformation_matrix, read upright: see
    upright()."""
    with upright(page):
        return fitz.Matrix(page.transformation_matrix)


def _valid_rects(doc, rects: list[dict]) -> list[tuple]:
    """(page index, fitz.Rect) for each entry of *rects* that names a page
    of *doc* and a real area. The others are _rect_region_check's to fail;
    the checks that use this have nothing to look under them."""
    found = []
    for r in rects:
        try:
            pno = int(r["page"])
            rect = fitz.Rect(float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
        except Exception:
            continue
        if 0 <= pno < len(doc) and rect.is_valid and not rect.is_empty:
            found.append((pno, rect))
    return found


def _rect_annotation_check(doc, output_path: str, rects: list[dict],
                           password: Optional[str] = None) -> VerificationCheck:
    """No annotation is left lying over a redaction rectangle.

    An annotation is painted above the page, so one that overlaps a box is
    painted above the box: a stamp, a signature picture, a note's icon.
    redact_pdf takes them off the page (and a widget's field is emptied as
    before); this is what would notice if it did not. Every kind of
    annotation counts but a popup, which draws nothing of its own on the
    page and goes with the note it belongs to. One whose rectangle cannot
    be read fails the check: it could be anywhere.

    The page's /Annots is read with pikepdf, entry by entry, because that
    reads an annotation written into the array itself like any other;
    PyMuPDF's lists pass over one that has no object number. Its /Rect is
    put into the coordinates redaction rectangles are given in (PyMuPDF's,
    on the page before any rotation) with the page's own matrix, read the
    way that is right on a turned page whose page box does not start at
    0 0 (pdf_to_page_matrix).
    """
    ev: list[str] = []
    by_page: dict[int, list] = {}
    for pno, rect in _valid_rects(doc, rects):
        by_page.setdefault(pno, []).append(rect)
    if not by_page:
        return VerificationCheck(
            id="redaction.rect_annotations",
            description="No annotation lies over a redaction rectangle", passed=True)
    kwargs = {"password": password} if password else {}
    try:
        with pikepdf.open(output_path, **kwargs) as pdf:
            for pno, boxes in sorted(by_page.items()):
                try:
                    to_fitz = pdf_to_page_matrix(doc[pno])
                    annots = pdf.pages[pno].obj.get("/Annots")
                    entries = list(annots) if isinstance(annots, pikepdf.Array) else []
                except Exception as exc:
                    ev.append(f"annotations on page {pno + 1} could not be read for "
                              f"verification ({type(exc).__name__})")
                    continue
                for annot in entries:
                    subtype = "unknown"
                    try:
                        if not isinstance(annot, pikepdf.Dictionary):
                            continue
                        subtype = str(annot.get("/Subtype", "")).lstrip("/") or "unknown"
                        if subtype == "Popup":
                            continue
                        rect = fitz.Rect(*(float(v) for v in annot["/Rect"]))
                        rect.normalize()
                        rect = rect * to_fitz
                        rect.normalize()
                    except Exception:
                        ev.append(f"the rectangle of a {subtype} annotation on page {pno + 1} "
                                  f"could not be read for verification")
                        continue
                    if any(rect.intersects(box) for box in boxes):
                        ev.append(f"a {subtype} annotation still lies over a redaction "
                                  f"rectangle on page {pno + 1}")
    except Exception as exc:
        ev.append(f"annotations on page {min(by_page) + 1} could not be read for "
                  f"verification ({type(exc).__name__})")
    return VerificationCheck(
        id="redaction.rect_annotations",
        description="No annotation lies over a redaction rectangle",
        passed=not ev, evidence=_dedupe(ev))


def path_pieces(drawing: dict, steps: int = 24) -> list[tuple[bool, list[tuple]]]:
    """The path of one PyMuPDF drawing (an entry of page.get_drawings()) as
    (straight, points) pairs, one per line, rectangle, quad or curve of it.
    The points of a straight piece are its corners, each pair of neighbours
    one edge. A curve is walked in *steps* straight pieces: where it runs,
    not where its control points are."""
    pieces = []
    for item in drawing.get("items", ()):
        try:
            kind = item[0]
            if kind == "l":
                pieces.append((True, [(item[1].x, item[1].y), (item[2].x, item[2].y)]))
            elif kind == "re":
                r = item[1]
                pieces.append((True, [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1),
                                      (r.x0, r.y0)]))
            elif kind == "qu":
                q = item[1]
                pieces.append((True, [(p.x, p.y) for p in (q.ul, q.ur, q.lr, q.ll, q.ul)]))
            elif kind == "c":
                p = item[1:5]
                points = []
                for n in range(steps + 1):
                    s = n / steps
                    u = 1 - s
                    w = (u ** 3, 3 * u * u * s, 3 * u * s * s, s ** 3)
                    points.append((sum(k * q.x for k, q in zip(w, p, strict=True)),
                                   sum(k * q.y for k, q in zip(w, p, strict=True))))
                pieces.append((False, points))
        except Exception:
            continue
    return pieces


def path_lines(drawing: dict, steps: int = 24) -> list[list[tuple]]:
    """The points of path_pieces, without which pieces are straight."""
    return [points for _straight, points in path_pieces(drawing, steps)]


def _passes_inside(p: tuple, q: tuple, box) -> bool:
    """Does the straight line from *p* to *q* pass through the inside of
    *box*, a quarter of a point in from its edges? (A rule along the edge of
    a box, and the edges of the box's own fill, do not.)"""
    x0, y0, x1, y1 = box.x0 + 0.25, box.y0 + 0.25, box.x1 - 0.25, box.y1 - 0.25
    if x0 >= x1 or y0 >= y1:
        return False
    enter, leave = 0.0, 1.0
    for start, delta, low, high in ((p[0], q[0] - p[0], x0, x1), (p[1], q[1] - p[1], y0, y1)):
        if abs(delta) < 1e-12:
            if not low < start < high:
                return False
            continue
        a, b = (low - start) / delta, (high - start) / delta
        enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
    return enter < leave or (p == q and x0 < p[0] < x1 and y0 < p[1] < y1)


def is_plain_black(drawing: dict) -> bool:
    """A drawing that is nothing but rectangles filled black — what the
    fill of a redaction box is. Under a black box it shows nothing."""
    fill = drawing.get("fill")
    return (bool(drawing.get("items")) and fill is not None and not any(fill)
            and all(item[0] == "re" for item in drawing["items"]))


def has_edge_inside(drawing: dict, box) -> bool:
    """True if something of *drawing*'s path is under *box* (a fitz.Rect):
    a piece of it passes through the inside of the box and is not a
    straight line that runs right through.

    A straight line both of whose ends are outside the box's interior hides
    nothing under it: it goes in one side and out another, and the part the
    box covers is fixed by the parts it does not — a rule across the page,
    the border of a table, the edge of a shaded row. Each edge of a
    rectangle is judged as such a line. A line that ends under the box, a
    corner under it, any curve that runs through it, and anything wholly
    inside it are under the box: where the line ends, where the corner is
    and how the curve bends there is what the box was drawn to hide.
    """
    def inside(p) -> bool:
        return box.x0 < p[0] < box.x1 and box.y0 < p[1] < box.y1

    for straight, points in path_pieces(drawing):
        for a, b in zip(points, points[1:], strict=False):
            if _passes_inside(a, b, box) and not (straight and not inside(a) and not inside(b)):
                return True
    return False


def _reaches(drawing: dict, box) -> bool:
    """Can *drawing* have anything inside *box* at all? By its bounding
    rectangle — which holds its path, and a curve's control points with it
    — so that a page of ten thousand drawings is not walked point by point
    for the few that are near a box."""
    try:
        r = drawing["rect"]
        return r.x0 < box.x1 and r.x1 > box.x0 and r.y0 < box.y1 and r.y1 > box.y0
    except Exception:
        return True


def _rect_line_art_check(doc, rects: list[dict]) -> VerificationCheck:
    """No drawing has an edge inside a redaction rectangle.

    A stroked path under a box drawn by hand stays in the page's content,
    under the fill: the signature drawn as a curve, the line of a chart.
    PyMuPDF removes a drawing that lies wholly inside the box, as it
    measures that (by a curve's control points, not by where it runs), and
    redact_pdf lets it remove whatever touches the box only when nothing
    else would go with it. What is left under a box (has_edge_inside: not
    a straight line that only runs through it) fails this check. It is page
    content, so the page can be flattened, and the box is then black on the
    picture. A solid black rectangle is passed over: every box's own fill
    is one, and it shows nothing.
    """
    ev: list[str] = []
    by_page: dict[int, list] = {}
    for pno, rect in _valid_rects(doc, rects):
        by_page.setdefault(pno, []).append(rect)
    for pno, boxes in sorted(by_page.items()):
        try:
            drawings = doc[pno].get_drawings()
        except Exception as exc:
            ev.append(f"the drawings on page {pno + 1} could not be read for "
                      f"verification ({type(exc).__name__})")
            continue
        if any(_reaches(d, box) and has_edge_inside(d, box)
               for d in drawings if not is_plain_black(d) for box in boxes):
            ev.append(f"a drawing still has an edge inside a redaction rectangle "
                      f"on page {pno + 1}")
    return VerificationCheck(
        id="redaction.rect_line_art",
        description="No drawing has an edge inside a redaction rectangle",
        passed=not ev, evidence=ev)


#: The most decoded bytes a picture may have for the check that its data is
#: blanked under a box to read it; a larger one is "could not be checked".
_OCR_IMAGE_BYTES = 400_000_000


def _shown_pixels(page, dpi: int, *, grey: bool = False, annots: bool = True):
    """*page* as shown at *dpi* as an array of pixels, and the matrix that
    takes page space to them (pdf_ocr._render's, unturned)."""
    import numpy as np
    zoom = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=zoom, colorspace=fitz.csGRAY if grey else fitz.csRGB,
                          alpha=False, annots=annots)
    to_pixels = page_to_picture_matrix(page) * zoom * fitz.Matrix(1, 0, 0, 1, -pix.x, -pix.y)
    pixels = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return pixels, to_pixels


def _inside(rect, shape, margin: float = 1.0):
    """The rows and columns of the pixels of an array of *shape* that lie
    wholly inside *rect* (pixel space), *margin* pixels in from its edge."""
    r = fitz.Rect(rect).normalize()
    y0, y1 = max(int(math.ceil(r.y0 + margin)), 0), min(int(math.floor(r.y1 - margin)), shape[0])
    x0, x1 = max(int(math.ceil(r.x0 + margin)), 0), min(int(math.floor(r.x1 - margin)), shape[1])
    return slice(y0, max(y0, y1)), slice(x0, max(x0, x1))


def _pixels_blank_under(pix, transform, box) -> bool:
    """Whether the decoded picture *pix*, drawn by *transform*, holds one
    value under *box* (page space), counting the pixels wholly inside it."""
    import numpy as np
    data = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    # The box in the picture's own pixels, rows counted from its top.
    r = fitz.Rect(box) * ~fitz.Matrix(transform)
    r = fitz.Rect(r.x0 * pix.width, r.y0 * pix.height, r.x1 * pix.width, r.y1 * pix.height)
    under = data[_inside(r, data.shape)]
    return not under.size or int((under.max(axis=(0, 1)) - under.min(axis=(0, 1))).max()) <= 4


def _image_blank_under(doc, xref: int, transform, box) -> Optional[bool]:
    """Whether the data of the picture *xref*, drawn by *transform*, is one
    value under *box* (page space) — in its own pixels and in those of its
    soft mask or mask, if it has one. None when it cannot be read."""
    streams = [xref]
    for key in ("SMask", "Mask"):
        kind, value = doc.xref_get_key(xref, key)
        if kind == "xref":
            streams.append(int(value.split()[0]))
    for x in streams:
        try:
            width = int(doc.xref_get_key(x, "Width")[1])
            height = int(doc.xref_get_key(x, "Height")[1])
            if width * height * 4 > _OCR_IMAGE_BYTES:
                return None
            pix = fitz.Pixmap(doc, x)
        except Exception:
            return None
        if not _pixels_blank_under(pix, transform, box):
            return False
    return True


def _inline_blank_under(blocks: list, transform, box) -> Optional[bool]:
    """The same for a picture written into the page's content (BI ... EI),
    which has no object of its own: read from the page's text dictionary,
    *blocks*, by where it is drawn. None when it cannot be found or read."""
    found = [b for b in blocks if fitz.Matrix(b.get("transform") or (0,) * 6) == fitz.Matrix(transform)]
    if not found:
        return None
    try:
        return all(_pixels_blank_under(fitz.Pixmap(b["image"]), transform, box) for b in found)
    except Exception:
        return None


def _changed_outside(page, now, then, to_pixels, pno: int, rects: list, on: str) -> list[str]:
    """Evidence that *page* (*now*, its fingerprint pixels) changed from
    *then* outside every box of *rects* on it, each grown by two pixels:
    pixels more than 24 grey levels apart. On a page with a user unit too:
    the black fill is the box there (pdf_ops._fill_black, #134), and black
    past it is counted like any other change."""
    import numpy as np
    changed = np.abs(now.astype(np.int16) - then.astype(np.int16)) > 24
    for r in rects:
        try:
            if int(r["page"]) != pno:
                continue
            box = fitz.Rect(float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"]))
        except Exception:
            continue
        changed[_inside(box * to_pixels, changed.shape, margin=-2.0)] = False
    return [f"{int(changed.sum()):,} pixels changed outside the boxes {on}"] if changed.any() else []


def _ocr_checks(doc, ocr_pages: list, terms: list[str], case_sensitive: bool,
                tessdata: Optional[str], rects: list) -> list[VerificationCheck]:
    """The checks of the places OCR found (#128): a picture cannot be read
    for text, so what the page shows and what its pictures hold is looked at.

    ``redaction.ocr_fill``
        On the page as shown, every box shows only the fill colour (black):
        nothing is drawn over it.
    ``redaction.ocr_image``
        The data of every picture the page draws under a box is blanked
        there — its own pixels, and those of its soft mask — so that the
        term is not in the picture for anyone who takes it out of the file.
        The picture as the page has it, not only as it is shown: a picture
        can be drawn twice, or on another page.
    ``redaction.ocr_reread``
        OCR reads the page again at each turn it found the term at, and
        does not find it anywhere on the page. Turning the page into a
        picture would not change what OCR reads, so no flatten is offered.
    ``redaction.ocr_outside``
        Given a fingerprint of the page from before (pdf_ocr.fingerprint),
        the page as shown, without annotations, is the same outside the
        boxes on it (every box of *rects* on the page, grown by two pixels
        of the fingerprint). Both are drawn the same way, at a resolution
        at which a picture stored again without loss draws as it did
        (pdf_ocr.fingerprint_pixels). Damage, not a leak: no flatten
        either. A page with a user unit is held to it like any other: its
        black fill is the box (pdf_ops._fill_black, #134).

    *ocr_pages* is redact_pdf's account of the pages: {"page" (0-based),
    "boxes" ([x0, y0, x1, y1] in page space), "dpi", "turns", "before"
    (optional), "search_boxes" (optional: the boxes the search itself put
    on the page, left out of the outside check like *rects*)}. A box of a page the file does not have, a picture that
    cannot be decoded, or no language file: could not be checked."""
    import numpy as np

    import pdf_ocr
    fill_ev: list[str] = []
    image_ev: list[str] = []
    read_ev: list[str] = []
    outside_ev: list[str] = []
    unchecked: list[str] = []
    fingerprints = False
    for entry in ocr_pages:
        try:
            pno = int(entry["page"])
            boxes = [fitz.Rect(b) for b in entry.get("boxes") or []]
            page = doc[pno]
        except Exception:
            unchecked.append("OCR places on a page the output does not have could not be checked")
            continue
        on = f"on page {pno + 1}"
        dpi = int(entry.get("dpi") or pdf_ocr.MIN_DPI)
        # (a) What the page shows under each box.
        shown, to_pixels = _shown_pixels(page, min(dpi, 150))
        for box in boxes:
            under = shown[_inside(box * to_pixels, shown.shape)]
            if under.size and int(under.max()) > 8:
                fill_ev.append(f"an OCR box {on} does not show only black (something is drawn over it)")
                break
        # (b) What the pictures under each box hold.
        inline = None
        for info in page.get_image_info(xrefs=True):
            hit = [box for box in boxes if fitz.Rect(info["bbox"]).intersects(box)]
            if not hit:
                continue
            xref = info.get("xref") or 0
            if xref:
                blank = [_image_blank_under(doc, xref, info["transform"], box) for box in hit]
            else:
                if inline is None:
                    inline = [b for b in page.get_text("dict", flags=fitz.TEXT_PRESERVE_IMAGES)["blocks"]
                              if b.get("type") == 1]
                blank = [_inline_blank_under(inline, info["transform"], box) for box in hit]
            if any(b is None for b in blank):
                unchecked.append(f"a picture under an OCR box {on} could not be read, so it could not be checked")
            elif not all(blank):
                image_ev.append(f"a picture under an OCR box {on} still holds what was under the box")
        # (c) Nothing changed outside the boxes: the page drawn the way its
        # fingerprint was (pdf_ocr.fingerprint_pixels), so that a picture
        # stored again without loss draws as it did.
        before = entry.get("before")
        if before:
            fingerprints = True
            now, to_print = pdf_ocr.fingerprint_pixels(page, int(before["dpi"]))
            then = np.frombuffer(zlib.decompress(before["samples"]), np.uint8)
            # The search's own boxes on the page are boxes too: a stamp's
            # text it removed is not damage.
            searched = [{"page": pno, "x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]}
                        for b in entry.get("search_boxes") or []]
            if now.shape != (before["height"], before["width"]):
                outside_ev.append(f"the page {on} is not the size it was")
            else:
                outside_ev += _changed_outside(page, now, then.reshape(now.shape), to_print,
                                               pno, list(rects) + searched, on)
        # (d) What OCR reads on the page now.
        if not tessdata:
            unchecked.append(f"the OCR language file was not given: what OCR reads {on} could not be checked")
        else:
            for turn in entry.get("turns") or [0]:
                again = pdf_ocr.read_at(page, terms, case_sensitive, tessdata, int(turn), dpi)
                if again.boxes:
                    read_ev.append(f"OCR still reads a term {on}, turned {int(turn)} degrees "
                                   f"({len(again.boxes)} place{'' if len(again.boxes) == 1 else 's'})")
    checks = [
        VerificationCheck(id="redaction.ocr_fill",
                          description="Every place OCR found shows only black",
                          passed=not fill_ev, evidence=fill_ev),
        VerificationCheck(id="redaction.ocr_image",
                          description="The pictures under the places OCR found are blanked there",
                          passed=not image_ev and not unchecked, evidence=image_ev + unchecked,
                          unevaluated=bool(unchecked) and not image_ev),
        VerificationCheck(id="redaction.ocr_reread",
                          description="OCR no longer reads the term on the pages it was found on",
                          passed=not read_ev, evidence=read_ev, flattenable=False),
    ]
    if fingerprints:
        checks.append(VerificationCheck(
            id="redaction.ocr_outside",
            description="The pages OCR redacted are unchanged outside the boxes",
            passed=not outside_ev, evidence=outside_ev, flattenable=False))
    return checks


def verify_redaction(output_path: str, terms: Optional[list[str]] = None, *,
                     rects: Optional[list[dict]] = None,
                     case_sensitive: bool = False,
                     password: Optional[str] = None,
                     input_path: str = "",
                     source_ids: Optional[list[str]] = None,
                     source_sha256: Optional[str] = None,
                     ocr_pages: Optional[list[dict]] = None,
                     tessdata: Optional[str] = None) -> VerificationReport:
    """Independently confirm a redacted output no longer carries its targets.

    Mirrors redact_pdf's selection model: *terms* (search-term redactions)
    and/or *rects* (user-drawn boxes, same dict shape) — at least one
    required. Term mode searches every extractable surface; rect mode
    looks under each box for characters (chars_under), annotations and
    line art, so rect-only redactions aren't a blind spot. *input_path* is
    carried into the report verbatim for the caller (#99) — verification
    itself never reads the input file.

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
            report.checks.append(_term_sweep_check(
                output_path, terms, case_sensitive, password))
        if rects:
            report.checks.append(_rect_region_check(doc, rects))
            report.checks.append(_rect_annotation_check(doc, output_path, rects, password))
            report.checks.append(_rect_line_art_check(doc, rects))
        if ocr_pages and terms:
            report.checks.extend(_ocr_checks(doc, ocr_pages, terms, case_sensitive,
                                             tessdata, rects))
    finally:
        doc.close()
    report.checks.extend(_whole_file_checks(output_path, password))
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


def _segments(path) -> list[str]:
    segs = []
    while path is not None:
        segs.append(path[0])
        path = path[1]
    segs.reverse()
    return segs


def render_path(path) -> str:
    """Text for a path node from :func:`walk_objects`, e.g.
    ``trailer/Root/Pages/Kids[0]/Annots[2]/A``. A run of one repeated
    segment — a long /Next chain — is folded: ``/Next(x4000)``."""
    segs = _segments(path)
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

    *path* is an opaque node (it remembers each object on the way down);
    :func:`render_path` turns it into text.
    """
    containers = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)
    seen: set = set()
    stack = [("trailer", None, pdf.trailer)]
    while stack:
        path = stack.pop()
        obj = path[2]
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
                # In key order: keys() is a set, and an object reachable two
                # ways must be reported under the same path on every run.
                children = [(str(k), obj[k]) for k in sorted(obj.keys())]
            for seg, child in reversed(children):
                if isinstance(child, containers):
                    stack.append((seg, path, child))
                elif strings and isinstance(child, pikepdf.String):
                    yield (seg, path, child), child
        except Exception as exc:
            # The type only: library messages quote the path of the file.
            status.problems.append(
                f"could not read {render_path(path)} ({type(exc).__name__})")


#: Keys that are the ordinary home of what is found under them, and so add
#: nothing to a description of where it is.
_ORDINARY_KEYS = frozenset({
    "/A", "/PA", "/AA", "/OpenAction", "/Next", "/Metadata", "/EF", "/FS",
    "/3DD", "/JavaScript", "/EmbeddedFiles", "/Templates", "/Info", "/Root",
    "/Pages", "/Kids", "/Annots", "/Names", "/Outlines", "/First", "/Last",
    "/Prev", "/Parent", "/P", "/AcroForm", "/Fields"})


def plain_location(segments: list[str], page: Optional[int] = None) -> str:
    """Where an object is, in plain words, from the segments of its object
    path (``["trailer", "/Root", "/Pages", "/Kids", "[0]", ...]``) and the
    number of the page it is on, if it is on one.

    "on page 3, annotation 2, under /Stash"; "in a bookmark"; "in the
    document's open action, 40 steps down a chain of actions". Written for
    someone who has never seen a PDF's object graph; the raw path is kept
    beside it for those who have.
    """
    def following(key: str) -> str:
        return segments[segments.index(key) + 1] if key in segments[:-1] else ""

    names = following("/Names") if segments[1:3] == ["/Root", "/Names"] else ""
    if page is not None:
        where = f"on page {page}"
    elif names == "/Templates":
        where = "on a template page"
    elif names == "/Pages":
        where = "on a named page"
    elif names == "/JavaScript":
        where = "in the document's named scripts"
    elif names == "/EmbeddedFiles":
        where = "in the document's attachment list"
    elif names == "/Dests":
        where = "in the document's named destinations"
    elif segments[1:3] == ["/Root", "/StructTreeRoot"]:
        where = "in the document's structure tree"
    elif segments[1:3] == ["/Root", "/OCProperties"]:
        where = "in the document's layer list"
    elif "/Outlines" in segments:
        where = "in a bookmark"
    elif "/AcroForm" in segments:
        where = "in a form field"
    elif segments[1:2] == ["/Info"]:
        where = "in the document information"
    elif segments[1:3] == ["/Root", "/OpenAction"]:
        where = "in the document's open action"
    elif segments[1:2] == ["/Root"]:
        where = "in the document catalog"
    else:
        where = "in the file trailer"
    parts = [where]

    index = following("/Annots")
    if index.startswith("[") and index[1:-1].isdigit():
        parts.append(f"annotation {int(index[1:-1]) + 1}")

    # A run of /Next at the end is a chain of actions leading to it.
    body = list(segments)
    steps = 0
    while body and body[-1] == "/Next":
        body.pop()
        steps += 1
    # The key it sits under, when that is not one of its ordinary homes
    # (an /AA trigger such as /O or /K is ordinary there).
    keys = [i for i, s in enumerate(body) if s.startswith("/")]
    if keys:
        last = keys[-1]
        is_trigger = last > 0 and body[last - 1] == "/AA"
        if body[last] not in _ORDINARY_KEYS and not is_trigger:
            parts.append(f"under {body[last]}")
    if steps:
        parts.append(f"{steps} step{'' if steps == 1 else 's'} down a chain of actions")
    return ", ".join(parts)


# ── Whole-file term sweep (redaction, #119) ──────────────────────────
#
#  The surface checks look where redaction knows a term can be: page text,
#  annotations, form values, links, document information, bookmarks. The
#  term sweep is the second look, as the sanitization sweep is: every
#  string in the file and the decoded bytes of every stream, wherever it
#  hangs. A term in a place nobody thought of is still bytes in the file.
#
#  What it does not read, and why:
#    * images — pixels, not text (finding a word in pixels is OCR, #128);
#    * the internals of fonts and colour profiles. An embedded font program
#      carries its name, its foundry and a copyright line; a character map
#      says "Adobe" and "Identity" (in nearly half of real files); a colour
#      profile says "Copyright" and names its maker. None of that is the
#      document's text, and searching it would refuse those words in every
#      file that has such a font;
#    * names. A font's name, a style name, a layer's intent are PDF names,
#      not strings.
#  So a term that is, or is part of, a font's or a profile's own name stays
#  in the output. That is the known limit of this check.

#: /Type of a font object -> the entries under it that are streams of font
#: machinery: embedded programs and binary tables, character maps.
_FONT_STREAM_KEYS = {
    "/FontDescriptor": ("/FontFile", "/FontFile2", "/FontFile3", "/CIDSet"),
    "/Font": ("/ToUnicode", "/Encoding", "/CIDToGIDMap"),
    "/CMap": ("/UseCMap",),
}

#: ... and the entries that are strings a font carries about itself.
_FONT_STRING_KEYS = {"/FontDescriptor": ("/FontFamily", "/CharSet")}


def _type_name(obj) -> str:
    kind = obj.get("/Type")
    return str(kind) if isinstance(kind, pikepdf.Name) else ""


def _font_streams(obj) -> list:
    """The streams that are font machinery because of what *obj* is: a font
    descriptor's programs, a font's character maps, a character map itself.
    Judged by the /Type the holder declares, never by a key name alone —
    a /FontFile entry on anything else is just a stream."""
    kind = _type_name(obj)
    found = [obj] if kind == "/CMap" and isinstance(obj, pikepdf.Stream) else []
    for key in _FONT_STREAM_KEYS.get(kind, ()):
        value = obj.get(key)
        if isinstance(value, pikepdf.Stream):
            found.append(value)
    return found


def _is_font_string(path) -> bool:
    """True for a string a font carries about itself: a font descriptor's
    /FontFamily or /CharSet, or the /Registry and /Ordering of a character
    collection (a dictionary that has both, as strings)."""
    holder = path[1][2] if path[1] is not None else None
    if not isinstance(holder, (pikepdf.Dictionary, pikepdf.Stream)):
        return False
    key = path[0]
    if key in _FONT_STRING_KEYS.get(_type_name(holder), ()):
        return True
    return (key in ("/Registry", "/Ordering")
            and isinstance(holder.get("/Registry"), pikepdf.String)
            and isinstance(holder.get("/Ordering"), pikepdf.String))


def _is_image(stream) -> bool:
    """An image XObject, or a thumbnail (an image that need not say so)."""
    subtype = stream.get("/Subtype")
    if isinstance(subtype, pikepdf.Name):
        return str(subtype) == "/Image"
    return ("/Width" in stream and "/Height" in stream
            and ("/BitsPerComponent" in stream or "/ImageMask" in stream))


def _owner_id(path) -> Optional[tuple]:
    """(num, gen) of the nearest indirect dictionary, stream or array at or
    above *path*: the object a hit belongs to."""
    containers = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)
    while path is not None:
        obj = path[2]
        try:
            if isinstance(obj, containers) and obj.is_indirect:
                return tuple(obj.objgen)
        except Exception:
            return None
        path = path[1]
    return None


def _pages_reaching(pdf, wanted: set) -> dict:
    """{object id: [page numbers]} — the pages each of *wanted* can be
    reached from.

    This decides whether flattening can remove a hit. Flattening rebuilds
    the document from its pages, the flattened ones as plain images, so an
    object survives it only if a page that was kept still leads to it. A
    hit reachable from pages is therefore fixed by flattening all of them;
    one no page leads to (the catalog's own entries, the structure tree) is
    document-level.

    The walk from a page stops at other pages and at the page tree, and
    takes the resources a page inherits from above it. Only run when there
    is a hit to place.
    """
    containers = (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)
    found: dict = {key: [] for key in wanted}
    for number, page in enumerate(pdf.pages, 1):
        start = page.obj
        stack = [start]
        try:
            node, hops = start, 0
            while "/Resources" not in node and hops < 64:
                node = node.get("/Parent")
                if not isinstance(node, pikepdf.Dictionary):
                    break
                hops += 1
            if hops and isinstance(node, pikepdf.Dictionary) and "/Resources" in node:
                stack.append(node["/Resources"])
        except Exception:
            pass
        seen: set = set()
        try:
            start_key = tuple(start.objgen) if start.is_indirect else None
        except Exception:
            start_key = None
        while stack:
            cur = stack.pop()
            try:
                if not isinstance(cur, containers):
                    continue
                key = tuple(cur.objgen) if cur.is_indirect else None
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                    if key in found:
                        found[key].append(number)
                if isinstance(cur, pikepdf.Array):
                    stack.extend(v for v in cur if isinstance(v, containers))
                    continue
                is_start = cur is start or (key is not None and key == start_key)
                if not is_start and _type_name(cur) in ("/Page", "/Pages", "/Catalog"):
                    continue
                skip = ("/Parent",) if is_start else ()
                stack.extend(cur[k] for k in cur.keys()
                             if k not in skip and isinstance(cur[k], containers))
            except Exception:
                continue
    return found


def _term_sweep_check(output_path: str, terms: list[str], case_sensitive: bool,
                      password: Optional[str] = None) -> VerificationCheck:
    """No target term is left in any string or stream of the output.

    Fail-closed twice over, like the sanitization sweep. A hit fails the
    check, with where it is in plain words (the raw object path is kept
    beside it). And a search that did not cover the whole file — the output
    would not open, an object or a stream could not be read — fails it too:
    what was not read cannot be reported free of the terms.

    A hit on an object some page leads to names the page (and offers the
    flatten, which removes it); any other is document-level.
    """
    desc = "No target term appears in any string or stream of the file"
    finder = TermFinder(terms, case_sensitive)
    hits: list = []            # (term, what, path)
    problems: list[str] = []
    lines: list[str] = []
    paths: list[str] = []
    pages: set = set()
    kwargs = {"password": password} if password else {}
    try:
        with pikepdf.open(output_path, **kwargs) as pdf:
            # Font machinery by identity first: a character map two fonts
            # share is one wherever the walk happens to reach it from.
            machinery: set = set()
            for obj in pdf.objects:
                try:
                    if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                        machinery.update(tuple(s.objgen) for s in _font_streams(obj))
                except Exception:
                    continue           # the walk below meets it too, and says so
            status = WalkStatus()
            for path, obj in walk_objects(pdf, status, strings=True):
                try:
                    if isinstance(obj, pikepdf.String):
                        if _is_font_string(path):
                            continue
                        found, what = finder.find(bytes(obj)), "a text string"
                    else:
                        # An inline font dictionary is in no object list.
                        machinery.update(tuple(s.objgen) for s in _font_streams(obj))
                        if not isinstance(obj, pikepdf.Stream):
                            continue
                        if tuple(obj.objgen) in machinery or _is_image(obj):
                            continue
                        data = obj.read_bytes()
                        if data[36:40] == b"acsp":       # an ICC colour profile
                            continue
                        found, what = finder.find(data), "a stream"
                except Exception as exc:
                    # The type only: library messages quote the file's path.
                    problems.append(
                        f"could not read {render_path(path)} ({type(exc).__name__})")
                    continue
                hits.extend((term, what, path) for term in found)
            problems = status.problems + problems

            owners = {id(h): _owner_id(h[2]) for h in hits}
            reach = _pages_reaching(pdf, {o for o in owners.values() if o}) if hits else {}
            placed = [(h, reach.get(owners[id(h)]) or []) for h in hits]
            seen: set = set()
            # Document-level hits first: the list is cut at twenty lines, and
            # the one that rules out the flatten must not be what is cut.
            for (term, what, path), on in sorted(placed, key=lambda p: bool(p[1])):
                raw = render_path(path)
                if (term, raw) in seen:
                    continue
                seen.add((term, raw))
                segments = _segments(path)
                if on:
                    pages.update(on)
                    more = (f", and on {len(on) - 1} more page{'' if len(on) == 2 else 's'}"
                            if len(on) > 1 else "")
                    lines.append(f"term '{term}' found in {what} "
                                 f"{plain_location(segments, on[0])}{more}")
                else:
                    lines.append(f"term '{term}' found in {what} "
                                 f"{plain_location(segments)} (document-level)")
                paths.append(raw)
    except Exception as exc:
        problems.append(f"the output could not be opened ({type(exc).__name__})")
    incomplete = [f"the whole-file search did not cover the whole file ({p}); what it "
                  f"did not read cannot be reported free of the terms (document-level)"
                  for p in problems[:5]]
    evidence = lines[:20] + ([f"... and {len(lines) - 20} more"] if len(lines) > 20 else [])
    return VerificationCheck(
        id="redaction.term_sweep", description=desc,
        passed=not (lines or incomplete), evidence=evidence + incomplete,
        pages=sorted(pages), unevaluated=bool(incomplete) and not lines,
        object_paths=paths[:20])


class SweepHit(NamedTuple):
    """One thing the sweep found."""
    option: str      # the sanitize option that promises it gone
    finding: str     # the analyzer finding it belongs under
    what: str        # "a JavaScript action"
    where: str       # in plain words: "on page 1, annotation 2"
    path: str        # the raw object path
    positional: bool = False   # found by where it is, not by what it is


#: Sanitize options the sweep has a predicate for (all of them, today).
_SWEEP_OPTIONS = ("javascript", "launch_actions", "auto_actions",
                  "submit_actions", "embedded_files", "external_links",
                  "metadata")

#: Action /S value -> (option that removes it, finding, what to call it).
_SWEEP_ACTION_TYPES = {
    "/JavaScript": ("javascript", "scripts.js", "a JavaScript action"),
    "/Launch": ("launch_actions", "actions.launch", "a launch action"),
    "/SubmitForm": ("submit_actions", "forms.submit", "a submit-form action"),
    "/ImportData": ("submit_actions", "forms.submit", "an import-data action"),
    "/URI": ("external_links", "links.uri", "a link to a web address"),
}

#: The trigger names PDF 32000 defines for additional-actions dictionaries
#: (catalog, page, annotation, form field).
_AA_TRIGGERS = frozenset({
    "/WC", "/WS", "/DS", "/WP", "/DP", "/O", "/C", "/E", "/X", "/D", "/U",
    "/Fo", "/Bl", "/PO", "/PC", "/PV", "/PI", "/K", "/F", "/V"})

_EF_KEYS = ("/F", "/UF", "/DOS", "/Mac", "/Unix")


def _xfa_packets(pdf) -> set:
    """``(num, gen)`` of every stream that is a packet of the catalog's
    /AcroForm /XFA entry: the entry itself when it is one stream, an element
    of it when it is an array.

    Those streams are the XFA form, by position, whatever /Type they
    declare — and Adobe LiveCycle Designer declares them /EmbeddedFile.
    """
    acro = pdf.Root.get("/AcroForm")
    xfa = acro.get("/XFA") if isinstance(acro, pikepdf.Dictionary) else None
    parts = list(xfa) if isinstance(xfa, pikepdf.Array) else [xfa]
    return {tuple(p.objgen) for p in parts if isinstance(p, pikepdf.Stream)}


def _sweep(pdf, opts: dict) -> tuple[dict[str, list[SweepHit]], WalkStatus]:
    """Look at every object in *pdf* for anything an enabled option removes.

    Returns ``({option: [SweepHit, ...]}, status)`` for the enabled options.

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
        a file elsewhere (reported by the analyzer as `files.external`). A
        packet of the catalog's /AcroForm /XFA entry, even one typed
        /EmbeddedFile (Adobe LiveCycle Designer types them so): by position
        it is the XFA form, which the analyzer reports as `forms.xfa` and no
        option removes (#112). A file specification that embeds such a
        packet is still a hit, as any dictionary with a payload is.
    metadata
        A stream typed /Metadata or /XML, on any object. Whatever stream is
        in the /Metadata slot of the catalog, or of any object that declares
        a /Type name (a page, a font, an image), typed or not: there the
        entry is the object's metadata by definition, and the holder says
        what it is — a resource dictionary, which may map the name /Metadata
        to an image, declares no /Type. A trailer /Info with any entry.
        Not covered: EXIF inside embedded images (#111); private application
        data such as /PieceInfo; the file identifier (checked separately);
        a former /Info dictionary still held under some other key, which
        nothing in the output marks as document information (for that
        reason Sanitize empties the dictionary itself, when it is nothing
        but document information; one that is also a page, the catalog or
        anything else with a job is only unhooked from the trailer).

    A hit at a place the analyzer has no site for is still a hit: the option
    promised the thing would be gone from the file, not from a list of
    places.
    """
    hits: dict[str, list[SweepHit]] = {o: [] for o in _SWEEP_OPTIONS if opts.get(o)}
    status = WalkStatus()
    try:
        page_numbers = {tuple(p.obj.objgen): n for n, p in enumerate(pdf.pages, 1)}
    except Exception as exc:
        page_numbers = {}
        status.problems.append(f"could not read the page tree ({type(exc).__name__})")

    def page_of(path) -> Optional[int]:
        while path is not None:
            obj = path[2]
            try:
                if (isinstance(obj, pikepdf.Dictionary) and obj.is_indirect
                        and tuple(obj.objgen) in page_numbers):
                    return page_numbers[tuple(obj.objgen)]
            except Exception:
                return None
            path = path[1]
        return None

    def hit(option: str, finding: str, what: str, path, positional: bool = False) -> None:
        if option in hits:
            hits[option].append(SweepHit(
                option, finding, what,
                plain_location(_segments(path), page_of(path)), render_path(path),
                positional))

    def typed(obj, key: str) -> str:
        v = obj.get(key)
        return str(v) if isinstance(v, pikepdf.Name) else ""

    # -- the few things that are defined by where they are --------------
    trailer_path = ("trailer", None, None)
    xfa_packets: set = set()
    try:
        root = pdf.Root
        if "embedded_files" in hits:
            xfa_packets = _xfa_packets(pdf)
        root_path = ("/Root", trailer_path, root)
        if isinstance(root.get("/OpenAction"), pikepdf.Dictionary):
            hit("auto_actions", "actions.autorun", "an action that runs on opening",
                ("/OpenAction", root_path, None), True)
        names = root.get("/Names")
        if isinstance(names, pikepdf.Dictionary):
            names_path = ("/Names", root_path, None)
            if "/JavaScript" in names:
                hit("javascript", "scripts.js", "named scripts",
                    ("/JavaScript", names_path, None), True)
            if "/EmbeddedFiles" in names:
                hit("embedded_files", "files.embedded", "an attachment list",
                    ("/EmbeddedFiles", names_path, None), True)
        if isinstance(root.get("/Metadata"), pikepdf.Stream):
            hit("metadata", "meta.xmp", "a metadata stream",
                ("/Metadata", root_path, None), True)
        info = pdf.trailer.get("/Info")
        if isinstance(info, pikepdf.Dictionary) and len(info.keys()):
            hit("metadata", "meta.docinfo",
                f"document information with {len(info.keys())} entries",
                ("/Info", trailer_path, None), True)
    except Exception as exc:
        status.problems.append(f"could not read the document catalog ({type(exc).__name__})")

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
                    hit("javascript", "scripts.js", "a script", path)
            if "/AA" in keys and "auto_actions" in hits:
                aa = obj.get("/AA")
                if isinstance(aa, pikepdf.Dictionary):
                    for trig in aa.keys():
                        entry = aa[trig] if str(trig) in _AA_TRIGGERS else None
                        if isinstance(entry, pikepdf.Dictionary) and typed(entry, "/S"):
                            hit("auto_actions", "actions.autorun",
                                "an action that runs by itself",
                                (str(trig), ("/AA", path, None), None))
            if "/EF" in keys and "embedded_files" in hits:
                ef = obj.get("/EF")
                if isinstance(ef, pikepdf.Dictionary) and any(
                        isinstance(ef.get(k), pikepdf.Stream) for k in _EF_KEYS):
                    hit("embedded_files", "files.embedded", "an embedded file", path)
            if "/Metadata" in keys and "metadata" in hits and typed(obj, "/Type"):
                md = obj.get("/Metadata")
                # A declared one is found below, as itself, wherever it is.
                if isinstance(md, pikepdf.Stream) and not (
                        typed(md, "/Type") == "/Metadata" or typed(md, "/Subtype") == "/XML"):
                    hit("metadata", "meta.xmp", "a metadata stream",
                        ("/Metadata", path, None))
            if isinstance(obj, pikepdf.Stream):
                kind, subtype = typed(obj, "/Type"), typed(obj, "/Subtype")
                if kind == "/Metadata" or subtype == "/XML":
                    hit("metadata", "meta.xmp", "a metadata stream", path)
                # Asked of the object, not of the path: the walk reaches a
                # stream by whichever reference it meets first.
                if kind == "/EmbeddedFile" and tuple(obj.objgen) not in xfa_packets:
                    hit("embedded_files", "files.embedded", "an embedded file's contents", path)
                if kind == "/3D" and isinstance(obj.get("/OnInstantiate"), pikepdf.Stream):
                    hit("javascript", "scripts.js",
                        "a script that runs when 3D artwork loads", path)
        except Exception as exc:
            status.problems.append(
                f"could not inspect {render_path(path)} ({type(exc).__name__})")
    return hits, status


def _sweep_checks(output_path: str, opts: dict) -> list[VerificationCheck]:
    """One check per enabled option: the sweep found nothing it removes.

    Fail-closed twice over. A hit fails the option's check; its evidence
    says what was found and where in plain words, and the raw object path is
    kept beside it. And a sweep that did not cover the whole file — the
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
        hits, problems = {}, [f"the output could not be opened ({type(exc).__name__})"]
    incomplete = [f"the sweep did not cover the whole file ({p}); what it "
                  f"did not inspect cannot be reported absent (document-level)"
                  for p in problems[:5]]
    checks = []
    for opt in enabled:
        found: dict[str, str] = {}                      # raw path -> plain evidence
        for h in hits.get(opt, []):
            found.setdefault(h.path, f"{h.what} {h.where}")
        paths = list(found)[:20]
        ev = [found[p] for p in paths]
        if len(found) > 20:
            ev.append(f"... and {len(found) - 20} more")
        checks.append(VerificationCheck(
            id=f"sanitize.sweep_{opt}",
            description=f"Whole-file sweep finds nothing option '{opt}' removes",
            passed=not (found or incomplete), evidence=ev + incomplete,
            unevaluated=bool(incomplete) and not found, object_paths=paths))
    return checks


#: What to call each category in a sentence. A site check and the sweep
#: check for the same category share a name, so it is mentioned once.
_CATEGORY_WORDS = {
    "scripts_js": "JavaScript", "sweep_javascript": "JavaScript",
    "actions_launch": "launch actions", "sweep_launch_actions": "launch actions",
    "actions_autorun": "auto-run actions", "sweep_auto_actions": "auto-run actions",
    "forms_submit": "form submit actions", "sweep_submit_actions": "form submit actions",
    "files_embedded": "embedded files", "sweep_embedded_files": "embedded files",
    "links_uri": "external links", "sweep_external_links": "external links",
    "meta_docinfo": "document metadata", "meta_xmp": "document metadata",
    "sweep_metadata": "document metadata",
}


def sanitize_refusal_message(report: VerificationReport) -> str:
    """One sentence saying why a sanitize was refused, from its failed checks.

    Three different things can have gone wrong, and the user needs to know
    which: something the options promised to remove is still there (it is
    named); something could not be checked at all (it is named, and said to
    be unchecked, not present); or the output would still carry its source's
    identifier. Where exactly, and the object paths, are on the report.
    """
    failed = report.failed_checks()

    def words(checks) -> list[str]:
        return _dedupe([_CATEGORY_WORDS.get(c.id.split(".", 1)[-1],
                                            "content the selected options remove")
                        for c in checks])

    special = {"sanitize.file_id", "sanitization.readable"}
    rest = [c for c in failed if c.id not in special]
    survivors = words(c for c in rest if not c.unevaluated)
    unchecked = [w for w in words(c for c in rest if c.unevaluated) if w not in survivors]
    parts = []
    if survivors:
        parts.append(f"still present in the output: {', '.join(survivors)}")
    if unchecked:
        parts.append(f"{', '.join(unchecked)} could not be checked")
    if any(c.id == "sanitization.readable" for c in failed):
        parts.append("the cleaned file could not be read back")
    if any(c.id == "sanitize.file_id" for c in failed):
        parts.append("the output would still carry the source file's identifier")
    return "Sanitize could not be verified: " + ("; ".join(parts) or "a check failed") + "."


def sanitize_refusal_detail(report: VerificationReport) -> dict:
    """The same refusal as the short lists a screen shows.

    ``stillPresent``
        One line for each category an enabled option promised to remove and
        the output still has: its name, then where. The places are the
        sweep's evidence, which is already in plain words; where only the
        audit found it, they are the audit's own lines, the ones Analyze
        lists. Three places at most — the report has them all.
    ``notChecked``
        The categories that could not be checked at all. A category that is
        still present is not repeated here.
    ``identifier``
        The output would still carry its source's identifier.
    ``unreadable``
        The output could not be read back.

    Everything is empty or false for a report that verified. The wording is
    produced here, not in the window, so that it is in one place and tested.
    """
    def name(check: VerificationCheck) -> str:
        words = _CATEGORY_WORDS.get(check.id.split(".", 1)[-1],
                                    "content the selected options remove")
        return words[:1].upper() + words[1:]

    failed = report.failed_checks()
    special = {"sanitize.file_id", "sanitization.readable"}
    swept: dict[str, list[str]] = {}        # category -> where, in the sweep's words
    audited: dict[str, list[str]] = {}      # category -> the audit's own lines
    present, unchecked = [], []
    for c in failed:
        if c.id in special:
            continue
        if c.unevaluated:
            unchecked.append(name(c))
            continue
        present.append(name(c))
        if c.id.startswith("sanitize.sweep_"):
            # A sweep check lists what it found first, one line per object
            # path; anything after that is about the sweep, not a place.
            swept.setdefault(name(c), []).extend(c.evidence[:len(c.object_paths)])
        else:
            # The first line names the finding; a later one may say the
            # audit also could not finish.
            audited.setdefault(name(c), []).extend(
                e for e in c.evidence[1:] if "could not be fully checked" not in e)

    still = []
    for category in _dedupe(present):
        places = _dedupe(swept.get(category) or audited.get(category, []))
        shown = "; ".join(places[:3] + (["and others"] if len(places) > 3 else []))
        still.append(f"{category}: {shown}" if shown else category)
    return {
        "stillPresent": still,
        "notChecked": [c for c in _dedupe(unchecked) if c not in present],
        "identifier": any(c.id == "sanitize.file_id" for c in failed),
        "unreadable": any(c.id == "sanitization.readable" for c in failed),
    }


# ── What a refused redaction says (#119, R4) ─────────────────────────


def pages_in_words(pages, cap: int = 8) -> str:
    """Page numbers as a person reads them: "page 3", "pages 1 and 3",
    "pages 2-4 and 9". Runs of three pages or more are joined. More than *cap* runs are
    cut to the first six and a count of the rest: "pages 3, 6, 9, 12, 15,
    18 and 94 more"."""
    numbers = sorted({int(p) for p in pages})
    if not numbers:
        return "the pages"
    runs: list[list[int]] = []
    for n in numbers:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    # A run of three pages or more is joined with a dash; two read better apart.
    runs = [r for a, b in runs for r in ([[a, a], [b, b]] if b == a + 1 else [[a, b]])]
    texts = [str(a) if a == b else f"{a}-{b}" for a, b in runs]
    if len(runs) > cap:
        shown = runs[:6]
        rest = len(numbers) - sum(b - a + 1 for a, b in shown)
        texts = texts[:6] + [f"{rest:,} more"]
    head = "page" if len(numbers) == 1 else "pages"
    body = texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " and " + texts[-1]
    return f"{head} {body}"


_SEARCHED = "What you searched for"
_TERM_FOUND = re.compile(r"^term '.*?' found in ")


def _evidence_pages(lines) -> list[int]:
    """The pages *lines* name ("on page N"), document-level lines aside."""
    return VerificationCheck._derive_pages(list(lines))


# plain_location's words for the PDF's insides, as the Redact window says them.
_PLAIN_WHERE = {
    "in the document information": "in the document's details",
    "in the document's named scripts": "in the file's scripts",
    "in the document's attachment list": "in the file's list of attached files",
    "in the document's named destinations": "in the file's list of places to jump to",
    "in the document's structure tree": "in the file's structure",
    "in the document's layer list": "in the file's list of layers",
    "in the document's open action": "in what the file does when it opens",
    "in the document catalog": "in the file's own settings",
    "in the file trailer": "in the file's own settings",
}


def _sweep_place(line: str) -> str:
    """Where a whole-file search hit is, without the PDF's own names: "term
    'x' found in a stream on page 2, annotation 1, under /Stash" is "in a
    note or mark on page 2". The raw line stays in the technical details."""
    place = _TERM_FOUND.sub("", line).replace(" (document-level)", "").rstrip(".")
    parts = re.sub(r"^a (text string|stream) ", "", place).split(", ")
    where = _PLAIN_WHERE.get(parts[0], parts[0])
    if where.startswith("on ") and any(re.fullmatch(r"annotation \d+", p) for p in parts):
        where = f"in a note or mark {where}"
    return ", ".join([where] + [p for p in parts[1:] if p.startswith("and on ")])


def _named_elsewhere(failed) -> set:
    """The places, as _sweep_place says them, that a failed check other than
    the whole-file search already names, so that the window says each once."""
    named: set = set()
    for c in failed:
        pages = _evidence_pages(c.evidence)
        if c.id == "redaction.docinfo":
            named.add("in the document's details")
        elif c.id == "redaction.bookmarks":
            named.add("in a bookmark")
        elif c.id in ("redaction.annotations", "redaction.links", "redaction.form_fields"):
            named.update(f"in a note or mark on page {p}" for p in pages)
            if c.id == "redaction.form_fields":
                named.add("in a form field")
        elif c.id in ("redaction.page_text", "redaction.raw_content"):
            named.update(f"on page {p}" for p in pages)
    return named


def _refusal_lines(check: VerificationCheck, named: frozenset = frozenset()) -> list[str]:
    """What one failed redaction check found, in plain words. *named* is the
    places other checks already name (_named_elsewhere); the whole-file
    search leaves them out."""
    unread = [e for e in check.evidence if "could not be" in e or "did not cover" in e]
    seen = [e for e in check.evidence if e not in unread and not e.startswith("... and")]
    lines: list[str] = []
    if unread or check.unevaluated:
        if check.id == "redaction.rect_regions":
            lines.append("A box could not be checked, because it has no size or is for a page "
                         "the file does not have.")
        elif check.id == "redaction.readable":
            lines.append("The new file could not be opened to check it.")
        else:
            pages = _evidence_pages(unread)
            where = pages_in_words(pages) if pages else "the file"
            lines.append(f"Part of {where} could not be read, so it could not be checked.")
    if not seen or check.unevaluated:
        return lines
    pages = _evidence_pages(seen)
    where = pages_in_words(pages)
    cid = check.id
    if cid == "redaction.page_text":
        lines.append(f"{_SEARCHED} can still be read in the text of {where}.")
    elif cid == "redaction.raw_content":
        lines.append(f"{_SEARCHED} is still in the hidden page code of {where} (for example, "
                     f"text placed outside the visible page).")
    elif cid == "redaction.annotations":
        lines.append(f"{_SEARCHED} is still in a note or comment"
                     + (f" on {where}." if pages else "."))
    elif cid == "redaction.form_fields":
        lines.append(f"{_SEARCHED} is still in a form field"
                     + (f" on {where}." if pages else " that is not shown on any page."))
    elif cid == "redaction.links":
        lines.append(f"{_SEARCHED} is still in a link" + (f" on {where}." if pages else "."))
    elif cid == "redaction.docinfo":
        lines.append(f"{_SEARCHED} is still in the document's details, such as the title or "
                     f"author.")
    elif cid == "redaction.xmp":
        lines.append(f"{_SEARCHED} is still in the document's hidden details (XMP).")
    elif cid == "redaction.bookmarks":
        lines.append(f"{_SEARCHED} is still in a bookmark title.")
    elif cid == "redaction.embedded_files":
        lines.append(f"{_SEARCHED} is still in the name of an attached file.")
    elif cid == "redaction.term_sweep":
        places = [p for p in _dedupe([_sweep_place(e) for e in seen if _TERM_FOUND.match(e)])
                  if p.split(", and on ")[0] not in named]
        more = max(len(places) - 3, 0) + sum(
            int(m.group(1).replace(",", "")) for e in check.evidence
            for m in [re.match(r"^\.\.\. and ([\d,]+) more", e)] if m)
        lines.extend(f"{_SEARCHED} is still hidden {place}." for place in places[:3])
        if more > 0:
            lines.append(f"And in {more:,} more place{'' if more == 1 else 's'} in the file.")
    elif cid == "redaction.rect_regions":
        lines.append(f"Text is still under a box you drew on {where}.")
    elif cid == "redaction.rect_annotations":
        lines.append(f"A note, stamp or other mark still lies over a box you drew on {where}.")
    elif cid == "redaction.rect_line_art":
        lines.append(f"A drawing under a box on {where} could not be removed without also "
                     f"removing the part of it that goes on outside the box.")
    elif cid == "redaction.structure_streams":
        if any("stream" in e for e in seen):
            lines.append("The file's structure still keeps a hidden copy of content that was "
                         "removed.")
        if any("annotation" in e for e in seen):
            lines.append("The file's structure still keeps a note or form field that was taken "
                         "off its page.")
    elif cid == "redaction.thumbnails":
        lines.append("The file still has small preview pictures of pages, made before "
                     "redaction." if len(seen) > 1 else
                     "The file still has a small preview picture of a page, made before "
                     "redaction.")
    elif cid == "redaction.file_id":
        lines.append("The new file would still carry the original file's ID, which links the "
                     "two files.")
    elif cid == "redaction.ocr_fill":
        lines.append(f"Something is drawn over a place on {where} where OCR found what you "
                     f"searched for, so it does not show black.")
    elif cid == "redaction.ocr_image":
        lines.append(f"A picture on {where} still holds what OCR found, under the black box. "
                     f"It could be taken out of the file.")
    elif cid == "redaction.ocr_reread":
        lines.append(f"OCR can still read {_SEARCHED.lower()} in the picture of {where}.")
    elif cid == "redaction.ocr_outside":
        lines.append(f"The picture of {where} would have changed outside the places that were "
                     f"blacked out.")
    else:
        lines.append("Something you asked to remove is still in the new file.")
    return lines


#: The headline of a refusal whose only failure is redaction.ocr_outside:
#: damage, not a leak (#128). pdf_ops' one-line message says the same.
DAMAGE_HEADLINE = ("Redaction refused: it would have changed more of the page than the places "
                   "found. Nothing was saved.")


def redaction_refusal_detail(report: VerificationReport, offer: Optional[list] = None) -> dict:
    """A refused redaction in plain words, for the window, from its failed
    checks (#119, R4). *offer* is the pages redact_pdf offers to turn into
    pictures (RedactionVerificationError.flatten_pages), None for none.

    ``headline``
        One sentence: something asked to be removed is still in the new
        file; or part of it could not be read, so it could not be checked;
        or the new file would still be linked to the original by its ID.
        Each ends "Nothing was saved."
    ``found``
        What was found, and where: one line per place, the pages named.
    ``nextStep``
        What can be done: turn the pages offered into pictures (which
        covers a drawing under a box, and removes anything else on them),
        or why that would not help. No promise of a later fix.

    Like sanitize_refusal_detail, the wording is made here so that it is
    in one place and tested; the window shows the lines it is given.
    """
    failed = report.failed_checks()
    named = frozenset(_named_elsewhere(failed))
    found = _dedupe([line for c in failed for line in _refusal_lines(c, named)])
    identifier = bool(failed) and all(c.id == "redaction.file_id" for c in failed)
    unread = bool(failed) and all(
        c.unevaluated or all("could not be" in e or "did not cover" in e for e in c.evidence)
        for c in failed)
    damage = bool(failed) and all(c.id == "redaction.ocr_outside" for c in failed)
    if identifier:
        headline = ("Redaction refused: the new file would still be linked to the original. "
                    "Nothing was saved.")
        step = "The ID is not on any page, so turning pages into pictures cannot change it."
    elif damage:
        headline = DAMAGE_HEADLINE
        step = "The file was left as it was."
    else:
        headline = ("Redaction refused: part of the new file could not be read, so it could "
                    "not be checked. Nothing was saved." if unread else
                    "Redaction refused: some of what you asked to remove is still in the new "
                    "file. Nothing was saved.")
        if offer:
            many = len(set(offer)) > 1
            verb = ("cover" if all(c.id == "redaction.rect_line_art" for c in failed)
                    else "remove")
            step = (f"You can turn {pages_in_words(offer)} into "
                    f"{'pictures' if many else 'a picture'} to {verb} it. "
                    f"{'The pages keep their look' if many else 'The page keeps its look'}, "
                    f"with what was found blacked out, but {'their' if many else 'its'} text "
                    f"can no longer be selected or searched.")
        elif report.flatten_target_pages():
            many = len(report.flatten_target_pages()) > 1
            step = (f"Turning the {'pages' if many else 'page'} into "
                    f"{'pictures' if many else 'a picture'} did not remove it.")
        elif unread:
            step = "Nothing was found there, but a file that could not be checked is not saved."
        elif any(c.id == "redaction.ocr_reread" for c in failed):
            step = ("It is in the picture of the page, so turning the page into a picture would "
                    "not remove it.")
        else:
            step = "It is not drawn on a page, so turning pages into pictures would not remove it."
    return {"headline": headline, "found": found, "nextStep": step}


def verify_sanitization(output_path: str, options: Optional[dict] = None, *,
                        input_path: str = "",
                        source_ids: Optional[list[str]] = None,
                        source_sha256: Optional[str] = None) -> VerificationReport:
    """Re-audit a sanitized output and assert the promised removals held.

    *options* is the same dict passed to ``sanitize_pdf`` (missing keys
    default via ``DEFAULT_SANITIZE``). For each ENABLED option:

      * the mapped finding id(s) must be absent from a fresh
        ``analyze_document`` run on the output, AND that run must have been
        able to evaluate them — a finding the analyzer could not fully check
        is a failed check, marked ``unevaluated``, never a pass;
      * the whole-file sweep must find nothing the option removes, and must
        have covered the whole file.

    Findings outside the promised set are returned in ``residual_findings``
    — honest residue, not a failure. Findings outside the promised set that
    could not be evaluated go in ``unevaluated``, so the report never
    implies they were checked.

    *source_ids* (the source's trailer /ID elements, hex) and
    *source_sha256* are plain values the caller captured while it had the
    input open; this function never opens the input. When *source_ids* is
    given, a ``sanitize.file_id`` check asserts the output shares none of
    them — unless output and source are byte-identical, which is what
    sanitizing an already-clean file produces (see :func:`_file_id_check`).

    Fail-closed: a locked or unreadable output yields a failed
    ``sanitization.readable`` check, never a pass (a locked file re-scans
    as a single info finding with nothing inspected — that must not count
    as clean).
    """
    opts = {**DEFAULT_SANITIZE, **(options or {})}
    report = VerificationReport(input_path=input_path, output_path=output_path,
                                tool="sanitization", timestamp=_now_iso(),
                                source_sha256=source_sha256)
    if os.path.isfile(output_path):
        report.output_sha256 = _output_sha256(output_path)

    readable = VerificationCheck(
        id="sanitization.readable",
        description="Output opens and re-scans for verification", passed=True)
    try:
        # Without the analyzer's own whole-file step: the sweep below is that
        # check, run against the promises made.
        scan = analyze_document(output_path, unremovable=False)
    except FileNotFoundError:
        raise
    except Exception as exc:
        readable.passed, readable.unevaluated = False, True
        readable.evidence = [f"output could not be re-scanned "
                             f"({type(exc).__name__}) (document-level)"]
        report.checks.append(readable)
        return report

    findings = scan.get("findings", [])
    unevaluated = scan.get("unevaluated", {})
    by_id: dict[str, dict] = {}
    for f in findings:
        by_id.setdefault(f["id"], f)

    if "encryption.locked" in by_id:
        readable.passed, readable.unevaluated = False, True
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
            unread = unevaluated.get(fid)
            ev: list[str] = []
            if found is not None:
                ev.append(f"'{found['title']}' still detected in the output")
                ev.extend(str(item) for item in found.get("items", [])[:20])
            if unread:
                ev.append(f"'{fid}' could not be fully checked: "
                          f"{'; '.join(unread[:3])} (document-level)")
            report.checks.append(VerificationCheck(
                id="sanitize." + fid.replace(".", "_"),
                description=f"Re-scan finds no '{fid}' finding "
                            f"(promised by option '{opt}')",
                passed=not ev, evidence=ev,
                unevaluated=bool(unread) and found is None))

    # The second, independent look: every object in the output, judged by
    # what it is rather than by where the analyzer expects to find it.
    report.checks.extend(_sweep_checks(output_path, opts))

    if source_ids is not None:
        report.checks.append(_file_id_check(
            "sanitize.file_id", output_path, source_ids, source_sha256,
            report.output_sha256))

    report.unevaluated = [{"id": fid, "reasons": list(why)}
                          for fid, why in unevaluated.items() if fid not in promised]
    report.residual_findings = [
        f for f in findings
        if f["id"] not in promised and f["id"] not in ("clean", "audit.incomplete")
    ]
    return report
