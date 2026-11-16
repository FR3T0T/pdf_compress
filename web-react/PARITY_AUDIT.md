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
  was not rebuilt. The verification payload on `sanitizeDocument` arrives
  with the #101 gate.
