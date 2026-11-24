# Frontend parity audit — `web/` (legacy) → `web-react/` (active)

Page-by-page audit of the React frontend against the legacy vanilla-JS
frontend it replaced. It confirmed the migration was functionally complete,
tracked the remaining polish items to closure, and cleared the way to retire
`web/`.

> **Status: complete.** All items below are resolved and the legacy `web/`
> frontend has been deleted (see "Retiring `web/`"). This document is kept as
> the migration record. References to the "vanilla page" / `web/*` are
> historical — those files no longer exist.

**Date:** 2026-07-07 · **Branch:** `chore/v4.21-code-health`
**Verdict:** migration complete. Backend wiring is 100% intact across all
22 tools; the gaps found were cosmetic/UX polish, not functional regressions,
and have since been closed.

## Method

For each of the 23 pages (22 tools + home), compared:

1. The backend `start*` operation invoked (the actual tool call).
2. Auxiliary bridge calls (thumbnails, TOC, per-file analysis, settings).
3. Inline-documented deviations in the React port.

Every apparent gap was verified by reading the source. Several first-pass
"gaps" were false positives caused by bridge calls line-wrapped across two
lines (`bridgeApi`\n`.foo()`) — e.g. `getSanitizeDefaults` (analyze) and
`getToc` (split) are both present and were miscounted initially.

## Parity summary

- **All 22 tools + home exist** and invoke the **same backend operation** as
  the vanilla page. No tool is missing.
- One intentional rename: vanilla `rotate` → React `page_ops` (`PageOpsPage`,
  consolidating rotate + reorder + delete).
- Full parity confirmed (including features first suspected missing):
  - **Analyze** — `getSanitizeDefaults` + sanitize panel intact.
  - **Split** — all four modes present, including **by-chapters/bookmarks**
    (`getToc`) with per-chapter selection.
- **Parity win:** **Redact** gains a **visual box-drawing mode**
  (`getPageImages`) that the vanilla page never had. React is ahead here.

## Open items

Severity: 🟡 minor UX · ⚪ cosmetic/behavioral. None are functional breakages.

- [x] ⚪ **Compress — visual richness.** ~~React omits per-file thumbnails, live
  DPI/image-analysis chips, the animated circular savings gauge, and
  individually editable output filenames.~~ **Done (mostly):** added
  `CompressFileCard` — page-1 thumbnail (`getThumbnail`) + live analysis
  (`analyzeFile`): size, pages, image count, image DPI with a downscale
  warning, and per-preset estimated savings that update with the selected
  preset. Dev mocks enriched so it's reviewable in `vite dev`. **Two
  sub-parts intentionally deferred:** the *animated circular savings gauge*
  (pure polish) and *editable per-file output names* — the latter is
  non-functional without the backend fix tracked in the next section, so an
  editable field would be decorative. (`CompressFileCard.tsx`,
  `CompressPage.tsx`, `bridge/bridgeApi.ts` mocks)

- [x] 🟡 **Merge — per-file page counts.** ~~The vanilla page called
  `analyzeFile` per added file to show its page count in the list; React shows
  only `total_pages` in the final result.~~ **Done:** `MergePage` now fetches
  `analyzeFile` per file as it's added (guarded against re-fetch) and populates
  `PickedFile.pages`; `FileList` renders the count. Field fallback matches
  vanilla (`info.pages ?? info.page_count`). (`src/pages/tools/MergePage.tsx`,
  `src/components/shared/FileList.tsx`)

- [x] 🟡 **Keyboard shortcuts — per-page.** ~~`Ctrl+O` (add files),
  `Ctrl+Enter` (run), and `Esc` (clear) were implemented on 3 vanilla pages
  (merge/split/watermark) and are not carried into React.~~ **Done:** added a
  shared `useHotkeys` hook (`src/bridge/useHotkeys.ts`, cross-platform
  Ctrl/Cmd) and an imperative `open()` handle on `DropZone`, wired into the
  primary workflow pages **Compress, Merge, Split, Watermark**. `Esc` is
  ignored while typing in a field. App-level `Ctrl+T` (theme) / `Ctrl+Home`
  (dashboard) remain in Python (`ui/web_shell.py:348`). Other tool pages can
  adopt the same hook incrementally.

