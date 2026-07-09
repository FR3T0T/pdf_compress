# CLAUDE.md

Guidance for Claude (and humans) working in this repo. Keep it short and true —
prune anything that goes stale. The [README](README.md) covers what the tools do;
this file covers how to work here without stepping on a rake.

## What this is

A **fully offline** PDF toolkit. Python backend (compression `engine.py`, PDF ops
`pdf_ops.py`, crypto `epdf_crypto.py`, analysis `pdf_analyze.py`, translation
`pdf_translate.py`) behind a PySide6 **QWebEngine** shell (`ui/`), driving a
**React + Vite + TypeScript** frontend in `web-react/`. Python↔JS talk over a
QWebChannel bridge (`ui/bridge.py`).

## Run / test / lint

```bash
python app.py                    # GUI     (or compress_pdf.bat on Windows)
python compress_pdf.py file.pdf  # CLI (compression only)

pip install -r requirements-dev.txt   # pytest, pytest-cov, ruff
python -m pytest -q                    # tests
python -m ruff check .                 # lint
```

Use `python -m pytest` / `python -m ruff` — the console scripts are often not on
PATH here. CI (`.github/workflows/ci.yml`) runs ruff + a pytest matrix on
Ubuntu + Windows, Python 3.10 and 3.12.

## Keep the docs current

Docs are how everyone knows where the project stands — **never let `main` drift
from them.** When you make a change, update the affected docs **as part of the
same change**, not "later":

- **`CHANGELOG.md`** — the running record. Every notable change (feature, fix,
  removal, dependency or version bump, security/behavior change) gets a bullet in
  the top section *as you make it*. This is the first place a returning teammate
  looks; keep it complete and honest.
- **`README.md`** — update when behavior, the tool list, dependencies, commands,
  the Files table, or the version badge change.
- **`web-react/PARITY_AUDIT.md`** and **`web-react/README.md`** — update when the
  frontend architecture, parity status, or the bridge surface changes.
- **`CLAUDE.md`** (this file) — update when a rule, convention, or workflow here
  changes.
- **GitHub Project board** — <https://github.com/users/FR3T0T/projects/7>
  tracks work items with a `Status` field (`Todo` / `In progress` / `Done`) and
  an `owner:<name>` label per item. Keep it in step with actual repo state, not
  just intentions:
  - Flip an item to **In progress** *before* starting work on it, not after.
  - Only flip it to **Done** once the fix is **merged into `main`** — a local
    commit or an open PR is still `In progress`. This matches how every
    existing `Done` item got there (each has a merged, linked PR).
  - Editing status needs the `project` (write) OAuth scope — `gh auth status`
    to check; if only `read:project` is present, the user must run
    `gh auth refresh -s project` themselves (browser approval, can't be
    scripted). Read-only lookups (`gh project item-list`) only need
    `read:project`.
- **Version string** lives in four places — keep them in sync (they have drifted
  before): `app.py` `VERSION`, `pyproject.toml` `version`, the `README.md` badge,
  and the top `CHANGELOG.md` heading.

A reader should be able to learn the current state of the project from the docs
alone. If a change makes a doc wrong, fixing the doc is part of that change.

## Rules that matter

- **Tests must not import Qt.** Importing anything under `ui/` pulls in the whole
  PySide6 GUI stack (`ui/__init__` → `web_shell` → QtWebEngine), which can't load
  on a headless CI runner (`libEGL.so.1`) and kills the test job at collection.
  Keep pure, unit-testable logic in **top-level Qt-free modules** (e.g.
  `compress_paths.py`) and import it there from tests — never reach through
  `ui.bridge`. (This already broke Linux CI once.)

- **Offline is a hard invariant.** The app must never make a network request.
  `ui/net_guard.py` blocks every non-local request and the CSP sets
  `connect-src 'none'`. Don't add anything that phones home. (The one explicit,
  user-run online step is `setup_translation.py`, which is separate from the app.)

- **The frontend build is committed.** `web-react/dist/` is checked in so end
  users need no Node. If you change `web-react/src/`, rebuild
  (`cd web-react && npm run build`) and commit `dist/`. A comment-only src change
  needs no rebuild. The bridge's `@Slot` methods in `ui/bridge.py` are the RPC
  contract with `web-react/src/bridge/` — don't rename/reshape a slot without
  updating both sides.

- **Match the existing style.** ruff (config in `pyproject.toml`) runs `E,F,W,B,I`
  but ignores `E701/E702/E741` — the compact one-liner style (`if x: return ...`)
  is deliberate; follow it. Imports stay isort-sorted.

## Releasing

- Rename the top `CHANGELOG.md` section from its working heading to `vX.Y`, and
  bump the version in all four places (see *Keep the docs current*).
- Tag the release commit with an annotated tag: `git tag -a vX.Y` (as of v4.22).
