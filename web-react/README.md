# Analyze Document — React POC

A proof-of-concept rebuild of the **Analyze Document** tool page in React +
Vite + TypeScript, styled as a dark "security console." This is a scoped
evaluation POC — it does not touch `web/` (the shipping vanilla-JS app) or
any Python (`ui/bridge.py`, `pdf_analyze.py`). Nothing else in the toolkit
was rewired.

## Requirements

Node.js 18+ and npm. Neither is required to *run* the shipped app — only to
build this POC.

## Commands

```bash
npm install       # install React, Vite, TypeScript (all local, no postinstall network calls beyond this)
npm run dev        # http://localhost:5173 — runs with MOCK analyze/sanitize data
npm run build       # type-checks, then produces dist/ — a file://-loadable production bundle
npm run preview     # optional: serve the dist/ build locally to sanity-check it
```

`npm run dev` opens in a normal browser, where `window.BridgeAPI` doesn't
exist. The bridge module (`src/bridge/bridgeApi.ts`) detects that and
serves realistic mock analyze/sanitize responses instead, so the whole page
— including the drop zone (falls back to a real `<input type="file">`),
finding rows, and the sanitize panel — is reviewable standalone.

## How the bridge works

`src/bridge/bridgeApi.ts` is a thin pass-through:

- If `window.BridgeAPI` exists (i.e. the page is running inside the PySide6
  app, after `web/js/bridge.js` has set it up), every call goes straight to
  it — `analyzeDocument(path)`, `sanitizeDocument(path, outputPath,
  options)`, `getSanitizeDefaults()`, `openFiles(filter)`,
  `saveFile(filter, defaultName)`, `basename(path)` — same names, same
  arguments, same return shapes as the vanilla page. **No Python changes
  are required or were made.**
- If it doesn't exist, calls resolve to data in `src/bridge/mockData.ts`
  (one realistic multi-severity report + a synthetic sanitize result),
  with a short artificial delay so loading states are visible.
- `bridgeApi.onFilesDropped()` optionally subscribes to the same
  `EventBus` `"files-dropped"` signal the vanilla page uses for native OS
  drag-and-drop paths (see `web/js/app.js`); it's a no-op when the bus
  isn't present (e.g. in browser dev).

Types in `src/types/analyze.ts` mirror `pdf_analyze.py`'s
`AnalysisResult.to_dict()` / `Finding` dataclass field-for-field
(`fileName`, `fileSizeStr`, `overallRisk`, `counts`, `findings[].id` /
`.severity` / `.count` / `.items`, etc.) and `ui/bridge.py`'s JSON
envelopes (`{success, report}` / `{success, removed, total_removed,
output}`).

## CSP / offline compliance

The app enforces `connect-src 'none'` and `script-src 'self' qrc:` via both
a `QWebEngineUrlRequestInterceptor` and a CSP meta tag (see
`web/index.html`). This build is compliant by construction:

- `vite.config.ts` sets `base: './'` — every asset reference in the built
  `dist/index.html` and JS chunks is relative (`./assets/...`), never an
  absolute `/assets/...` path that would 404 under `file://` or `qrc://`.
- `build.modulePreload.polyfill = false` — Vite's default module-preload
  shim is an inline `<script>`; disabling it means the CSP's
  `script-src 'self'` never needs `'unsafe-inline'`.
- No `<script>` tags are written by hand anywhere in `index.html` or JSX;
  everything is React-rendered DOM.
- No CDN links, no Google Fonts, no external `<link>`/`<script src="http...">`
  anywhere. `src/styles/theme.css` uses a system monospace stack
  (`ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas, monospace`)
  and the platform sans-serif stack — zero font files to bundle or fetch.
- All state lives in React (`useState`); nothing touches `localStorage` or
  `sessionStorage`.
- `bridgeApi.ts` never calls `fetch`/`XMLHttpRequest` — mock mode uses only
  in-memory data, and real mode goes through the QWebChannel bridge object
  (RPC over the Qt transport, not an HTTP request), so `connect-src 'none'`
  is never touched.

After `npm run build`, inspect `dist/index.html` — it should contain only
`<link rel="stylesheet">` and `<script type="module" src="./assets/...">`
tags with relative `./` paths, and no inline `<script>` blocks.

## Integrating into the app (future work, not done here)

This POC is standalone and is **not** wired into `web/index.html`. If the
migration is approved, integration would look like:

1. Run `npm run build` to produce `web-react/dist/` (`index.html` +
   `assets/*.js` + `assets/*.css`, all relative paths).
2. Either:
   - Copy `dist/assets/*` into `web/` (e.g. `web/react/analyze/`) and add a
     route/entry point in the existing `Router` (`web/js/app.js`) that
     mounts this bundle into a container element when the "Analyze
     Document" tool is selected, instead of calling the current
     `AnalyzePage()` in `web/js/pages/analyze.js`; or
   - Serve `dist/index.html` directly as its own page/tab if the migration
     moves to a fully separate React shell per tool.
3. No changes to `ui/bridge.py` or `pdf_analyze.py` are needed either way —
   `window.BridgeAPI` is already global once `web/js/bridge.js` has run,
   and this bundle detects and uses it automatically.
4. If tools beyond Analyze migrate later, the CSP's `script-src 'self'
   qrc:` and `connect-src 'none'` already accommodate this pattern; no CSP
   changes needed.

## What's deliberately out of scope

- Only the Analyze Document page was rebuilt. No other tool pages, the
  sidebar, dashboard, or theming system were touched.
- No Python/backend files were modified.
- No dependency on `web/css/*.css` or `web/js/components.js` — this is a
  fully self-contained styling system (`src/styles/theme.css`) so the two
  implementations can be compared side by side without interference.