- [x] ⚪ **Router — page state resets on revisit.** ~~The React router unmounts
  a page on navigate-away; the vanilla router cached page instances, so a
  half-filled form survived navigating away and back.~~ **Done:** `AppShell`
  now keeps each visited route mounted and toggles `display:none` instead of
  unmounting, restoring the vanilla cached-page behavior. Pages mount lazily
  (only once visited); the router's busy guard still blocks navigation mid-op,
  so hidden pages are inert. (`src/shell/AppShell.tsx`, `src/router/Router.tsx`)

## Backend fix (was: confirmation to double-check)

- [x] **Compress output controls / batch overwrite.** ~~The vanilla page's
  "Output Folder" / "Output Suffix" controls were silently non-functional:
  `startCompress` read `outputPath` (singular) and applied it to every file in
  a batch, so a shared path would overwrite.~~ **Fixed in `compress_paths.py`:**
  `compress_output_path()` honors `outputDir` + a `naming` template to build a
  distinct path per file, applies a single explicit `outputPath` only to
  single-file calls, and otherwise defaults to `<name>_compressed.pdf` beside
  the source. Covered by `tests/test_bridge.py`. The React page doesn't yet
  expose output-folder/naming controls, but the backend now supports them.

## Retiring `web/` — done

All four open items are resolved, so the legacy `web/` frontend has been
**deleted**. `_resolve_index_html()` (`ui/web_shell.py`) now loads only the
React build (`web-react/dist/`) and the `PDF_TOOLKIT_UI=legacy` escape hatch
is gone; the PyInstaller spec bundles `web-react/dist/` instead of `web/`.
Only the backend confirmation item above (`startCompress` output path)
remains, and it lives in `ui/bridge.py`, not the frontend.

## Post-migration bridge changes

Changes to the Python↔JS contract made after the migration record above.

### #99 — Redact is now fail-closed with a verification report (2026-07-22)

Redaction verifies its own output and refuses to save a file it can't prove
clean. This reshapes the `startRedact` contract on both ends:

- **Success payload** (`RedactResult`) gains `surface_counts` (per-surface
  removal breakdown), `verification` (the `verify_redaction` report:
  `{verified, checks[], flattenTargetPages}`), and `flattened_pages`. The
  React page shows a **"Verified — 0 occurrences remain"** proof line and, in
  the toast, "Verified clean."
- **Failure payload.** When `verify_redaction` refuses the output,
  `ui/bridge.py`'s worker catches `RedactionVerificationError` and emits a
  `success:false` `operationDone` carrying three **new top-level fields**:
  `verification` (the report), `flattenablePages` (`number[] | null`), and
  `documentLevel` (`bool`). The old contract sent only a `message` string,
  which would have destroyed the page list the flatten offer needs.
- **New request param.** `startRedact` accepts `flatten_pages` (1-based page
  numbers) for the **D1 flatten-to-image retry** — sent only on the
  user-confirmed second attempt after a page-content residual.
- **`RedactPage.tsx`** renders the failure detail (what survived, per page)
  and, only when every residual is page-content, a **"Force removal: flatten
  page(s) N"** button that re-dispatches with `flatten_pages`. A
  document-level residual shows no offer (flatten cannot fix it). Browser-dev
  mock (`bridgeApi.ts`) returns a `verified:true` report so the page is
  reviewable in `vite dev`.

### #116, #117 — Redact verifies before it commits; the report gains file identity (2026-11-15)

The shape of the `startRedact` contract is unchanged; what the payloads mean
and carry is not:

- **Refusal no longer touches `outputPath`.** The candidate is verified in a
  temp file and renamed into place only on success, so a `success:false`
  refusal means nothing was written (previously: written, then deleted — which
  destroyed the original when `outputPath` was the input).
- **`verification` gains two fields**, on both the success and the failure
  payload: `sourceSha256` (`string | null`) and `outputSha256`
  (`string | null`). Its `outputPath` always names the real destination.
- **New check id in `verification.checks[]`: `redaction.file_id`** — the
  output must not carry any of the source's trailer `/ID` elements. It is
  always document-level, so a refusal on it never comes with
  `flattenablePages`. When it is the only failed check the `message` reads
  "…the output would still carry the source file's identifier."
