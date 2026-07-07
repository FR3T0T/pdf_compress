# Frontend parity audit — `web/` (legacy) → `web-react/` (active)

Page-by-page audit of the React frontend against the legacy vanilla-JS
frontend it replaced. Purpose: confirm the migration is functionally complete
before `web/` is retired, and track the remaining polish items.

**Date:** 2026-07-07 · **Branch:** `chore/v4.21-code-health`
**Verdict:** near-complete migration. Backend wiring is 100% intact across all
22 tools; remaining gaps are cosmetic/UX polish, not functional regressions.
Nothing here blocks retiring `web/`.

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

- [ ] ⚪ **Compress — visual richness.** React omits per-file thumbnails, live
  DPI/image-analysis chips, the animated circular savings gauge, and
  individually editable output filenames; it uses the shared
  `FileList`/`ResultsPanel` instead. Core workflow (multi-file, presets, real
  per-file progress, before/after sizes) is intact. *Deliberate — documented
  at `src/pages/tools/CompressPage.tsx` top-of-file comment.* Decide whether
  to rebuild any of this bespoke UI or accept the simpler shared components.

- [ ] 🟡 **Merge — per-file page counts.** The vanilla page called
  `analyzeFile` per added file to show its page count in the list; React shows
  only `total_pages` in the final result. Restoring it means calling
  `bridgeApi.analyzeFile(path)` as files are added and surfacing the count in
  `FileList`. *Undocumented.* (`src/pages/tools/MergePage.tsx`)

- [ ] 🟡 **Keyboard shortcuts — per-page.** `Ctrl+O` (add files), `Ctrl+Enter`
  (run), and `Esc` (clear) were implemented on 3 vanilla pages
  (merge/split/watermark) and are not carried into React. App-level `Ctrl+T`
  (theme) and `Ctrl+Home` (dashboard) still work — they live in Python
  (`ui/web_shell.py:348`), independent of frontend. The main `README.md`
  keyboard table was corrected to list only the two that actually work.
  Decide whether to reintroduce per-page shortcuts (e.g. a shared `useHotkeys`
  hook) or leave them retired.

- [x] ⚪ **Router — page state resets on revisit.** ~~The React router unmounts
  a page on navigate-away; the vanilla router cached page instances, so a
  half-filled form survived navigating away and back.~~ **Done:** `AppShell`
  now keeps each visited route mounted and toggles `display:none` instead of
  unmounting, restoring the vanilla cached-page behavior. Pages mount lazily
  (only once visited); the router's busy guard still blocks navigation mid-op,
  so hidden pages are inert. (`src/shell/AppShell.tsx`, `src/router/Router.tsx`)

## Confirmation to double-check (not a gap)

- **Compress output controls.** The vanilla page's "Output Folder" / "Output
  Suffix" controls were **silently non-functional** in the batch case it
  always used: `startCompress`'s Python side reads `outputPath` (singular), so
  output always lands as `<name>_compressed.pdf` beside each source. React
  deliberately omits those dead controls rather than replicate them (see the
  `CompressPage.tsx` comment). Confirm this is the intended behavior; if
  configurable output is actually wanted, it needs a **backend** fix in
  `ui/bridge.py`, not just UI.

## Retiring `web/`

Once the items above are triaged (fixed or accepted), `web/` can be deleted —
the same clean-removal pattern as the ~9,500-line native-Qt widget UI removed
earlier on this branch. Until then it remains the `PDF_TOOLKIT_UI=legacy`
fallback. See `ui/web_shell.py` (`_resolve_index_html()`) for the switch.
