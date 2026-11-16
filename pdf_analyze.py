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
bits in place (or to a new file) and reports exactly what it removed.

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
from dataclasses import asdict, dataclass, field
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

    def add(self, f: Finding) -> None:
        self.findings.append(f)

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


def _iter_all_objects(pdf: pikepdf.Pdf):
    """Yield every indirect object once (bounded, cycle-safe)."""
    try:
        for obj in pdf.objects:
            yield obj
    except Exception:
        return


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
    except Exception:
        return False


def _metadata_streams(pdf: pikepdf.Pdf) -> dict:
    """{(num, gen): stream} for every metadata stream in the file.

    Found by presence. Nothing is decoded or parsed, so a packet that is
    malformed, in another encoding, or not decodable at all still counts —
    it is in the file either way. Covers every stream typed as metadata, on
    whatever object it hangs, plus whatever stream sits in the catalog's
    /Metadata slot even if untyped: that slot is the document's metadata by
    position, and it is where readers look.
    """
    found = {}
    for obj in _iter_all_objects(pdf):
        if _is_metadata_stream(obj):
            found[tuple(obj.objgen)] = obj
    try:
        md = pdf.Root.get("/Metadata")
        if isinstance(md, pikepdf.Stream):
            found.setdefault(tuple(md.objgen), md)
    except Exception:
        pass
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
        except Exception:
            pass

    held_by(pdf.Root, "Document catalog")
    try:
        for pi, page in enumerate(pdf.pages, 1):
            held_by(page, f"Page {pi}")
    except Exception:
        pass
    for num, gen in sorted(set(streams) - placed):
        where.append(f"Object {num} {gen} (not on the catalog or a page)")
    return where


