"""
pdf_analyze — Document privacy & security audit engine.

Scans a PDF and reports everything that is privacy- or security-relevant
*without* sending anything anywhere.  Everything runs locally on pikepdf
(required) with optional PyMuPDF (fitz) enhancements when available.

What it surfaces
----------------
  - Identifying metadata (every /Info entry; XMP streams on any object,
    found by presence — parseable or not)
  - Embedded JavaScript (the name tree, and every action site below)
  - Auto-run actions (/OpenAction, and /AA on the document, a page, an
    annotation or a form field)
  - Launch actions (/Launch — can start external programs)
  - External links & URIs (potential tracking beacons / phone-home)
  - Remote/embedded GoTo actions (/GoToR, /GoToE)
  - Embedded files / attachments (any file specification with a payload,
    wherever it sits), and references to external files

Actions are looked for at every place one can hang — the catalog, pages,
annotations, bookmarks and form fields, each down its /Next chain — through
one definition (:func:`_action_owners`) that the sanitizer shares, so what
is reported and what can be removed do not drift apart.
  - Form submit / import actions (/SubmitForm, /ImportData) and XFA forms
  - Hidden optional-content layers (OCG)
  - Invisible text (text render mode 3 — common in fake "redactions")
  - Encryption status

Plus :func:`sanitize_pdf`, a one-pass cleaner that strips the dangerous
bits in place (or to a new file) and reports exactly what it removed. It
verifies the cleaned copy before committing it and refuses one it cannot
prove clean (``pdf_verify.verify_sanitization``). :func:`sanitize_payload`
is that same run as the JSON-ready answer the app's bridge returns —
success, refusal or failure — kept here so it is testable without Qt.

The module is dependency-light and import-safe: optional libraries are
imported lazily inside the functions that need them.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import tempfile
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, NamedTuple, Optional

import pikepdf

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB — matches the rest of the toolkit

# Severity ordering for rollups
_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


# ════════════════════════════════════════════════════════════════════
#  Result types
# ════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    """A single audit observation."""
    id: str
    category: str          # metadata | scripts | actions | links | files | forms | content | encryption
    severity: str          # info | low | medium | high
    title: str
    detail: str = ""
    count: int = 0
    items: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    file_name: str = ""
    file_path: str = ""
    file_size: int = 0
    pages: int = 0
    pdf_version: str = ""
    encrypted: bool = False
    findings: list[Finding] = field(default_factory=list)
    #: finding id -> why it could not be fully evaluated. An id in here means
    #: "not checked", which is not "not there": the audit tolerates a
    #: structure it cannot read and carries on, but it says so, by the id of
    #: every finding that structure could have produced.
    unevaluated: dict[str, list[str]] = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def incomplete(self, ids, reasons) -> None:
        """Record that the findings *ids* could not be fully evaluated."""
        reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        if not reasons:
            return                  # nothing went wrong: nothing to record
        for fid in ids:
            known = self.unevaluated.setdefault(fid, [])
            known.extend(r for r in reasons if r not in known)

    def find(self, fid: str) -> Optional[Finding]:
        return next((f for f in self.findings if f.id == fid), None)

    # -- rollups -------------------------------------------------------
    def counts(self) -> dict[str, int]:
        out = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out

    def overall_risk(self) -> str:
        worst = "info"
        for f in self.findings:
            if _SEVERITY_RANK.get(f.severity, 0) > _SEVERITY_RANK[worst]:
                worst = f.severity
        return worst

    def to_dict(self) -> dict[str, Any]:
        d = {
            "fileName": self.file_name,
            "filePath": self.file_path,
            "fileSize": self.file_size,
            "fileSizeStr": _fmt_size(self.file_size),
            "pages": self.pages,
            "pdfVersion": self.pdf_version,
            "encrypted": self.encrypted,
            "findings": [asdict(f) for f in self.findings],
            "counts": self.counts(),
            "overallRisk": self.overall_risk(),
            "unevaluated": {fid: list(why) for fid, why in self.unevaluated.items()},
        }
        return d


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


# ════════════════════════════════════════════════════════════════════
#  Helpers — safe traversal of the PDF object graph
# ════════════════════════════════════════════════════════════════════

def _as_str(obj: Any) -> str:
    try:
        return str(obj)
    except Exception:
        return ""


# ── Tolerant, not silent ─────────────────────────────────────────────
#
#  The walkers below are tolerant by design: one structure they cannot read
#  must not end the audit or the cleanup. But "could not look" is not
#  "nothing there", so every place that swallows an exception reports it
#  with _trouble(). Whoever is running the walk collects the reports — the
#  analyzer turns them into "this finding could not be evaluated", the
#  sanitizer keeps them as diagnostics for a refusal. A context variable
#  carries the collector so the walkers, which both sides share, need no
#  extra parameter and cannot be called in a way that drops the report.

_trouble_sink: ContextVar[Optional[list]] = ContextVar("_trouble_sink", default=None)


def _trouble(what: str, exc: Optional[BaseException] = None) -> None:
    """Report a structure that could not be read. *what* says which, in
    words that name no file; of the exception only its type is kept, because
    library messages quote the path of the file they were reading."""
    sink = _trouble_sink.get()
    if sink is not None:
        note = what if exc is None else f"{what} ({type(exc).__name__})"
        if note not in sink:
            sink.append(note)


@contextmanager
def _collecting_trouble():
    """Collect what the walkers report while the block runs; yields the list."""
    notes: list[str] = []
    token = _trouble_sink.set(notes)
    try:
        yield notes
    finally:
        _trouble_sink.reset(token)


def _iter_all_objects(pdf: pikepdf.Pdf):
    """Yield every indirect object in the file once."""
    try:
        for obj in pdf.objects:
            yield obj
    except Exception as exc:
        _trouble("the list of objects in the file could not be read to the end", exc)


def _name_tree_leaves(node):
    """Yield (node, entries) for every node of a name tree that holds
    entries: leaves hold ``[key, value, key, value, ...]`` in /Names,
    intermediate nodes hold /Kids. Iterative and cycle-safe, so it needs no
    depth limit."""
    seen, stack = set(), [node]
    while stack:
        cur = stack.pop()
        try:
            if not isinstance(cur, pikepdf.Dictionary):
                continue
            if cur.is_indirect:
                key = tuple(cur.objgen)
                if key in seen:
                    continue
                seen.add(key)
            entries = cur.get("/Names")
            kids = cur.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                stack.extend(list(kids)[::-1])
            if isinstance(entries, pikepdf.Array):
                yield cur, entries
        except Exception as exc:
            _trouble("part of a name tree could not be read", exc)


def _name_tree_items(node):
    """Yield (key, value) for every entry of a name tree (see
    _name_tree_leaves)."""
    for _leaf, entries in _name_tree_leaves(node):
        try:
            for i in range(0, len(entries) - 1, 2):
                yield entries[i], entries[i + 1]
        except Exception as exc:
            _trouble("part of a name tree could not be read", exc)


def _name_tree_values(node):
    """The values of a name tree (see _name_tree_items)."""
    for _key, value in _name_tree_items(node):
        yield value


#: Name trees under the catalog's /Names whose values are pages that are not
#: in the page tree: page templates, and "named pages". A viewer can show or
#: spawn them, so whatever hangs on them is in the document.
_PAGE_NAME_TREES = (("/Templates", "Template page"), ("/Pages", "Named page"))


def _all_pages(pdf: pikepdf.Pdf):
    """Yield (label, page dictionary) for every page of the document: the
    ones in the page tree, then the ones held only by the /Names /Templates
    and /Names /Pages name trees. Each page once.

    The analyzer and the sanitizer both take their pages from here, so a
    page one of them walks is a page the other walks.
    """
    seen = set()

    def first_visit(obj) -> bool:
        try:
            if not obj.is_indirect:
                return True
            key = tuple(obj.objgen)
        except Exception as exc:
            _trouble("a page's identity could not be read", exc)
            return True
        if key in seen:
            return False
        seen.add(key)
        return True

    try:
        tree_pages = [page.obj for page in pdf.pages]
    except Exception as exc:
        _trouble("the page tree could not be read", exc)
        tree_pages = []
    for pi, page in enumerate(tree_pages, 1):
        if first_visit(page):
            yield f"Page {pi}", page
    try:
        names = pdf.Root.get("/Names")
        trees = ([(names.get(key), word) for key, word in _PAGE_NAME_TREES]
                 if isinstance(names, pikepdf.Dictionary) else [])
    except Exception as exc:
        _trouble("the catalog's name trees could not be read", exc)
        trees = []
    for tree, word in trees:
        if tree is None:
            continue
        for name, page in _name_tree_items(tree):
            if isinstance(page, pikepdf.Dictionary) and first_visit(page):
                yield f"{word} {_label(name)}", page


_URL_RE = re.compile(r"https?://[^\s/]+", re.IGNORECASE)


def _domain_of(uri: str) -> str:
    m = _URL_RE.match(uri.strip())
    if not m:
        return uri.strip()[:80]
    return m.group(0).lower()


# ════════════════════════════════════════════════════════════════════
#  Individual scanners
# ════════════════════════════════════════════════════════════════════

#: The standard /Info keys, reported first and in this order; any other key
#: follows alphabetically.
_INFO_STANDARD = ("/Author", "/Creator", "/Producer", "/Title",
                  "/Subject", "/Keywords", "/CreationDate", "/ModDate")

#: /Info entries that say nothing about who made the file or how. /Trapped is
#: a prepress flag (/True, /False or /Unknown).
_INFO_NOT_IDENTIFYING = frozenset({"/Trapped"})

#: What a value in a document information dictionary can be: a string, a
#: name, a number or a boolean (pikepdf hands the last two over as Python
#: values).
_INFO_VALUE_TYPES = (pikepdf.String, pikepdf.Name, int, float, Decimal)


def _is_plain_info(obj) -> bool:
    """True if *obj* is nothing but document information: a dictionary, not
    a stream, that declares no /Type, /Subtype or /S, and whose every value
    is a string, a name, a number or a boolean.

    The trailer's /Info is whatever object the trailer points at, and a file
    can point it at one with another job: a real one has /Info and the page
    tree root as the same object. Such an object says what it is (/Type,
    /Subtype, /S) or holds what document information never does — an array,
    a dictionary, a stream. Only an object that passes this test may be
    emptied; the others are left exactly as they are.
    """
    try:
        if not isinstance(obj, pikepdf.Dictionary):
            return False
        keys = obj.keys()
        return (not keys & {"/Type", "/Subtype", "/S"}
                and all(isinstance(obj[k], _INFO_VALUE_TYPES) for k in keys))
    except Exception as exc:
        _trouble("the document information dictionary could not be read", exc)
        return False


def _is_metadata_stream(obj) -> bool:
    """True for a stream that declares itself metadata: /Type /Metadata or
    /Subtype /XML.

    Judged by what the stream says it is, never by the key it is stored
    under. A font or image resource may legally be *named* /Metadata, and
    deleting "the /Metadata key" there would take page content with it.
    """
    try:
        return (isinstance(obj, pikepdf.Stream)
                and (_as_str(obj.get("/Type")) == "/Metadata"
                     or _as_str(obj.get("/Subtype")) == "/XML"))
    except Exception as exc:
        _trouble("a stream's type could not be read", exc)
        return False


def _metadata_slots(pdf: pikepdf.Pdf):
    """Yield (holder, stream) for every /Metadata entry that is metadata by
    position: on the catalog, on a page, and on any other object that says
    what it is with a /Type name.

    On those the entry is defined to be the object's metadata, and it is
    where a reader looks, so the stream there counts whether or not it is
    typed. A resource dictionary is not such an object: it maps arbitrary
    names — /Metadata among them — to fonts and images, and it declares no
    /Type.
    """
    def candidates():
        try:
            yield pdf.Root, True
        except Exception as exc:
            _trouble("the document catalog could not be read", exc)
        for _label, page in _all_pages(pdf):
            yield page, True
        for obj in _iter_all_objects(pdf):
            yield obj, False

    seen = set()
    for holder, known in candidates():
        try:
            if not isinstance(holder, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            md = holder.get("/Metadata")
            if not isinstance(md, pikepdf.Stream):
                continue
            if not (known or isinstance(holder.get("/Type"), pikepdf.Name)):
                continue
            if holder.is_indirect:
                key = tuple(holder.objgen)
                if key in seen:
                    continue
                seen.add(key)
            yield holder, md
        except Exception as exc:
            _trouble("an object's metadata entry could not be read", exc)


def _metadata_streams(pdf: pikepdf.Pdf) -> dict:
    """{(num, gen): stream} for every metadata stream in the file.

    Found by presence. Nothing is decoded or parsed, so a packet that is
    malformed, in another encoding, or not decodable at all still counts —
    it is in the file either way. Covers every stream typed as metadata, on
    whatever object it hangs, plus whatever stream sits in a /Metadata slot
    (_metadata_slots) even if untyped.
    """
    found = {}
    for obj in _iter_all_objects(pdf):
        if _is_metadata_stream(obj):
            found[tuple(obj.objgen)] = obj
    for _holder, md in _metadata_slots(pdf):
        found.setdefault(tuple(md.objgen), md)
    return found


def _metadata_locations(pdf: pikepdf.Pdf, streams: dict) -> list[str]:
    """Where each metadata stream hangs, for the report: the catalog, a page,
    or — for one attached to neither — its object number."""
    where, placed = [], set()

    def held_by(holder, label: str) -> None:
        try:
            md = holder.get("/Metadata")
            if isinstance(md, pikepdf.Stream) and tuple(md.objgen) in streams:
                where.append(label)
                placed.add(tuple(md.objgen))
        except Exception as exc:
            _trouble(f"the metadata entry of {label.lower()} could not be read", exc)

    held_by(pdf.Root, "Document catalog")
    for label, page in _all_pages(pdf):
        held_by(page, label)
    for num, gen in sorted(set(streams) - placed):
        where.append(f"Object {num} {gen} (not on the catalog or a page)")
    return where


def _scan_metadata(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Identifying document-info fields and XMP metadata streams."""
    leaky = []
    with _collecting_trouble() as trouble:
        try:
            docinfo = pdf.trailer.get("/Info")
            keys = ([str(k) for k in docinfo.keys()]
                    if isinstance(docinfo, pikepdf.Dictionary) else [])
        except Exception as exc:
            _trouble("the document information dictionary could not be read", exc)
            docinfo, keys = None, []
        # Every entry counts, not only the standard ones: a custom key such
        # as /Company identifies its author just as well as /Author does.
        for k in ([k for k in _INFO_STANDARD if k in keys]
                  + sorted(k for k in keys if k not in _INFO_STANDARD)):
            if k in _INFO_NOT_IDENTIFYING:
                continue
            try:
                val = _as_str(docinfo[k]).strip()
                if val:
                    leaky.append(f"{k[1:]}: {val[:120]}")
            except Exception as exc:
                _trouble("a document information entry could not be read", exc)
    res.incomplete(("meta.docinfo",), trouble)

    with _collecting_trouble() as trouble:
        streams = _metadata_streams(pdf)
        locations = _metadata_locations(pdf, streams) if streams else []
    res.incomplete(("meta.xmp",), trouble)

    if leaky:
        res.add(Finding(
            id="meta.docinfo",
            category="metadata",
            severity="low",
            title="Document metadata present",
            detail="These /Info fields travel with the file and can identify "
                   "the author, software, and creation history.",
            count=len(leaky),
            items=leaky,
        ))
    if streams:
        res.add(Finding(
            id="meta.xmp",
            category="metadata",
            severity="low",
            title="XMP metadata stream present",
            detail="An XMP packet can carry author, tool, edit-history, and "
                   "GPS/device hints beyond the basic /Info dictionary. It "
                   "can be attached to the document or to a single page or "
                   "object.",
            count=len(streams),
            items=locations,
        ))


