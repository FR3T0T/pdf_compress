"""
removal_report — the proof of removal, in plain words, and the report saved
on request (#100).

Redact and Sanitize verify what they write before they save it, and return
the verification report (pdf_verify.VerificationReport.to_dict()). This
module turns a finished run into what a person reads:

``assemble`` (``proof`` on the answer the window gets)
    Three groups, by page where there are pages: what was removed and is
    confirmed gone, with the checks that confirm it; what was found and not
    targeted, each with a next step; and what was not checked, so that
    nothing reads as checked when it was not. Then a line on the earlier
    versions the source kept (#122), and one on the network (#120 d). Each
    item is ``{"what", "pages", "where", ...}``: its words never name its
    pages; "where" says them ("Pages 1-3 and 9"), for a reader to put in
    front. The redaction's items are made by
    pdf_ops.redaction_proof, Sanitize's by :func:`sanitize_proof`.
``save_payload`` (the Save report button)
    Writes the proof beside the new file as "<name>.removal-report.json"
    and "<name>.removal-report.txt", never over a file that is there
    (" (2)", " (3)" … are added to both), and answers with the two names.

The report never holds the words searched for, the text found, a password
or a folder: only file names, hashes, counts, pages and words made here.
Every line is built from those, never from a check's evidence, a finding's
items or an attachment's name, which can quote the document.

Qt-free, and imports no module of the app's at the top (pdf_ops and
pdf_analyze import this one); see CLAUDE.md.
"""

from __future__ import annotations

import json
import logging
import os
import re

import network_guard

log = logging.getLogger(__name__)

FORMAT_VERSION = 1

#: Check id -> what it confirms, in plain words.
CHECK_WORDS = {
    "redaction.readable": "The new file opens and can be read to check it",
    "redaction.page_text": "None of the searched-for words can be read in the text of the pages",
    "redaction.raw_content": "None of the searched-for words is in the code that draws the pages",
    "redaction.annotations": "None of the searched-for words is in a note or comment",
    "redaction.form_fields": "None of the searched-for words is in what is filled into a form "
                             "field",
    "redaction.links": "None of the searched-for words is in a link",
    "redaction.docinfo": "None of the searched-for words is in the document's details, such as "
                         "the title or author",
    "redaction.xmp": "None of the searched-for words is in the hidden document details (XMP)",
    "redaction.bookmarks": "None of the searched-for words is in a bookmark title",
    "redaction.embedded_files": "None of the searched-for words is in the name of an attached "
                                "file",
    "redaction.term_sweep": "A search of everything else in the file finds none of the "
                            "searched-for words",
    "redaction.rect_regions": "No text is left under the boxes",
    "redaction.rect_annotations": "No note, stamp or other mark lies over the boxes",
    "redaction.rect_line_art": "No drawing has an edge inside the boxes",
    "redaction.search_image": "The pictures under the words found are blanked there",
    "redaction.ocr_fill": "Every place OCR found shows only black",
    "redaction.ocr_image": "The pictures under the places OCR found are blanked there",
    "redaction.ocr_reread": "OCR no longer reads the searched-for words on the pages it found "
                            "them on",
    "redaction.ocr_outside": "The pages OCR redacted are unchanged outside the boxes",
    "redaction.text_outside": "Text outside the boxes shows as it did",
    "redaction.left_behind": "Nothing a box covered is left in the file where no page shows it",
    "redaction.structure_streams": "Nothing is kept in the file by its structure alone",
    "redaction.thumbnails": "No page has a small preview picture of itself",
    "redaction.one_version": "The new file keeps no earlier versions of itself",
    "redaction.file_id": "The new file does not carry the original's ID, which would link the two",
    "sanitization.readable": "The clean copy opens and can be scanned again",
    "sanitize.one_version": "The clean copy keeps no earlier versions of itself",
    "sanitize.file_id": "The clean copy does not carry the original's ID, which would link the "
                        "two",
}

#: Finding id -> what Sanitize's re-scan looks for, in words.
_FINDING_WORDS = {
    "scripts.js": "JavaScript", "actions.launch": "actions that start another program",
    "actions.autorun": "actions that run by themselves",
    "forms.submit": "actions that send or load form data", "files.embedded": "attached files",
    "links.uri": "web links", "meta.docinfo": "document details, such as the title or author",
    "meta.xmp": "hidden document details (XMP)",
}

