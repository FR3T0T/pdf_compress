"""Redact: a search that finds nothing, and a cancel, are not errors; the page
shows which file is loaded (#135).

  * A search that finds nothing raises RedactionNothingFoundError, a
    ValueError: callers that catch ValueError still do. The bridge's worker
    gives it a branch of its own, as it does RedactionNotPossibleError: one
    info line in the log, no stack trace, "nothingFound" in the done
    payload, and on the page a plain note with the whole message.
  * A cancel raises InterruptedError, which the worker already treats as a
    cancel. Its done payload says "cancelled", and the shared operation hook
    (useOperation) lets it go: the page that was cancelled has said so
    already, in its own line. That is every tool page's Cancel, not only
    Redact's: each showed a red "Cancelled" pop-up beside its own line.
  * The Redact page's drop box shows the loaded file's name, as Compare's
    do, with "click or drop to pick another".

The bridge and the window cannot be imported here (Qt); their sources, and
the built bundle, are read instead. Qt-free (see CLAUDE.md).
"""

import pathlib
import re
import threading

import pikepdf
import pytest
from pikepdf import Dictionary, Name

import pdf_ops

fitz = pytest.importorskip("fitz")

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _source(*parts) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _bundle() -> str:
    return "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in (ROOT / "web-react" / "dist").rglob("*.js"))


def _braced(text: str, at: int) -> str:
    """The JSX attribute value that starts at *at* ("{...}"), braces matched."""
    assert text[at] == "{", text[at:at + 40]
    depth = 0
    for i in range(at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[at:i + 1]
    raise AssertionError("unbalanced braces")


def _branch(source: str, start: str) -> str:
    """The except branch of the worker that begins with *start*."""
    at = source.index(start)
    return source[at:source.index("\n        except ", at + 10)]


def _pdf(path, *lines) -> str:
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    body = "\n".join(f"BT /F1 12 Tf 72 {720 - 20 * i} Td ({line}) Tj ET" for i, line in enumerate(lines))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792],
                                             Resources=Dictionary(Font=Dictionary(F1=font)),
                                             Contents=pdf.make_stream(body.encode()))))
    pdf.save(str(path))
    return str(path)


# ── A search that finds nothing ─────────────────────────────────────────