_TRIGGER_MEANINGS = {
    "/WC": "fires just before the document is closed",
    "/WS": "fires just before the document is saved",
    "/DS": "fires right after the document is saved",
    "/WP": "fires just before the document is printed",
    "/DP": "fires right after the document is printed",
    "/O":  "fires when the page is opened",
    "/C":  "fires when the page is closed",
    "/E":  "fires when the pointer enters the field",
    "/X":  "fires when the pointer leaves the field",
    "/D":  "fires on mouse-down in the field",
    "/U":  "fires on mouse-up in the field",
    "/Fo": "fires when the field gains focus",
    "/Bl": "fires when the field loses focus",
    "/PO": "fires when the field's page opens",
    "/PC": "fires when the field's page closes",
    "/PV": "fires when the field's page becomes visible",
    "/PI": "fires when the field's page becomes hidden",
    "/K":  "fires on each keystroke in the field",
    "/F":  "fires when the field is formatted",
    "/V":  "fires when the field is validated",
    "/Calc": "fires when field values are recalculated",
}

_ACTION_VERBS = {
    "/JavaScript": "runs embedded JavaScript",
    "/Launch":     "launches an external program or file",
    "/URI":        "opens a URL",
    "/GoToR":      "opens an external document",
    "/GoToE":      "opens an embedded document",
    "/SubmitForm": "submits form data over the network",
    "/ImportData": "imports data from another file",
    "/Named":      "runs a named viewer command",
    "/Rendition":  "plays embedded media",
    "/Sound":      "plays an embedded sound",
    "/Movie":      "plays an embedded movie",
}


def _describe_autorun(site, where: str, stype: str) -> str:
    if site.kind == "open_action":
        when = "fires as soon as the document is opened"
    elif site.kind in ("annot_aa", "field_aa") and site.key == "/C":
        when = _TRIGGER_MEANINGS["/Calc"]       # /C on a field is "calculate", not "page closed"
    else:
        when = _TRIGGER_MEANINGS.get(site.key, f"fires on the {site.key} event")
    verb = _ACTION_VERBS.get(stype, "runs an action")
    return f"{where} — {when}; {verb}"


# ── Action sites ─────────────────────────────────────────────────────
#
#  An action can hang off the catalog (/OpenAction, /AA), a page (/AA), an
#  annotation (/A, /PA, /AA), a bookmark (/A), or a form field (/A, /AA) —
#  and from any of those through a /Next chain. The analyzer and the sanitizer
#  must agree on that list exactly: a place only the analyzer knew would be
#  reported and never removed, and a place only the sanitizer knew would be
#  removed without ever having been shown. So there is one definition of
#  the owners (_action_owners) and one of the entries on an owner
#  (_owner_sites), and both sides use them.

#: Every kind of place an action can hang. tests/test_sanitize_parity.py is
#: generated from this tuple, so a kind cannot be given to one side only.
ACTION_SITE_KINDS = ("open_action", "document_aa", "page_aa", "annot_a",
                     "annot_pa", "annot_aa", "outline_a", "field_a", "field_aa")

#: Kinds that fire by themselves — on open, close, print, focus, keystroke —
#: rather than when the user activates something.
_AUTORUN_KINDS = frozenset({"open_action", "document_aa", "page_aa",
                            "annot_aa", "field_aa"})

#: owner kind -> ((site kind, key) for each entry that holds one action,
#: kind of its /AA entries or None). /PA on a link annotation is the URI
#: action it had before it was retargeted (web capture keeps it there).
_OWNER_ENTRIES = {
    "document": ((("open_action", "/OpenAction"),), "document_aa"),
    "page":     ((), "page_aa"),
    "annot":    ((("annot_a", "/A"), ("annot_pa", "/PA")), "annot_aa"),
    "outline":  ((("outline_a", "/A"),), None),
    "field":    ((("field_a", "/A"),), "field_aa"),
}


class _Site(NamedTuple):
    """One place an action is stored: ``holder[key]`` is the action — or, in
    an /AA dictionary, possibly an array of them (see _entry_actions)."""
    kind: str        # one of ACTION_SITE_KINDS
    where: str       # label for the report
    holder: Any      # the owner itself, or its /AA dictionary
    key: str         # "/A", "/OpenAction", or the /AA trigger name


def _entry_actions(entry) -> list:
    """The action dictionaries one stored entry holds.

    An entry is one action. A file can also put an array of actions under an
    /AA trigger; that is not what the format defines, but the array is in
    the file and so is what it holds, and with `auto_actions` the whole /AA
    goes — so the options that go by what an action does must see inside it
    too, or `javascript` alone would leave a script that `auto_actions`
    removes.
    """
    if isinstance(entry, pikepdf.Dictionary):
        return [entry]
    if isinstance(entry, pikepdf.Array):
        return [a for a in entry if isinstance(a, pikepdf.Dictionary)]
    return []


def _label(text: Any) -> str:
    """A title or field name as it appears inside a site label."""
    s = " ".join(_as_str(text).split())[:60]
    return f'"{s}"' if s else "(unnamed)"


def _plain_owner(label: str) -> str:
    """A site label for a diagnostic: lower-cased, and without the quoted
    title or field name, which is document content."""
    plain = re.sub(r'\s*"[^"]*"', "", label).strip()
    return plain[:1].lower() + plain[1:]


def _outline_items(root):
    """Yield every bookmark (outline item) dictionary, the whole tree, once.

    Siblings are linked by /Next and children by /First, so this walks a
    linked structure a file can make arbitrarily long or circular: it is
    iterative, and an item already seen ends its chain.
    """
    try:
        outlines = root.get("/Outlines")
        first = outlines.get("/First") if isinstance(outlines, pikepdf.Dictionary) else None
    except Exception as exc:
        _trouble("the bookmark tree could not be read", exc)
        return
    seen, stack = set(), [first]
    while stack:
        item = stack.pop()
        while isinstance(item, pikepdf.Dictionary):
            try:
                if item.is_indirect:
                    key = tuple(item.objgen)
                    if key in seen:
                        break
                    seen.add(key)
                yield item
                child = item.get("/First")
                if child is not None:
                    stack.append(child)
                item = item.get("/Next")
            except Exception as exc:
                _trouble("a bookmark could not be read", exc)
                break


def _form_fields(root):
    """Yield every field dictionary in the AcroForm tree, through /Kids, once.

    Includes fields no page shows — a parent whose only widget is a kid, or
    a field with no widget at all — which a walk over page annotations never
    reaches. Iterative and cycle-safe.
    """
    try:
        acro = root.get("/AcroForm")
        fields = acro.get("/Fields") if isinstance(acro, pikepdf.Dictionary) else None
        stack = list(fields)[::-1] if isinstance(fields, pikepdf.Array) else []
    except Exception as exc:
        _trouble("the form's field list could not be read", exc)
        return
    seen = set()
    while stack:
        fld = stack.pop()
        try:
            if not isinstance(fld, pikepdf.Dictionary):
                continue
            if fld.is_indirect:
                key = tuple(fld.objgen)
                if key in seen:
                    continue
                seen.add(key)
            yield fld
            kids = fld.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                stack.extend(list(kids)[::-1])
        except Exception as exc:
            _trouble("a form field could not be read", exc)


def _page_annotations(page) -> list:
    """The dictionaries in a page's /Annots, in order. Null and other
    non-dictionary entries are skipped; the array may be direct or indirect."""
    try:
        annots = page.get("/Annots")
        if not isinstance(annots, pikepdf.Array):
            return []
        return [a for a in annots if isinstance(a, pikepdf.Dictionary)]
    except Exception as exc:
        _trouble("a page's annotation list could not be read", exc)
        return []


def _action_owners(pdf: pikepdf.Pdf):
    """Yield (owner_kind, label, owner) for every object that can carry an
    action: the catalog, each page (in the page tree or not — see
    _all_pages), each annotation, each bookmark, each form field. An object
    reachable two ways — an annotation listed on two pages, a widget that is
    also a field — is yielded once.
    """
    root = pdf.Root
    yield "document", "Document", root
    seen = set()

    def first_visit(obj) -> bool:
        try:
            if not obj.is_indirect:
                return True
            key = tuple(obj.objgen)
        except Exception as exc:
            _trouble("an object's identity could not be read", exc)
            return True
        if key in seen:
            return False
        seen.add(key)
        return True

    for label, page in _all_pages(pdf):
        yield "page", label, page
        for annot in _page_annotations(page):
            if first_visit(annot):
                yield "annot", f"{label} annotation", annot
    for item in _outline_items(root):
        yield "outline", f"Bookmark {_label(item.get('/Title'))}", item
    for fld in _form_fields(root):
        if first_visit(fld):
            yield "field", f"Form field {_label(fld.get('/T'))}", fld


def _owner_sites(owner_kind: str, label: str, owner):
    """Yield a _Site for each action stored on one owner.

    Only dictionaries are actions. An /OpenAction that is an array or a name
    is a destination — where the document opens, not something that runs —
    and is not a site. An /AA entry is a site when it is an action or an
    array holding one (_entry_actions).
    """
    entries, aa_kind = _OWNER_ENTRIES[owner_kind]
    for kind, key in entries:
        try:
            if isinstance(owner.get(key), pikepdf.Dictionary):
                yield _Site(kind, label, owner, key)
        except Exception as exc:
            _trouble(f"the {key} entry of {_plain_owner(label)} could not be read", exc)
    if aa_kind is not None:
        try:
            aa = owner.get("/AA")
            if isinstance(aa, pikepdf.Dictionary):
                for trig in aa.keys():
                    if _entry_actions(aa[trig]):
                        yield _Site(aa_kind, label, aa, str(trig))
        except Exception as exc:
            _trouble(f"the additional actions of {_plain_owner(label)} could not be read", exc)


def _action_sites(pdf: pikepdf.Pdf):
    """Yield a _Site for every place in the document an action is stored.

    One owner that cannot be read is reported and passed over; the others
    are still walked.
    """
    for owner_kind, label, owner in _action_owners(pdf):
        try:
            yield from _owner_sites(owner_kind, label, owner)
        except Exception as exc:
            _trouble(f"the actions of {_plain_owner(label)} could not be read", exc)