#: Sanitize option -> what it removes, in words (its sweep check).
_OPTION_WORDS = {
    "javascript": "JavaScript", "launch_actions": "actions that start another program",
    "auto_actions": "actions that run by themselves",
    "submit_actions": "actions that send or load form data", "embedded_files": "attached files",
    "external_links": "web links", "metadata": "document details",
}


def check_in_words(check: dict) -> str:
    """What one check confirms, in plain words; a check this does not know
    keeps its own description."""
    cid = str(check.get("id", ""))
    if cid in CHECK_WORDS:
        return CHECK_WORDS[cid]
    if cid.startswith("sanitize.sweep_") and cid[len("sanitize.sweep_"):] in _OPTION_WORDS:
        return f"A look at every object in the file finds no {_OPTION_WORDS[cid[15:]]}"
    if cid.startswith("sanitize."):
        fid = cid[len("sanitize."):].replace("_", ".", 1)
        if fid in _FINDING_WORDS:
            return f"A new scan finds no {_FINDING_WORDS[fid]}"
    return str(check.get("description", ""))


# ── Sanitize ─────────────────────────────────────────────────────────────

def _tick(label: str) -> str:
    return f"Run Sanitize with “{label}” ticked."


#: Finding id -> the next step for it, when Sanitize left it in the copy.
#: The option names are the panel's own (web-react SANITIZE_FIELDS).
NEXT_STEPS = {
    "meta.docinfo": _tick("Document & XMP metadata"),
    "meta.xmp": _tick("Document & XMP metadata"),
    "scripts.js": _tick("Embedded JavaScript"),
    "actions.launch": _tick("Launch actions (run external programs)"),
    "actions.autorun": _tick("Auto-run actions (/OpenAction, /AA)"),
    "forms.submit": _tick("Form submit / import actions"),
    "files.embedded": _tick("Embedded files / attachments"),
    "links.uri": _tick("External links / URLs (trackers)"),
    "actions.remote": ("Sanitize has no option that removes these: they open another file. Check "
                       "where they point before you follow them."),
    "files.external": ("Nothing to remove: the file only names another file. Check that file "
                       "before you open it."),
    "forms.xfa": "Sanitize has no option that removes an XFA form.",
    "forms.acro": "Use Flatten to remove the form fields, if you no longer need them.",
    "content.ocg": ("Sanitize has no option that removes layers. Open the file in a viewer that "
                    "lists its layers to see what they hold."),
    "content.invisible_text": ("Normal for a scan read with OCR. If it is text someone meant to "
                               "hide, remove it with Redact."),
    "content.earlier_versions": "Run Sanitize: the copy it writes keeps only the current version.",
    "location.embedded_image": "Sanitize has no option that removes data inside pictures.",
    "metadata.embedded_image": "Sanitize has no option that removes data inside pictures.",
}
_NO_OPTION = "Sanitize has no option that removes this."

#: (surface, removed[] keys, one, many): what Sanitize counted, in words.
_SANITIZE_REMOVED = (
    ("javascript", {"annot_javascript", "instantiate_script"}, "script", "scripts"),
    ("javascript", {"named_javascript"}, "set of the document's named scripts",
     "sets of the document's named scripts"),
    ("launch_actions", {"launch_action"}, "action that starts another program",
     "actions that start other programs"),
    ("auto_actions", {"open_action", "document_aa", "page_aa", "annot_aa", "field_aa"},
     "action that runs by itself", "actions that run by themselves"),
    ("submit_actions", {"submit_action"}, "action that sends or loads form data",
     "actions that send or load form data"),
    ("external_links", {"external_link"}, "web link", "web links"),
    ("embedded_files", {"embedded_files", "embedded_file", "associated_file",
                        "file_attachment_annot"}, "attached file", "attached files"),
    ("metadata", {"doc_info"}, "set of document details, such as the title or author",
     "sets of document details, such as the title or author"),
    ("metadata", {"xmp_metadata"}, "set of hidden document details (XMP)",
     "sets of hidden document details (XMP)"),
)