def _scan_metadata(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Identifying document-info fields and XMP metadata streams."""
    leaky = []
    try:
        docinfo = pdf.trailer.get("/Info")
    except Exception:
        docinfo = None

    if isinstance(docinfo, pikepdf.Dictionary):
        try:
            keys = [str(k) for k in docinfo.keys()]
        except Exception:
            keys = []
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
            except Exception:
                continue

    streams = _metadata_streams(pdf)

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
            items=_metadata_locations(pdf, streams),
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
    """One place an action is stored: ``holder[key]`` is the action."""
    kind: str        # one of ACTION_SITE_KINDS
    where: str       # label for the report
    holder: Any      # the owner itself, or its /AA dictionary
    key: str         # "/A", "/OpenAction", or the /AA trigger name


def _label(text: Any) -> str:
    """A title or field name as it appears inside a site label."""
    s = " ".join(_as_str(text).split())[:60]
    return f'"{s}"' if s else "(unnamed)"


def _outline_items(root):
    """Yield every bookmark (outline item) dictionary, the whole tree, once.

    Siblings are linked by /Next and children by /First, so this walks a
    linked structure a file can make arbitrarily long or circular: it is
    iterative, and an item already seen ends its chain.
    """
    try:
        outlines = root.get("/Outlines")
        first = outlines.get("/First") if isinstance(outlines, pikepdf.Dictionary) else None
    except Exception:
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
            except Exception:
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
    except Exception:
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
        except Exception:
            continue


def _page_annotations(page) -> list:
    """The dictionaries in a page's /Annots, in order. Null and other
    non-dictionary entries are skipped; the array may be direct or indirect."""
    try:
        annots = page.get("/Annots")
        if not isinstance(annots, pikepdf.Array):
            return []
        return [a for a in annots if isinstance(a, pikepdf.Dictionary)]
    except Exception:
        return []


def _action_owners(pdf: pikepdf.Pdf):
    """Yield (owner_kind, label, owner) for every object that can carry an
    action: the catalog, each page, each annotation, each bookmark, each
    form field. An object reachable two ways — an annotation listed on two
    pages, a widget that is also a field — is yielded once.
    """
    root = pdf.Root
    yield "document", "Document", root
    seen = set()

    def first_visit(obj) -> bool:
        try:
            if not obj.is_indirect:
                return True
            key = tuple(obj.objgen)
        except Exception:
            return True
        if key in seen:
            return False
        seen.add(key)
        return True

    try:
        pages = list(pdf.pages)
    except Exception:
        pages = []
    for pi, page in enumerate(pages, 1):
        yield "page", f"Page {pi}", page
        for annot in _page_annotations(page):
            if first_visit(annot):
                yield "annot", f"Page {pi} annotation", annot
    for item in _outline_items(root):
        yield "outline", f"Bookmark {_label(item.get('/Title'))}", item
    for fld in _form_fields(root):
        if first_visit(fld):
            yield "field", f"Form field {_label(fld.get('/T'))}", fld


def _owner_sites(owner_kind: str, label: str, owner):
    """Yield a _Site for each action stored on one owner.

    Only dictionaries are actions. An /OpenAction that is an array or a name
    is a destination — where the document opens, not something that runs —
    and is not a site.
    """
    entries, aa_kind = _OWNER_ENTRIES[owner_kind]
    for kind, key in entries:
        try:
            if isinstance(owner.get(key), pikepdf.Dictionary):
                yield _Site(kind, label, owner, key)
        except Exception:
            pass
    if aa_kind is not None:
        try:
            aa = owner.get("/AA")
            if isinstance(aa, pikepdf.Dictionary):
                for trig in aa.keys():
                    if isinstance(aa[trig], pikepdf.Dictionary):
                        yield _Site(aa_kind, label, aa, str(trig))
        except Exception:
            pass


def _action_sites(pdf: pikepdf.Pdf):
    """Yield a _Site for every place in the document an action is stored."""
    for owner_kind, label, owner in _action_owners(pdf):
        yield from _owner_sites(owner_kind, label, owner)


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
    """
    seen = set()

    def _walk(action, where: str, depth: int = 0):
        if action is None or depth > 32:
            return
        try:
            if not isinstance(action, pikepdf.Dictionary):
                return
        except Exception:
            return
        # Dedupe only on *indirect* objects via their stable (num, gen) id.
        # Inline dicts get fresh transient Python wrappers whose id() is
        # reused after GC, so id()-based dedup wrongly skips siblings.
        try:
            if action.is_indirect:
                key = tuple(action.objgen)
                if key in seen:
                    return
                seen.add(key)
        except Exception:
            pass
        yield (where, action)
        # follow /Next (single dict or array)
        try:
            nxt = action.get("/Next")
            if isinstance(nxt, pikepdf.Array):
                for n in nxt:
                    yield from _walk(n, where + " /Next", depth + 1)
            elif nxt is not None:
                yield from _walk(nxt, where + " /Next", depth + 1)
        except Exception:
            pass

    for site in _action_sites(pdf):
        try:
            action = site.holder[site.key]
        except Exception:
            continue
        for where, node in _walk(action, _site_where(site)):
            yield site, where, node


def _collect_actions(pdf: pikepdf.Pdf):
    """Yield (where, action_dict) for every action reachable in the document."""
    for _site, where, action in _iter_actions(pdf):
        yield where, action


def _scan_scripts_and_actions(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """JavaScript, auto-run actions, launch actions, remote GoTo, submit."""
    js_hits: list[str] = []
    launch_hits: list[str] = []
    autorun: list[str] = []
    remote_goto: list[str] = []
    submit_hits: list[str] = []

    root = pdf.Root

    # -- Name tree /JavaScript (document-wide named scripts) -----------
    try:
        names = root.get("/Names")
        if names is not None and "/JavaScript" in names:
            js_hits.append("Named JavaScript entries in /Names tree")
    except Exception:
        pass

    # -- Every reachable action ----------------------------------------
    for site, where, action in _iter_actions(pdf):
        try:
            stype = _as_str(action.get("/S"))
        except Exception:
            stype = ""

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
            submit_hits.append(f"{where} ({stype[1:]}): {tgt[:100]}" if tgt else f"{where}: {stype}")

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
    for _where, action in _collect_actions(pdf):
        try:
            if _as_str(action.get("/S")) == "/URI" and "/URI" in action:
                uris.append(_as_str(action["/URI"]))
        except Exception:
            continue

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


def _walk_ef_name_tree(node, out: list, depth: int = 0) -> None:
    """Collect embedded-file display names from an /EmbeddedFiles name tree,
    recursing intermediate nodes that hold /Kids instead of /Names.

    A PDF name tree can be a single leaf ({/Names: [key, val, ...]}) or a
    balanced tree of intermediate {/Kids: [...]} nodes with leaves at the
    bottom — the old code only read a root-level /Names and missed any
    /Kids-structured tree (ANL-04). Depth-capped and exception-tolerant.
    """
    if depth > 50:
        return
    try:
        if not isinstance(node, pikepdf.Dictionary):
            return
    except Exception:
        return
    try:
        arr = node.get("/Names")
        if arr is not None:
            # Leaf: [key1, filespec1, key2, filespec2, ...] — keys are the
            # display names.
            for i in range(0, len(arr) - 1, 2):
                try: out.append(_as_str(arr[i]))
                except Exception: continue
    except Exception:
        pass
    try:
        kids = node.get("/Kids")
        if kids is not None:
            for kid in kids:
                _walk_ef_name_tree(kid, out, depth + 1)
    except Exception:
        pass


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
    except Exception:
        return False


def _filespec_name(d) -> str:
    try:
        return _as_str(d.get("/UF") or d.get("/F") or "").strip()
    except Exception:
        return ""


def _nested_dicts(obj, depth: int = 0):
    """Yield *obj* if it is a dictionary or stream, then every direct
    (inline) dictionary nested inside it. Indirect children are not
    followed: each is an object in its own right and is visited as one."""
    if depth > 100:
        return
    if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
        yield obj
        children = [obj[k] for k in obj.keys()]
    elif isinstance(obj, pikepdf.Array):
        children = list(obj)
    else:
        return
    for child in children:
        if isinstance(child, (pikepdf.Dictionary, pikepdf.Array)) and not child.is_indirect:
            yield from _nested_dicts(child, depth + 1)


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
        except Exception:
            continue


def _name_tree_values(node):
    """Yield every value stored in a name tree: leaves hold
    ``[key, value, key, value, ...]`` in /Names, intermediate nodes hold
    /Kids. Iterative and cycle-safe."""
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
            if isinstance(entries, pikepdf.Array):
                for i in range(1, len(entries), 2):
                    yield entries[i]
            kids = cur.get("/Kids")
            if isinstance(kids, pikepdf.Array):
                stack.extend(list(kids)[::-1])
        except Exception:
            continue


def _scan_embedded_files(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Embedded files (attachments), and references to files outside the PDF."""
    names: list[str] = []
    external: list[str] = []
    try:
        root = pdf.Root
        nm = root.get("/Names")
        if isinstance(nm, pikepdf.Dictionary) and "/EmbeddedFiles" in nm:
            _walk_ef_name_tree(nm["/EmbeddedFiles"], names)
    except Exception:
        pass

    # Filespecs anywhere, not only in the name tree: on /FileAttachment and
    # other annotations, in /AF arrays, inside actions. /Type is optional on
    # a filespec and omitted by some producers (ANL-04), so what decides is
    # the payload: a filespec whose /EF holds a stream is an embedded file,
    # whatever it is called and wherever it sits. One with no payload only
    # points at a file elsewhere — nothing to remove, but the path it carries
    # is reported separately.
    for d in _filespec_dicts(pdf):
        name = _filespec_name(d)
        if _has_payload(d):
            names.append(name or "(unnamed embedded file)")
        elif name and _as_str(d.get("/Type")) == "/Filespec":
            external.append(name)

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
    """AcroForm and XFA presence."""
    try:
        acro = pdf.Root.get("/AcroForm")
    except Exception:
        acro = None
    if acro is None:
        return
    try:
        has_xfa = "/XFA" in acro
        nfields = len(acro.get("/Fields", []))
    except Exception:
        has_xfa, nfields = False, 0

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
    """Optional content groups (layers) — content can be hidden."""
    try:
        ocp = pdf.Root.get("/OCProperties")
    except Exception:
        ocp = None
    if ocp is None:
        return
    try:
        groups = ocp.get("/OCGs", [])
        n = len(groups)
    except Exception:
        n = 0
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
    except Exception:
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
        log.debug("invisible-text scan skipped: %s", exc)
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

def analyze_document(path: str, password: Optional[str] = None) -> dict:
    """Run the full privacy/security audit and return a JSON-ready dict."""
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

        # Run every scanner defensively — one failure must not abort the rest.
        # (_scan_embedded_image_metadata is defined lower, in the image
        # section, so it can reuse the standalone-image EXIF helpers.)
        for scan in (_scan_metadata, _scan_scripts_and_actions, _scan_links,
                     _scan_embedded_files, _scan_forms, _scan_optional_content,
                     _scan_embedded_image_metadata):
            try:
                scan(pdf, res)
            except Exception as exc:
                log.warning("scanner %s failed: %s", scan.__name__, exc)

    # Invisible-text scan needs the path (PyMuPDF), runs outside the pikepdf ctx
    try:
        _scan_invisible_text(path, res)
    except Exception as exc:
        log.debug("invisible text scan failed: %s", exc)

    if not res.findings:
        res.add(Finding(
            id="clean", category="content", severity="info",
            title="No privacy or security concerns detected",
            detail="No JavaScript, auto-run actions, embedded files, trackers, "
                   "or identifying metadata were found.",
        ))

    return res.to_dict()


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


def _iter_image_xobjects(pdf: pikepdf.Pdf):
    """Yield ``(page_index, name, xobject)`` for every image XObject reachable
    from a page's resources, recursing into form XObjects.

    Deduplicates by indirect-object identity (``objgen``) so an image reused
    on many pages is visited exactly once — the caller gets each distinct
    embedded image a single time.
    """
    seen = set()

    def walk(resources, page_index, depth=0):
        if resources is None or depth > 8:
            return
        try:
            xobjs = resources.get("/XObject")
        except Exception:
            return
        if xobjs is None:
            return
        for name, xobj in xobjs.items():
            try:
                subtype = str(xobj.get("/Subtype"))
            except Exception:
                continue
            if subtype == "/Image":
                og = xobj.objgen
                if og in seen:
                    continue
                seen.add(og)
                yield page_index, name, xobj
            elif subtype == "/Form":
                yield from walk(xobj.get("/Resources"), page_index, depth + 1)

    for pi, page in enumerate(pdf.pages, 1):
        try:
            resources = page.get("/Resources")
        except Exception:
            continue
        yield from walk(resources, pi)


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

    A node is dangerous when its /S is JavaScript/Launch/SubmitForm/
    ImportData (gated on the matching opt) or it carries an inline /JS
    entry (gated on ``javascript``) — the same signal the analyzer's
    _scan_scripts_and_actions flags (``/S`` or inline ``/JS``).
    """
    try:
        stype = _as_str(action.get("/S"))
    except Exception:
        return None
    opt_counter = _DANGEROUS_ACTIONS.get(stype)
    if opt_counter is not None and opts.get(opt_counter[0]):
        return opt_counter[1]
    try:
        if "/JS" in action and opts.get("javascript"):
            return "annot_javascript"
    except Exception:
        pass
    return None


def _clean_action_next(action, opts: dict, bump, depth: int = 0) -> None:
    """Recursively excise dangerous nodes buried in *action*'s ``/Next``
    chain, in place, preserving benign nodes and their surviving tail.

    ANL-02: the analyzer's ``_collect_actions._walk`` follows ``/Next``
    when flagging buried JavaScript/Launch/SubmitForm, but the sanitiser
    used to inspect only the top-level ``/A`` — so an annotation with a
    benign ``/URI`` head (kept when ``external_links`` is off) but a
    ``/Next`` that runs JavaScript survived even with ``javascript`` on.
    Each dropped node bumps the same counter its head would, so the count
    stays honest. Depth-capped (a >30-deep action chain isn't legitimate)
    and exception-tolerant like the rest of this module.
    """
    if depth > 30:
        try: del action["/Next"]
        except Exception: pass
        return
    try:
        nxt = action.get("/Next")
    except Exception:
        return
    if nxt is None:
        return
    try:
        nodes = list(nxt) if isinstance(nxt, pikepdf.Array) else [nxt]
    except Exception:
        return

    survivors = []
    for node in nodes:
        try:
            if not isinstance(node, pikepdf.Dictionary):
                continue
        except Exception:
            continue
        # Clean this node's own tail first so a promoted survivor is already
        # scrubbed and never needs re-processing.
        _clean_action_next(node, opts, bump, depth + 1)
        key = _action_head_drop(node, opts)
        if key is None:
            survivors.append(node)
            continue
        bump(key)
        # Dangerous node excised — promote its (already-cleaned) /Next tail
        # so a benign action chained after it isn't lost with it.
        try:
            tail = node.get("/Next")
        except Exception:
            tail = None
        if isinstance(tail, pikepdf.Array):
            survivors.extend(list(tail))
        elif tail is not None:
            survivors.append(tail)

    try:
        if not survivors:
            del action["/Next"]
        elif len(survivors) == 1:
            action["/Next"] = survivors[0]
        else:
            action["/Next"] = pikepdf.Array(survivors)
    except Exception:
        pass


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
    the owner's /AA goes whole; otherwise it goes only once it is empty.

    Returns True if the owner's own /A entry was deleted. What that means
    for the owner is the caller's business: an annotation is dropped from
    its page, a bookmark or a field stays. Losing a /PA does not count — the
    link's present target is untouched.
    """
    head_removed = False
    for site in list(_owner_sites(owner_kind, label, owner)):
        try:
            action = site.holder[site.key]
            key = _entry_removal(site.kind, action, opts)
            if key is None:
                _clean_action_next(action, opts, bump)
                continue
            del site.holder[site.key]
            bump(key)
            if site.key == "/A":
                head_removed = True
        except Exception:
            continue
    if _OWNER_ENTRIES[owner_kind][1] is not None:
        try:
            aa = owner.get("/AA")
            if aa is not None and (opts.get("auto_actions") or (
                    isinstance(aa, pikepdf.Dictionary) and len(aa.keys()) == 0)):
                del owner["/AA"]
        except Exception:
            pass
    return head_removed


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
        except Exception:
            continue


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

    def clean(container, depth: int = 0) -> None:
        if depth > 100:
            return
        if isinstance(container, pikepdf.Array):
            for i in range(len(container) - 1, -1, -1):
                v = container[i]
                if is_target(v): del container[i]
                elif is_direct_container(v): clean(v, depth + 1)
        else:
            for k in list(container.keys()):
                v = container[k]
                if is_target(v): del container[k]
                elif is_direct_container(v): clean(v, depth + 1)

    for obj in [pdf.trailer, *_iter_all_objects(pdf)]:
        try:
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
                clean(obj)
        except Exception:
            continue


def sanitize_pdf(input_path: str, output_path: str,
                 options: Optional[dict] = None) -> dict:
    """Strip dangerous/active content from a PDF.

    Returns a dict describing what was removed.  Writes atomically.
    """
    opts = {**DEFAULT_SANITIZE, **(options or {})}
    removed: dict[str, int] = {}

    def _bump(key: str, n: int = 1) -> None:
        if n > 0:                   # a counter exists only for what was removed
            removed[key] = removed.get(key, 0) + n

    # allow_overwriting_input reads the input fully into memory at open time and
    # releases the OS file handle, so the atomic write below can os.replace over
    # input_path for an in-place sanitise (output_path == input_path). Without
    # it, os.replace hits a handle pikepdf still holds → PermissionError
    # [WinError 5] on Windows (ANL-01). Harmless when output_path differs.
    with pikepdf.open(input_path, allow_overwriting_input=True) as pdf:
        root = pdf.Root

        # Catalog actions: /OpenAction and the document's /AA, entry by entry.
        _scrub_owner_actions("document", "Document", root, opts, _bump)
        # /Names -> /JavaScript (document-level named scripts)
        try:
            names = root.get("/Names")
            if names is not None and "/JavaScript" in names and opts["javascript"]:
                del names["/JavaScript"]
                _bump("named_javascript")
        except Exception:
            pass

        # Embedded files. One file is one count, however many places list
        # it: the counter that gets it is the first removal to reach it.
        # (pikepdf >= 10.16 registers an attachment in both the name tree and
        # the catalog's /AF, as the same object.)
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

        def _drop_af(holder) -> None:
            """Delete *holder*'s /AF (associated files), counting the files
            in it that nothing counted before. /AF references embedded
            streams independently of the name tree, so it is stripped
            wherever it appears — root, pages, annotations — or the streams
            stay referenced and survive (ANL-04)."""
            try:
                af = holder.get("/AF")
                if af is None:
                    return
                listed = list(af) if isinstance(af, pikepdf.Array) else [af]
                del holder["/AF"]
                _bump("associated_file", sum(1 for fs in listed if _first_time(fs)))
            except Exception:
                pass

        if opts["embedded_files"]:
            try:
                names = root.get("/Names")
                if names is not None and "/EmbeddedFiles" in names:
                    listed = list(_name_tree_values(names["/EmbeddedFiles"]))
                    del names["/EmbeddedFiles"]
                    _bump("embedded_files", sum(1 for fs in listed if _first_time(fs)))
            except Exception:
                pass
            _drop_af(root)

        # Walk pages & annotations for active content. This loop reaches the
        # annotations itself rather than through _action_owners because it
        # rewrites each page's /Annots as it goes; a test holds the two walks
        # to the same set of annotations.
        dropped: set = set()        # (num, gen) of annotations already dropped
        for pi, page in enumerate(pdf.pages, 1):
            _scrub_owner_actions("page", f"Page {pi}", page, opts, _bump)
            if opts["embedded_files"]:
                _drop_af(page)

            annots = page.get("/Annots")
            if annots is None:
                continue
            keep = []
            for annot in list(annots):
                try:
                    ident = tuple(annot.objgen) if annot.is_indirect else None
                    if ident is not None and ident in dropped:
                        continue             # listed on an earlier page too; gone there
                    # /A and /AA, by the rule every site uses: an action goes
                    # for what it does or, in /AA, for firing by itself; one
                    # that stays has its /Next chain cleaned (ANL-02). When
                    # the annotation's own /A had to go, the annotation is
                    # dropped — and the /A has been deleted from it as well,
                    # because taking a widget off the page does not take it
                    # out of the file: /AcroForm still lists it.
                    drop = _scrub_owner_actions("annot", f"Page {pi} annotation",
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
                        _drop_af(annot)
                    if not drop:
                        keep.append(annot)
                    elif ident is not None:
                        dropped.add(ident)
                except Exception:
                    keep.append(annot)
            if len(keep) != len(annots):
                page["/Annots"] = pikepdf.Array(keep)

        # Bookmarks, and form fields no page shows (a parent field, a field
        # with no widget, a widget just dropped above). The owners come from
        # the same generator the analyzer walks.
        for owner_kind, label, owner in _action_owners(pdf):
            if owner_kind in ("outline", "field"):
                _scrub_owner_actions(owner_kind, label, owner, opts, _bump)

        # Embedded files the structural removals above could not reach: a
        # payload on any other holder (a /Screen or /RichMedia annotation, a
        # rendition, an action, a private key). It is found by what it is —
        # a dictionary whose /EF holds a stream — among the objects that will
        # still be written, and emptied in place: the payload, and with it
        # the file name, description and everything else that describes it.
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
            except Exception:
                pass

        # A script run when 3D artwork loads: an /OnInstantiate stream on a
        # stream typed /3D. It is JavaScript, but not an action, so no action
        # site holds it; it is found by what the stream is. The artwork stays.
        if opts["javascript"]:
            for obj in _iter_all_objects(pdf):
                try:
                    if (isinstance(obj, pikepdf.Stream)
                            and _as_str(obj.get("/Type")) == "/3D"
                            and "/OnInstantiate" in obj):
                        del obj["/OnInstantiate"]
                        _bump("instantiate_script")
                except Exception:
                    continue

        if opts["metadata"]:
            # Delete the metadata streams themselves, wherever they hang.
            # Never open the packet for editing: pikepdf's recovering parser
            # accepts malformed XMP it cannot then clear, so clear()-and-save
            # writes the recovered tree back with its contents intact — and
            # entering the editing context at all invents a packet (and an
            # /Info) in a file that had neither.
            try:
                streams = _metadata_streams(pdf)
                if streams:
                    _drop_stream_refs(pdf, set(streams))
                    _bump("xmp_metadata", len(streams))
            except Exception:
                pass
            try:
                info = pdf.trailer.get("/Info")
                if info is not None:
                    try: had_entries = len(info.keys()) > 0
                    except Exception: had_entries = True
                    del pdf.trailer["/Info"]
                    if had_entries:
                        _bump("doc_info")
            except Exception:
                pass

        # Atomic write. fix_metadata_version stays off: it parses any XMP
        # packet on the way out, which rewrites one the user kept and raises
        # outright on one that cannot be decoded.
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
        os.close(fd)
        save_kwargs = {"fix_metadata_version": False,
                       "object_stream_mode": pikepdf.ObjectStreamMode.generate}
        try:
            save_with_content_id(pdf, tmp, **save_kwargs)
            # Write what was just written. qpdf packs objects into object
            # streams by the numbers they had on the way in, so the first
            # write of a file is not the layout qpdf gives that same file
            # the next time. The second is, and stays: that is what makes
            # sanitizing an already-sanitized file change no byte, and lets
            # its identifier (a digest of the content) come out the same.
            with pikepdf.open(tmp, allow_overwriting_input=True) as settled:
                save_with_content_id(settled, tmp, **save_kwargs)
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