def _site_where(site: _Site) -> str:
    """The site's label as reports show it, e.g. "Page 2 annotation /AA /U"."""
    if site.kind.endswith("_aa"):
        return f"{site.where} /AA {site.key}"
    return f"{site.where} {site.key}"


def _iter_actions(pdf: pikepdf.Pdf):
    """Yield (site, where, action_dict) for every action reachable in the
    document: the action at each site and everything down its /Next chain.

    This is where Launch / URI / SubmitForm / GoToR / JavaScript actions
    actually live, and many are *inline* dictionaries that ``pdf.objects``
    never yields.

    A /Next chain is a linked list a file can make as long as it likes, so
    it is followed iteratively and to its end: there is no depth at which an
    action stops being looked at. An indirect action is yielded once, which
    is also what ends a chain that loops back on itself.
    """
    seen = set()
    for site in _action_sites(pdf):
        base = _site_where(site)
        try:
            stack = [(a, 0) for a in reversed(_entry_actions(site.holder[site.key]))]
        except Exception as exc:
            _trouble(f"an action of {_plain_owner(site.where)} could not be read", exc)
            continue
        while stack:
            action, depth = stack.pop()
            try:
                if not isinstance(action, pikepdf.Dictionary):
                    continue
                # Dedupe only on *indirect* objects via their stable (num,
                # gen) id. Inline dicts get fresh transient Python wrappers
                # whose id() is reused after GC, so id()-based dedup wrongly
                # skips siblings.
                if action.is_indirect:
                    key = tuple(action.objgen)
                    if key in seen:
                        continue
                    seen.add(key)
                nxt = action.get("/Next")
                following = (list(nxt)[::-1] if isinstance(nxt, pikepdf.Array)
                             else [] if nxt is None else [nxt])
            except Exception as exc:
                _trouble(f"an action of {_plain_owner(site.where)} could not be read", exc)
                continue
            yield site, (base if depth == 0 else f"{base} /Next" if depth == 1
                         else f"{base} /Next x{depth}"), action
            stack.extend((n, depth + 1) for n in following)


def _collect_actions(pdf: pikepdf.Pdf):
    """Yield (where, action_dict) for every action reachable in the document."""
    for _site, where, action in _iter_actions(pdf):
        yield where, action


#: Findings the action walk can produce (see _scan_scripts_and_actions).
_ACTION_FINDINGS = ("scripts.js", "actions.launch", "actions.autorun",
                    "actions.remote", "forms.submit")


def _instantiate_scripts(pdf: pikepdf.Pdf):
    """Yield every stream typed /3D that carries an /OnInstantiate script.

    That script runs when the 3D artwork loads. It is JavaScript but not an
    action, so it sits at no action site; it is found by what the stream is.
    The analyzer reports from this and the sanitizer removes from this.
    """
    for obj in _iter_all_objects(pdf):
        try:
            if (isinstance(obj, pikepdf.Stream)
                    and _as_str(obj.get("/Type")) == "/3D"
                    and "/OnInstantiate" in obj):
                yield obj
        except Exception as exc:
            _trouble("a stream's type could not be read", exc)


