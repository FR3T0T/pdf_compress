# PDF Toolkit — Frontend (React + Vite + TypeScript)

This is the **active GUI** for PDF Toolkit: a React + Vite + TypeScript app
that renders inside the PySide6/QtWebEngine desktop shell. It covers **all 22
tools**, the sidebar/router shell, theming, and a shared component library.

> **History:** this started as a scoped proof-of-concept rebuilding only the
> *Analyze Document* page. That evaluation was accepted and the whole frontend
> was migrated (see the v4.20 entry in the root `CHANGELOG.md`). The legacy
> vanilla-JS frontend in `web/` is retained only as a fallback — see below.
>
> **Migration status:** see [`PARITY_AUDIT.md`](./PARITY_AUDIT.md) for the
> page-by-page audit against `web/` and the remaining polish items before
> `web/` is retired.

## Which frontend loads

`ui/web_shell.py` (`_resolve_index_html()`) decides at startup:

1. `PDF_TOOLKIT_UI=legacy` → force the vanilla `web/` frontend.
2. Otherwise, if `web-react/dist/index.html` exists → load this React build
   (the default; `dist/` is committed).
3. Otherwise → fall back to `web/` and log a warning.

So end users run the React build with **no Node.js required** — the built
bundle is committed to `dist/`. Node is only needed to *rebuild* the frontend.

## Requirements (for development only)

Node.js 18+ and npm. Not required to run the shipped app.

## Commands

```bash
npm install       # install React, Vite, TypeScript (local only)
npm run dev       # http://localhost:5173 — runs with MOCK bridge data
npm run build     # type-checks, then produces dist/ (file://-loadable)
npm run preview   # optional: serve the dist/ build locally to sanity-check
```

`npm run dev` opens in a normal browser where `window.BridgeAPI` doesn't
exist. The bridge module (`src/bridge/bridgeApi.ts`) detects that and serves
realistic mock responses instead, so every page is reviewable standalone
(drop zones fall back to a real `<input type="file">`). Route `#/gallery` is a
dev-only component gallery, not shown in the sidebar.

## Project structure

```
src/
  main.tsx              entry — connects the QWebChannel bridge, then mounts <App>
  App.tsx               route table (all 22 tools + home + dev gallery)
  shell/                AppShell, Sidebar — the app chrome
  router/Router.tsx     hash router (#/<key>), port of web/js/router.js
  pages/                HomePage, AnalyzePage, and pages/tools/*Page.tsx (one per tool)
  components/           tool-specific + components/shared/ reusable UI
  bridge/               bridgeApi (real vs mock), qwebchannel-connect, useOperation hook
  types/                TypeScript mirrors of the Python bridge payloads
  styles/theme.css      self-contained design system (light + dark)
```

Routes are keyed to match the Python tool registry (`ui/tool_registry.py`) and
the legacy `web/` page keys, with one consolidation: the legacy `rotate` page
is served here by `PageOpsPage` under the `page_ops` key (rotate + reorder +
delete).

## How the bridge works

`src/bridge/bridgeApi.ts` is a thin pass-through over the QWebChannel object:

- If `window.BridgeAPI` exists (running inside the PySide6 app), every call
  goes straight to it — same method names, arguments, and return shapes as the
  Python `ui/bridge.py` exposes. **No Python changes are required to run this
  frontend; the bridge contract is unchanged from the vanilla app.**
- If it doesn't exist (browser dev), calls resolve to `src/bridge/mockData.ts`
  with a short artificial delay so loading states are visible.
- Async operations (progress / cancel / done) are centralized in the
  `useOperation` hook (`src/bridge/useOperation.ts`).
- Types in `src/types/` mirror the Python dataclasses and JSON envelopes
  field-for-field.

## CSP / offline compliance

The desktop shell enforces `connect-src 'none'` and `script-src 'self' qrc:`
via both a `QWebEngineUrlRequestInterceptor` (`ui/net_guard.py`) and a CSP meta
tag. This build is compliant by construction:

- `vite.config.ts` sets `base: './'` — every asset reference in the built
  `dist/` is relative (`./assets/...`), never an absolute path that would 404
  under `file://` / `qrc://`.
- `build.rollupOptions.output.format = 'iife'` + `inlineDynamicImports` — the
  entry is a single classic `<script>`, not an ES module. Chromium's module
  loader needs CORS that `file://` origins can't satisfy, so a `type="module"`
  entry would silently fail to execute under `QUrl.fromLocalFile()`. A custom
  `classicScriptTag()` Vite plugin also strips the `type="module"`/`crossorigin`
  attributes Vite hardcodes, leaving `<script defer src="./assets/...">`.
- `build.modulePreload.polyfill = false` — avoids Vite's inline preload
  `<script>`, so `script-src` never needs `'unsafe-inline'`.
- No inline `<script>` blocks; the only hand-written tag is the `qrc:`
  QWebChannel bridge include (allowed by `script-src 'self' qrc:`, and it 404s
  harmlessly in browser dev). All app UI is React-rendered DOM.
- No CDN links, no web fonts — `theme.css` uses system font stacks, zero font
  files to fetch.
- All state lives in React; nothing touches `localStorage`/`sessionStorage`.
- `bridgeApi.ts` never calls `fetch`/`XMLHttpRequest` — mock mode is in-memory,
  real mode is QWebChannel RPC (not HTTP), so `connect-src 'none'` is never hit.

After `npm run build`, `dist/index.html` should contain only
`<link rel="stylesheet">` and `<script defer src="./assets/...">` tags with
relative `./` paths, and no inline `<script>` blocks.

## Committed `dist/`

`dist/` is checked into git so end users don't need a Node toolchain. The
trade-off: **the build can drift from source** if `src/` changes without a
rebuild. When you change anything under `src/`, run `npm run build` and commit
the regenerated `dist/` in the same change.
