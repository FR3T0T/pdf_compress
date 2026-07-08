"""
pdf_analyze — Document privacy & security audit engine.

Scans a PDF and reports everything that is privacy- or security-relevant
*without* sending anything anywhere.  Everything runs locally on pikepdf
(required) with optional PyMuPDF (fitz) enhancements when available.

What it surfaces
----------------
  - Identifying metadata (Author / Creator / Producer / dates / XMP)
  - Embedded JavaScript (/JS in the name tree, /OpenAction, page & field /AA)
  - Auto-run actions (/OpenAction, document /AA triggers)
  - Launch actions (/Launch — can start external programs)
  - External links & URIs (potential tracking beacons / phone-home)
  - Remote/embedded GoTo actions (/GoToR, /GoToE)
  - Embedded files / attachments (/EmbeddedFiles, /Filespec)
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

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

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

def _scan_metadata(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Identifying document-info fields and XMP metadata."""
    leaky = []
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            xmp_present = bool(len(meta))
    except Exception:
        xmp_present = False

    info_keys = ("/Author", "/Creator", "/Producer", "/Title",
                 "/Subject", "/Keywords", "/CreationDate", "/ModDate")
    try:
        docinfo = pdf.trailer.get("/Info")
    except Exception:
        docinfo = None

    if docinfo is not None:
        for k in info_keys:
            try:
                if k in docinfo:
                    val = _as_str(docinfo[k]).strip()
                    if val:
                        leaky.append(f"{k[1:]}: {val[:120]}")
            except Exception:
                continue

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
    if xmp_present:
        res.add(Finding(
            id="meta.xmp",
            category="metadata",
            severity="low",
            title="XMP metadata stream present",
            detail="An XMP packet can carry author, tool, edit-history, and "
                   "GPS/device hints beyond the basic /Info dictionary.",
            count=1,
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


def _describe_autorun(where: str, stype: str) -> str:
    if "/OpenAction" in where:
        when = "fires as soon as the document is opened"
    elif "/AA" in where:
        toks = where.replace("/Next", "").split()
        trig = next((t for t in reversed(toks) if t.startswith("/") and t != "/AA"), "")
        when = _TRIGGER_MEANINGS.get(trig, f"fires on the {trig} event" if trig else "fires automatically")
    else:
        when = "fires automatically"
    verb = _ACTION_VERBS.get(stype, "runs an action")
    return f"{where} — {when}; {verb}"


def _collect_actions(pdf: pikepdf.Pdf):
    """Yield (where, action_dict) for every action reachable in the document.

    Covers document /OpenAction, document /AA, page /AA, and each
    annotation's /A and /AA — following /Next chains.  This is where
    Launch / URI / SubmitForm / GoToR / JavaScript actions actually live,
    and many are *inline* dictionaries that ``pdf.objects`` never yields.
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

    root = pdf.Root
    try:
        if "/OpenAction" in root:
            yield from _walk(root["/OpenAction"], "Document /OpenAction")
    except Exception:
        pass
    try:
        if "/AA" in root and isinstance(root["/AA"], pikepdf.Dictionary):
            for trig in root["/AA"].keys():
                yield from _walk(root["/AA"][trig], f"Document /AA {trig}")
    except Exception:
        pass

    try:
        pages = pdf.pages
    except Exception:
        pages = []
    for pi, page in enumerate(pages):
        try:
            if "/AA" in page and isinstance(page["/AA"], pikepdf.Dictionary):
                for trig in page["/AA"].keys():
                    yield from _walk(page["/AA"][trig], f"Page {pi+1} /AA {trig}")
        except Exception:
            pass
        try:
            annots = page.get("/Annots")
        except Exception:
            annots = None
        if annots is None:
            continue
        for annot in annots:
            try:
                if "/A" in annot:
                    yield from _walk(annot["/A"], f"Page {pi+1} annotation /A")
                if "/AA" in annot and isinstance(annot["/AA"], pikepdf.Dictionary):
                    for trig in annot["/AA"].keys():
                        yield from _walk(annot["/AA"][trig],
                                         f"Page {pi+1} annotation /AA {trig}")
            except Exception:
                continue


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
    for where, action in _collect_actions(pdf):
        try:
            stype = _as_str(action.get("/S"))
        except Exception:
            stype = ""

        if where.startswith("Document /OpenAction") or "/AA" in where:
            autorun.append(_describe_autorun(where, stype))

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


def _scan_embedded_files(pdf: pikepdf.Pdf, res: AnalysisResult) -> None:
    """Attachments / embedded files."""
    names: list[str] = []
    try:
        root = pdf.Root
        nm = root.get("/Names")
        if isinstance(nm, pikepdf.Dictionary) and "/EmbeddedFiles" in nm:
            _walk_ef_name_tree(nm["/EmbeddedFiles"], names)
    except Exception:
        pass

    # Also catch filespecs anywhere — including those that ride on
    # /FileAttachment annotations rather than the name tree. /Type is
    # OPTIONAL on a filespec and omitted by some producers, so treat any dict
    # carrying /EF (an embedded-file stream) as one regardless of /Type
    # (ANL-04); external filespecs keep the older /Type-gated, name-required
    # behaviour.
    for obj in _iter_all_objects(pdf):
        try:
            if not isinstance(obj, pikepdf.Dictionary):
                continue
            if "/EF" in obj:
                f = _as_str(obj.get("/F") or obj.get("/UF") or "")
                names.append(f if f.strip() else "(unnamed embedded file)")
            elif _as_str(obj.get("/Type")) == "/Filespec":
                f = _as_str(obj.get("/F") or obj.get("/UF") or "")
                if f.strip():
                    names.append(f)
        except Exception:
            continue

    names = _dedupe([n for n in names if n.strip()])
    if names:
        res.add(Finding(
            id="files.embedded", category="files", severity="high",
            title="Embedded files / attachments",
            detail="The PDF carries embedded files. These can hide additional "
                   "documents or executable payloads.",
            count=len(names), items=names[:50],
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


def _page_content_blobs(doc, page) -> list:
    """Decoded bytes of every content stream on *page*, plus the streams of
    any form XObjects it references.

    Render-mode-3 text commonly lands in a *later* content stream after
    incremental edits, or inside a form XObject — the old code read only
    ``page.get_contents()[0]`` and saw neither (ANL-03). Image XObjects are
    skipped (their streams are pixel data, not operators).
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
            content = b"\n".join(_page_content_blobs(doc, page))
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
        for scan in (_scan_metadata, _scan_scripts_and_actions, _scan_links,
                     _scan_embedded_files, _scan_forms, _scan_optional_content):
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


# Maps a dangerous action /S subtype to (sanitize opt, removed[] counter key).
# /SubmitForm and /ImportData share the submit gate/counter, mirroring the
# analyzer's grouping in _scan_scripts_and_actions.
_DANGEROUS_ACTIONS = {
    "/JavaScript": ("javascript", "annot_javascript"),
    "/Launch": ("launch_actions", "launch_action"),
    "/SubmitForm": ("submit_actions", "submit_action"),
    "/ImportData": ("submit_actions", "submit_action"),
}


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


def sanitize_pdf(input_path: str, output_path: str,
                 options: Optional[dict] = None) -> dict:
    """Strip dangerous/active content from a PDF.

    Returns a dict describing what was removed.  Writes atomically.
    """
    opts = {**DEFAULT_SANITIZE, **(options or {})}
    removed: dict[str, int] = {}

    def _bump(key: str, n: int = 1) -> None:
        removed[key] = removed.get(key, 0) + n

    # allow_overwriting_input reads the input fully into memory at open time and
    # releases the OS file handle, so the atomic write below can os.replace over
    # input_path for an in-place sanitise (output_path == input_path). Without
    # it, os.replace hits a handle pikepdf still holds → PermissionError
    # [WinError 5] on Windows (ANL-01). Harmless when output_path differs.
    with pikepdf.open(input_path, allow_overwriting_input=True) as pdf:
        root = pdf.Root

        if opts["javascript"] or opts["auto_actions"]:
            # /OpenAction
            try:
                if "/OpenAction" in root:
                    oa = _as_str(root["/OpenAction"])
                    if opts["auto_actions"] or "/JavaScript" in oa or "/JS" in oa:
                        del root["/OpenAction"]
                        _bump("open_action")
            except Exception:
                pass
            # Document /AA
            try:
                if opts["auto_actions"] and "/AA" in root:
                    del root["/AA"]
                    _bump("document_aa")
            except Exception:
                pass
            # /Names -> /JavaScript
            try:
                names = root.get("/Names")
                if names is not None and "/JavaScript" in names and opts["javascript"]:
                    del names["/JavaScript"]
                    _bump("named_javascript")
            except Exception:
                pass

        # Embedded files
        if opts["embedded_files"]:
            try:
                names = root.get("/Names")
                if names is not None and "/EmbeddedFiles" in names:
                    del names["/EmbeddedFiles"]
                    _bump("embedded_files")
            except Exception:
                pass
            # /AF (associated files) reference embedded streams independently
            # of the name tree; strip it wherever it appears — root, pages,
            # annotations — or the streams stay referenced and survive (ANL-04).
            try:
                if "/AF" in root:
                    del root["/AF"]
                    _bump("associated_file")
            except Exception:
                pass

        # Walk pages & annotations for active content
        for page in pdf.pages:
            try:
                if "/AA" in page and opts["auto_actions"]:
                    del page["/AA"]
                    _bump("page_aa")
            except Exception:
                pass
            try:
                if opts["embedded_files"] and "/AF" in page:
                    del page["/AF"]
                    _bump("associated_file")
            except Exception:
                pass

            annots = page.get("/Annots")
            if annots is None:
                continue
            keep = []
            for annot in list(annots):
                try:
                    drop = False
                    a = annot.get("/A")
                    if a is not None:
                        head_key = _action_head_drop(a, opts)
                        if head_key is not None:
                            drop = True; _bump(head_key)      # dangerous head → drop annotation
                        else:
                            stype = _as_str(a.get("/S"))
                            if stype == "/URI" and opts["external_links"]:
                                drop = True; _bump("external_link")
                            else:
                                # Benign head kept — but walk its /Next chain so
                                # a buried JS/Launch/Submit can't ride along (ANL-02).
                                _clean_action_next(a, opts, _bump)
                    # field/annotation additional actions (/AA): process each
                    # trigger entry the same way, excising dangerous heads and
                    # buried /Next nodes rather than blindly dropping benign ones.
                    aa = annot.get("/AA")
                    if isinstance(aa, pikepdf.Dictionary):
                        for trig in list(aa.keys()):
                            try:
                                entry = aa[trig]
                                if not isinstance(entry, pikepdf.Dictionary):
                                    continue
                                ek = _action_head_drop(entry, opts)
                                if ek is not None:
                                    del aa[trig]; _bump(ek)
                                else:
                                    _clean_action_next(entry, opts, _bump)
                            except Exception:
                                continue
                        try:
                            if len(aa.keys()) == 0:
                                del annot["/AA"]
                        except Exception:
                            pass
                    # Embedded files carried by the annotation itself:
                    # /FileAttachment annots hold a stream via /FS→/EF, and /AF
                    # arrays reference embedded streams — both independent of
                    # the name tree, and both invisible to the /A→/S check
                    # above (ANL-04).
                    if opts["embedded_files"]:
                        if _as_str(annot.get("/Subtype")) == "/FileAttachment":
                            drop = True; _bump("file_attachment_annot")
                        try:
                            if "/AF" in annot:
                                del annot["/AF"]; _bump("associated_file")
                        except Exception:
                            pass
                    if not drop:
                        keep.append(annot)
                except Exception:
                    keep.append(annot)
            if len(keep) != len(annots):
                page["/Annots"] = pikepdf.Array(keep)

        # AcroForm submit cleanup is implicit via annotation removal.

        if opts["metadata"]:
            try:
                with pdf.open_metadata(set_pikepdf_as_editor=False) as m:
                    m.clear()
                _bump("xmp_metadata")
            except Exception:
                pass
            try:
                if "/Info" in pdf.trailer:
                    del pdf.trailer["/Info"]
                    _bump("doc_info")
            except Exception:
                pass

        # Atomic write
        import tempfile
        out_dir = os.path.dirname(os.path.abspath(output_path)) or "."
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=out_dir)
        os.close(fd)
        try:
            pdf.save(tmp, fix_metadata_version=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate)
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