def _scan_scripts_and_actions(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """JavaScript, auto-run actions, launch actions, remote GoTo, submit."""
    js_hits: list[str] = []
    launch_hits: list[str] = []
    autorun: list[str] = []
    remote_goto: list[str] = []
    submit_hits: list[str] = []

    root = pdf.Root

    # -- Scripts that are not actions ----------------------------------
    with _collecting_trouble() as trouble:
        try:
            # Name tree /JavaScript (document-wide named scripts)
            names = root.get("/Names")
            if names is not None and "/JavaScript" in names:
                js_hits.append("Named JavaScript entries in /Names tree")
        except Exception as exc:
            _trouble("the catalog's name trees could not be read", exc)
        for _stream in _instantiate_scripts(pdf):
            js_hits.append("3D artwork: a script that runs when the artwork loads")
    res.incomplete(("scripts.js",), trouble)

    # -- Every reachable action ----------------------------------------
    with _collecting_trouble() as trouble:
        for site, where, action in _iter_actions(pdf):
            try:
                stype = _as_str(action.get("/S"))
                if site.kind in _AUTORUN_KINDS:
                    autorun.append(_describe_autorun(site, where, stype))

                if stype == "/JavaScript" or "/JS" in action:
                    js_hits.append(f"{where}: JavaScript")
                elif stype == "/Launch":
                    tgt = _as_str(action.get("/F") or action.get("/Win") or "")
                    launch_hits.append(f"{where}: {tgt[:100]}" if tgt else where)
                elif stype in ("/GoToR", "/GoToE"):
                    tgt = _as_str(action.get("/F"))
                    remote_goto.append(f"{where}: {tgt[:100]}" if tgt else f"{where}: {stype}")
                elif stype in ("/SubmitForm", "/ImportData"):
                    tgt = _as_str(action.get("/F"))
                    submit_hits.append(f"{where} ({stype[1:]}): {tgt[:100]}" if tgt
                                       else f"{where}: {stype}")
            except Exception as exc:
                _trouble(f"an action of {_plain_owner(site.where)} could not be read", exc)
    # Anything the walk could not read might have been any kind of action.
    res.incomplete(_ACTION_FINDINGS, trouble)

    # -- Emit findings -------------------------------------------------
    js_hits = _dedupe(js_hits)
    launch_hits = _dedupe(launch_hits)
    autorun = _dedupe(autorun)
    remote_goto = _dedupe(remote_goto)
    submit_hits = _dedupe(submit_hits)

    if js_hits:
        res.add(Finding(
            id="scripts.js", category="scripts", severity="high",
            title="Embedded JavaScript",
            detail="PDF JavaScript can run automatically in many viewers and is "
                   "a common malware and tracking vector. Sanitizing removes it.",
            count=len(js_hits), items=js_hits,
        ))
    if launch_hits:
        res.add(Finding(
            id="actions.launch", category="actions", severity="high",
            title="Launch action(s)",
            detail="/Launch actions can start external programs or open files "
                   "outside the PDF. These are rarely legitimate.",
            count=len(launch_hits), items=launch_hits,
        ))
    if autorun:
        res.add(Finding(
            id="actions.autorun", category="actions", severity="medium",
            title="Auto-run actions",
            detail="Actions that fire automatically when the document is opened, "
                   "printed, or closed. Review before trusting the file.",
            count=len(autorun), items=autorun,
        ))
    if remote_goto:
        res.add(Finding(
            id="actions.remote", category="actions", severity="medium",
            title="Remote / external GoTo actions",
            detail="References that point to external files or remote locations.",
            count=len(remote_goto), items=remote_goto,
        ))
    if submit_hits:
        res.add(Finding(
            id="forms.submit", category="forms", severity="high",
            title="Form submit / import actions",
            detail="/SubmitForm can transmit entered data over the network; "
                   "/ImportData pulls data from another file.",
            count=len(submit_hits), items=submit_hits,
        ))


def _scan_links(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """External URI links — possible tracking beacons / phone-home."""
    uris: list[str] = []
    with _collecting_trouble() as trouble:
        for site, _where, action in _iter_actions(pdf):
            try:
                if _as_str(action.get("/S")) == "/URI" and "/URI" in action:
                    uris.append(_as_str(action["/URI"]))
            except Exception as exc:
                _trouble(f"an action of {_plain_owner(site.where)} could not be read", exc)
    res.incomplete(("links.uri",), trouble)

    uris = [u for u in uris if u.strip()]
    if not uris:
        return

    domains = _dedupe([_domain_of(u) for u in uris])
    # Heuristic: many duplicate links to the same domain across pages can be
    # a tracking pixel pattern; flag http (non-TLS) separately.
    has_plain_http = any(u.lower().startswith("http://") for u in uris)
    sev = "medium" if has_plain_http else "low"
    res.add(Finding(
        id="links.uri", category="links", severity=sev,
        title="External links / URLs",
        detail="Outbound URLs embedded in the document. Clicking (or, in some "
               "viewers, opening) can contact these hosts. Review for trackers."
               + (" Some use plain http:// (no encryption)." if has_plain_http else ""),
        count=len(uris), items=domains[:50],
    ))


#: Keys of an /EF dictionary under which the embedded file's stream is stored.
_EF_STREAM_KEYS = ("/F", "/UF", "/DOS", "/Mac", "/Unix")


def _has_payload(d) -> bool:
    """True if dictionary *d* embeds a file: its /EF is a dictionary holding
    a stream.

    That is what makes a file specification an *embedded* file; one without
    it only names a file somewhere else. Judged by what /EF contains, not by
    there being a key called /EF — a resource dictionary may legally have a
    font named /EF, and its value holds no stream under these keys.
    """
    try:
        ef = d.get("/EF")
        return (isinstance(ef, pikepdf.Dictionary)
                and any(isinstance(ef.get(k), pikepdf.Stream) for k in _EF_STREAM_KEYS))
    except Exception as exc:
        _trouble("a file specification could not be read", exc)
        return False


def _filespec_name(d) -> str:
    try:
        return _as_str(d.get("/UF") or d.get("/F") or "").strip()
    except Exception as exc:
        _trouble("a file specification's name could not be read", exc)
        return ""


def _nested_dicts(obj):
    """Yield *obj* if it is a dictionary or stream, then every direct
    (inline) dictionary nested inside it. Indirect children are not
    followed: each is an object in its own right and is visited as one.
    Iterative; inline nesting cannot loop, so it needs no depth limit."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, (pikepdf.Dictionary, pikepdf.Stream)):
            yield cur
            children = [cur[k] for k in cur.keys()]
        elif isinstance(cur, pikepdf.Array):
            children = list(cur)
        else:
            continue
        stack.extend(c for c in reversed(children)
                     if isinstance(c, (pikepdf.Dictionary, pikepdf.Array)) and not c.is_indirect)


def _filespec_dicts(pdf: pikepdf.Pdf):
    """Yield every dictionary in the file that looks like a file
    specification — it has an /EF entry or says /Type /Filespec — including
    ones written inline inside another object, which ``pdf.objects`` does not
    yield on their own.

    Descending into every object's inline children would touch every key in
    the file, so each object's serialized form is checked first; only the
    few that mention /EF or /Filespec are descended into.
    """
    for obj in _iter_all_objects(pdf):
        try:
            body = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
            if not isinstance(body, (pikepdf.Dictionary, pikepdf.Array)):
                continue
            raw = body.unparse(resolved=True)    # the object's own text, not "12 0 R"
            if b"/EF" not in raw and b"/Filespec" not in raw:
                continue
            for d in _nested_dicts(obj):
                if "/EF" in d or _as_str(d.get("/Type")) == "/Filespec":
                    yield d
        except Exception as exc:
            _trouble("an object could not be searched for file specifications", exc)


def _scan_embedded_files(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Embedded files (attachments), and references to files outside the PDF."""
    names: list[str] = []
    external: list[str] = []
    with _collecting_trouble() as trouble:
        try:
            # A name tree may be a single leaf or intermediate /Kids nodes
            # with leaves below (ANL-04); its keys are the display names.
            nm = pdf.Root.get("/Names")
            if isinstance(nm, pikepdf.Dictionary) and "/EmbeddedFiles" in nm:
                names.extend(_as_str(key) for key, _value
                             in _name_tree_items(nm["/EmbeddedFiles"]))
        except Exception as exc:
            _trouble("the catalog's attachment list could not be read", exc)

        # Filespecs anywhere, not only in the name tree: on /FileAttachment
        # and other annotations, in /AF arrays, inside actions. /Type is
        # optional on a filespec and omitted by some producers (ANL-04), so
        # what decides is the payload: a filespec whose /EF holds a stream is
        # an embedded file, whatever it is called and wherever it sits. One
        # with no payload only points at a file elsewhere — nothing to
        # remove, but the path it carries is reported separately.
        for d in _filespec_dicts(pdf):
            name = _filespec_name(d)
            if _has_payload(d):
                names.append(name or "(unnamed embedded file)")
            elif name and _as_str(d.get("/Type")) == "/Filespec":
                external.append(name)
    res.incomplete(("files.embedded", "files.external"), trouble)

    names = _dedupe([n for n in names if n.strip()])
    if names:
        res.add(Finding(
            id="files.embedded", category="files", severity="high",
            title="Embedded files / attachments",
            detail="The PDF carries embedded files. These can hide additional "
                   "documents or executable payloads.",
            count=len(names), items=names[:50],
        ))
    external = _dedupe(external)
    if external:
        res.add(Finding(
            id="files.external", category="files", severity="low",
            title="References to external files",
            detail="File specifications that point to a file outside this "
                   "PDF. Nothing is embedded, but the file name or path "
                   "travels with the document.",
            count=len(external), items=external[:50],
        ))


def _scan_forms(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """AcroForm and XFA presence.

    A catalog entry that is not a dictionary is not a form, and a field list
    that is not an array holds no fields: neither is something that could
    not be read. What does fail to read is reported, so the two findings
    show as not checked rather than as absent.
    """
    try:
        acro = pdf.Root.get("/AcroForm")
        if not isinstance(acro, (pikepdf.Dictionary, pikepdf.Stream)):
            return
        has_xfa = "/XFA" in acro
        fields = acro.get("/Fields")
        nfields = len(fields) if isinstance(fields, pikepdf.Array) else 0
    except Exception as exc:
        _trouble("the form could not be read", exc)
        return

    if has_xfa:
        res.add(Finding(
            id="forms.xfa", category="forms", severity="medium",
            title="XFA dynamic form",
            detail="XFA forms embed XML and scripting; they can behave like a "
                   "mini-application and are a larger attack surface.",
            count=1,
        ))
    elif nfields:
        res.add(Finding(
            id="forms.acro", category="forms", severity="info",
            title="Interactive form fields",
            detail="The document contains fillable form fields.",
            count=nfields,
        ))


def _scan_optional_content(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Optional content groups (layers) — content can be hidden.

    Like _scan_forms: what is not a layer list is not one, and what cannot
    be read is reported.
    """
    try:
        ocp = pdf.Root.get("/OCProperties")
        if not isinstance(ocp, (pikepdf.Dictionary, pikepdf.Stream)):
            return
        groups = ocp.get("/OCGs")
        n = len(groups) if isinstance(groups, pikepdf.Array) else 0
    except Exception as exc:
        _trouble("the layer list could not be read", exc)
        return
    if n:
        res.add(Finding(
            id="content.ocg", category="content", severity="low",
            title="Optional-content layers",
            detail="Layers can hide content from view while keeping it in the "
                   "file. Hidden layers may still contain sensitive data.",
            count=n,
        ))


# Text render mode 3 = "invisible" (drawn but not shown) — the classic
# fake-redaction / OCR-underlay tell. Match the `3 Tr` operator on token
# boundaries: a leading start-of-stream or whitespace so "13 Tr"/"23 Tr"
# don't false-positive, any whitespace between operand and operator, and a
# word boundary after Tr.
_TR3_RE = re.compile(rb"(?:^|[\s])3\s+Tr\b")


def page_content_blobs(doc, page) -> list:
    """Decoded bytes of every content stream on *page*, plus the streams of
    any form XObjects it references.

    Render-mode-3 text commonly lands in a *later* content stream after
    incremental edits, or inside a form XObject — the old code read only
    ``page.get_contents()[0]`` and saw neither (ANL-03). Image XObjects are
    skipped (their streams are pixel data, not operators).

    Public (used by pdf_verify's redaction verifier); a decoded stream that
    is not readable is silently skipped here — callers that must fail closed
    on an unreadable stream should compare the blob count to the returned
    list length themselves (pdf_verify does).
    """
    blobs = []
    try:
        for xref in page.get_contents():
            try: blobs.append(doc.xref_stream(xref))
            except Exception: continue
    except Exception:
        pass
    try:
        for item in page.get_xobjects():
            try:
                xref = item[0]
                subtype = doc.xref_get_key(xref, "Subtype")
                if subtype and _as_str(subtype[1]).lstrip("/") == "Form":
                    blobs.append(doc.xref_stream(xref))
            except Exception:
                continue
    except Exception:
        pass
    return blobs


def _scan_invisible_text(path: str, res: AnalysisResult) -> None:
    """Invisible text (render mode 3) — a classic fake-redaction tell.

    Uses PyMuPDF when available for an accurate read; otherwise skipped.
    """
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        _trouble("PyMuPDF is not available to read page content", exc)
        return

    pages_with_invisible = 0
    try:
        doc = fitz.open(path)
        for page in doc:
            # Join streams with whitespace so a `3 Tr` at the start of a later
            # stream still sits on a token boundary for _TR3_RE.
            content = b"\n".join(b for b in page_content_blobs(doc, page)
                                 if isinstance(b, (bytes, bytearray)))
            if _TR3_RE.search(content):
                pages_with_invisible += 1
        doc.close()
    except Exception as exc:
        # The type only: library messages quote the path they were reading.
        log.debug("invisible-text scan stopped (%s)", type(exc).__name__)
        _trouble("page content could not be read", exc)
        return

    if pages_with_invisible:
        res.add(Finding(
            id="content.invisible_text", category="content", severity="medium",
            title="Invisible text layer",
            detail="Text drawn in invisible render mode was found. This is "
                   "normal for OCR scans, but it is also how failed "
                   "'redactions' leak — the black box hides text that is still "
                   "selectable and searchable underneath.",
            count=pages_with_invisible,
        ))


def _dedupe(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ════════════════════════════════════════════════════════════════════
#  Public API — analysis
# ════════════════════════════════════════════════════════════════════

def analyze_document(path: str, password: Optional[str] = None, *,
                     unremovable: bool = True) -> dict:
    """Run the full privacy/security audit and return a JSON-ready dict.

    The result's ``unevaluated`` maps a finding id to why it could not be
    fully checked; when it is not empty the audit reports
    ``audit.incomplete`` and never ``clean``.

    *unremovable* (on by default) adds the whole-file check for content
    Sanitize has no way to remove (see _scan_unremovable). The verifier
    turns it off: it runs that sweep itself, against the promises made.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {_fmt_size(size)} (max 2 GB)")

    # Magic-byte validation (consistent with the rest of the toolkit)
    with open(path, "rb") as fh:
        head = fh.read(5)
    if head != b"%PDF-":
        raise ValueError("Not a PDF (missing %PDF- header)")

    res = AnalysisResult(
        file_name=os.path.basename(path),
        file_path=path,
        file_size=size,
    )

    open_kwargs = {}
    if password:
        open_kwargs["password"] = password

    try:
        pdf = pikepdf.open(path, **open_kwargs)
    except pikepdf.PasswordError:
        res.encrypted = True
        res.add(Finding(
            id="encryption.locked", category="encryption", severity="info",
            title="Password-protected",
            detail="The file is encrypted and needs a password before it can "
                   "be analyzed.",
        ))
        return res.to_dict()

    with pdf:
        try:
            res.pages = len(pdf.pages)
        except Exception:
            res.pages = 0
        try:
            res.pdf_version = str(pdf.pdf_version)
        except Exception:
            res.pdf_version = ""
        try:
            res.encrypted = bool(pdf.is_encrypted)
        except Exception:
            res.encrypted = False

        if res.encrypted:
            res.add(Finding(
                id="encryption.present", category="encryption", severity="info",
                title="Document is encrypted",
                detail="Standard PDF encryption is in use.",
            ))

        # Run every scanner defensively — one failure must not abort the rest
        # — and pair each with the findings it can produce, so that one which
        # dies, or which meets something it cannot read, is recorded against
        # those findings instead of leaving them looking clean. The pairs are
        # built here, per call, so a scanner swapped out at run time is still
        # matched to its findings. (_scan_embedded_image_metadata is defined
        # lower, in the image section, so it can reuse the EXIF helpers.)
        for scan, ids in (
                (_scan_metadata, ("meta.docinfo", "meta.xmp")),
                (_scan_scripts_and_actions, _ACTION_FINDINGS),
                (_scan_links, ("links.uri",)),
                (_scan_embedded_files, ("files.embedded", "files.external")),
                (_scan_forms, ("forms.xfa", "forms.acro")),
                (_scan_optional_content, ("content.ocg",)),
                (_scan_embedded_image_metadata,
                 ("location.embedded_image", "metadata.embedded_image"))):
            _run_scanner(res, ids, scan, pdf, res)

        # Last, because it changes the in-memory document: what would still
        # be here after a sanitize with everything on.
        if unremovable:
            _run_scanner(res, _SWEPT_FINDINGS, _scan_unremovable, pdf, res)

    # Invisible-text scan needs the path (PyMuPDF), runs outside the pikepdf ctx
    _run_scanner(res, ("content.invisible_text",), _scan_invisible_text, path, res)

    if res.unevaluated:
        res.add(Finding(
            id="audit.incomplete", category="audit", severity="medium",
            title="Audit incomplete",
            detail="Part of this file could not be read, so the checks listed "
                   "here did not finish. For these, no finding does not mean "
                   "nothing is there.",
            count=len(res.unevaluated),
            items=[f"{_FINDING_INFO.get(fid, ('', '', fid))[2]}: {'; '.join(why[:3])}"
                   for fid, why in res.unevaluated.items()],
        ))
    elif not res.findings:
        res.add(Finding(
            id="clean", category="content", severity="info",
            title="No privacy or security concerns detected",
            detail="No JavaScript, auto-run actions, embedded files, trackers, "
                   "or identifying metadata were found.",
        ))

    return res.to_dict()


def _run_scanner(res: AnalysisResult, ids, scan, *args) -> None:
    """Run one scanner; whatever stopped it, or whatever it could not read
    and did not account for itself, is recorded against *ids*."""
    with _collecting_trouble() as trouble:
        try:
            scan(*args)
        except Exception as exc:
            # The type only: library messages quote the path they were reading.
            log.warning("an audit check stopped early (%s)", type(exc).__name__)
            trouble.append(f"the check stopped early ({type(exc).__name__})")
    res.incomplete(ids, trouble)


#: finding id -> (category, severity, title). Used to name a finding that
#: could not be evaluated, and to open one the whole-file check found where
#: no scanner looks.
_FINDING_INFO = {
    "meta.docinfo": ("metadata", "low", "Document metadata"),
    "meta.xmp": ("metadata", "low", "XMP metadata stream"),
    "scripts.js": ("scripts", "high", "Embedded JavaScript"),
    "actions.launch": ("actions", "high", "Launch action(s)"),
    "actions.autorun": ("actions", "medium", "Auto-run actions"),
    "actions.remote": ("actions", "medium", "Remote / external GoTo actions"),
    "forms.submit": ("forms", "high", "Form submit / import actions"),
    "links.uri": ("links", "low", "External links / URLs"),
    "files.embedded": ("files", "high", "Embedded files / attachments"),
    "files.external": ("files", "low", "References to external files"),
    "forms.xfa": ("forms", "medium", "XFA dynamic form"),
    "forms.acro": ("forms", "info", "Interactive form fields"),
    "content.ocg": ("content", "low", "Optional-content layers"),
    "content.invisible_text": ("content", "medium", "Invisible text layer"),
    "location.embedded_image": ("location", "high", "Location data in embedded images"),
    "metadata.embedded_image": ("metadata", "medium", "Camera data in embedded images"),
}

#: The findings the whole-file check can add to: everything a sanitize
#: option promises to remove.
_SWEPT_FINDINGS = ("scripts.js", "actions.launch", "actions.autorun", "forms.submit",
                   "files.embedded", "links.uri", "meta.docinfo", "meta.xmp")


def _scan_unremovable(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Report what the verifier's sweep sees and the scanners do not.

    The scanners look where the sanitizer knows how to remove; the
    verifier's sweep looks at every object. A user told `clean` here must
    not then be refused there, so the sweep is run here as well, twice.

    After a sanitize with every option on — done in memory only; this
    handle is never saved. Whatever is left sits somewhere no removal
    reaches. Each leftover is added to the finding it belongs to, with where
    it is and the note that Sanitize cannot remove it.

    And before it, on the file as it is. Something the scanners list
    nowhere can still be removable — but only along with what holds it, by
    another option (a script tucked inside an /AA dictionary goes with
    `auto_actions`). The first pass shows nothing left, yet `javascript`
    alone would be refused. So a category the sweep finds in the untouched
    file and no scanner reported is reported from the sweep. Only what the
    sweep finds by what an object is counts here; what it finds by position
    (a name tree, the /Info dictionary) the option concerned removes by
    position, whatever is inside.

    Must run after every other scanner: it changes the in-memory document.
    """
    # Imported here, not at the top: pdf_verify imports this module.
    import pdf_verify

    everything = {k: True for k in DEFAULT_SANITIZE}
    before, status_before = pdf_verify._sweep(pdf, everything)
    with _collecting_trouble():       # the removal's own trouble shows as leftovers
        _remove_content(pdf, everything)
    after, status = pdf_verify._sweep(pdf, everything)
    problems = status_before.problems + status.problems
    if problems:
        res.incomplete(_SWEPT_FINDINGS, _dedupe(
            [f"the whole-file check stopped short: {p}" for p in problems])[:3])

    def report(hit, note: str, detail: str) -> None:
        finding = res.find(hit.finding)
        if finding is None:
            category, severity, title = _FINDING_INFO[hit.finding]
            finding = Finding(id=hit.finding, category=category, severity=severity,
                              title=title, detail=detail)
            res.add(finding)
        item = f"{hit.what[:1].upper()}{hit.what[1:]} {hit.where} — {note}"
        if item not in finding.items:
            finding.items.append(item)
            finding.count += 1

    for hit in (h for found in after.values() for h in found):
        report(hit, "Sanitize cannot remove it from here",
               "Found by the whole-file check, in a place Sanitize has no way to "
               "remove it from.")
    unlisted = [h for found in before.values() for h in found
                if not h.positional and res.find(h.finding) is None]
    for hit in unlisted:
        report(hit, "Sanitize removes it only together with what holds it",
               "Found by the whole-file check, inside something another Sanitize "
               "option removes whole. Removing this category alone may be refused.")


# ════════════════════════════════════════════════════════════════════
#  Image analysis (JPEG / PNG metadata privacy)
# ════════════════════════════════════════════════════════════════════
#
#  Reuses Finding / AnalysisResult so analyze_image() returns the exact
#  same JSON shape as analyze_document() -- the frontend renders both
#  identically. Uses Pillow (already a dependency); nothing here makes a
#  network request. Phase 1 is backend + tests only (no bridge/UI wiring).

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"

# EXIF tag ids inside the GPS sub-IFD (see PIL.ExifTags.GPSTAGS).
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4
# Thumbnail markers in the IFD1 (first-IFD) block.
_THUMB_OFFSET, _THUMB_LENGTH = 513, 514  # JPEGInterchangeFormat[/Length]


def _exif_text(v: Any) -> str:
    """Coerce an EXIF value to a clean display string.

    Handles the Windows ``XP*`` tags (UTF-16LE, delivered as raw ``bytes``
    or a tuple of byte ints) and ordinary string/byte tags; strips embedded
    NULs and surrounding whitespace.
    """
    if v is None:
        return ""
    if isinstance(v, bytes):
        for enc in ("utf-16-le", "utf-8", "latin-1"):
            try:
                return v.decode(enc).replace("\x00", "").strip()
            except Exception:
                continue
        return ""
    if isinstance(v, tuple):
        try:
            raw = bytes(b & 0xFF for b in v)
            return raw.decode("utf-16-le", "ignore").replace("\x00", "").strip()
        except Exception:
            return str(v).strip()
    return str(v).replace("\x00", "").strip()


def _dms_to_decimal(dms, ref) -> float:
    """Convert an EXIF GPS (degrees, minutes, seconds) triple + hemisphere
    ref ('N'/'S'/'E'/'W') to signed decimal degrees."""
    deg, minute, sec = (float(x) for x in dms)
    dd = deg + minute / 60.0 + sec / 3600.0
    if ref is not None and str(ref).strip().upper() in ("S", "W"):
        dd = -dd
    return dd


def _scan_image_gps(gps: dict, res: AnalysisResult) -> None:
    """GPS coordinates — the headline privacy leak in a photo."""
    if not gps:
        return
    lat, lat_ref = gps.get(_GPS_LAT), gps.get(_GPS_LAT_REF)
    lon, lon_ref = gps.get(_GPS_LON), gps.get(_GPS_LON_REF)
    if lat is None or lon is None:
        return
    try:
        lat_dd = _dms_to_decimal(lat, lat_ref)
        lon_dd = _dms_to_decimal(lon, lon_ref)
        detail = (f"GPS coordinates are embedded in this image "
                  f"({lat_dd:.6f}, {lon_dd:.6f}). Anyone with the file can see "
                  f"exactly where the photo was taken.")
        items = [f"Latitude: {lat_dd:.6f}", f"Longitude: {lon_dd:.6f}"]
    except Exception:
        detail = ("GPS location tags are embedded in this image, revealing "
                  "where the photo was taken.")
        items = []
    res.add(Finding(
        id="location.gps", category="location", severity="high",
        title="Photo location embedded",
        detail=detail, count=1, items=items,
    ))


def _scan_image_camera(tags: dict, res: AnalysisResult) -> None:
    """Camera make/model, capture timestamp, and editing-software traces."""
    items: list[str] = []
    make = _exif_text(tags.get("Make"))
    model = _exif_text(tags.get("Model"))
    device = (make + " " + model).strip()
    if device:
        items.append(f"Device: {device}")
    captured = _exif_text(tags.get("DateTimeOriginal")) or _exif_text(tags.get("DateTime"))
    if captured:
        items.append(f"Captured: {captured}")
    software = _exif_text(tags.get("Software"))
    if software:
        items.append(f"Software: {software}")
    if not items:
        return
    res.add(Finding(
        id="metadata.camera", category="metadata", severity="medium",
        title="Camera and capture details",
        detail="EXIF metadata reveals the device, capture time, and/or "
               "editing software. This can identify the source of the photo "
               "and when it was taken.",
        count=len(items), items=items,
    ))


def _scan_image_thumbnail(ifd1: dict, res: AnalysisResult) -> None:
    """Embedded thumbnail (in the EXIF IFD1 block). It may show a pre-edit /
    pre-crop version of the picture, since thumbnails aren't always
    regenerated when the main image is changed."""
    if not ifd1 or not (_THUMB_OFFSET in ifd1 or _THUMB_LENGTH in ifd1):
        return
    res.add(Finding(
        id="content.thumbnail", category="content", severity="medium",
        title="Embedded thumbnail",
        detail="The image contains an embedded thumbnail preview. Thumbnails "
               "are often not updated when the main image is edited or "
               "cropped, so they can reveal an earlier version of the picture.",
        count=1,
    ))


def _scan_image_authorship(tags: dict, res: AnalysisResult) -> None:
    """Author / copyright / description metadata."""
    items: list[str] = []
    for tag, label in (("Artist", "Artist"), ("Copyright", "Copyright"),
                       ("XPAuthor", "Author"), ("XPComment", "Comment"),
                       ("ImageDescription", "Description")):
        text = _exif_text(tags.get(tag))
        if text:
            items.append(f"{label}: {text}")
    if not items:
        return
    res.add(Finding(
        id="metadata.authorship", category="metadata", severity="low",
        title="Author / copyright metadata",
        detail="The image carries authorship or descriptive metadata that "
               "can identify the creator or add hidden notes.",
        count=len(items), items=items,
    ))


def _read_exif_blocks(img) -> tuple[dict, dict, dict]:
    """Resolve an open PIL image's EXIF into plain ``(tags, gps, ifd1)`` dicts.

    Shared by :func:`analyze_image` (standalone files) and
    :func:`_scan_embedded_image_metadata` (images pulled out of a PDF) so the
    EXIF parsing lives in exactly one place. Each block is read defensively —
    a malformed sub-IFD yields an empty dict rather than aborting the read.
    """
    from PIL import ExifTags

    tags: dict = {}
    gps: dict = {}
    ifd1: dict = {}
    try:
        exif = img.getexif()
        for tag_id, val in exif.items():
            tags[ExifTags.TAGS.get(tag_id, tag_id)] = val
        try:
            for tag_id, val in exif.get_ifd(ExifTags.IFD.Exif).items():
                tags.setdefault(ExifTags.TAGS.get(tag_id, tag_id), val)
        except Exception:
            pass
        try:
            gps = dict(exif.get_ifd(ExifTags.IFD.GPSInfo))
        except Exception:
            gps = {}
        try:
            ifd1 = dict(exif.get_ifd(ExifTags.IFD.IFD1))
        except Exception:
            ifd1 = {}
    except Exception as exc:
        log.warning("EXIF read failed: %s", exc)
    return tags, gps, ifd1


def analyze_image(path: str) -> dict:
    """Run the image privacy audit and return a JSON-ready dict.

    Mirrors :func:`analyze_document` exactly (same AnalysisResult / Finding
    shape) for JPEG and PNG files. Surfaces GPS location, camera/capture
    details, embedded thumbnails, and authorship metadata. A file with no
    sensitive metadata yields no findings (overallRisk "info"), same as a
    clean PDF.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {_fmt_size(size)} (max 2 GB)")

    with open(path, "rb") as fh:
        head = fh.read(8)
    if not (head.startswith(_JPEG_MAGIC) or head.startswith(_PNG_MAGIC)):
        raise ValueError("Not a supported image (expected JPEG or PNG)")

    res = AnalysisResult(
        file_name=os.path.basename(path),
        file_path=path,
        file_size=size,
    )

    from PIL import Image

    # Resolve the EXIF blocks once, then hand plain dicts to each scanner so
    # a single malformed block can't abort the rest.
    tags: dict = {}
    gps: dict = {}
    ifd1: dict = {}
    try:
        with Image.open(path) as img:
            tags, gps, ifd1 = _read_exif_blocks(img)
    except Exception as exc:
        log.warning("EXIF read failed for %s: %s", path, exc)

    scanners = (
        (_scan_image_gps, gps),
        (_scan_image_camera, tags),
        (_scan_image_thumbnail, ifd1),
        (_scan_image_authorship, tags),
    )
    for scan, data in scanners:
        try:
            scan(data, res)
        except Exception as exc:
            log.warning("image scanner %s failed: %s", scan.__name__, exc)

    return res.to_dict()


# ════════════════════════════════════════════════════════════════════
#  Embedded-image EXIF (photos dropped into a PDF keep their metadata)
# ════════════════════════════════════════════════════════════════════
#
#  A phone photo placed into a PDF is stored as a /DCTDecode (JPEG) stream
#  whose bytes are the *original* file, EXIF and all -- so its GPS/camera
#  metadata rides along invisibly. We recover the original bytes with
#  read_raw_bytes() (NOT a re-encoded pixmap, which would strip EXIF) and
#  reuse the standalone-image EXIF parsing. Verified empirically: EXIF/GPS
#  survives round-tripping through PDF embedding. Non-JPEG images
#  (Flate/CCITT/etc.) don't carry EXIF and are skipped.

# Stream filters whose raw bytes are a complete, EXIF-bearing image file.
_EXIF_IMAGE_FILTERS = {"/DCTDecode", "/JPXDecode"}


def _image_filter_names(xobj) -> list:
    """Return an image XObject's /Filter entries as a list of ``str`` names
    (handles the single-Name and Array-of-Names forms)."""
    filt = xobj.get("/Filter")
    if filt is None:
        return []
    try:
        if isinstance(filt, pikepdf.Array):
            return [str(f) for f in filt]
    except Exception:
        pass
    return [str(filt)]


def _xobject_entries(holder) -> list:
    """The ``(name, xobject)`` pairs in the /XObject dictionary of *holder*'s
    resources — *holder* being a page or a form XObject. Empty if it has
    none."""
    try:
        resources = holder.get("/Resources")
        xobjs = resources.get("/XObject") if resources is not None else None
    except Exception:
        return []
    return list(xobjs.items()) if xobjs is not None else []


def _iter_image_xobjects(pdf: pikepdf.Pdf):
    """Yield ``(page_index, name, xobject)`` for every image XObject reachable
    from a page's resources, through any depth of form XObjects.

    Each image is yielded once and each form is entered once per document,
    both remembered by object number. For images that is the point: one
    reused on many pages is one embedded image. For forms it is what makes
    the walk finish. Forms nest, and share what they nest — a few hundred
    forms that each draw the same handful of others have more paths through
    them than can be walked (a real two-page file with 270 forms did not
    finish) — so a form already entered, from another parent or another
    page, is not entered again. An image is reported on the first page that
    reaches it.

    Iterative, with no limit on depth: remembering the forms is also what
    ends a loop of forms that contain each other.
    """
    seen_images, seen_forms = set(), set()
    for page_index, page in enumerate(pdf.pages, 1):
        # One iterator per form being read, innermost last, so images come
        # out in the order a reader meets them.
        stack = [iter(_xobject_entries(page))]
        while stack:
            for name, xobj in stack[-1]:
                try:
                    subtype = str(xobj.get("/Subtype"))
                except Exception:
                    continue
                if subtype == "/Image":
                    og = xobj.objgen
                    if og in seen_images:
                        continue
                    seen_images.add(og)
                    yield page_index, name, xobj
                elif subtype == "/Form":
                    # A form is a stream, so it has an object number. One
                    # written inline is not a form any reader would draw; it
                    # cannot be shared or contain itself, so it is read
                    # where it stands.
                    if xobj.is_indirect:
                        og = tuple(xobj.objgen)
                        if og in seen_forms:
                            continue
                        seen_forms.add(og)
                    stack.append(iter(_xobject_entries(xobj)))
                    break
            else:
                stack.pop()


def _scan_embedded_image_metadata(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Surface EXIF GPS/camera metadata carried by embedded JPEG images."""
    try:
        from PIL import Image
    except Exception:
        return

    gps_hits: list[str] = []
    cam_hits: list[str] = []

    for page_index, name, xobj in _iter_image_xobjects(pdf):
        if not (_EXIF_IMAGE_FILTERS & set(_image_filter_names(xobj))):
            continue
        try:
            raw = xobj.read_raw_bytes()          # original bytes — EXIF intact
        except Exception:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as img:
                tags, gps, _ifd1 = _read_exif_blocks(img)
        except Exception:
            continue

        where = f"page {page_index}, image {str(name).lstrip('/')}"

        lat, lon = gps.get(_GPS_LAT), gps.get(_GPS_LON)
        if lat is not None and lon is not None:
            try:
                lat_dd = _dms_to_decimal(lat, gps.get(_GPS_LAT_REF))
                lon_dd = _dms_to_decimal(lon, gps.get(_GPS_LON_REF))
                gps_hits.append(f"{where}: {lat_dd:.6f}, {lon_dd:.6f}")
            except Exception:
                gps_hits.append(f"{where}: GPS coordinates present")

        cam_bits: list[str] = []
        device = (_exif_text(tags.get("Make")) + " " + _exif_text(tags.get("Model"))).strip()
        if device:
            cam_bits.append(device)
        captured = _exif_text(tags.get("DateTimeOriginal")) or _exif_text(tags.get("DateTime"))
        if captured:
            cam_bits.append(captured)
        software = _exif_text(tags.get("Software"))
        if software:
            cam_bits.append(software)
        if cam_bits:
            cam_hits.append(f"{where}: {' · '.join(cam_bits)}")

    if gps_hits:
        res.add(Finding(
            id="location.embedded_image", category="location", severity="high",
            title="Location data in an embedded image",
            detail=(f"{len(gps_hits)} embedded image(s) carry GPS coordinates in "
                    f"their EXIF metadata — revealing where the photo was taken. "
                    f"A photo dropped into a PDF keeps its original camera "
                    f"metadata even though it isn't visible on the page."),
            count=len(gps_hits), items=gps_hits,
        ))
    if cam_hits:
        res.add(Finding(
            id="metadata.embedded_image", category="metadata", severity="medium",
            title="Camera metadata in an embedded image",
            detail=(f"{len(cam_hits)} embedded image(s) carry EXIF camera/capture "
                    f"metadata (device, capture time, and/or editing software) "
                    f"that can identify the source of the photo and when it was "
                    f"taken."),
            count=len(cam_hits), items=cam_hits,
        ))


# ════════════════════════════════════════════════════════════════════
#  Public API — dispatcher (PDF vs image)
# ════════════════════════════════════════════════════════════════════

def analyze_file(path: str, password: Optional[str] = None) -> dict:
    """Sniff the file type and route to the right analyzer.

    ``%PDF-`` → :func:`analyze_document`; JPEG/PNG → :func:`analyze_image`.
    Raises ValueError for anything else. (Phase 1: defined but not yet wired
    into the bridge — that's Phase 2.)
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF-"):
        return analyze_document(path, password=password)
    if head.startswith(_JPEG_MAGIC) or head.startswith(_PNG_MAGIC):
        return analyze_image(path)
    raise ValueError("Unsupported file type (expected PDF, JPEG, or PNG)")


# ════════════════════════════════════════════════════════════════════
#  File identity — shared by the transforms and their verifier
# ════════════════════════════════════════════════════════════════════
#
#  The trailer /ID exists so that revisions of one document can be recognised
#  as the same document, and both qpdf and MuPDF honour that: /ID[0] is
#  carried from input to output. For a sanitized or redacted copy that is a
#  link back to the original -- two files sharing an /ID element are the same
#  lineage, whatever else was removed. These helpers live here, at the bottom
#  of the import graph (pdf_ops -> pdf_verify -> pdf_analyze), so the code
#  that writes an identifier and the code that checks it read it one way.

def id_elements(tid) -> list[str]:
    """The elements of a trailer /ID value as lowercase hex ([] for None).

    A well-formed /ID is an array of two byte strings, but whatever a file
    has there is carried along by the libraries all the same, so every shape
    is read and none is rejected: a value that is not an array is one
    element (MuPDF carries a bare string through a save untouched), and an
    element that is not a string is taken by its PDF serialization, so it
    still compares equal to a copy of itself.
    """
    if tid is None:
        return []
    return [_id_element(x) for x in (tid if isinstance(tid, pikepdf.Array) else [tid])]


def _id_element(x) -> str:
    if isinstance(x, pikepdf.String):
        return bytes(x).hex()
    try:
        # pikepdf hands numbers and booleans back as plain Python values.
        raw = x.unparse(resolved=True) if isinstance(x, pikepdf.Object) else str(x).encode()
    except Exception:
        raw = repr(x).encode()
    return raw.hex()


def trailer_ids(pdf: pikepdf.Pdf) -> list[str]:
    """Each element of *pdf*'s trailer /ID as lowercase hex ([] if none)."""
    return id_elements(pdf.trailer.get("/ID"))


def file_sha256(path: str) -> str:
    """Hex SHA-256 of the file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_with_content_id(pdf: pikepdf.Pdf, path: str, **save_kwargs) -> None:
    """Save *pdf* to *path* with an identifier that owes nothing to its source.

    This must be the LAST write of an output: anything saved afterwards by
    another library puts its own /ID back. The trailer /ID is dropped first
    because qpdf keeps an existing /ID[0] even when asked for a deterministic
    one; deleting it alone is not enough either, since qpdf would then mint
    one from the clock and the same input would never produce the same file
    twice. With both, the two elements are a digest of the output's own
    content. *save_kwargs* pass through to ``Pdf.save``.
    """
    if "/ID" in pdf.trailer:
        del pdf.trailer["/ID"]
    pdf.save(path, deterministic_id=True, **save_kwargs)


# ════════════════════════════════════════════════════════════════════
#  Public API — sanitize
# ════════════════════════════════════════════════════════════════════

DEFAULT_SANITIZE = {
    "javascript": True,
    "launch_actions": True,
    "auto_actions": True,      # /OpenAction + /AA
    "embedded_files": True,
    "submit_actions": True,
    "external_links": False,   # off by default — links are often wanted
    "metadata": False,         # off by default — handled by the Metadata tool
}


# Maps a removable action /S subtype to (sanitize opt, removed[] counter key).
# /SubmitForm and /ImportData share the submit gate/counter, mirroring the
# analyzer's grouping in _scan_scripts_and_actions. /URI is here too: a link
# is removed wherever its action sits — as the head of an annotation's /A,
# down a /Next chain, in /OpenAction — not only in the first of those.
_DANGEROUS_ACTIONS = {
    "/JavaScript": ("javascript", "annot_javascript"),
    "/Launch": ("launch_actions", "launch_action"),
    "/SubmitForm": ("submit_actions", "submit_action"),
    "/ImportData": ("submit_actions", "submit_action"),
    "/URI": ("external_links", "external_link"),
}

#: Sites whose removals have always been counted by where they are rather
#: than by what the action does.
_COUNTED_BY_SITE = frozenset({"open_action", "document_aa", "page_aa"})


def _action_head_drop(action, opts: dict) -> Optional[str]:
    """Return the removed[] counter key if *action*'s own node must be
    dropped under *opts*, else None.

    A node goes when its /S is JavaScript/Launch/SubmitForm/ImportData/URI
    (gated on the matching opt) or it carries an inline /JS entry (gated on
    ``javascript``) — the same signal the analyzer's
    _scan_scripts_and_actions flags (``/S`` or inline ``/JS``).
    """
    try:
        stype = _as_str(action.get("/S"))
        opt_counter = _DANGEROUS_ACTIONS.get(stype)
        if opt_counter is not None and opts.get(opt_counter[0]):
            return opt_counter[1]
        if "/JS" in action and opts.get("javascript"):
            return "annot_javascript"
    except Exception as exc:
        _trouble("an action's type could not be read", exc)
    return None


def _as_list(value) -> list:
    """A /Next (or /AF) value as a list: it may be one object or an array."""
    if value is None:
        return []
    return list(value) if isinstance(value, pikepdf.Array) else [value]


def _clean_action_next(action, opts: dict, bump) -> None:
    """Excise from *action*'s ``/Next`` chain every node *opts* removes, in
    place, keeping the others and whatever follows them.

    ANL-02: the analyzer follows ``/Next`` when flagging buried
    JavaScript/Launch/SubmitForm, so the sanitizer must too — an annotation
    with a harmless head whose ``/Next`` runs JavaScript must not keep the
    script. A removed node's own ``/Next`` is promoted into its place, so a
    harmless action chained after it is not lost with it. Each removed node
    bumps the counter its type has.

    Iterative, and to the end of the chain however long it is. A chain is a
    linked list a file can make as long as it likes; stopping partway would
    either leave what was asked for or cut off what was not.
    """
    todo, seen, counted = [action], set(), set()
    while todo:
        owner = todo.pop()
        try:
            pending = _as_list(owner.get("/Next"))
            if not pending:
                continue
            survivors, changed, spliced = [], False, set()
            while pending:
                node = pending.pop(0)
                if not isinstance(node, pikepdf.Dictionary):
                    changed = True                    # not an action: dropped
                    continue
                key = _action_head_drop(node, opts)
                if key is None:
                    survivors.append(node)
                    continue
                changed = True
                ident = tuple(node.objgen) if node.is_indirect else None
                if ident is not None and ident in spliced:
                    continue                          # a chain looping through it
                spliced.add(ident)
                if ident is None or ident not in counted:
                    bump(key)
                    counted.add(ident)
                pending[:0] = _as_list(node.get("/Next"))   # promote what followed it
            if changed:
                if not survivors:
                    del owner["/Next"]
                elif len(survivors) == 1:
                    owner["/Next"] = survivors[0]
                else:
                    owner["/Next"] = pikepdf.Array(survivors)
            # Carry on down what was kept, re-read from the owner so that the
            # nodes cleaned next are the ones it now holds.
            for node in _as_list(owner.get("/Next")):
                if not isinstance(node, pikepdf.Dictionary):
                    continue
                if node.is_indirect:
                    ident = tuple(node.objgen)
                    if ident in seen:
                        continue
                    seen.add(ident)
                todo.append(node)
        except Exception as exc:
            _trouble("a chain of actions could not be cleaned", exc)


def _entry_removal(kind: str, action, opts: dict) -> Optional[str]:
    """Return the removed[] counter key if the action stored at a site of
    *kind* must go under *opts*, else None.

    An action goes for one of two reasons. For what it does: its type's own
    option is on — and that holds at every site, so `javascript` alone
    removes a script from a page /AA or from /OpenAction. Or for where it
    is: the site fires by itself and `auto_actions` is on, whatever the
    action does.
    """
    key = _action_head_drop(action, opts)
    if key is None and opts.get("auto_actions") and kind in _AUTORUN_KINDS:
        key = kind
    if key is not None and kind in _COUNTED_BY_SITE:
        key = kind
    return key


def _scrub_owner_actions(owner_kind: str, label: str, owner, opts: dict, bump) -> bool:
    """Apply *opts* to every action stored on one owner (see _owner_sites).

    An action that must go is deleted from where it is stored, and counted
    once; one that stays has its /Next chain cleaned. With `auto_actions`
    the owner's /AA goes whole; otherwise it goes only once it is empty. An
    /AA that is not a dictionary holds no action this walk can count one by
    one, so its removal is itself the count: one, under the owner's /AA
    counter.

    Returns True if the owner's own /A entry was deleted. What that means
    for the owner is the caller's business: an annotation is dropped from
    its page, a bookmark or a field stays. Losing a /PA does not count — the
    link's present target is untouched.
    """
    head_removed = False
    for site in list(_owner_sites(owner_kind, label, owner)):
        try:
            entry = site.holder[site.key]
            if isinstance(entry, pikepdf.Array):
                # An /AA entry holding several actions (_entry_actions): each
                # is judged like a lone one; the entry goes once none is left.
                keep, gone = [], []
                for action in entry:
                    key = (_entry_removal(site.kind, action, opts)
                           if isinstance(action, pikepdf.Dictionary) else None)
                    if key is None:
                        if isinstance(action, pikepdf.Dictionary):
                            _clean_action_next(action, opts, bump)
                        keep.append(action)
                    else:
                        gone.append(key)
                if gone:
                    if any(isinstance(a, pikepdf.Dictionary) for a in keep):
                        site.holder[site.key] = pikepdf.Array(keep)
                    else:
                        del site.holder[site.key]
                    for key in gone:
                        bump(key)
                continue
            key = _entry_removal(site.kind, entry, opts)
            if key is None:
                _clean_action_next(entry, opts, bump)
                continue
            del site.holder[site.key]
            bump(key)
            if site.key == "/A":
                head_removed = True
        except Exception as exc:
            _trouble(f"an action of {_plain_owner(label)} could not be removed", exc)
    aa_kind = _OWNER_ENTRIES[owner_kind][1]
    if aa_kind is not None:
        try:
            aa = owner.get("/AA")
            if aa is not None and (opts.get("auto_actions") or (
                    isinstance(aa, pikepdf.Dictionary) and len(aa.keys()) == 0)):
                del owner["/AA"]
                if not isinstance(aa, pikepdf.Dictionary):
                    bump(aa_kind)
        except Exception as exc:
            _trouble(f"the additional actions of {_plain_owner(label)} could not be removed", exc)
    return head_removed


def _clean_named_scripts(tree, opts: dict, bump) -> None:
    """Apply *opts* to the catalog's named scripts when `javascript` is off
    and the scripts themselves stay.

    Each named script is an action, so what the other options remove can be
    chained after one through /Next — or stored in place of one. With
    `javascript` on the whole tree goes and takes all of that with it; with
    it off, a launch action down a script's chain is still a launch action
    `launch_actions` promised to remove. An entry whose own action must go
    is taken out of the tree, name and all; the others have their chains
    cleaned.
    """
    for leaf, entries in _name_tree_leaves(tree):
        try:
            keep, gone = [], []
            for i in range(0, len(entries) - 1, 2):
                action = entries[i + 1]
                is_action = isinstance(action, pikepdf.Dictionary)
                key = _action_head_drop(action, opts) if is_action else None
                if key is not None:
                    gone.append(key)
                    continue
                if is_action:
                    _clean_action_next(action, opts, bump)
                keep += [entries[i], action]
            if gone:
                leaf["/Names"] = pikepdf.Array(keep)
                for key in gone:
                    bump(key)
        except Exception as exc:
            _trouble("the document's named scripts could not be cleaned", exc)


def _reachable(pdf: pikepdf.Pdf):
    """Yield every dictionary and stream reachable from the trailer, once.

    That is what the output will contain: the sanitizer removes things by
    deleting references to them, and an object nothing references is not
    written. Iterative and cycle-safe. A consumer may delete keys from the
    object it is handed; children are read after it returns, so what it
    deleted is not followed.
    """
    seen, stack = set(), [pdf.trailer]
    while stack:
        obj = stack.pop()
        try:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
                continue
            if obj.is_indirect:
                key = tuple(obj.objgen)
                if key in seen:
                    continue
                seen.add(key)
            if isinstance(obj, pikepdf.Array):
                children = list(obj)
            else:
                yield obj
                children = [obj[k] for k in obj.keys()]
            stack.extend(c for c in children if isinstance(
                c, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)))
        except Exception as exc:
            _trouble("an object could not be read while looking for embedded files", exc)


def _drop_stream_refs(pdf: pikepdf.Pdf, targets: set) -> None:
    """Delete every reference to the streams in *targets* ((num, gen) keys).

    Wherever one is held: a dictionary entry under any key, or an element of
    an array. Each indirect object is cleaned together with the direct
    dictionaries and arrays nested inside it (which ``pdf.objects`` does not
    yield on their own); the indirect objects it points to get their own
    turn. With no reference left, the stream is not written to the output.
    """
    def is_target(v) -> bool:
        return isinstance(v, pikepdf.Stream) and tuple(v.objgen) in targets

    def is_direct_container(v) -> bool:
        return isinstance(v, (pikepdf.Dictionary, pikepdf.Array)) and not v.is_indirect

    for obj in [pdf.trailer, *_iter_all_objects(pdf)]:
        try:
            stack = [obj] if isinstance(
                obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)) else []
            while stack:
                container = stack.pop()
                if isinstance(container, pikepdf.Array):
                    for i in range(len(container) - 1, -1, -1):
                        v = container[i]
                        if is_target(v): del container[i]
                        elif is_direct_container(v): stack.append(v)
                else:
                    for k in list(container.keys()):
                        v = container[k]
                        if is_target(v): del container[k]
                        elif is_direct_container(v): stack.append(v)
        except Exception as exc:
            _trouble("an object could not be cleared of its metadata reference", exc)


class SanitizationVerificationError(Exception):
    """Sanitize produced a candidate that verify_sanitization could not prove
    clean.

    Raised instead of returning success (fail-closed, #101). ``report`` is
    the verification report as a dict: which checks failed and why, what
    could not be checked at all, and any diagnostics from the removal.
    ``detail`` is the same refusal as the short lists a screen shows (see
    pdf_verify.sanitize_refusal_detail).
    Nothing was written to the destination: the candidate lived in a temp
    file, now deleted, and whatever was at output_path before — the input
    itself, when sanitizing in place — is untouched.
    """

    def __init__(self, message: str, *, report: Optional[dict] = None,
                 detail: Optional[dict] = None):
        super().__init__(message)
        self.report = report
        self.detail = detail


class SanitizationOutputError(RuntimeError):
    """The cleaned copy could not be written, read back, or moved into place.

    Not a refusal: nothing was found wrong with the output, it could not be
    produced. As with a refusal, nothing was written to the destination and
    the candidate is gone. The message is for a person and names no file.
    The library error behind it does — it quotes the path of the temp file
    it was working on — so of that error only the type is kept, in
    ``cause``, and it is not chained to this one.
    """

    def __init__(self, message: str, *, cause: str = ""):
        super().__init__(message)
        self.cause = cause


#: stage at which the candidate was lost -> (what to tell the user, what to
#: add when the operating system is what refused).
_NOT_SAVED = {
    "write": ("The cleaned copy could not be written, so nothing was saved.",
              " Check that the destination folder exists and can be written to."),
    "read": ("The cleaned copy could not be read back after it was written, so "
             "nothing was saved.", ""),
    "commit": ("The cleaned copy could not be moved into place, so nothing was saved.",
               " Check that the destination file is not open in another program "
               "and is not read-only."),
}


def _not_saved(stage: str, exc: BaseException) -> SanitizationOutputError:
    """The error for a candidate lost at *stage* to *exc*: plain words, and
    the type of *exc*. Raise it ``from None``."""
    message, hint = _NOT_SAVED[stage]
    cause = type(exc).__name__
    log.warning("sanitize: the cleaned copy was lost at the %s stage (%s)", stage, cause)
    return SanitizationOutputError(
        message + (hint if isinstance(exc, OSError) else ""), cause=cause)


def _remove_content(pdf: pikepdf.Pdf, opts: dict) -> dict[str, int]:
    """Remove from the open document everything *opts* asks for; return the
    removed[] counters.

    This is the whole of what Sanitize does to a file, apart from writing
    it. It is tolerant — a structure it cannot process is reported with
    _trouble() and passed over — because the gate that follows checks the
    result, not this function's opinion of itself. The analyzer also runs
    it, on a handle it never saves, to learn what would be left.
    """
    removed: dict[str, int] = {}

    def _bump(key: str, n: int = 1) -> None:
        if n > 0:                   # a counter exists only for what was removed
            removed[key] = removed.get(key, 0) + n

    root = pdf.Root

    # Catalog actions: /OpenAction and the document's /AA, entry by entry.
    try:
        _scrub_owner_actions("document", "Document", root, opts, _bump)
    except Exception as exc:
        _trouble("the document's own actions could not be cleaned", exc)
    # /Names -> /JavaScript (document-level named scripts): the tree goes
    # whole with `javascript`; without it the scripts stay and only what the
    # other options remove is taken from them.
    try:
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            if opts["javascript"]:
                del names["/JavaScript"]
                _bump("named_javascript")
            else:
                _clean_named_scripts(names["/JavaScript"], opts, _bump)
    except Exception as exc:
        _trouble("the document's named scripts could not be removed", exc)

    # Embedded files. One file is one count, however many places list it:
    # the counter that gets it is the first removal to reach it. (pikepdf >=
    # 10.16 registers an attachment in both the name tree and the catalog's
    # /AF, as the same object.)
    counted_files: set = set()

    def _first_time(fs) -> bool:
        try:
            if fs is None or not fs.is_indirect:
                return True         # an inline filespec has one holder
            key = tuple(fs.objgen)
        except Exception:
            return True
        if key in counted_files:
            return False
        counted_files.add(key)
        return True

    def _drop_af(holder, whose: str) -> None:
        """Delete *holder*'s /AF (associated files), counting the files in
        it that nothing counted before. /AF references embedded streams
        independently of the name tree, so it is stripped wherever it
        appears — root, pages, annotations — or the streams stay referenced
        and survive (ANL-04)."""
        try:
            af = holder.get("/AF")
            if af is None:
                return
            listed = _as_list(af)
            del holder["/AF"]
            _bump("associated_file", sum(1 for fs in listed if _first_time(fs)))
        except Exception as exc:
            _trouble(f"the associated files of {whose} could not be removed", exc)

    if opts["embedded_files"]:
        try:
            names = root.get("/Names")
            if names is not None and "/EmbeddedFiles" in names:
                listed = list(_name_tree_values(names["/EmbeddedFiles"]))
                del names["/EmbeddedFiles"]
                _bump("embedded_files", sum(1 for fs in listed if _first_time(fs)))
        except Exception as exc:
            _trouble("the document's attachment list could not be removed", exc)
        _drop_af(root, "the document")

    # Pages and their annotations — the pages in the page tree and the ones
    # outside it (_all_pages, which the analyzer walks too). This loop
    # reaches the annotations itself rather than through _action_owners
    # because it rewrites each page's /Annots as it goes; a test holds the
    # two walks to the same set of annotations.
    dropped: set = set()        # (num, gen) of annotations already dropped
    for label, page in _all_pages(pdf):
        try:
            _scrub_owner_actions("page", label, page, opts, _bump)
            if opts["embedded_files"]:
                _drop_af(page, _plain_owner(label))
            annots = page.get("/Annots")
            if not isinstance(annots, pikepdf.Array):
                continue
        except Exception as exc:
            _trouble(f"{_plain_owner(label)} could not be cleaned", exc)
            continue
        keep = []
        for annot in list(annots):
            try:
                ident = tuple(annot.objgen) if annot.is_indirect else None
                if ident is not None and ident in dropped:
                    continue             # listed on an earlier page too; gone there
                # /A, /PA and /AA, by the rule every site uses: an action
                # goes for what it does or, in /AA, for firing by itself; one
                # that stays has its /Next chain cleaned (ANL-02). When the
                # annotation's own /A had to go, the annotation is dropped —
                # and the /A has been deleted from it as well, because
                # taking a widget off the page does not take it out of the
                # file: /AcroForm still lists it.
                drop = _scrub_owner_actions("annot", f"{label} annotation",
                                            annot, opts, _bump)
                # Embedded files carried by the annotation itself:
                # /FileAttachment annots hold a stream via /FS→/EF, and /AF
                # arrays reference embedded streams — both independent of
                # the name tree, and both invisible to the /A→/S check
                # above (ANL-04).
                if opts["embedded_files"]:
                    if _as_str(annot.get("/Subtype")) == "/FileAttachment":
                        drop = True
                        if _first_time(annot.get("/FS")):
                            _bump("file_attachment_annot")
                    _drop_af(annot, f"{_plain_owner(label)} annotation")
                if not drop:
                    keep.append(annot)
                elif ident is not None:
                    dropped.add(ident)
            except Exception as exc:
                if isinstance(annot, pikepdf.Dictionary):     # a null entry is not trouble
                    _trouble(f"{_plain_owner(label)} annotation could not be cleaned", exc)
                keep.append(annot)
        try:
            if len(keep) != len(annots):
                page["/Annots"] = pikepdf.Array(keep)
        except Exception as exc:
            _trouble(f"the annotation list of {_plain_owner(label)} could not be rewritten", exc)

    # Bookmarks, and form fields no page shows (a parent field, a field with
    # no widget, a widget just dropped above). The owners come from the same
    # generator the analyzer walks.
    for owner_kind, label, owner in _action_owners(pdf):
        if owner_kind in ("outline", "field"):
            try:
                _scrub_owner_actions(owner_kind, label, owner, opts, _bump)
            except Exception as exc:
                _trouble(f"{_plain_owner(label)} could not be cleaned", exc)

    # Embedded files the structural removals above could not reach: a
    # payload on any other holder (a /Screen or /RichMedia annotation, a
    # rendition, an action, a private key). It is found by what it is — a
    # dictionary whose /EF holds a stream — among the objects that will
    # still be written, and emptied in place: the payload, and with it the
    # file name, description and everything else that describes it.
    if opts["embedded_files"]:
        try:
            if any(_has_payload(d) for d in _filespec_dicts(pdf)):
                for d in _reachable(pdf):
                    if _has_payload(d):
                        for k in list(d.keys()):
                            if k != "/Type":
                                del d[k]
                        if _first_time(d):
                            _bump("embedded_file")
        except Exception as exc:
            _trouble("embedded files outside the attachment list could not be removed", exc)

    # A script run when 3D artwork loads (_instantiate_scripts). The artwork
    # stays; the script goes.
    if opts["javascript"]:
        for stream in list(_instantiate_scripts(pdf)):
            try:
                del stream["/OnInstantiate"]
                _bump("instantiate_script")
            except Exception as exc:
                _trouble("a 3D artwork script could not be removed", exc)

    if opts["metadata"]:
        # Delete the metadata streams themselves, wherever they hang. Never
        # open the packet for editing: pikepdf's recovering parser accepts
        # malformed XMP it cannot then clear, so clear()-and-save writes the
        # recovered tree back with its contents intact — and entering the
        # editing context at all invents a packet (and an /Info) in a file
        # that had neither.
        try:
            streams = _metadata_streams(pdf)
            if streams:
                # A /Metadata slot is emptied where it stands. A stream that
                # declares itself metadata is then unhooked from wherever
                # else it is referenced; one that does not is not — outside
                # a /Metadata slot nothing says it is metadata, and a file
                # can point a page's /Contents at the same stream.
                for holder, _md in list(_metadata_slots(pdf)):
                    try:
                        del holder["/Metadata"]
                    except Exception as exc:
                        _trouble("an object's metadata entry could not be removed", exc)
                _drop_stream_refs(pdf, {k for k, s in streams.items()
                                        if _is_metadata_stream(s)})
                _bump("xmp_metadata", len(streams))
        except Exception as exc:
            _trouble("metadata streams could not be removed", exc)
        try:
            info = pdf.trailer.get("/Info")
            if info is not None:
                # Counted if there was anything to remove: a dictionary's
                # entries, or an /Info that is no dictionary at all — a
                # string, an array, a name, a number are each something the
                # trailer held and now does not.
                try:
                    had_entries = (not isinstance(info, (pikepdf.Dictionary, pikepdf.Stream))
                                   or len(info.keys()) > 0)
                except Exception:
                    had_entries = True
                # Emptied, not only unhooked from the trailer: the same
                # dictionary can be held under another key as well, and what
                # is still referenced is still written. But only when it is
                # nothing except document information (_is_plain_info). A
                # trailer can point /Info at the page tree root, a page, the
                # catalog; emptying that would take the document with it, so
                # there the trailer's entry is all that goes.
                if _is_plain_info(info):
                    try:
                        for k in list(info.keys()):
                            del info[k]
                    except Exception as exc:
                        _trouble("the document information dictionary could not be emptied", exc)
                del pdf.trailer["/Info"]
                if had_entries:
                    _bump("doc_info")
        except Exception as exc:
            _trouble("the document information dictionary could not be removed", exc)

    return removed


def _shares_identifier(path: str, source_ids: list[str], source_sha: str) -> bool:
    """True if the file at *path* carries one of *source_ids* in its trailer
    /ID without being, byte for byte, the source itself."""
    wanted = {s for s in source_ids if s}
    if not wanted:
        return False
    with pikepdf.open(path) as pdf:
        shared = wanted & set(trailer_ids(pdf))
    return bool(shared) and file_sha256(path) != source_sha


def sanitize_pdf(input_path: str, output_path: str,
                 options: Optional[dict] = None) -> dict:
    """Strip dangerous/active content from a PDF, then PROVE it is gone or
    refuse (fail-closed, #101).

    Verify, then commit. The cleaned candidate is written to a temp file
    beside output_path — twice, so its layout is the settled one — and that
    file, the exact bytes that would be committed, is handed to
    verify_sanitization(). It must find every category the enabled options
    promise absent (by re-running the analyzer AND by an independent sweep of
    every object), must have been able to look everywhere, and must find no
    trailer /ID element in common with the source. Only then is the
    candidate renamed onto output_path.

    If it cannot be verified, SanitizationVerificationError is raised with
    the report, and the run has not created, replaced or deleted
    output_path — when that is the input itself (sanitizing in place), the
    original is untouched. Findings the options did not promise to remove
    are not a failure; they are listed on the report as residuals.

    If the candidate cannot be written, read back or moved into place — a
    folder that is not there, a full disk, a destination open in another
    program — SanitizationOutputError is raised instead, in plain words and
    naming no file. That too leaves output_path as it was.

    Returns {"removed", "total_removed", "output", "output_size",
    "verification"}: the counters, and the verification report as a dict.
    """
    opts = {**DEFAULT_SANITIZE, **(options or {})}
    source_sha = file_sha256(input_path)

    # allow_overwriting_input reads the input fully into memory at open time and
    # releases the OS file handle, so the commit below can os.replace over
    # input_path for an in-place sanitise (output_path == input_path). Without
    # it, os.replace hits a handle pikepdf still holds → PermissionError
    # [WinError 5] on Windows (ANL-01). Harmless when output_path differs.
    with pikepdf.open(input_path, allow_overwriting_input=True) as pdf:
        # The source's identity, before anything changes the document: the
        # verifier is handed these as plain values and never opens the input.
        source_ids = trailer_ids(pdf)

        with _collecting_trouble() as diagnostics:
            removed = _remove_content(pdf, opts)

        # A damaged source opens by having its cross-reference table
        # rebuilt, and the trailer rebuilt with it can lack /Size. qpdf
        # writes the true size over whatever that key holds — but only if
        # the key is there, and a file with object streams and no /Size
        # cannot be opened again, by qpdf or anything else.
        if "/Size" not in pdf.trailer:
            pdf.trailer["/Size"] = 0

        # The candidate. fix_metadata_version stays off: it parses any XMP
        # packet on the way out, which rewrites one the user kept and raises
        # outright on one that cannot be decoded.
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        save_kwargs = {"fix_metadata_version": False,
                       "object_stream_mode": pikepdf.ObjectStreamMode.generate}
        # Whatever goes wrong from here to the verified copy is a copy that
        # could not be produced, not a document that could not be cleaned:
        # it is reported in plain words (_not_saved), because the library's
        # own message names the temp file.
        tmp, stage = None, "write"
        try:
            fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
            os.close(fd)
            save_with_content_id(pdf, tmp, **save_kwargs)
            stage = "read"
            # Write what was just written. qpdf packs objects into object
            # streams by the numbers they had on the way in, so the first
            # write of a file is not the layout qpdf gives that same file
            # the next time. The second is, and stays: that is what makes
            # sanitizing an already-sanitized file change no byte, and lets
            # its identifier (a digest of the content) come out the same.
            with pikepdf.open(tmp, allow_overwriting_input=True) as settled:
                save_with_content_id(settled, tmp, **save_kwargs)
            # An identifier that is a digest of the content is the same
            # whenever the content is. A file sanitized before, then re-saved
            # or edited by a tool that keeps the first /ID element (as the
            # format asks of it), still carries the identifier this output
            # is about to get. The gate would refuse that, rightly, and the
            # user could do nothing about it — so the same content is
            # written in a second layout, whose digest is a different one.
            if _shares_identifier(tmp, source_ids, source_sha):
                other = {**save_kwargs,
                         "object_stream_mode": pikepdf.ObjectStreamMode.disable}
                for _ in range(2):
                    with pikepdf.open(tmp, allow_overwriting_input=True) as again:
                        save_with_content_id(again, tmp, **other)
        except Exception as exc:
            if tmp is not None and os.path.exists(tmp):
                os.unlink(tmp)
            raise _not_saved(stage, exc) from None

    # Verify, then commit: the temp is closed, and it is exactly what would
    # be renamed into place. (Imported here, not at the top: pdf_verify
    # imports this module.)
    try:
        import pdf_verify
        report = pdf_verify.verify_sanitization(
            tmp, opts, input_path=input_path,
            source_ids=source_ids, source_sha256=source_sha)
        report.retarget(output_path)
        report.diagnostics = list(diagnostics)
        if not report.verified:
            raise SanitizationVerificationError(
                pdf_verify.sanitize_refusal_message(report), report=report.to_dict(),
                detail=pdf_verify.sanitize_refusal_detail(report))
        try:
            os.replace(tmp, output_path)
        except OSError as exc:
            raise _not_saved("commit", exc) from None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    return {
        "removed": removed,
        "total_removed": sum(removed.values()),
        "output": output_path,
        "output_size": os.path.getsize(output_path),
        "verification": report.to_dict(),
    }


# ════════════════════════════════════════════════════════════════════
#  Public API — strip image metadata (the "remove" half of the image tool)
# ════════════════════════════════════════════════════════════════════
#
#  Mirrors sanitize_pdf: detect what sensitive metadata is present (reusing
#  the Phase-1 scanners so the "removed" report matches what analyze_image
#  would flag), then write a metadata-free copy of the SAME format,
#  atomically. JPEGs are re-saved losslessly (quality="keep" preserves the
#  original DCT coefficients — no recompression artifacts) while dropping the
#  EXIF/GPS/thumbnail blocks; other formats are rebuilt from a copy with a
#  cleared info dict. Verified empirically: re-scanning the output yields no
#  findings.

# Finding id (from the Phase-1 image scanners) -> removed[] category key.
_IMAGE_STRIP_CATEGORIES = {
    "location.gps": "gps",
    "metadata.camera": "camera",
    "content.thumbnail": "thumbnail",
    "metadata.authorship": "authorship",
}


def _detect_image_metadata(tags: dict, gps: dict, ifd1: dict) -> dict:
    """Run the Phase-1 image scanners against resolved EXIF blocks and report
    which sensitive categories are present, as ``{category: 1}``. Reuses the
    exact detection logic so ``removed`` matches ``analyze_image``'s findings."""
    probe = AnalysisResult()
    _scan_image_gps(gps, probe)
    _scan_image_camera(tags, probe)
    _scan_image_thumbnail(ifd1, probe)
    _scan_image_authorship(tags, probe)
    removed: dict[str, int] = {}
    for f in probe.findings:
        key = _IMAGE_STRIP_CATEGORIES.get(f.id)
        if key:
            removed[key] = 1
    return removed


def strip_image_metadata(input_path: str, output_path: str) -> dict:
    """Remove privacy metadata (EXIF/GPS/thumbnail/authorship) from a JPEG or
    PNG and write a clean copy of the same format.

    Returns a dict describing what was removed (same shape as
    :func:`sanitize_pdf`). Writes atomically (temp file + ``os.replace``).
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)

    size = os.path.getsize(input_path)
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {_fmt_size(size)} (max 2 GB)")

    with open(input_path, "rb") as fh:
        head = fh.read(8)
    if not (head.startswith(_JPEG_MAGIC) or head.startswith(_PNG_MAGIC)):
        raise ValueError("Not a supported image (expected JPEG or PNG)")

    from PIL import Image

    out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    with Image.open(input_path) as img:
        fmt = (img.format or "").upper()
        tags, gps, ifd1 = _read_exif_blocks(img)
        removed = _detect_image_metadata(tags, gps, ifd1)

        suffix = ".jpg" if fmt in ("JPEG", "MPO") else ".png" if fmt == "PNG" else ".img"
        tmp_fd, tmp = tempfile.mkstemp(suffix=suffix, dir=out_dir)
        os.close(tmp_fd)
        try:
            if fmt in ("JPEG", "MPO"):
                # Lossless re-encode (keeps DCT coefficients); no exif= arg, so
                # every metadata block is dropped.
                img.save(tmp, format="JPEG", quality="keep")
            else:
                # PNG (and any other supported raster): rebuild from a copy with
                # a cleared info dict so no ancillary chunks / exif ride along.
                clean = img.copy()
                clean.info = {}
                clean.save(tmp, format="PNG")
            os.replace(tmp, output_path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    return {
        "removed": removed,
        "total_removed": sum(removed.values()),
        "output": output_path,
        "output_size": os.path.getsize(output_path),
    }


def strip_file(input_path: str, output_path: str,
               options: Optional[dict] = None) -> dict:
    """Sniff the file type and route to the right cleaner.

    ``%PDF-`` → :func:`sanitize_pdf` (honouring ``options``); JPEG/PNG →
    :func:`strip_image_metadata`. Mirrors :func:`analyze_file`. Raises
    ValueError for anything else.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    with open(input_path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(b"%PDF-"):
        return sanitize_pdf(input_path, output_path, options)
    if head.startswith(_JPEG_MAGIC) or head.startswith(_PNG_MAGIC):
        return strip_image_metadata(input_path, output_path)
    raise ValueError("Unsupported file type (expected PDF, JPEG, or PNG)")


def sanitize_payload(input_path: str, output_path: str, options: Any = None) -> dict:
    """strip_file(), as the JSON-ready answer the app gives its window.

    It never raises — the bridge slot that returns this has nowhere to raise
    to — and it is here, not in the bridge, so that it can be tested without
    Qt. One of three shapes:

      * done — the strip_file result plus ``"success": True``. For a PDF
        that includes ``verification``, the report its output passed before
        it was committed; an image has none.
      * refused (SanitizationVerificationError) — ``"success": False``,
        ``"error"`` the one sentence saying why, ``"verification"`` the
        report, and ``"refusal"`` the same thing as short lists for a
        screen: what is still present, what could not be checked, whether
        it is the identifier (pdf_verify.sanitize_refusal_detail).
      * failed — ``"success": False``, ``"error"``, and ``"errorType"``,
        the type of what went wrong.

    *options* that are not a dict count as none given. Nothing logged from
    here names a file: of an unexpected error the log gets the type and
    where in the code it happened, not its text, because library messages
    quote the path they were reading.
    """
    try:
        result = strip_file(input_path, output_path,
                            options if isinstance(options, dict) else None)
    except SanitizationVerificationError as exc:
        log.info("sanitize refused: the output could not be verified")
        return {"success": False, "error": str(exc),
                "verification": exc.report, "refusal": exc.detail}
    except SanitizationOutputError as exc:          # logged where it was raised
        return {"success": False, "error": str(exc), "errorType": exc.cause}
    except FileNotFoundError:
        log.warning("sanitize failed: the input is not there (FileNotFoundError)")
        return {"success": False, "error": "The file to sanitize could not be found.",
                "errorType": "FileNotFoundError"}
    except Exception as exc:
        log.error("sanitize failed (%s)\n%s", type(exc).__name__,
                  "".join(traceback.format_tb(exc.__traceback__)))
        return {"success": False, "error": str(exc), "errorType": type(exc).__name__}
    return {**result, "success": True}


# ════════════════════════════════════════════════════════════════════
#  CLI for quick local testing
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json as _json
    import sys
    if len(sys.argv) < 2:
        print("usage: python pdf_analyze.py file.pdf [--sanitize out.pdf]")
        raise SystemExit(2)
    src = sys.argv[1]
    if "--sanitize" in sys.argv:
        out = sys.argv[sys.argv.index("--sanitize") + 1]
        print(_json.dumps(sanitize_pdf(src, out), indent=2))
    else:
        print(_json.dumps(analyze_document(src), indent=2))
