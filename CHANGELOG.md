# Changelog
## Unreleased

### Added
- **Image metadata privacy analyzer (backend).** `pdf_analyze.py` can now scan
  standalone JPEG/PNG image files for privacy-sensitive metadata, reusing the
  same `Finding`/`AnalysisResult` machinery as the PDF analyzer so the report
  shape (`to_dict()`) is identical. New `analyze_image(path)` validates the
  file's magic bytes (JPEG `FF D8 FF` / PNG signature, `ValueError` otherwise)
  and runs EXIF scanners over Pillow's IFDs: GPS coordinates (HIGH, decoded
  DMS→decimal), camera make/model/capture-time/software (MEDIUM), an embedded
  thumbnail (MEDIUM), and author/copyright metadata (LOW). A new
  `analyze_file(path, password=None)` dispatcher sniffs the magic bytes and
  routes to `analyze_document` (PDF) or `analyze_image` (JPEG/PNG). Backend +
  tests only — not yet wired into the bridge/UI (Phase 2). No new dependencies
  (Pillow was already required). Added `tests/test_pdf_analyze.py` coverage
  (`TestAnalyzeImage`, `TestAnalyzeFileDispatch`, `TestImageHelpers`).
- **Analyze tool now accepts JPEG/PNG images and reports their privacy
  metadata.** The `analyzeDocument` bridge slot now dispatches through
  `analyze_file` (Phase 1), so dropping/browsing an image into **Analyze**
  routes it to the image scanner while PDFs behave exactly as before. The
  `AnalyzePage` drop zone accepts `.pdf .jpg .jpeg .png` ("Drop a PDF or image
  to analyze"), the risk header omits the page-count segment when a file has
  no pages (images report `pages: 0`), and the Sanitize / clean-copy action is
  hidden for images (PDF-only until image stripping lands in Phase 3). Report
  rendering is format-agnostic, so image findings display through the existing
  risk header / finding rows. Frontend rebuilt (`web-react/dist/`).
- **PDF analyzer now scans embedded images for EXIF/GPS metadata.** A phone
  photo dropped into a PDF keeps its original EXIF — including GPS coordinates —
  a common, easily-missed privacy leak the analyzer previously walked past. New
  `_scan_embedded_image_metadata` (wired into `analyze_document`'s scanner list)
  walks each page's image XObjects (recursing into form XObjects, deduplicating
  by object identity so an image reused on many pages is reported once), and for
  JPEG (`/DCTDecode`/`/JPXDecode`) images recovers the **original** stream bytes
  via `read_raw_bytes()` — which preserves EXIF, unlike a re-encoded pixmap —
  then reuses the Phase-1 EXIF parsing. Surfaces two findings: "Location data in
  an embedded image" (HIGH, decoded coordinates + page/image location) and
  "Camera metadata in an embedded image" (MEDIUM, device/capture-time/software).
  Non-JPEG images (Flate/CCITT) don't carry EXIF and are skipped. The
  EXIF-survives-embedding round-trip was verified empirically end-to-end. No new
  dependencies. Added `tests/test_pdf_analyze.py::TestEmbeddedImageExif`.
- **Analyze tool can now strip metadata from images (detect → remove).** The
  earlier phases let the Analyze tool *detect* image privacy metadata; this
  completes it with a *remove* action mirroring `sanitize_pdf`. New backend
  `strip_image_metadata(input, output)` first detects what sensitive metadata is
  present (reusing the Phase-1 scanners, so the reported `removed` categories —
  `gps`/`camera`/`thumbnail`/`authorship` — match what the analyzer flags), then
  writes a metadata-free copy of the **same format** atomically (temp file +
  `os.replace`): JPEGs are re-encoded **losslessly** (`quality="keep"` preserves
  the original DCT coefficients, so no recompression artifacts) with every EXIF
  block dropped; PNGs are rebuilt from a copy with a cleared info dict. Re-scanning
  the output yields no findings (verified empirically). A new
  `strip_file(input, output, options)` dispatcher routes PDFs to `sanitize_pdf`
  (honouring the options) and images to `strip_image_metadata`, mirroring
  `analyze_file`. The `sanitizeDocument` bridge slot now goes through `strip_file`
  (PDF behaviour unchanged); the Analyze page re-enables the clean/strip action
  for images (hidden in Phase 2), names the clean copy with the image's own
  extension (`photo_clean.jpg`, not `_clean.pdf`) with a matching save-dialog
  filter, and shows image-appropriate copy in place of the PDF sanitize
  checkboxes. Frontend rebuilt (`web-react/dist/`). Added
  `tests/test_pdf_analyze.py::TestStripImageMetadata` and `TestStripFileDispatch`.

### Security
- **Redaction of scanned (image-only) PDFs now works instead of blanking the page
  (RED-01).** `redact_pdf` applied redactions with `PDF_REDACT_IMAGE_REMOVE`,
  which deletes any image a redaction rect touches. On a scanned page — whose
  whole content is one full-page image — a small box therefore removed the entire
  image and blanked the page, while still reporting `redaction_count` success
  (the sensitive content silently discarded rather than precisely masked). It now
  uses `PDF_REDACT_IMAGE_PIXELS`, which blacks out only the pixels under each
  rect and preserves the rest of the scan. Added an image-only regression test to
  `tests/test_pdf_ops.py::TestRedactPdf`.
- **Redact no longer advances the workspace with a round-tripped path (FE-03).**
  `RedactPage`'s op-done handler fed `workspace.applyResult` the `output_path`
  read back out of the backend result. It now advances the workspace with the
  known-good path the frontend itself computed via `workspaceOutputPath()`
  (stashed in a ref when the run launches), guarded on truthiness; the toast's
  match/page counts still come from the backend result, and the non-workspace
  file-output branch is unchanged. Frontend-only; `dist/` rebuilt.
- **Workspace file ops are now scoped to the workspace temp dir (BRG-02).** The
  bridge's `deleteFile`/`copyFile` slots deleted/read whatever path they were
  handed with no containment check. They now use a new Qt-free
  `is_within_directory()` guard (`pdf_ops.py`, realpath + `commonpath` — not
  `startswith`, so a sibling like `…/ws_evil` isn't treated as inside `…/ws`):
  `deleteFile` refuses any path outside `self._workspace_dir`, and `copyFile`
  requires its **source** to be inside it (the export **destination** stays
  unconstrained by design). Defense-in-depth — no known exploit path. Added
  `tests/test_pdf_ops.py::TestIsWithinDirectory`.
- **Sanitiser now neutralises JavaScript/Launch/Submit hidden in `/Next` action
  chains (ANL-02).** `sanitize_pdf` judged each annotation only by its top-level
  `/A` `/S`, so an annotation with a benign `/URI` head (kept when
  `external_links=False`) but a `/Next` running JavaScript survived intact even
  with `javascript=True` — the analyzer flagged it while sanitise reported
  success. The annotation loop now walks each `/A` (and each `/AA` trigger
  entry) `/Next` chain the way the analyzer's `_collect_actions` does, excising
  any `/JavaScript`/`/Launch`/`/SubmitForm`/`/ImportData` (or inline `/JS`) node
  under its matching opt while preserving the benign head and any surviving
  tail; removals bump the existing counters so the count stays honest. Added
  `tests/test_pdf_analyze.py`. Flips ANL-02 to Fixed in `AUDIT.md`.
- **Sanitiser now removes annotation-borne embedded files (ANL-04).** With
  `embedded_files=True`, `sanitize_pdf` only deleted the `/EmbeddedFiles` name-tree
  entry, leaving `/FileAttachment` annotations (which carry a stream via
  `/FS`→`/EF`) and `/AF` associated-file arrays intact — a surviving annotation
  kept the stream referenced, so the payload was never GC'd. The annotation loop
  now drops `/Subtype == /FileAttachment` annotations and strips `/AF` arrays at
  the root, on pages, and on annotations (new `file_attachment_annot` /
  `associated_file` counters), so no `/EF` stream survives. Detection
  (`_scan_embedded_files`) also now treats any dict with `/EF` as a filespec even
  when `/Type` is omitted, and walks the name tree through `/Kids`. Flips ANL-04
  to Fixed.

### Fixes
- **`images_to_pdf` now actually preserves quality for lossless sources
  (OPS-05).** Despite the docstring's claim, every input -- including
  lossless PNG/TIFF -- was unconditionally re-encoded to lossy JPEG q92, with
  no lossless path at all. The source format is now captured right after
  `Image.open` (before EXIF-orientation/transparency-compositing strip it); a
  JPEG source is still re-encoded (already lossy), but anything else is
  embedded losslessly via `zlib`-compressed raw pixel bytes under
  `/FlateDecode`. Verified empirically: a PNG source now round-trips
  pixel-for-pixel identical; pre-fix, the same source came out visibly
  altered. Added `tests/test_pdf_ops.py::TestImagesToPdf`.
- **Split no longer silently overwrites outputs with the same generated name
  (OPS-04).** Each group's output name came from `name_template.format(...)`
  with no collision check, so two groups formatting to the same name
  overwrote each other -- realistic in chapters mode, where the default
  template has no `{n}` and a repeated TOC title (Introduction, Summary,
  References…) collided, clobbering an earlier chapter's file while still
  reporting success. A `seen_counts` map now tracks each generated name within
  the run; the first occurrence is unchanged, later ones get `_2`, `_3`, …
  inserted before the extension, so every group survives as a distinct file.
  Verified empirically: 3 chapters titled "Introduction" previously produced
  1 file on disk (2 chapters silently discarded); now produce 3, each with the
  correct page count. Added
  `tests/test_pdf_ops.py::TestSplitChapters::test_repeated_titles_disambiguate_instead_of_overwriting`.
- **`add_watermark` no longer leaks an open file handle on malformed input
  (OPS-03).** A malformed `page_range` or `color` raised with `src` (the open
  `pikepdf.Pdf`) never closed -- both validations ran after `pikepdf.open`,
  before the save's own error handling. On Windows this can leave the input
  locked until GC eventually reclaims it. Color validation now runs before
  opening the PDF at all (no PDF needs to be open to parse a hex string);
  `page_range` validation (which needs the page count from the opened PDF) is
  now wrapped in a try/except that closes `src` before re-raising. Verified
  on Windows, reproducing the actual `PermissionError: [WinError 32]` against
  the pre-fix code. Added `tests/test_pdf_ops.py::TestAddWatermark`.
- **`protect_pdf` no longer lets the user password double as the owner
  password (OPS-01).** Permission flags (print/copy/edit/annotate) are only
  enforceable against someone without the owner password; without a distinct
  one, `owner == user`, so anyone who could open the file also held owner
  rights and could strip every restriction against a compliant reader. Now
  generates a random owner password via `secrets.token_urlsafe(24)` whenever
  the caller doesn't supply one -- never surfaced to the caller, and it only
  gates permission bits, never the ability to open the file. Verified via
  pikepdf's `owner_password_matched` flag before/after. Added
  `tests/test_pdf_ops.py::TestProtectPdf` and `TestUnlockPdf` (there was no
  protect/unlock test at all before -- also flips `TST-03` to Fixed).
- **Each compression preset's own tiny-image threshold now actually applies
  (ENG-06).** `_should_skip` skipped recompressing an image when
  `info.is_tiny or max(w,h) < preset.skip_below_px` -- but `is_tiny` was
  hardcoded to `<64px`, and every preset's own threshold (64/48/32/24/16 for
  screen/ebook/standard/high/prepress) is `≤ 64`, so the hardcoded clause
  always subsumed the preset's finer threshold. Every preset behaved
  identically (a flat 64px floor) for any image below 64px -- e.g. a 40px
  image was skipped by `standard`, `high`, and `prepress` even though their
  own thresholds (32/24/16) say it should be processed. Removed the hardcoded
  `is_tiny` clause (and the now-unused `ImageInfo.is_tiny` property); each
  preset's `skip_below_px` is now the sole floor. `estimate_output` picks up
  the fix automatically (calls `_should_skip` directly). Verified empirically
  before/after. Added `tests/test_engine.py::TestShouldSkipTiny`.
- **CLI now exits nonzero when any input fails (CLI-01).** `compress_pdf.py`'s
  `main()` tallied failures (invalid magic, encrypted, invalid, too-large,
  catch-all) in `n_err` but never called `sys.exit()` on that count, so the
  process always exited 0 regardless — any chained/scripted use (the
  documented `... -o out/ && next_step` batch pattern) treated an all-failed
  run as success. Now calls `sys.exit(1 if n_err else 0)` after the summary.
  An existing test had asserted `returncode == 0` on a failed run, codifying
  the bug; fixed to assert `returncode == 1`, plus added
  `test_success_exits_zero` and `test_mixed_batch_with_one_failure_exits_nonzero`.
  Note: `CLI-04` (not-found inputs never increment `n_err`) is a separate,
  still-open finding — a batch of only missing files still exits 0.
- **Flatten with "remove form fields" but not "remove annotations" now
  actually removes the form fields (OPS-02).** `flatten_pdf`'s `/Annots`
  handling was entirely nested inside `if annotations`, so the
  independently-reachable `annotations=False, forms=True` toggle combo (a
  real `FlattenPage.tsx` checkbox state) deleted `/AcroForm` but left every
  `/Widget` annotation's value and appearance stream fully intact and
  extractable — the operation reported success while doing nothing to the
  form data. Each annotation is now evaluated against both flags
  independently: a `/Widget` is dropped whenever `forms=True`, any other
  annotation is dropped whenever `annotations=True`, regardless of the other
  flag — so `forms=True` always removes form fields even when annotations are
  being kept. Verified for all four flag combinations, including reproducing
  the original bug against the pre-fix code. Added
  `tests/test_pdf_ops.py::TestFlatten` (one test per combination).
- **Ghostscript pass no longer deadlocks on large stderr output (ENG-04).**
  `compress_with_ghostscript`'s poll loop only called `proc.wait(timeout=2.0)`
  and never drained the piped stdout/stderr; if `gs` wrote more than the OS
  pipe buffer (~64 KB — font-substitution/recoverable-error diagnostics that
  `-dQUIET` doesn't suppress), it blocked on the full pipe while we blocked in
  `wait()`, stalling for the full 5-minute timeout before the GS pass was
  silently discarded. The loop now uses `proc.communicate(timeout=2.0)`
  instead, continuously draining both pipes while polling (safe to retry per
  the `subprocess` docs — no output lost); cancellation/timeout handling is
  unchanged. Reproduced the deadlock empirically (a child writing 500 KB to a
  piped stdout never returns from a `wait()`-only loop) and confirmed the fix
  resolves it in milliseconds. Added an optional `gs_timeout` parameter
  (default unchanged) so tests can verify this without waiting 5 minutes on a
  regression. Added `tests/test_engine.py::TestCompressWithGhostscript`.
- **Content-stream cleanup no longer corrupts text or inline images (ENG-03).**
  `_optimize_content_streams`' empty-`q`/`Q`-pair removal ran a raw-byte regex
  over the untokenized content stream, so a literal "q Q" inside a `Tj`/`TJ`
  string literal or an inline image's (`BI…ID…EI`) binary payload got deleted
  along with genuinely empty save/restore pairs — reproduced empirically: a
  string `(q Q inside a string)` was mangled to `( inside a string)`, and an
  inline image payload of literally `b"q Q"` was wiped to nothing. Content
  streams are now parsed with pikepdf's own tokenizer
  (`pikepdf.parse_content_stream`), which represents strings, hex strings, and
  inline images as opaque units, and empty-pair removal operates purely at the
  instruction level before re-serializing with `pikepdf.unparse_content_stream`
  — string/image bytes are never exposed to a byte-level pass at all. Also now
  correctly coalesces `/Contents` arrays (previously naively joined with
  `read_raw_bytes()` + `b"\n"`, ignoring per-part filters). Added
  `tests/test_engine.py::TestOptimizeContentStreams`.
- **Transparency is no longer destroyed when a soft mask can't be decoded
  (ENG-02).** Every image re-encode branch deleted `/SMask` whenever the
  original had one, gated only on the mask *existing* — not on compositing
  against it having actually succeeded. `_load_smask_image` decoded masks with
  a raw-byte-length heuristic that (like ENG-01) failed on standard
  FlateDecode/CCITT/LZW soft masks, so compositing silently skipped while
  `/SMask` was deleted anyway — baking previously-transparent regions into an
  opaque rectangle on re-encode (the common JPEG-base + FlateDecode-mask case).
  The mask now decodes via `pikepdf.PdfImage(...).as_pil_image()` (same fix as
  ENG-01), and all four deletion sites now gate on the composite having
  actually happened (`mask_img is not None`) rather than the mask merely being
  present — an undecodable mask now survives untouched (still valid against
  the re-encoded base image regardless of dimension changes) instead of being
  discarded. Added
  `tests/test_engine.py::TestCompressImagesSmartSoftMask` (decodable mask
  composites and is removed; undecodable mask is preserved; both fail
  pre-fix).
- **Compression now recompresses non-JPEG images instead of silently skipping
  them (ENG-01, ENG-05).** `compress_images_smart` decoded every image via
  `Image.open()` on the still-filter-encoded `read_raw_bytes()` output, which
  only self-describing formats like JPEG survive — Flate/CCITT/LZW/indexed
  images (diagrams, screenshots, scans) raised `UnidentifiedImageError`,
  silently swallowed, so they were never recompressed and the advertised
  1-bit/Flate-diagram encoding paths were dead code. Images now decode via
  `pikepdf.PdfImage(xobj).as_pil_image()` (falling back to the old `Image.open`
  path for anything it can't handle), so those branches are reachable for the
  first time. That exposed a second, coupled defect (ENG-05): the size-benefit
  check for both branches compared **uncompressed** candidate bytes (packed
  1-bit / raw RGB) against `info.raw_size`, the **already-compressed** original
  stream length — and then wrote those uncompressed bytes to the XObject
  tagged with a `/FlateDecode` filter it never actually applied, which
  `pdf.save()` does not fix up and produces an unreadable image. Both branches
  now `zlib.compress()` the candidate first and compare/write that. Added
  `tests/test_engine.py::TestCompressImagesSmartNonJpeg` (Flate-diagram and
  1-bit fixtures; both fail against the old decode path and pass now, with a
  round-trip decode assertion on the saved output). Flips ENG-01 and ENG-05 to
  Fixed in `AUDIT.md`.
- **Preview now updates after a merge (FE-04, issue #58).** Merging produced a
  correct combined PDF, but the in-app preview (the WorkspaceBar preview — the
  app's only preview surface, keyed on the workspace document) kept showing the
  pre-merge state, because MergePage — a multi-input tool — never repointed the
  workspace at its output. The op-done handler now calls `workspace.load(output)`
  with the merged file, so the preview follows the result (and re-scans it via the
  FE-02 machinery). It uses `load` rather than `applyResult` because merge writes
  to a user-chosen path that must never be auto-deleted (`applyResult` would mark
  it workspace-owned and a later Clear would delete the user's file); `load`
  adopts it as a fresh, unowned working document. Merge's output logic is
  unchanged. Frontend-only; `dist/` rebuilt.
- **Workspace risk badge now refreshes after every transform (FE-02).** The
  background security scan ran only in `WorkspaceContext`'s `load`, so after any
  transform (compress, flatten, redact, watermark…) `applyResult` repointed the
  working document to the tool's output but never re-scanned — the WorkspaceBar
  kept showing the originally-loaded document's findings, and AnalyzePage (which
  does re-scan) disagreed with the bar. The scan logic is now factored into a
  `startScan(path)` helper (keeping the `scanPathRef` newest-scan-wins guard) that
  both `load` and `applyResult` call, so the badge always reflects the current
  document — e.g. a Flatten that strips JS now lowers/clears the badge instead of
  showing stale warnings. Frontend-only; `dist/` rebuilt.
- **OCR fallback no longer leaks a temp PNG when the page rasterize/save fails
  (TRN-02).** In `_extract_pages`, `pix.save(tmp)` ran before the try/finally that
  unlinks the mkstemp'd file, so if the save raised (disk full, PyMuPDF error) the
  temp `.png` was orphaned — accumulating on large scanned docs where saves
  repeatedly fail. The save is now inside the try, so cleanup always runs. Added
  `tests/test_pdf_translate.py::TestExtractPagesTempCleanup`.
- **Offline text/image translation no longer hangs the UI on a malformed request
  (BRG-03).** `startTranslateText` / `startTranslateImage` read their required
  params (`text`/`path`, `target`) on the UI thread before dispatching the
  worker, so a missing key raised `KeyError` in the slot — no worker started, no
  `operationDone` was emitted, and the frontend spinner (which has no timeout)
  hung forever. Those reads now happen inside the worker's `_work` closure
  (mirroring `startMerge`), so a missing key surfaces as
  `operationDone(success=False, …)` and the UI resolves with an error.
- **A cancelled-then-rerun operation is cancellable again (BRG-01).** The bridge's
  worker-cleanup handler removed tracking entries by `tool_key` string, not by the
  identity of the worker that finished. Because `_make_cancel_event` overlaps
  same-key runs, a cancel-then-immediate-rerun let the *old* worker's finish
  handler evict the *new* run's worker and cancel event, so `cancelOperation`
  no-oped and the in-flight rerun could never be cancelled. `_on_finished` now
  removes each entry only when it's still this run's object (identity check), so
  a stale finish leaves the newer run intact. (Bridge/Qt path — not unit-tested,
  as the test suite is kept Qt-free; verified by inspection + real-app
  cancel→rerun.)
- **File drag-drop is now scoped to the active tool page (FE-01).** Under the
  AppShell keep-alive (every visited page stays mounted), each mounted `DropZone`
  subscribed to the global `files-dropped` EventBus, so a single OS file-drop was
  appended to *every* visited page's file list — staging files on pages the user
  never intended. `DropZone` now gates that subscription on `usePageActive()`
  (mirroring `useHotkeys`), so only the visible page receives the drop. Frontend
  change; `dist/` rebuilt.
- **PDF→PDF translation no longer aborts the whole document over one undetectable
  block (TRN-01).** In `_translate_pdf_to_pdf`, a block that failed
  `detect_language` under `source='auto'` (a page number, a year like `2024`, or a
  <3-char fragment) raised `TranslationError` out of the loop, so `out.save()` was
  never reached and no output was produced. Now, once the document language is
  detected from an earlier block it's reused for later blocks, and the per-block
  `translate_text` call is wrapped so an undetectable block is copied through
  verbatim (logged at debug) instead of aborting — the document always reaches
  `save()`. Added `tests/test_pdf_translate.py::TestTranslatePdfBlockResilience`.
  Flips TRN-01 to Fixed.
- **Translation from non-Latin/Cyrillic scripts no longer returns the source text
  unchanged (TRN-03).** `translate_line` gated on `_LETTER_RE =
  re.compile(r'[A-Za-zÀ-ɏЀ-ӿ]')` (Latin + Cyrillic only), so any fragment with no
  Latin/Cyrillic letter was treated as skippable punctuation and passed through
  verbatim — silently returning the original for Chinese, Arabic, Hindi, and
  Bengali (4 of 12 advertised source languages) while reporting success. The gate
  is now the Unicode-aware `if not any(ch.isalpha() for ch in residual)`, so real
  words in every script are translated while pure digits/punctuation/separators
  stay skipped. Added `tests/test_pdf_translate.py`. Flips TRN-03 to Fixed.
- **Invisible-text (fake-redaction) detector rebuilt (ANL-03).** The render-mode-3
  scan had a dead `rawdict` span loop and only inspected the *first* content
  stream with a naïve `b" 3 Tr"` substring test — missing `3 Tr` in later streams
  (common after incremental edits), `\n3 Tr`/other whitespace, and text in form
  XObjects. It now concatenates every `page.get_contents()` stream plus referenced
  form-XObject streams and matches the operator on token boundaries
  (`(?:^|[\s])3\s+Tr\b`), so `"13 Tr"` no longer false-positives. Flips ANL-03 to
  Fixed.
- **In-place sanitise no longer fails on Windows (ANL-01).** `sanitize_pdf`'s
  atomic write ran inside the `with pikepdf.open(input_path)` block, so
  `os.replace` over `input_path` (in-place, via Save-As) hit a still-open handle →
  `PermissionError [WinError 5]`. The input is now opened with
  `allow_overwriting_input=True` (reads it into memory, releasing the handle);
  the atomic write and its failure cleanup are unchanged. Flips ANL-01 to Fixed.

### Tests
- **Redaction now has test coverage (TST-01).** Added
  `tests/test_pdf_ops.py::TestRedactPdf` (fitz-gated; production `redact_pdf`
  unchanged) asserting redacted terms are gone from both `get_text()` and the
  decoded content-stream bytes (catches a regression to painting-over),
  `redaction_count`/`pages_affected` are correct, the `case_sensitive`
  re-extraction filter keeps the other case, an AcroForm text widget's `/V` value
  is removed and non-extractable, and `ValueError` is raised with neither
  `search_terms` nor `rects`. Flips TST-01 to Fixed.
- **Path-containment guard now tested (TST-02).** Added
  `tests/test_pdf_ops.py::TestContainedOutputPath` covering the security-critical
  negative path of `contained_output_path()` — `../` traversal and absolute
  `out_name` both raise `ValueError`, while a plain name and a non-escaping
  subfolder stay contained. Flips TST-02 to Fixed.
- **Backup-on-overwrite test no longer verifies nothing on skip (TST-04).**
  `tests/test_engine.py::test_compress_backup_on_overwrite` wrapped its only
  assertions in `if not result.skipped:`, so a compression that skipped (no size
  gain) ran no assertions and passed vacuously. Removed the guard — the test now
  asserts `backup_path is not None` and the file exists unconditionally, and also
  checks the backup is a real copy of the pre-compress original (same size). Safe
  because the engine creates the backup unconditionally before the skip decision;
  the un-guarded assertions pass. Production code unchanged. Flips TST-04 to Fixed.

### Docs
- **Added `AUDIT.md`** — a point-in-time code-audit snapshot of v4.22 (~40 open
  findings across the engine, PDF ops, crypto, analyze, translate, bridge, CLI,
  frontend, tests, docs, and packaging), each adversarially verified against the
  source, with severity, evidence, and a suggested fix. Nothing is fixed yet — it's
  the shared "where we stand" reference. Linked from `README.md` (Development
  section + Files table) and folded into `CLAUDE.md`'s doc-currency rules.
- **`CLAUDE.md` / `README.md`** — contributors now consult the relevant `AUDIT.md`
  findings before touching a subsystem and flip a finding's **Status** when they
  fix it (recording the fix in this changelog too).

## v4.22

A new **multi-tool workspace**, a frontend parity pass (the React app reached
full parity with the old vanilla-JS frontend, now retired), and a Linux CI fix.

### Multi-tool workspace (new)
- **Load a document once and carry it across tools (running-result model).** A
  persistent workspace bar holds one working document. Transforming tools
  (Watermark, Compress, Page Numbers, Rotate, Crop, Flatten, Redact, Protect,
  Metadata, N-up, Repair, Unlock) advance it in place; terminal tools (Split,
  PDF-to-Images, PDF-to-Word, Extract Text/Images) produce side-outputs without
  changing it; Analyze reads it read-only. Includes an enlargeable full-screen
  preview with keyboard paging, and an automatic scan-on-load risk audit that
  warns on load (framed as detection, not a safety guarantee). New bridge slots
  `getWorkspaceDir` / `deleteFile` / `copyFile` back it with a per-process temp
  working directory, cleaning up superseded working files as the workspace
  advances.

### Fixes
- **Batch compression no longer overwrites a single output path.**
  `startCompress` applied one explicit `outputPath` to every file in a batch,
  so all files wrote to the same path. It now builds a distinct per-file path
  from an optional `outputDir` + `naming` template (`compress_output_path` in
  `compress_paths.py`), applies an explicit `outputPath` only to single-file
  calls, and otherwise defaults to `<name>_compressed.pdf` beside the source.
  Added `tests/test_bridge.py`.
- **Linux CI no longer fails at test collection.** `tests/test_bridge.py`
  imported the compress path helper from `ui.bridge`, which pulls in the PySide6
  GUI stack (`ui/__init__` → `web_shell` → Qt). On the headless Ubuntu runner
  those libraries can't load (`libEGL.so.1` missing), so pytest errored the whole
  test job at collection — on both Python 3.10 and 3.12 — before any test ran.
  (Latent since the test was introduced with the batch-compression fix above;
  the workspace merge surfaced it, but did not cause it — the Linux matrix had
  been red on `main` for a while.) The pure helper now lives in the Qt-free
  `compress_paths.py`, so the suite imports no Qt at all, and the PyInstaller
  spec's `hiddenimports` gains `compress_paths`. As defense-in-depth the CI
  workflow also installs Qt's runtime libs (`libegl1`, `libgl1`, `libxkbcommon0`,
  `libdbus-1-3`) and sets `QT_QPA_PLATFORM=offscreen` on Linux, so future
  integration tests that construct the Bridge or a `QApplication` can run
  headless. The README gains a **Development** section documenting the
  test/lint/CI workflow and the Qt-free-helper convention.

### React frontend parity
- **Merge** shows each file's page count as it's added.
- **Per-page keyboard shortcuts** restored (Ctrl+O add files, Ctrl+Enter run,
  Esc clear) on Compress/Merge/Split/Watermark, via a shared `useHotkeys` hook
  scoped to the active page.
- **Router keep-alive** — visited pages stay mounted, so a half-filled form
  survives navigating away and back (matches the old router's cached pages).
- **Compress rich file cards** — page-1 thumbnail, live analysis chips (size,
  pages, images, DPI with a downscale warning), and per-preset estimated
  savings that update with the selected preset.

### Removed
- **Deleted the legacy vanilla-JS frontend (`web/`).** The React app
  (`web-react/`) is the only frontend now. `ui/web_shell.py` loads
  `web-react/dist/` directly and the `PDF_TOOLKIT_UI=legacy` override is gone;
  the PyInstaller spec bundles `web-react/dist/` in its place.

---
## v4.21

Maintenance pass — a code-health cleanup: dead-code removal, a real bug fix, and test/security hygiene. No user-facing feature changes.

### Fixes
- **Compression to a distinct output path now always produces a file.** When compression yielded no size gain, the skip branch returned early without writing the requested `output_path` — so `compress a.pdf -o out.pdf` could silently produce nothing when the file didn't shrink. It now writes a verbatim copy of the original to the requested path (in-place overwrites are unaffected). Added a regression test that exercises the skip branch.
- **Fixed a stale crypto test** that hard-coded `.epdf` format `version == 1`; it now asserts against the current `EPDF_VERSION` constant so it won't rot on the next format bump.

### Security & robustness
- **`.epdf` KDF-parameter clamping (DoS hardening)** — decryption previously fed the Argon2 cost parameters (`memory_cost`, `time_cost`, `parallelism`) straight from the file header into key derivation. A hostile `.epdf` could set `memory_cost` to, say, 64 GiB and OOM or hang the machine *before* any authentication ran. Key derivation now validates these against hard bounds (`MIN_KDF_PARAMS`/`MAX_KDF_PARAMS`) at a single chokepoint (`_validate_kdf_params` in `_derive_key`, so both encrypt and decrypt are guarded) and refuses out-of-range or non-integer values with a clear error. Ceilings sit far above the defaults, so legitimate high-security files still work. Added unit + integration tests (including a tampered-header file that must be refused).
- **Removed `_secure_delete_string()`** from `engine.py`. It cast `id(str)` to a raw pointer and zeroed a *guessed* memory range — which could corrupt interned strings (crash/data corruption) and over-write past the buffer, all while not reliably wiping anything (CPython strings are immutable). Net-negative "security theater" replaced by simply not doing it. Dropped the now-unused `ctypes` import.

### Tooling / CI
- **Added GitHub Actions CI** (`.github/workflows/ci.yml`): a `ruff` lint job plus a test matrix across Ubuntu + Windows on Python 3.10 and 3.12, running `pytest` with coverage. (The green-vs-broken state that let the stale crypto test above slip through is now caught automatically.)
- **Added `ruff`** with a pragmatic config in `pyproject.toml` — real-bug and hygiene rules (`E`, `F`, `W`, `B`, `I`) on, the codebase's deliberate compact one-liner style left alone (`E701`/`E702`/`E741` ignored). Added `ruff` to `requirements-dev.txt` and the `dev` optional-dependencies group.
- **Cleared all lint findings**: removed unused imports and dead local assignments (`ns`, `output_size`, `para`, `bpc`, `doc_text`, `raw`, unused `docx` imports), sorted imports, added `from None`/`from err` to re-raises inside `except` blocks (B904), and simplified a pointless single-iteration loop in the invisible-text scan. No behavior changes.
- Fixed the stale `version`/tool-count in `pyproject.toml` (`4.0.1` → `4.21`, "20" → "22 professional tools").
- `.gitignore`: ignore coverage (`.coverage`, `htmlcov/`) and `.ruff_cache/` artifacts.

### Removed (dead code)
- **Deleted the unreachable native-Qt widget UI** — ~9,500 lines across 32 files: `ui/shell.py`, `ui/sidebar.py`, `ui/dialogs.py`, `ui/icons.py`, `ui/widgets.py`, `ui/widgets_generic.py`, `ui/batch_helpers.py`, `ui/signals.py`, and all of `ui/pages/`. Since the React migration, `ui/__init__.py` exposes only `WebMainWindow`; none of these were reachable from the entry point. (This also removed the two remaining bare `except:` clauses.)
- **Decoupled `ui/tool_registry.py`** from the native UI: it previously imported every native page module (via `get_tools()` → `_tools()`) to build `page_factory` lambdas the web bridge never used. It now holds pure tool metadata with no UI-framework dependencies. Public API (`ToolDef`, `CATEGORIES`, `get_tools`, `get_tool`) and all 22 tool definitions are unchanged.
- Updated the README file map to drop the removed `ui/pages/` entry.

---
## v4.20

Major release: the entire frontend rebuilt in React, plus new tools, a redaction overhaul, and a security-hardening pass.

### React frontend (complete rewrite)
- The whole web UI is migrated from vanilla JS to **React + Vite + TypeScript** — all 22 tools, the sidebar/router shell, and shared components rebuilt as typed React components.
- **Security-console aesthetic** with full **light and dark themes** (WCAG-AA contrast), toggle persisted across restarts.
- The built bundle is committed to `web-react/dist/`, so the app runs with no Node build step. Developers modifying the frontend need Node + `npm install` + `npm run build` in `web-react/`.
- The Python bridge is unchanged; async operations (progress/cancel/done) are centralized in a single `useOperation` hook.

### New tools
- **Analyze Document** — offline privacy/security audit (`pdf_analyze.py`): flags embedded JavaScript, auto-run/launch actions, external trackers, embedded files, hidden layers, invisible text, and identifying metadata, with a one-click sanitizer. Auto-run findings now describe *when* each trigger fires and *what* it does.
- **Translate** — offline translation (`pdf_translate.py`) of PDF text and text inside images/photos/scans (Argos + Tesseract OCR), 12 languages. Includes proper-noun/separator protection (prevents place-name and separator corruption), a user "keep these words untranslated" field, and **image-preserving PDF output** using a bundled DejaVu font.

### Redaction (rewritten — true content destruction)
- Replaced the old content-stream regex approach (which silently missed most text) with **PyMuPDF `apply_redactions()`** — text and images under a redaction are permanently destroyed and unrecoverable.
- Added a **visual box-drawing mode**: render each page, draw boxes over content, true-redact those regions.
- **Fixed a critical data leak**: form-field (AcroForm) values survived redaction — a black box was drawn but the underlying value (e.g. an SSN on a fillable form) remained fully extractable. Overlapping form fields are now neutralized before redaction.
- Fixed box coordinate mapping (display-vs-natural image scaling) and removed an incorrect Y-axis inversion that sent boxes to the backend flipped.

### Watermark
- Added a **tiled/diagonal mode** — the watermark repeats across the whole page (staggered, translucent) so it can't be trivially cropped out. Single mode retained.

### Security & hardening
- **Network kill-switch** — `QWebEngineUrlRequestInterceptor` blocks every non-local request; CSP forbids all network egress. The app provably cannot phone home.
- **`.epdf` header authentication (v2)** — cipher/KDF/salt/nonce bound as Associated Data; tampering and downgrade attacks are detected on decrypt. v1 files still decrypt.
- **Output-path containment** — user-supplied names/templates can no longer escape the chosen folder (blocks `../` traversal and absolute-path override).
- **`openFile`/`openFolder` validation** — only real local paths are opened; URLs and protocol handlers are refused.
- Upgraded **stanza** to resolve **CVE-2026-54499** (critical RCE via unsafe pickle deserialization in model loading).

### Fixes
- **Translate page-load freeze** — a synchronous `argostranslate` import on tool open froze the whole window ~5s; provisioning status now runs off the UI thread with a loading state.
- **Progress/results not updating** — async tools read `data.tool`/`data.percent` while the bridge sent `toolKey`/`pct`/nested `results`; corrected across all tools.
- **Watermark opacity** — was sent on a 1–100 scale where the engine expects 0–1 (watermarks rendered fully opaque).
- **`getPresets`** — called with zero args where the slot requires one (preset dropdowns fell back silently).
- **Sidebar collapse persistence** and **`loadSetting` double-encoding** — settings now restore correctly.

### Notes
- No change for end users beyond installing dependencies — pull and run.
- New files: the `web-react/` React project (source + committed `dist/`), `assets/fonts/DejaVuSans.ttf`. The legacy `web/` vanilla frontend is retained but no longer the active UI.

---
## v4.10

Privacy/security hardening and offline translation — four additions, all fully offline.

### Analyze Document (new tool)
- **`pdf_analyze.py`** — an offline privacy/security audit engine (pikepdf, with optional PyMuPDF). Detects identifying metadata (/Info + XMP), embedded JavaScript, auto-run actions (/OpenAction, /AA), launch actions, external URI links / trackers, remote GoTo, embedded files/attachments, form submit/import actions, XFA/AcroForm, optional-content layers, and invisible text (the classic failed-redaction tell). Returns risk-graded findings and an overall risk level.
- **One-click sanitizer** — `sanitize_pdf()` writes a cleaned copy with the selected categories stripped (JavaScript, launch/auto actions, embedded files, submit actions, and optionally external links and metadata). The original file is never modified; writes are atomic.
- Wired into the web UI as a new **Analyze Document** tool (shield icon, Repair & Analysis category): drop a PDF → risk report → optional sanitize. New bridge slots `analyzeDocument`, `getSanitizeDefaults`, `sanitizeDocument`.

### Network kill-switch (provable offline)
- **`ui/net_guard.py`** — a `QWebEngineUrlRequestInterceptor` installed on the web profile blocks every request whose scheme isn't local (`file`/`qrc`/`data`/`blob`/`about`); it fails closed. Any tracker, beacon, web font, or stray `fetch()` is dropped before a byte leaves the machine.
- **Content-Security-Policy** added to `index.html` (`connect-src 'none'`, `object-src 'none'`, `base-uri 'none'`, restrictive `default-src`) as defense-in-depth; kept permissive for bundled local assets so rendering is unaffected.
- Hardened web settings: clipboard access and window-opening disabled for the page's JavaScript.

### Encryption header authentication (.epdf v2)
- The `.epdf` header (cipher, KDF parameters, salt, nonce) is now bound as **Associated Data**: AEAD ciphers authenticate it directly, and the Camellia HMAC now covers `header + iv + ciphertext`. Tampering with — or downgrading — the header is detected on decrypt instead of being silently accepted.
- Format version bumped to **v2**; existing **v1** files still decrypt unchanged.

### Translate (new tool)
- **`pdf_translate.py`** — an offline translation/OCR engine. Translates PDF text and the text inside images/photos/scans, entirely on-device: Argos Translate (CTranslate2 models) for translation, Tesseract for OCR, and langdetect for source auto-detection. No network calls.
- **Languages** — the global top-10 by speakers (English, Mandarin Chinese, Hindi, Spanish, Arabic, French, Bengali, Portuguese, Russian, Indonesian) plus **German** and **Danish** (12 total). Argos pivots through English, so a handful of installed languages covers translation between any of them.
- **Image translation** — drop a photo or scan; the tool OCRs the text, detects the language, and translates it (source text + translation shown side by side, copyable). Scanned PDF pages with no text layer are OCR'd automatically.
- **PDF translation** — runs on a background thread with progress and cancel; writes a `.txt` (page-delimited) or `.docx`. Text/Word output is used deliberately so every script (CJK, Cyrillic, Arabic, Devanagari) renders with the system's own fonts, avoiding fragile font embedding.
- Wired into the web UI as a new **Translate** tool (globe icon, Convert category) with From/To language pickers and a provisioning-status banner. New bridge slots `getTranslationStatus`, `translateText`, `translateImage`, and async `startTranslatePdf`.

### Model provisioning
- **`setup_translation.py`** — the one explicit online step. `--status` / `--list` show what's installed; `--install all` (or specific codes) downloads the Argos language packages and prints the per-OS commands for the Tesseract OCR packs. The app's network kill-switch is unaffected — it sandboxes the embedded web UI, while provisioning is a separate, user-run tool. After setup, translation is fully offline.

### Notes
- Translation dependencies are **optional**; the rest of the toolkit works without them. The Translate tool degrades gracefully and tells the user exactly what to install if a model is missing.
- No new dependencies for the analyze/kill-switch/encryption additions (PyMuPDF was already optional; it enables the invisible-text check).
- New files: `pdf_analyze.py`, `pdf_translate.py`, `setup_translation.py`, `ui/net_guard.py`, `web/js/pages/analyze.js`, `web/js/pages/translate.js`; edits to `epdf_crypto.py`, `ui/web_shell.py`, `ui/bridge.py`, `ui/tool_registry.py`, `web/index.html`, `web/js/bridge.js`, `web/js/app.js`, `web/js/icons.js`, `requirements.txt`.

## v4.0.1

Code quality, testing, documentation, and build hardening pass.

### Test suite (new)
- **76 automated tests** across 4 test files covering engine, PDF operations, encryption, and CLI
- `tests/test_engine.py` — compression presets, path sanitization, PDF validation, backup, cancellation
- `tests/test_pdf_ops.py` — merge, split, page operations, metadata, text extraction, flatten, repair
- `tests/test_epdf_crypto.py` — encrypt/decrypt round-trips for all 3 ciphers, wrong-password handling, format detection
- `tests/test_cli.py` — help flag, default compression, invalid file handling
- Shared fixtures in `tests/conftest.py` generate sample PDFs, encrypted PDFs, and invalid files on-the-fly

### Build & distribution (new)
- **PyInstaller spec** (`pdf_toolkit.spec`) — builds standalone `PDFToolkit.exe` with bundled frontend assets
- **build.bat** — one-click Windows build script
- **pyproject.toml** — project metadata, dependency declarations, pytest config
- **requirements-dev.txt** — dev dependencies (pytest, pytest-cov)

### Fixes
- **Ghostscript timeout** — added 5-minute wall-clock timeout to prevent runaway Ghostscript processes
- **Import ordering** — moved `logging.handlers` and `hashlib` imports to the top of `engine.py` (were deferred/inline, fragile under edge-case import ordering)
- **Deprecated shell.py** — added deprecation notice to the unused widget-based shell (replaced by `web_shell.py` in v4.0.0)

### Documentation
- **README: Files table** — corrected to reference `web_shell.py` (active) instead of `shell.py` (deprecated); added `epdf_crypto.py`, `ui/bridge.py`, and `web/` entries
- **README: Install** — manual install command now includes all required dependencies (PyMuPDF, argon2-cffi, pycryptodome, cryptography)
- **README: Tools** — added enhanced encryption (.epdf format) to the Security tools section
- **README: Security** — added Ghostscript timeout and enhanced encryption details

---

## v4.0.0

Complete rewrite from single-purpose PDF compressor to a full 20-tool PDF toolkit.

### New tools
- **Merge PDFs** — combine multiple PDFs into one document
- **Split PDF** — divide a PDF into separate files by page ranges
- **PDF to Images** — export pages as PNG or JPEG
- **Images to PDF** — convert images into a PDF document
- **PDF to Word** — extract text to a Word document (requires python-docx)
- **Protect PDF** — add password and set permissions (AES-256 encryption)
- **Unlock PDF** — remove password protection
- **Redact PDF** — permanently remove sensitive text
- **Rotate & Reorder** — rotate, reorder, or delete pages
- **Crop Pages** — trim page margins
- **Flatten PDF** — remove annotations and form fields
- **N-up Layout** — arrange multiple pages per sheet
- **Add Watermark** — overlay text or image watermark
- **Add Page Numbers** — insert page numbering
- **Edit Metadata** — view and edit PDF properties
- **Extract Images** — pull all images from a PDF
- **Extract Text** — export text content to a file
- **Repair PDF** — fix corrupted PDF files
- **Compare PDFs** — find differences between two PDFs

### New GUI architecture
- Dashboard home page with searchable tool grid organized by category
- Collapsible sidebar navigation with tool icons
- Stacked page system — each tool has its own dedicated page
- Centralized tool registry (`ui/tool_registry.py`)
- New `pdf_ops.py` module for all non-compression PDF operations
- Modular `ui/pages/` package with a base page class

### Improvements
- App renamed from "PDF Compress" to "PDF Toolkit"
- Ctrl+Home shortcut to return to dashboard
- python-docx added as optional dependency for PDF to Word conversion

---

## v3.0.0

Major engine rewrite with DPI-aware compression, smart format selection, and full GUI overhaul.

### Compression engine
- DPI-aware image downscaling using CTM (Current Transformation Matrix) tracking
- Smart image classification: photographic, diagram/screenshot, monochrome
- Optimal encoding per type: JPEG for photos, Flate for diagrams, 1-bit for B&W
- Grayscale preservation (single-channel JPEG saves ~66% vs RGB)
- Transparency handling — composites soft-masked images against white
- Duplicate font merging by SHA-256 hash
- Content stream optimization (removes empty q/Q pairs)
- Font subsetting via Ghostscript
- PDF structure cleanup: JavaScript, PieceInfo, empty AcroForm, page thumbnails
- Metadata stripping (XMP, document info dict)
- Object stream compression
- Smart skip logic for tiny/already-compressed images
- Decompression bomb protection (200M pixel limit)
- Content stream size limit (16 MB)
- File size limit (2 GB)

### GUI
- Five quality presets: Screen, E-book, Standard, High quality, Prepress
- Light and dark themes with Ctrl+T toggle
- Space audit dialog with image/font/content breakdown
- PDF/A detection with compliance warnings
- Invalid PDF detection (magic byte validation)
- Encrypted PDF support with password prompt
- Custom naming templates (`{name}`, `{preset}`, `{dpi}`)
- Backup on replace with rotation
- Linearization for web-optimized output
- Per-file progress bars
- Sortable file list (name, size, page count)
- Batch summary dialog
- Recent files
- System tray notifications
- Folder drag-and-drop
- Windows Explorer context menu integration
- Cancellation support
- Background file analysis
- Programmatic app icon

### CLI
- All five quality presets
- Batch processing with glob patterns
- `--linearize`, `--gs`, `--backup`, `--log` flags
- Progress bar with per-image status

### Security
- Ghostscript sandboxing (`-dSAFER`, `--` separator, path sanitization)
- Atomic file I/O (temp file + `os.replace()`)
- Thread-safe cancellation
- Diagnostic logging with rotation
- Path validation for `os.startfile`