def _how_many(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def sanitize_proof(result: dict, titles: dict | None = None) -> dict:
    """:func:`assemble` for a finished Sanitize (pdf_analyze.sanitize_pdf's
    answer): what it counted as removed, the findings its options did not
    cover (residuals) with their next step, and what could not be checked.
    *titles* names a finding id (pdf_analyze's own titles)."""
    titles = titles or {}
    counts = {k: int(n) for k, n in dict(result.get("removed") or {}).items() if n}
    removed, known = [], set()
    for surface, keys, one, many in _SANITIZE_REMOVED:
        known |= keys
        n = sum(counts.get(k, 0) for k in keys)
        if n:
            removed.append({"what": _how_many(n, one, many), "pages": [], "surface": surface})
    for key, n in counts.items():
        if key not in known:
            removed.append({"what": _how_many(n, "other item", "other items")
                            + f" ({key.replace('_', ' ')})", "pages": [], "surface": key})
    verification = result.get("verification") or {}
    not_targeted = [{"what": str(f.get("title") or titles.get(f.get("id"), f.get("id"))),
                     "pages": [], "next": NEXT_STEPS.get(f.get("id"), _NO_OPTION)}
                    for f in verification.get("residualFindings") or []]
    not_checked = [{"what": f"{titles.get(u.get('id'), u.get('id'))}: could not be fully checked"
                            + (f" ({(u.get('reasons') or [''])[0]})" if u.get("reasons") else "")
                            + ".",
                    "pages": [], "next": None}
                   for u in verification.get("unevaluated") or []]
    return assemble("Sanitize", verification, removed, not_targeted, not_checked)


# ── Both tools ───────────────────────────────────────────────────────────

def versions_in_words(earlier: dict) -> str:
    """The earlier-versions line (#122): "The source kept 2 earlier
    versions; this copy keeps none."."""
    earlier = dict(earlier or {})
    output = earlier.get("output")
    copy = ("this copy keeps none" if output == 0 else
            "this copy's could not be counted" if output is None else
            f"this copy keeps {output}")
    if "source" not in earlier:
        return f"Earlier versions: {copy}."
    source = earlier["source"]
    if source is None:
        return f"Whether the source kept earlier versions could not be checked; {copy}."
    if source == 0:
        return f"The source kept no earlier versions; {copy}."
    return f"The source kept {source} earlier version{'' if source == 1 else 's'}; {copy}."


def assemble(tool: str, verification: dict, removed: list, not_targeted: list,
             not_checked: list) -> dict:
    """The proof of a finished run: *tool* ("Redact" or "Sanitize"), the
    groups as given (items ``{"what", "pages", "surface" | "next"}``), every
    check in plain words with its id (``checks``: id, description, passed,
    pages), and the earlier-versions and network lines."""
    verification = dict(verification or {})
    earlier = dict(verification.get("earlierVersions") or {})
    not_checked = list(not_checked)
    if "source" in earlier and earlier["source"] is None:
        why = earlier.get("sourceWhy") or "they could not be counted"
        not_checked.append({"what": f"Whether the source kept earlier versions of itself: {why}.",
                            "pages": [], "next": None})

    def placed(items: list) -> list:
        # "where": the pages in words ("Pages 1-3 and 9"), "" for none.
        return [{**item, "where": _pages_label(item.get("pages") or [])} for item in items]

    checks = [{"id": str(c.get("id", "")), "description": check_in_words(c),
               "passed": bool(c.get("passed")), "pages": [int(p) for p in c.get("pages") or []]}
              for c in verification.get("checks") or []]
    return {"tool": tool, "removed": placed(removed), "notTargeted": placed(not_targeted),
            "notChecked": placed(not_checked), "checks": checks,
            "earlierVersions": versions_in_words(earlier),
            "network": network_guard.in_words(verification.get("network"))}


# ── The saved report ─────────────────────────────────────────────────────

def _file_name(path) -> str:
    """The last part of a path, whichever way its separators lean."""
    return re.split(r"[\\/]", str(path or ""))[-1]


def _pages_label(pages: list) -> str:
    if not pages:
        return ""
    # Here, not at the top: pdf_verify imports pdf_analyze, which imports this.
    from pdf_verify import pages_in_words
    words = pages_in_words(sorted(set(int(p) for p in pages)))
    return words[:1].upper() + words[1:]


def document(payload: dict) -> dict:
    """The saved report (JSON) for a finished run's *payload*: the answer the
    window got, with ``proof`` and ``verification``."""
    proof, verification = payload["proof"], payload.get("verification") or {}

    def items(group: str, step: bool) -> list:
        out = []
        for item in proof.get(group) or []:
            entry = {"what": item["what"], "pages": list(item.get("pages") or []),
                     "where": item.get("where") or _pages_label(item.get("pages") or [])}
            if step:
                entry["nextStep"] = item.get("next")
            else:
                entry["surface"] = item.get("surface")
            out.append(entry)
        return out

    return {
        "report": "Proof of removal",
        "formatVersion": FORMAT_VERSION,
        "tool": proof.get("tool"),
        "time": verification.get("timestamp"),
        "source": {"file": _file_name(verification.get("inputPath")),
                   "sha256": verification.get("sourceSha256")},
        "output": {"file": _file_name(verification.get("outputPath")),
                   "sha256": verification.get("outputSha256")},
        "verified": bool(verification.get("verified")),
        "removed": items("removed", step=False),
        "foundNotTargeted": items("notTargeted", step=True),
        "notChecked": items("notChecked", step=True),
        "earlierVersions": proof.get("earlierVersions"),
        "network": proof.get("network"),
        "checks": [dict(c) for c in proof.get("checks") or []],
    }


def as_text(doc: dict) -> str:
    """The same report, to read."""
    lines = [f"Proof of removal: {doc['tool']}", f"Made {doc['time']}", "",
             f"Original file: {doc['source']['file']}", f"  SHA-256: {doc['source']['sha256']}",
             f"New file: {doc['output']['file']}", f"  SHA-256: {doc['output']['sha256']}",
             "Result: " + ("verified; every check below passed." if doc["verified"] else
                           "not verified."), ""]

    def group(title: str, entries: list) -> None:
        lines.append(title)
        if not entries:
            lines.append("  Nothing.")
        for e in entries:
            where = e.get("where") or _pages_label(e.get("pages"))
            lines.append(f"  {where}: {e['what']}" if where else f"  {e['what']}")
            if e.get("nextStep"):
                lines.append(f"    Next step: {e['nextStep']}")
        lines.append("")

    group("Removed and confirmed gone", doc["removed"])
    group("Found but not targeted", doc["foundNotTargeted"])
    group("Not checked", doc["notChecked"])
    lines += [doc["earlierVersions"], doc["network"], "", "Checks"]
    for c in doc["checks"]:
        where = _pages_label(c.get("pages"))
        lines.append(f"  {'passed' if c['passed'] else 'FAILED'}  {c['id']}  {c['description']}"
                     + (f" ({where})" if where else ""))
    return "\n".join(lines) + "\n"


def save(payload: dict) -> tuple[str, str]:
    """Write the report for *payload* beside its new file; return the two
    paths. Never over a file that is there: the first free name of
    "<name>.removal-report", "<name> (2).removal-report", … is taken for
    both."""
    doc = document(payload)
    output = payload.get("output_path") or payload.get("output")
    folder = os.path.dirname(os.path.abspath(output))
    stem = os.path.splitext(os.path.basename(output))[0]
    data = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    text = as_text(doc)
    for n in range(1, 1000):
        tag = "" if n == 1 else f" ({n})"
        paths = [os.path.join(folder, f"{stem}{tag}.removal-report.{ext}") for ext in ("json", "txt")]
        if any(os.path.exists(p) for p in paths):
            continue
        written = []
        try:
            for path, body in zip(paths, (data, text), strict=True):
                with open(path, "x", encoding="utf-8", newline="\n") as fh:   # never over a file
                    written.append(path)
                    fh.write(body)
        except FileExistsError:
            for path in written:
                os.unlink(path)
            continue
        except BaseException:
            for path in written:
                if os.path.exists(path):
                    os.unlink(path)
            raise
        return paths[0], paths[1]
    raise FileExistsError("no free name for the report")


def save_payload(payload) -> dict:
    """The Save report button's answer: ``{"success": True, "files": [the
    two names]}``, or ``{"success": False, "error"}`` in plain words. Logs
    no file name above debug."""
    if not isinstance(payload, dict) or not payload.get("proof") or not payload.get("verification"):
        return {"success": False, "error": "There is no finished run to save a report for."}
    try:
        paths = save(payload)
    except OSError as exc:
        log.warning("The removal report could not be saved (%s)", type(exc).__name__)
        return {"success": False, "error": "The report could not be saved beside the new file."}
    log.info("Removal report saved (JSON and text)")
    log.debug("Removal report: %s", paths)
    return {"success": True, "files": [os.path.basename(p) for p in paths]}