class TestNothingFound:
    def test_it_has_an_error_of_its_own_that_is_still_a_value_error(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", "Nothing to see on this page.")
        out = tmp_path / "out.pdf"
        with pytest.raises(pdf_ops.RedactionNothingFoundError) as info:
            pdf_ops.redact_pdf(src, str(out), search_terms=["ZEBRA-0001"])
        assert isinstance(info.value, ValueError)
        assert str(info.value) == "No matching content found to redact. Nothing was saved."
        assert not out.exists()

    def test_the_notes_come_before_nothing_was_saved(self, tmp_path):
        doc = fitz.open()
        doc.new_page().draw_rect(fitz.Rect(72, 72, 300, 300), fill=(0.2, 0.4, 0.7))
        src = str(tmp_path / "drawing.pdf")
        doc.save(src)
        with pytest.raises(pdf_ops.RedactionNothingFoundError) as info:
            pdf_ops.redact_pdf(src, str(tmp_path / "out.pdf"), search_terms=["ZEBRA-0001"])
        assert str(info.value) == (
            "No matching content found to redact. Page 1 has no text: a search cannot read "
            "words in a scan or a picture. Nothing was saved.")

    def test_the_worker_gives_it_a_plain_branch(self):
        worker = _source("ui", "bridge.py")
        branch = _branch(worker, "except RedactionNothingFoundError")
        assert worker.index("except RedactionNothingFoundError") < worker.index(
            "except Exception as exc:\n            log.exception")
        assert "log.info" in branch
        assert "log.exception" not in branch and "traceback" not in branch
        assert "str(exc)" in branch and '"nothingFound": True' in branch

    def test_the_page_shows_a_plain_note_not_an_error(self):
        page = _source("web-react", "src", "pages", "tools", "RedactPage.tsx")
        effect = page[page.index("} else if (op.status === 'error') {"):]
        effect = effect[:effect.index("}, [op.status")]
        nothing = effect[effect.index("nothingFound"):]
        nothing = nothing[:nothing.index("\n      else")]
        assert "toast.error" not in nothing
        assert "<NothingFoundPanel" in page and "failure?.nothingFound" in page
        assert "NothingFoundPanel" in _bundle() or "nothingFound" in _bundle()


# ── A cancel ────────────────────────────────────────────────────────────

class TestCancel:
    def test_a_cancel_is_an_interrupted_error(self, tmp_path):
        src = _pdf(tmp_path / "src.pdf", "SECRET-TERM here")
        out = tmp_path / "out.pdf"
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(InterruptedError):
            pdf_ops.redact_pdf(src, str(out), search_terms=["SECRET-TERM"], cancel=cancel)
        assert not out.exists()

    def test_no_cancel_in_redact_is_a_value_error(self):
        """Every place redact_pdf stops for a cancel."""
        source = _source("pdf_ops.py")
        assert 'ValueError("Cancelled")' not in source

    def test_the_worker_says_it_was_cancelled_without_a_stack_trace(self):
        worker = _source("ui", "bridge.py")
        branch = _branch(worker, "except (CancelledError, InterruptedError)")
        assert '"cancelled": True' in branch
        assert "log.exception" not in branch

    def test_the_shared_hook_lets_a_cancelled_run_go(self):
        """useOperation, which every tool page uses: a done payload that says
        "cancelled" puts nothing on the page, so the page's own line ("...
        cancelled.", "Redaction stopped. Nothing was saved.") is the only
        one."""
        hook = _source("web-react", "src", "bridge", "useOperation.ts")
        done = hook[hook.index("useEventBus('done'"):]
        done = done[:done.index("});")]
        assert re.search(r"if \(data\.cancelled\) return;", done)
        assert done.index("data.cancelled") < done.index("setStatus")
        assert "cancelled" in _source("web-react", "src", "types", "bridge.ts")

    def test_every_cancel_button_leaves_the_running_state(self):
        """What the shared fix relies on: every Cancel button in the window —
        each onCancel handler, not each page — stops the run, resets the
        operation it belongs to and says so in a line of its own. A handler
        that only asked the bridge to cancel would wait for a payload the
        hook now lets go, and stay "running" (Translate's setup panel did:
        onCancel={setupOp.cancel})."""
        handlers = []
        for path in sorted((ROOT / "web-react" / "src").rglob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            for at in (m.end() for m in re.finditer(r"onCancel=", text)):
                handlers.append((path.name, _braced(text, at)))
        pages = [(name, h) for name, h in handlers if name != "ComponentGallery.tsx"]
        assert len(pages) >= 22
        for name, handler in pages:
            assert re.search(r"\w+\.reset\(\)", handler), (name, handler)
            assert re.search(r"bridgeApi\.cancel\(|\w+\.cancel\(\)", handler), (name, handler)
            assert re.search(r"toast\.(info|warning)\(", handler), (name, handler)

    def test_the_hooks_own_cancel_leaves_the_running_state_too(self):
        hook = _source("web-react", "src", "bridge", "useOperation.ts")
        cancel = hook[hook.index("const cancel = useCallback"):]
        cancel = cancel[:cancel.index("}, [")]
        assert "bridgeApi.cancel(toolKey)" in cancel and "setStatus('idle')" in cancel


# ── The loaded file ─────────────────────────────────────────────────────

class TestTheLoadedFile:
    def test_the_drop_box_shows_the_file_once_one_is_loaded(self):
        page = _source("web-react", "src", "pages", "tools", "RedactPage.tsx")
        zone = page[page.index("<DropZone"):]
        zone = zone[:zone.index("/>")]
        assert "compact={files.length > 0}" in zone and "multiple={false}" in zone

    def test_a_drop_box_for_one_file_offers_to_pick_another(self):
        """Compact, with a file in it: "<name> — click or drop to pick
        another" for a box that takes one file, "... to add more" for one
        that takes several (as before)."""
        zone = _source("web-react", "src", "components", "shared", "DropZone.tsx")
        compact = zone[zone.index("{compact && files.length > 0 ?"):]
        compact = compact[:compact.index("</div>")]
        assert "{summary} — click or drop to {multiple ? 'add more' : 'pick another'}" in compact
        assert "pick another" in _bundle()