- **No frontend change.** `RedactPage.tsx` renders failed checks generically
  from `evidence`, and its local `VerificationReport` type ignores the extra
  fields; `web-react/dist/` was not rebuilt.

### #101 — Analyze/Sanitize coverage: new values, same shapes (2026-11-16)

No slot was renamed or reshaped; the payloads carry new values:

- **`analyzeDocument` report.** A new finding id, `files.external`
  (category `files`, severity `low`): a file specification that points to a
  file outside the PDF. `files.embedded` now means a payload is actually
  present. `meta.xmp` gains `items` (where each metadata stream hangs) and
  its `count` is the number of streams. Bookmark and form-field actions
  appear in `scripts.js` / `actions.*` / `links.uri` items with labels such
  as `Bookmark "Title" /A` and `Form field "name" /AA /K`.
- **`sanitizeDocument` result.** `removed` may carry four new keys —
  `annot_aa`, `field_aa`, `embedded_file`, `instantiate_script` — and
  `document_aa` / `page_aa` count entries rather than dictionaries. Embedded
  files are counted once each, so `embedded_files` is the number of files in
  the name tree. The frontend shows only `total_removed`, so nothing there
  depends on the keys.
- **No frontend change.** `AnalyzePage.tsx` renders findings generically;
  `SANITIZE_FIELDS` labels ("Embedded JavaScript", "Auto-run actions
  (/OpenAction, /AA)") still describe what the options do. `web-react/dist/`
  was not rebuilt. The verification payload on `sanitizeDocument` is the next
  section.

### #101 — Sanitize answers with its verification; a refusal carries the report (2026-11-16)

Sanitize verifies its cleaned copy before saving it and refuses one it
cannot prove clean. `sanitizeDocument` keeps its name and arguments; its
answer is now built by the Qt-free `pdf_analyze.sanitize_payload` and has
three shapes (`SanitizeResponse` in `src/types/analyze.ts`):

- **Success.** As before (`removed`, `total_removed`, `output`,
  `output_size`), plus `verification` for a PDF: the `verify_sanitization`
  report the output passed — `verified`, `checks[]` (each with `unevaluated`
  and `objectPaths`), `residualFindings[]` (what no ticked option promised to
  remove), `unevaluated[]` (checks outside the ticked options that could not
  be completed), `sourceSha256`, `outputSha256`, `diagnostics[]`. An image
  has no `verification`; nothing may assume it is present.
- **Refusal.** `success:false` with `error` (one sentence), `verification`
  (the failed report) and the **new top-level `refusal`**:
  `{stillPresent: string[], notChecked: string[], identifier: boolean,
  unreadable: boolean}` — the same refusal as the lists a screen shows, worded
  in Python (`pdf_verify.sanitize_refusal_detail`). Nothing was written to
  `outputPath`. The old contract sent only `error`.
- **Failure.** `success:false` with `error` and the new `errorType` (the
  exception's type name). A cleaned copy that could not be written, read back
  or moved into place arrives this way, in plain words and with no path.
- **`analyzeDocument` report** gains `unevaluated` (`{finding id:
  string[]}`) and may carry the finding `audit.incomplete` (category `audit`,
  severity `medium`) in place of `clean`.
- **Frontend.** `AnalyzePage.tsx` keeps the last answer and renders it under
  the Sanitize panel through the new `components/SanitizeOutcome.tsx`: a
  verified run with its residual count and titles, or a refusal with the
  three lists. The toast says the same in one line. `audit.incomplete` sorts
  first and `FindingRow` opens it by default. Deliberately thin — the
  exportable report and its panel are #100. `mockSanitizeResult` returns the
  new shapes and refuses when "External links" is ticked.
- **`RedactPage.tsx`** now words a refusal whose only failed check is
  `redaction.file_id` as what it is (the copy would still be linked to the
  original) instead of "Sensitive content was still detectable". No contract
  change. `web-react/dist/` rebuilt.

### #119 — Redact answers in plain words; boxes are read as drawn (2026-11-22)

`startRedact` keeps its name and arguments. What it is sent and what it
answers:

- **Boxes are read as they lie on the page picture.** `rects` were always
  drawn on a picture of the page as it is shown, turned by its `/Rotate`.
  The slot now puts each one back into the page's own space with the Qt-free
  `pdf_ops.rects_from_page_pictures` before `redact_pdf` sees it. On an
  upright page nothing changes; on a turned page a box used to land
  somewhere else. The shape of `rects` is unchanged.
- **Success.** Built by the Qt-free `pdf_ops.redact_result_payload`: the
  `RedactResult` fields as before, which now include `notes` (`[{kind,
  ...}]`: `no_text`, `text_layer`, `attachments`, `flattened`,
  `boxes_only`, `text_may_move`, `field_still_shown`), `flattened_areas`
  (`{page: areas}`) and more `surface_counts` keys, plus the **new
  `summary`**: `{headline, checked, removed: string[], notes: string[]}`,
  worded in Python (`pdf_ops.redaction_summary`).
- **Refusal.** As before (`verification`, `flattenablePages`,
  `documentLevel`), plus the **new top-level `refusal`**: `{headline, found:
  string[], nextStep}`, worded in Python
  (`pdf_verify.redaction_refusal_detail`).
- **Not possible.** A form stored only as XFA that holds a term, and
  PyMuPDF 1.28, raise `RedactionNotPossibleError`. It used to arrive as any
  failure, with a stack trace in the log; it is now `success:false` with the
  plain `message` and the **new top-level `notPossible: true`**, and the log
  gets one line.
- **Frontend.** `RedactPage.tsx` shows `summary` (headline, checked line,
  "Also removed", "Good to know"), for a workspace run too; a refusal from
  `refusal` (headline, what was found, the next step, a "Turn page N into a
  picture" button when pages are offered, and the raw evidence folded under
  "Technical details"); and a not-possible refusal as a plain note with a
  warning toast. "Please report this file — the scrubber should have removed
  it." is gone, and the page's other words are plainer. The browser-dev mock
  answers from the search word: `everything`, `lineart` (and its flatten
  retry), `refuse`, `xfa`. `web-react/dist/` rebuilt.

### #128 — Redact can read scanned pages with OCR (2026-11-23)

- **New slot `getOcrStatus()`** → `{ready: boolean, reason: string | null}`
  (Qt-free `pdf_ocr.ocr_readiness`). `reason` is one plain line: in the
  packaged app "The OCR language file is missing from this copy of PDF
  Toolkit: reinstall it."; from source "OCR isn't set up: run python
  setup_ocr.py --install, then restart the app." (or that numpy is needed).
  The language file's path is never sent, nor logged: it sits in the user's
  own folder. No download from the app: setup is the `setup_ocr.py` command.
- **New request param.** `startRedact` accepts `ocr` (boolean, off when
  absent), handed to `redact_pdf(ocr=...)`.
- **Progress.** While a page is read with OCR the slot sends a
  `progressUpdate` with the count as before and `filename` set to "Reading
  page 3 of 12 with OCR" (`redact_pdf(on_status=...)`); the panel shows it
  beside the count, as a file name. No other tool's progress changes.
- **Success.** `surface_counts` may hold `ocr_page_content`, and `notes`
  new kinds: `ocr`, `ocr_unread`, `ocr_not_ready`, `mostly_picture`. All
  are worded in `summary` by `pdf_ops.redaction_summary`, whose `checked`
  line after an OCR run says what was checked there and no more.
- **Refusal.** New check ids `redaction.ocr_fill`, `redaction.ocr_image`,
  `redaction.ocr_reread`, `redaction.ocr_outside`, worded in `refusal` as
  before.
- **Frontend.** `RedactPage.tsx` has the option "Also read scanned pages
  with OCR (slower)" under the search words: off by default, disabled with
  no words typed, and disabled with the `reason` line under it when OCR is
  not ready. The shared `Checkbox` gains an optional `disabled`. The
  browser-dev mock is ready unless the address has `?ocr=off`; a run with the
  option ticked shows the OCR progress line and an OCR summary, and the
  search word `damage` shows the refusal for a page that would have changed
  outside the places found. `web-react/dist/` rebuilt.

### #135 — Nothing found and Cancel are not errors; Redact shows its file (2026-11-23)

- **Done payload, a search that finds nothing.** `redact_pdf` raises
  `RedactionNothingFoundError` (a ValueError). The worker gives it its own
  branch, like `RedactionNotPossibleError`: one info line in the log, no
  stack trace, `success: false` with the whole plain message (it ends
  "Nothing was saved.") and the **new top-level `nothingFound: true`**.
  `RedactPage.tsx` shows a plain note ("Nothing to redact.") with the
  message and an info pop-up, not a red error.
- **Done payload, a cancel (every tool).** The worker's
  `CancelledError`/`InterruptedError` branch now logs one info line and adds
  the **new top-level `cancelled: true`** (message still "Cancelled").
  `redact_pdf` raises `InterruptedError` on a cancel, as the other tools
  did, instead of `ValueError("Cancelled")`. The shared `useOperation` hook
  ignores a done payload with `cancelled` (`DonePayload.cancelled` in
  `types/bridge.ts`): every tool page's Cancel button had already reset the
  operation and said so in its own line, and the payload then set it to an
  error, so a red "Cancelled" pop-up came as well — on all 21 tool pages
  with a Cancel button. Now only the page's own line shows, and a cancelled
  run cannot end a run started after it. Since that payload no longer ends
  the run, every Cancel resets the operation itself: the hook's own
  `cancel()` now resets as the pages' handlers do, and Translate's setup
  panel (which passed `setupOp.cancel` straight to its ProgressPanel) has a
  handler like the others that resets and says "Setup cancelled.".
- **Frontend.** The Redact page's drop box is compact once a file is
  loaded and shows its name, as the Compare page's drop boxes do; the shared
  `DropZone` says "click or drop to pick another" in a box that takes one
  file (it said "add more", Compare's too). The browser-dev mock answers
  `bridgeApi.cancel` as the bridge does (a done payload with `cancelled`),
  and the Redact search word `nothing` shows the nothing-found note.
  `web-react/dist/` rebuilt.

### #121 — Translation offline after setup; no silent English (2026-11-23)

No new slot; three payloads change.

- **`startTranslatePdf` done payload.** `results` gains
  **`untranslatedBlocks`**, the number of text blocks that could not be
  translated and were kept as they were. A run in which no block could be
  translated now fails (`success: false`, nothing saved) with a message
  that starts "None of the text could be translated, so nothing was saved."
  and gives the first reason (often "The sentence model for German isn't
  installed. Run translation setup again."). `TranslatePage.tsx` shows
  "N text blocks couldn't be translated and were left as they were." under
  the saved file ("1 text block couldn't be translated and was left as it
  was." for one).
- **`startSetupTranslation` done payload.** `results` gains
  **`sentenceModels`**, the sentence models now in place (the chosen
  languages' and English's).
- **Translation status (`getTranslationStatus`, `startGetTranslationStatus`).**
  Same shape; a language's `translateTo`/`translateFrom` are true only when
  its packs, its sentence model and English's are installed. The Translate
  page now counts itself ready when some language other than English can be
  translated both ways (it used to look only at `argosPairs`), so a set-up
  made before the sentence models were needed shows the setup card, and
  running it again completes it. The setup card says each language also
  installs a small model for splitting its sentences.
- **Mocks.** The setup mock returns `sentenceModels`; the PDF mock returns
  `untranslatedBlocks: 0`, or 2 when "partial" is among the words kept
  untranslated. `web-react/dist/` rebuilt.

### #120 — The activity log: detailed log and Clear logs (2026-11-23)

Three new synchronous slots, all on the home screen's new "Activity log"
panel (`HomePage.tsx`, below the tool sections):

| Slot | Returns | Used for |
|------|---------|----------|
| `getLogSettings()` | `{"detailedLog": bool}` | The switch's state when the home screen opens |
| `setDetailedLog(on: bool)` | `{"detailedLog": bool}` | "Detailed log (includes file names)": saved in QSettings `diagnostics/detailedLog` and applied at once (`app_log.set_detailed`) |
| `clearLogs()` | `{"success": bool, "removed": n}` (and `error` if it failed) | "Clear logs": the page shows "Logs cleared." |

- **Types.** `BridgeAPI` in `types/global.d.ts`; the raw slots and their
  JSON parsing in `qwebchannel-connect.ts`.
- **Mocks.** `bridgeApi.ts` keeps the switch for the page's lifetime and
  answers Clear logs with `{success: true, removed: 2}`.
- **No payload changes.** `getWorkspaceDir` returns the same string; the
  folder now holds an owner file and is deleted when the app quits.
  `web-react/dist/` rebuilt.
