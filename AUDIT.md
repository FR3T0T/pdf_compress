# Code Audit — PDF Toolkit v4.22

> **Status: informational snapshot. Nothing here is fixed yet.**
> This document records where the codebase stands as of the audit date so every
> contributor shares the same picture. It proposes fixes but **prescribes no
> work** — prioritisation is a separate decision.

- **Audit date:** 2026-07-08
- **Version audited:** v4.22
- **Git HEAD:** `331b69f` (branch `main`)
- **Method:** multi-agent static audit (12 subsystem passes), every candidate
  finding independently re-checked against the source before inclusion; plus a
  manual bridge-contract cross-check and one manual re-verification (see
  [Coverage & method](#coverage--method)).

---

## 1. Executive summary

The project is **mature and unusually well-documented**. Docs and the version
string are consistent across all four required places, CI is sound, the
committed `web-react/dist/` build is current (the only `src` change since the
last build is comment-only), the offline invariant holds, and the
**Python↔JS bridge contract is fully in sync** (verified — see §3). Nothing is
on fire and the app works for the common case.

However, the audit surfaced a cluster of **real functional bugs**, four of them
high-impact, concentrated in the two flagship features (compression, translation)
and the security-tool (Analyze). In short: **compression and translation quietly
do less than the docs claim, and the Analyze sanitiser gives false "clean"
reports on two evasion vectors.**

### Counts (post-verification severity)

| Severity | Count | Meaning |
|---|---|---|
| 🔴 High | 4 | Real, user-visible, flagship-feature or security impact |
| 🟠 Medium | 12 | Correctness / robustness / security-path test gaps |
| 🟡 Low | 22 | Minor correctness, hygiene, doc/build drift |
| ⚪ Info | 1 | Test-quality nit, no runtime risk |
| 🔵 Plausible | 1 | Real config trap, only bites under a specific build condition |
| ❌ Rejected | 4 | Investigated and dismissed — **do not re-report** (§7) |

**The four highs at a glance:**

1. **`ENG-01` — Compression only ever re-compresses existing JPEGs.** Every
   Flate/CCITT/LZW/indexed image (diagrams, screenshots, scans) is silently
   skipped; the advertised 1-bit / Flate-for-diagrams / smart-format branches are
   dead code.
2. **`ENG-02` — Transparency is destroyed.** Soft masks are deleted even when
   compositing failed, baking transparent images into opaque rectangles.
3. **`TRN-03` — Translation doesn't translate from CJK/Arabic/Hindi/Bengali.**
   4 of 12 advertised source languages return the source text unchanged while
   reporting success.
4. **`ANL-02` — Analyze's sanitiser leaves JavaScript hidden in `/Next` action
   chains.** Reports success while the exact flagged high-severity script
   survives. **✅ Fixed** — the sanitiser now walks `/Next` (and `/AA` entry)
   chains like the analyzer; see §5.

---

## 2. How to read this document

Each finding has a stable ID (`AREA-NN`), a post-verification severity, and:

- **Location** — file:line (as of `331b69f`).
- **What** — the defect.
- **Evidence** — the specific code that grounds it.
- **Impact** — who/what it affects and how it manifests.
- **Fix** — a suggested remedy (not a mandate).
- **Verification** — `CONFIRMED` (code-verified during the audit) or
  `PLAUSIBLE`.
- **Status** — all findings are **Open** as of this snapshot.

Where the post-verification severity differs from the original finder's rating,
the change is noted (verification frequently *downgraded* severity after checking
real-world reachability — those notes are kept for honesty).

Area prefixes: `ENG` engine · `OPS` pdf_ops · `CRY` epdf_crypto · `ANL`
pdf_analyze · `TRN` pdf_translate · `BRG` ui/bridge · `CLI` compress_pdf ·
`FE` web-react frontend · `TST` tests/CI · `DOC` docs · `PKG` packaging/build.

---

## 3. Coverage & method

- **12 subsystem finders** ran in parallel (engine, pdf_ops, crypto, analyze,
  translate, bridge/offline, CLI/entry, bridge-contract, frontend, tests/CI,
  docs, deps/build). Each candidate finding was then handed to an independent
  **adversarial verifier** told to *refute* it against the source; only findings
  that survived are included here.
- **Two audit agents hit a session limit mid-run.** Both gaps were closed
  manually:
  - The **bridge-contract cross-check** was re-run by hand — **result: clean**
    (details below).
  - One translation finding's verifier died; it was **re-verified by hand** and
    confirmed → `TRN-03` (kept as a full High finding).
- **Not exhaustive.** This is a static review. It did not run the app, build the
  frozen exe, or execute the translation stack end-to-end (that needs the
  optional Argos/Tesseract models installed). Runtime-only behaviours are called
  out where relevant.

### Bridge contract — verified clean ✅

All 50 unique `@Slot` methods in `ui/bridge.py` were diffed against every
`raw.*` call made by the frontend adapter in
`web-react/src/bridge/qwebchannel-connect.ts`:

- Every frontend call maps to an existing slot with matching arity; friendly
  names (`openFiles`, `cancel`, `openFolderPath`) are correctly adapted to slot
  names (`openFileDialog`, `cancelOperation`, `openFolder`).
- `startXxx(params)` objects are `JSON.stringify`-ed before the call (slots take
  `json_params: str`); returns are `safeJsonParse`-ed back.
- The two Qt gotchas are handled deliberately: `getPresets('')` /
  `getSanitizeDefaults('')` pass the required dummy arg; `translateText` /
  `translateImage` carry a **double `@Slot`** (3- and 4-arg) so the 4-arg
  `protectTerms` call resolves.
- All four signals (`progressUpdate`, `operationDone`, `filesDropped`,
  `themeChanged`) exist as `Signal(str)`.
- No orphan slots; `formatSize`/`formatPct`/`basename`/`dirname` are pure JS
  helpers (no slot obligation).

The v4.20-era class of bug (frontend reading `data.foo` while the bridge sent
`data.bar`; `getPresets` called with wrong arity) is **not present now.**

---

## 4. Master list

| ID | Sev | Area | Title | Location | Status |
|----|-----|------|-------|----------|--------|
| ENG-01 | 🔴 High | engine | Only JPEG images are recompressed; non-JPEG silently skipped | `engine.py:909` | Open |
| ENG-02 | 🔴 High | engine | `/SMask` deleted even when compositing failed → transparency lost | `engine.py:1052` | Open |
| TRN-03 | 🔴 High | translate | Source text in non-Latin/Cyrillic scripts returned untranslated | `pdf_translate.py:305` | ✅ Fixed |
| ANL-02 | 🔴 High | analyze | Sanitiser leaves JS/Launch/Submit in `/Next` action chains | `pdf_analyze.py:778` | ✅ Fixed |
| ENG-03 | 🟠 Med | engine | `q Q` regex can corrupt text / inline images in uncompressed streams | `engine.py:1684` | Open |
| ENG-04 | 🟠 Med | engine | Ghostscript pipes never drained → deadlock until 5-min timeout | `engine.py:1318` | Open |
| OPS-02 | 🟠 Med | pdf_ops | `flatten(forms=True, annotations=False)` leaves form-field values | `pdf_ops.py:1291` | Open |
| ANL-01 | 🟠 Med | analyze | In-place sanitise fails on Windows (`os.replace` over open handle) | `pdf_analyze.py:884` | ✅ Fixed |
| ANL-03 | 🟠 Med | analyze | Invisible-text ("failed redaction") detector largely non-functional | `pdf_analyze.py:620` | ✅ Fixed |
| ANL-04 | 🟠 Med | analyze | Embedded-file sanitiser leaves `/FileAttachment` & `/AF` files | `pdf_analyze.py:918` | ✅ Fixed |
| TRN-01 | 🟠 Med | translate | PDF→PDF translate aborts entirely on one undetectable short block | `pdf_translate.py:600` | ✅ Fixed |
| BRG-01 | 🟠 Med | bridge | Worker cleanup keyed by `tool_key` breaks cancel after rapid restart | `ui/bridge.py:327` | ✅ Fixed |
| CLI-01 | 🟠 Med | CLI | CLI always exits 0 even when files fail | `compress_pdf.py:214` | Open |
| FE-01 | 🟠 Med | frontend | Drag-drop not scoped to active page → pollutes every mounted page | `DropZone.tsx:66` | ✅ Fixed |
| TST-01 | 🟠 Med | tests | Redaction (data-destruction) has zero test coverage | `pdf_ops.py:1517` | ✅ Fixed |
| TST-02 | 🟠 Med | tests | Path-containment guard `contained_output_path()` untested | `pdf_ops.py:22` | ✅ Fixed |
| RED-01 | 🟠 Med | pdf_ops | Redaction destroys entire page on image-only (scanned) PDFs | `pdf_ops.py:1649` | ✅ Fixed |
| ENG-05 | 🟡 Low | engine | Size-benefit check compares uncompressed candidate vs compressed original | `engine.py:979` | Open |
| ENG-06 | 🟡 Low | engine | Hardcoded `is_tiny` (<64px) overrides per-preset `skip_below_px` | `engine.py:721` | Open |
| OPS-01 | 🟡 Low | pdf_ops | `protect_pdf` sets owner password = user password → restrictions bypassable | `pdf_ops.py:430` | Open |
| OPS-03 | 🟡 Low | pdf_ops | `add_watermark` leaks open file handle on malformed range/color | `pdf_ops.py:823` | Open |
| OPS-04 | 🟡 Low | pdf_ops | `split_pdf` filename collisions silently overwrite | `pdf_ops.py:300` | Open |
| OPS-05 | 🟡 Low | pdf_ops | `images_to_pdf` re-encodes to JPEG q92 despite "preserves quality" | `pdf_ops.py:593` | Open |
| CRY-01 | 🟡 Low | crypto | Decrypt raises non-`EPDFError` types on malformed headers | `epdf_crypto.py:442` | Open |
| CRY-02 | 🟡 Low | crypto | Non-dict `kdf_params` bypasses validation → uncaught `TypeError` | `epdf_crypto.py:134` | Open |
| TRN-02 | 🟡 Low | translate | Temp PNG leaks if `pix.save` fails before the cleanup try/finally | `pdf_translate.py:450` | ✅ Fixed |
| BRG-02 | 🟡 Low | bridge | `deleteFile`/`copyFile` lack workspace-dir path containment | `ui/bridge.py:851` | ✅ Fixed |
| BRG-03 | 🟡 Low | bridge | Slot-level param parse runs outside worker try/except → UI hangs | `ui/bridge.py:722` | ✅ Fixed |
| CLI-02 | 🟡 Low | CLI | Batch: two inputs sharing a basename overwrite each other's output | `compress_pdf.py:128` | Open |
| CLI-03 | 🟡 Low | CLI | `input()` at exit raises `EOFError` traceback on non-interactive stdin | `compress_pdf.py:211` | Open |
| CLI-04 | 🟡 Low | CLI | Not-found inputs omitted from summary counts / failure tally | `compress_pdf.py:115` | Open |
| FE-02 | 🟡 Low | frontend | Workspace risk badge/findings never refreshed after a transform | `WorkspaceContext.tsx:123` | Open |
| FE-03 | 🟡 Low | frontend | `RedactPage` advances workspace with an unguarded `output_path` | `RedactPage.tsx:191` | ✅ Fixed |
| TST-03 | 🟡 Low | tests | Password protect/unlock round-trip untested | `pdf_ops.py:408` | Open |
| TST-04 | 🟡 Low | tests | Backup-on-overwrite test asserts nothing when compression skips | `tests/test_engine.py:239` | ✅ Fixed |
| DOC-01 | 🟡 Low | docs | README advertises a Windows context-menu + About dialog that no longer exist | `README.md:180` | Open |
| DOC-02 | 🟡 Low | docs | CHANGELOG documents a "stanza" security upgrade for a never-present dep | `CHANGELOG.md:115` | Open |
| PKG-01 | 🟡 Low | build | `assets/fonts/DejaVuSans.ttf` not bundled in the PyInstaller spec | `pdf_toolkit.spec:25` | Open |
| PKG-02 | 🟡 Low | build | Spec lists a deleted module `ui.dialogs` as a hidden import | `pdf_toolkit.spec:75` | Open |
| TST-05 | ⚪ Info | tests | Crypto round-trip tests check only the 5-byte `%PDF-` magic | `tests/test_epdf_crypto.py:46` | Open |
| PKG-03 | 🔵 Plaus | build | UPX enabled for all binaries incl. Qt/WebEngine DLLs (frozen-build trap) | `pdf_toolkit.spec:118` | Open |

---

## 5. High-severity findings

### ENG-01 — Compression only ever re-compresses existing JPEGs 🔴
- **Location:** `engine.py:909` (decode); dead branches at `:946`, `:966`;
  swallowing `except` at `:1063`.
- **What:** `compress_images_smart` decodes every image with
  `Image.open(io.BytesIO(xobj.read_raw_bytes()))`. `read_raw_bytes()` returns the
  **still-PDF-filter-encoded** stream. Only self-describing formats decode that
  way — DCTDecode (literal JPEG), maybe JPXDecode. FlateDecode / CCITTFax / LZW /
  RunLength / indexed images raise `UnidentifiedImageError`, which is swallowed at
  `:1063`, so the image is skipped untouched.
- **Evidence:** `raw = bytes(xobj.read_raw_bytes()); img = Image.open(io.BytesIO(raw))`
  at `:908-909`. `_is_photographic` returns `True` for every JPEG at `:634`, so
  the only images that *do* open are always classified `is_photo=True` → the
  `elif not is_photo` (Flate-for-diagrams, `:966`) and the
  `if is_monochrome or bpc==1` (1-bit, `:946`) branches are unreachable.
- **Impact:** Every non-JPEG image — Flate "PNG-like" diagrams, screenshots,
  indexed images, and CCITT/Flate 1-bit scans — is **never recompressed or
  downscaled**. The engine only ever re-JPEGs existing JPEGs. Scanned and
  diagram-heavy PDFs get little/no image compression, with **no error surfaced**.
  The advertised "1-bit encoding / Flate for diagrams / smart format selection"
  features are inert on real inputs.
- **Fix:** Decode via `pikepdf.PdfImage(xobj).as_pil_image()` (or
  `read_bytes()` + `Image.frombytes` with the colorspace) so Flate/CCITT/LZW/
  indexed images actually decode and the downscale/Flate/B&W branches become
  reachable. **Note:** fixing this *activates* `ENG-05`, which must be fixed in
  the same pass. The same `read_raw_bytes` misuse recurs at `:537`/`:568` and in
  `_load_smask_image` (`:767`) — see `ENG-02`.
- **Verification:** CONFIRMED.

### ENG-02 — Soft mask deleted even when compositing failed 🔴
- **Location:** `engine.py:1052` (and sibling deletes at `:960`, `:988`,
  `:1018`); mask loader `_load_smask_image` at `:764`.
- **What:** Every re-encode branch deletes `/SMask` whenever the original had one,
  gated only on `smask_obj is not None` — **not** on compositing having
  succeeded. `_load_smask_image` reads the mask via `read_raw_bytes()` (same bug
  as `ENG-01`); for a standard FlateDecode soft mask `Image.open` fails and it
  returns `None`, so compositing is skipped — yet `/SMask` is still deleted.
- **Evidence:** compositing runs only `if mask_img is not None` (`:915`); the
  deletion `if smask_obj is not None and "/SMask" in xobj: del xobj["/SMask"]`
  fires regardless (`:1051-1052`).
- **Impact:** The base image is re-encoded (typically to alpha-less JPEG) and its
  alpha is permanently destroyed, baking previously-transparent regions in as an
  opaque rectangle. Reproduces for the common DCTDecode-base + FlateDecode-mask
  case.
- **Fix:** Decode the mask via `pikepdf.PdfImage(smask_obj).as_pil_image()`, and
  only `del xobj["/SMask"]` when compositing actually succeeded
  (`mask_img is not None`).
- **Verification:** CONFIRMED.

### TRN-03 — Non-Latin/Cyrillic source text returned untranslated 🔴 ✅ Fixed
- **Location:** `pdf_translate.py:309` (the letter gate in `translate_line`).
- **What:** `_LETTER_RE = re.compile(r'[A-Za-zÀ-ɏЀ-ӿ]')` matched **only** Latin
  (incl. Latin-1/Extended-A) and Cyrillic. In `translate_line`, any text part
  whose residual contained no match was appended **verbatim** and skipped
  (`if not _LETTER_RE.search(residual): out.append(part); continue`).
- **Impact:** When translating **from** Chinese, Arabic, Hindi, or Bengali — 4 of
  the 12 advertised source languages, none of which use Latin/Cyrillic — the gate
  never matched, so every line was returned unchanged. The operation reported
  success while producing the original text. (Greek, Hebrew, Thai, etc. were
  affected identically.)
- **Fix (applied):** Inverted the gate to a Unicode-aware test rather than an
  enumerated letter table — `if not any(ch.isalpha() for ch in residual)`. A
  fragment is skipped only when it holds no letter in *any* script (pure
  digits/punctuation/whitespace/separators, still skipped as before); real words
  in every script now reach the translator. `_LETTER_RE` (its only use) was
  removed; `_protect`/`_restore`, `_SEP_RE` splitting, and residual stripping are
  unchanged. Tests: `tests/test_pdf_translate.py::TestTranslateLineScriptGate`.
- **Verification:** CONFIRMED; now covered by tests (CJK/Arabic/Hindi/Bengali/
  mixed reach the translator; Latin still does; pure punctuation/numbers stay
  skipped).

### ANL-02 — Sanitiser leaves JavaScript in `/Next` action chains 🔴 ✅ Fixed
- **Location:** `pdf_analyze.py:778` (sanitiser annotation loop, `:775-796`);
  analyzer walk at `:282-290`.
- **What:** The analyzer follows `/Next` action chains and flags buried
  JavaScript/Launch/SubmitForm as `high`. The **sanitiser did not** — for each
  annotation it inspected only the top-level `/A`'s `/S` and never walked
  `a.get('/Next')`. An annotation whose `/A` is a benign `/URI` (kept by default,
  `external_links=False`) but whose `/Next` runs JavaScript was left fully intact
  even with `javascript=True`.
- **Impact:** `sanitize_pdf` reported success while the exact high-severity script
  the analyzer flagged survived — false security on a well-known malware evasion
  vector. Reproduced live during the audit (analyzer flags it; sanitiser returns
  `removed={}`; re-analysis still finds the JS).
- **Fix (applied):** Added `_action_head_drop()` and a recursive
  `_clean_action_next()`; the annotation loop now runs each `/A` **and each
  `/AA` trigger entry** through them, excising any node whose `/S` is
  `/JavaScript`/`/Launch`/`/SubmitForm`/`/ImportData` (or that carries `/JS`),
  gated on the matching opt, while preserving the benign head (e.g. a kept
  `/URI`) and any surviving tail. Dropped nodes bump the existing
  `annot_javascript`/`launch_action`/`submit_action` counters so the count is
  honest. Depth-capped at 30. Regression test:
  `tests/test_pdf_analyze.py::TestSanitizeNextChain`.
- **Verification:** CONFIRMED (code + live repro); now covered by an automated
  test that builds the `/URI`→`/Next`=`/JavaScript` evasion, sanitises it, and
  asserts the JS is gone while the link survives.

---

## 6. Medium & lower findings

### 🟠 Medium

#### ENG-03 — `q Q` regex can corrupt uncompressed content streams
- **Location:** `engine.py:1684` (`_optimize_content_streams`).
- **What:** `re.sub(rb'\bq\s+Q\b', b'', raw)` runs over the **untokenized** content
  stream; it doesn't skip `(...)`/`<...>` string literals or `BI…ID…EI`
  inline-image binary. A literal `q Q` inside a `Tj`/`TJ` string or inline-image
  data is deleted.
- **Impact:** Silent, persisted corruption of visible text or an inline image,
  written back when net savings exceed 16 bytes. **Only affects uncompressed
  content streams** (for FlateDecode streams `raw` is compressed binary, so the
  string-literal case can't match) — which narrows but doesn't eliminate it.
- **Fix:** Tokenise before removing empty `q/Q` pairs, or drop the
  micro-optimisation (pikepdf's stream recompression already yields the bulk of
  savings safely).
- **Verification:** CONFIRMED (reproduced empirically against pikepdf 10.5.0).

#### ENG-04 — Ghostscript pipes never drained → deadlock until timeout
- **Location:** `engine.py:1318-1349` (`compress_with_ghostscript`).
- **What:** `gs` is launched with `stdout=PIPE, stderr=PIPE`, but the poll loop
  only calls `proc.wait(timeout=2.0)` and never drains the pipes (stderr is read
  only after the loop). If `gs` writes more than the ~64 KB OS pipe buffer (font
  substitution / recoverable-error diagnostics that `-dQUIET` doesn't suppress),
  it blocks on the full pipe while the parent blocks in `wait()` — the classic
  Popen deadlock.
- **Impact:** Not an infinite hang — the 300 s timeout kills it — but a wasted
  ~5-minute stall on affected inputs, after which the GS pass is silently
  discarded (no font-subsetting benefit). No crash / data loss.
- **Fix:** Use `proc.communicate(timeout=…)` in the poll loop, or redirect
  stdout/stderr to temp files / `DEVNULL`.
- **Verification:** CONFIRMED.

#### OPS-02 — `flatten(forms=True, annotations=False)` leaves form-field values
- **Location:** `pdf_ops.py:1290-1309`.
- **What:** All `/Annots` manipulation is nested inside `if annotations and
  "/Annots" in page`. With `annotations=False, forms=True` (an independently
  reachable toggle combo — `FlattenPage.tsx` checkboxes → `bridge.py:1473-1474`),
  only `/AcroForm` is deleted; every `/Widget` annotation stays with its `/V`
  value and appearance stream.
- **Impact:** The "remove form fields" operation silently fails to remove the form
  data — values remain rendered and extractable — and the output is structurally
  inconsistent (orphaned widgets with no AcroForm).
- **Fix:** Remove `/Widget` annotations whenever `forms=True`, regardless of the
  `annotations` flag.
- **Verification:** CONFIRMED.

#### ANL-01 — In-place sanitise fails on Windows ✅ Fixed
- **Location:** `pdf_analyze.py:884` (`with pikepdf.open(...)`) / `:1033`
  (`os.replace`).
- **What:** The atomic write (`mkstemp` → `pdf.save(tmp)` → `os.replace`) ran
  **inside** the `with pikepdf.open(input_path) as pdf:` block. When
  `output_path == input_path` (in-place sanitise), `os.replace` overwrote a file
  pikepdf still held open → `PermissionError [WinError 5]` on Windows (the
  primary platform). In-place is reachable via the Save-As picker.
- **Impact:** User got a generic "Sanitize failed" toast; the original was
  preserved (no data loss, no false success), but the operation couldn't complete
  in-place. (POSIX was unaffected.)
- **Fix (applied):** Open with `pikepdf.open(input_path,
  allow_overwriting_input=True)` — it reads the input fully into memory and
  releases the OS handle, so the existing atomic write can `os.replace` over
  `input_path`. The atomic write (and its `os.unlink(tmp)`-on-failure cleanup) is
  unchanged. Regression test: `tests/test_pdf_analyze.py::TestSanitizeInPlace`.
- **Verification:** CONFIRMED; now covered by tests (in-place replace, distinct
  output, and simulated save-failure tmp cleanup / original untouched).

#### ANL-03 — Invisible-text ("failed redaction") detector largely non-functional ✅ Fixed
- **Location:** `pdf_analyze.py:620` (`_scan_invisible_text`), helper
  `_page_content_blobs` at `:590`.
- **What:** Two defects. (1) The dict/span branch was dead: the guard
  `span.get('flags', 0) & 0 == 0` was constant-true and the body was `pass`, so
  `found` was never set there. (2) The only working check read **only the first
  content stream** (`page.get_contents()[0]`) and matched only `b' 3 Tr'`
  or a stream ending in `3 Tr` — missing render-mode-3 text in later streams
  (common after incremental edits), `\n3 Tr`/other whitespace, and text inside
  form XObjects.
- **Impact:** False negatives on the security-relevant fake-redaction check; the
  `get_text("rawdict")` call feeding the dead loop was wasted work. Still fired in
  the narrow single-stream, space-prefixed case.
- **Fix (applied):** Removed the dead span loop (and its `rawdict` call); added
  `_page_content_blobs`, which concatenates **all** `page.get_contents()` streams
  plus the streams of any form XObjects the page references (image XObjects
  skipped); match the `3 Tr` operator with `_TR3_RE =
  re.compile(rb"(?:^|[\s])3\s+Tr\b")` so leading whitespace is any of
  space/newline/tab/CR and `"13 Tr"`/`"23 Tr"` don't false-positive. Regression
  test: `tests/test_pdf_analyze.py::TestInvisibleText`.
- **Verification:** CONFIRMED; now covered by tests (2nd-stream, `\n3 Tr`, form
  XObject → detected; `13 Tr` and clean → not flagged).

#### ANL-04 — Embedded-file sanitiser leaves `/FileAttachment` & `/AF` files ✅ Fixed
- **Location:** `pdf_analyze.py:918`/`:995` (sanitiser); detection
  `_scan_embedded_files` at `:486`, name-tree walk `_walk_ef_name_tree` at `:451`.
- **What:** With `embedded_files=True`, the sanitiser only did
  `del names["/EmbeddedFiles"]`. The annotation loop dropped annotations solely by
  `/A` → `/S` action type and never inspected `/Subtype`, so `/FileAttachment`
  annotations (which carry an embedded stream via `/FS` → `/EF`) were kept; `/AF`
  associated-file arrays were handled nowhere. A surviving annotation held an
  indirect reference to the stream, so deleting the name-tree entry didn't GC it.
  Detection also gated the object-scan fallback on `/Type == /Filespec`, which is
  optional and omitted by some producers.
- **Impact:** Annotation-borne embedded files survived `embedded_files=True`, and
  the "removed" report under-reported. (Detection's `/Kids` gap was mitigated — the
  object-scan fallback still caught those.)
- **Fix (applied):** *Detection* — `_scan_embedded_files` now treats any dict
  carrying `/EF` as a filespec regardless of missing `/Type`, and walks the
  `/EmbeddedFiles` name tree through `/Kids` via `_walk_ef_name_tree`. *Sanitiser*
  — when `embedded_files` is on it also drops `/Subtype == /FileAttachment`
  annotations (`file_attachment_annot` counter) and strips `/AF` arrays at the
  document root, on pages, and on annotations (`associated_file` counter); once
  those references are gone the embedded streams are unreferenced and dropped on
  `save`, so no `/EF` stream survives (verified, not just the name-tree entry).
  Regression test: `tests/test_pdf_analyze.py::TestEmbeddedFiles`.
- **Verification:** CONFIRMED; now covered by tests (annotation-borne file
  detected + payload gone after sanitise; no-`/Type` filespec; `/AF` strip;
  `/Kids` tree walked; clean PDF removes nothing).

#### TRN-01 — PDF→PDF translate aborts entirely on one undetectable block ✅ Fixed
- **Location:** `pdf_translate.py:600` (`_translate_pdf_to_pdf` block loop).
- **What:** Each block's `translate_text(...)` call was **not** wrapped in
  try/except (only the subsequent `_insert_autofit_text` was). In `source='auto'`
  (the default), a block that's a page number, a year (`2024`), or <3 chars fails
  `detect_language` → `_resolve_source` raises `TranslationError`, which propagated
  out and aborted the whole document — `out.save()` was never reached.
- **Impact:** Real PDFs routinely contain such short blocks, so default-mode
  PDF→PDF translation was fragile and produced **no output at all**. (The
  .txt/.docx path detects per whole page, so it's less exposed.)
- **Fix (applied):** Both AUDIT-suggested approaches combined. (1) Once a source
  is detected from an earlier block, subsequent blocks under `source='auto'` pass
  that `detected_source` to `translate_text` (`effective_source`), so short blocks
  that would fail auto-detection still translate using the known document
  language. (2) The per-block `translate_text` call is wrapped in
  `try/except Exception` (covers `TranslationError` + backstop): on failure the
  block's original text is copied through verbatim with a `log.debug`, so the loop
  continues and `out.save()` is still reached. `_insert_autofit_text`'s existing
  try/except and the .txt/.docx path are unchanged. Tests:
  `tests/test_pdf_translate.py::TestTranslatePdfBlockResilience`.
- **Verification:** CONFIRMED; now covered by a monkeypatched integration test
  (one block raises → document still saves, other block translated, number
  preserved).

#### BRG-01 — Worker cleanup keyed by `tool_key` breaks cancel after restart ✅ Fixed
- **Location:** `ui/bridge.py:327-345` (`_run_in_thread`'s `_on_finished`).
- **What:** `_on_finished` cleaned up via `self._workers.pop(tool_key)` /
  `self._cancel_events.pop(tool_key)` — by string, not by the captured `worker`
  identity. `_make_cancel_event` is explicitly designed to overlap same-key runs
  (signals the old event, installs a new one). After a cancel-then-rerun of the
  same tool, the *old* worker's finished handler ran after the *new* worker
  registered, so it popped the **new** worker/event out of the tracking dicts.
- **Impact:** `cancelOperation(tool_key)` then found no event and silently
  no-oped — the in-flight second run became permanently uncancellable. The
  frontend may also receive two `operationDone` payloads for one `tool_key`. UI
  logic race, not a crash (parent keeps the worker alive).
- **Fix (applied):** Captured this run's cancel event (`my_evt`) at dispatch and
  guarded both pops on identity — `_on_finished` removes a tracking-dict entry
  only when `self._workers.get(tool_key) is worker` / `self._cancel_events.get(
  tool_key) is my_evt`, so a stale old-worker finish leaves the newer run's
  worker/event intact and cancellable. `_make_cancel_event`'s overlap design and
  `cancelOperation` are unchanged. The double-`operationDone` on overlap is
  inherent and left as-is (the critical fix is not evicting the new run's cancel
  event).
- **Verification:** CONFIRMED by inspection; not unit-tested because
  `_run_in_thread` is bound to the Bridge QObject + `_Worker` QThread and
  importing `ui/` pulls the full PySide6 stack (CLAUDE.md keeps the test suite
  Qt-free). Real-app check: cancel a long op, immediately rerun the same tool,
  confirm the rerun is still cancellable.

#### CLI-01 — CLI always exits 0 even when files fail
- **Location:** `compress_pdf.py:214` (`main` fall-through).
- **What:** `main()` tallies failures in `n_err` (invalid magic, encrypted,
  invalid, too-large, catch-all) but never calls `sys.exit()` on that count. It
  returns `None` → process exits 0 regardless of failures.
- **Impact:** Any chained/scripted use (`python compress_pdf.py *.pdf -o out/ &&
  next_step`, the documented batch pattern; `--no-pause` exists for exactly this)
  treats an all-failed run as success.
- **Fix:** After the summary, `sys.exit(1 if n_err else 0)`.
- **Verification:** CONFIRMED.

#### FE-01 — Drag-drop not scoped to the active page ✅ Fixed
- **Location:** `web-react/src/components/shared/DropZone.tsx:66`.
- **What:** Under AppShell keep-alive every visited tool page stays mounted. Each
  mounted `DropZone` subscribed to the **global** `files-dropped` EventBus signal
  with no active-page gate. One OS drop emitted one global event → **every**
  mounted DropZone appended the file to its own page's list. This was the exact
  keep-alive scoping hazard `useHotkeys` was fixed for via `usePageActive()` —
  DropZone never got the guard.
- **Impact:** Dropping a file while viewing Compress (after visiting Merge/Split)
  silently staged it on those pages too; the user might later run an operation on
  files they never intended. Manifested in the default (no-workspace) mode.
- **Fix (applied):** `DropZone` now calls `usePageActive()` and the
  `onFilesDropped` subscription early-returns when the page isn't active, so only
  the visible page subscribes — mirroring `useHotkeys` exactly (same gate, same
  `pageActive` dep-array entry). The browse/manual-add and drag-over visual
  handlers are unchanged. `dist/` rebuilt.
- **Verification:** CONFIRMED. (No unit test on this keep-alive path — validated
  by build + a real-app check: a drop on one page must not appear on other
  visited pages.)

#### TST-01 — Redaction (data-destruction) has zero test coverage ✅ Fixed
- **Location:** `pdf_ops.py:1517` (`redact_pdf`).
- **What:** `redact_pdf` guarantees stripped text "isn't recoverable" and contains
  subtle security-critical logic (case-sensitive re-extraction filter
  `:1591-1593`; AcroForm widget neutralisation `:1615-1622`, added to fix a
  documented real leak; `PDF_REDACT_IMAGE_REMOVE` `:1624`). A repo-wide grep for
  `redact` in `tests/` returned nothing.
- **Impact:** A regression to painting-over (or skipping the widget/image path)
  would leave "redacted" text fully extractable while reporting
  `redaction_count` success — and CI would stay green. No present-day defect; it
  was the highest-value **untested** path in the module.
- **Fix (applied):** Added `tests/test_pdf_ops.py::TestRedactPdf` (fitz-gated,
  test-only — `redact_pdf` unchanged). Covers: redacted term absent from
  `get_text()` **and** from the decoded content-stream bytes (the painting-over
  guard); `redaction_count`/`pages_affected` correctness; the `case_sensitive`
  re-extraction filter (exact-case removed, other case survives; case-insensitive
  removes both); the AcroForm case — a redaction rect over a text widget removes
  its `/V` and makes the value non-extractable; and `ValueError` with neither
  `search_terms` nor `rects`. All pass — the guarantees hold today.
- **Verification:** CONFIRMED; now covered by automated tests.

#### TST-02 — Path-containment guard `contained_output_path()` untested ✅ Fixed
- **Location:** `pdf_ops.py:22-40`; callers `bridge.py:1064/1144/1325`,
  `compress_paths.py:45`, `pdf_ops.py:295`.
- **What:** This is the sole guard stopping a user-editable naming template /
  output name from escaping the chosen folder (absolute path that `os.path.join`
  honours, or `../` traversal). No test asserted it raises on escapes/absolute
  names. Happy-path was covered indirectly; the security-critical negative path
  was not.
- **Impact:** A regression dropping the `commonpath` check would reintroduce an
  arbitrary-file-write / traversal vulnerability undetected.
- **Fix (applied):** Added `tests/test_pdf_ops.py::TestContainedOutputPath` (pure,
  Qt-free): a plain name returns a path inside the folder; a `sub/` name stays
  contained (guard isn't over-eager); `../../../etc/passwd` raises `ValueError`;
  and an absolute `out_name` (`os.path.abspath(os.sep + …)`, absolute on the
  running OS) raises `ValueError`. All pass.
- **Verification:** CONFIRMED; now covered by automated tests.

#### RED-01 — Redaction destroys entire page on image-only (scanned) PDFs ✅ Fixed
- **Location:** `pdf_ops.py:1649` (`apply_redactions(images=…)` in `redact_pdf`).
- **What:** On a page whose content is a single full-page image (scanned docs — `get_text()` empty, `get_images()` == 1), any redaction rect overlapping the image caused `PDF_REDACT_IMAGE_REMOVE` to remove the entire image, not just the region under the rect. The whole page is that image, so the result was blank.
- **Impact:** Redacting a box on a scanned PDF wiped the page while reporting `redaction_count` success. Scanned docs are a primary redaction use case, so this was a high-value silent failure.
- **Repro:** Direct `redact_pdf` call on `tests/test1.pdf` (images:1, text:0) with a small rect returned `redaction_count=1` but a blank output page. Discovered during FE-03 real-app smoke testing.
- **Fix (applied):** Changed the mode to `fitz.PDF_REDACT_IMAGE_PIXELS`, which blacks out only the pixels under each redaction rect and leaves the rest of the image intact (no whole-image-remove branching needed; `_PIXELS` universally). PyMuPDF pin `>=1.23.0` supports it. Text-redaction, AcroForm widget neutralisation, and everything else unchanged. Regression test: `tests/test_pdf_ops.py::TestRedactPdf::test_image_only_page_only_boxed_region_redacted` (builds a full-page image, redacts a small rect, asserts the boxed pixels are black while pixels outside stay intact and the image isn't removed).
- **Status:** Fixed.

### 🟡 Low

#### ENG-05 — Size check compares uncompressed candidate vs compressed original
- **Location:** `engine.py:951` (B&W), `:979` (Flate diagram).
- **What:** `len(bw_data) < info.raw_size` / `len(raw_pixels) < info.raw_size`
  compare **pre-compression** candidate bytes (packed bits / raw RGB, which
  pikepdf will still Flate on write) against `info.raw_size`, the **already-
  compressed** original stream length. Apples-to-oranges.
- **Impact:** The B&W accept/reject decision doesn't measure real post-Flate
  savings, and the diagram primary branch rarely fires (uncompressed RGB usually
  exceeds the compressed original), routing diagrams to the lossy JPEG fallback.
  Conservative (never bloats). **Currently latent** because these branches are
  unreachable per `ENG-01` — but becomes live the moment `ENG-01` is fixed, so fix
  both together.
- **Fix:** Compress the candidate (`zlib.compress`) and compare that length, or
  compare actual written stream sizes.
- **Verification:** CONFIRMED.

#### ENG-06 — Hardcoded `is_tiny` (<64px) overrides per-preset `skip_below_px`
- **Location:** `engine.py:452-453`, `:721`.
- **What:** `_should_skip` returns "tiny" when `info.is_tiny or max(w,h) <
  preset.skip_below_px`. `is_tiny` is hardcoded `<64`, and all presets set
  `skip_below_px ≤ 64` (64/48/32/24/16), so the OR always subsumes the preset
  clause — the finer per-preset thresholds never take effect.
- **Impact:** Dead configuration; no corruption. `estimate_output` inherits the
  same behaviour (self-consistent).
- **Fix:** Drop the hardcoded `is_tiny` clause and rely on `preset.skip_below_px`,
  or raise the preset thresholds where a larger floor is intended.
- **Verification:** CONFIRMED.

#### OPS-01 — `protect_pdf` sets owner password = user password
- **Location:** `pdf_ops.py:430`; caller `ui/bridge.py:1019/1073`.
- **What:** `owner=owner_password or user_password`. PDF permission flags are only
  enforceable against someone **without** the owner password. When a user sets an
  open password + restrictions but no owner password, `owner == user`, so anyone
  who can open the file (they hold the user password) has owner rights and can
  strip every restriction.
- **Impact:** Real and reachable from the UI, and the tool reports success. But
  largely **inherent PDF-permission behaviour** (any non-compliant tool strips
  permission bits regardless), and the empty-owner / no-open-password variant is
  **not** reachable (the UI requires a user password). Only hardens against
  compliant readers.
- **Fix:** When restrictions are set but no distinct owner password is supplied,
  generate a strong random owner password (or require one) — with the caveat that
  this only defends against compliant readers.
- **Verification:** CONFIRMED. (Finder rated Medium; downgraded to Low.)

#### OPS-03 — `add_watermark` leaks open file handle on malformed input
- **Location:** `pdf_ops.py:823`; validations at `:826-827` (`_parse_ranges`) and
  `:835-836` (hex color) run **after** open, **before** the try/except at `:918`.
- **What:** A malformed `page_range` (out-of-bounds/non-numeric) or `color`
  (`#88`, `red`) raises with `src` never closed.
- **Impact:** On Windows the un-closed pikepdf handle keeps the input open until
  GC — can block a retry/follow-up on the same file. Narrow: under CPython
  refcounting the handle is reclaimed once the bridge releases the traceback. Same
  open-before-validate shape exists in `add_page_numbers`/`apply_page_operations`.
- **Fix:** Validate `page_range`/`color` before `pikepdf.open`, or wrap the body in
  try/except that closes `src` on any failure.
- **Verification:** CONFIRMED.

#### OPS-04 — `split_pdf` filename collisions silently overwrite
- **Location:** `pdf_ops.py:290-303`; `_sanitize_title` at `:191-195`.
- **What:** Each group's output name comes from `name_template.format(...)` and is
  saved with no collision check. Two groups formatting to the same name overwrite
  each other. Realistic in chapters mode: `_sanitize_title` truncates to 80 chars
  and maps empty/unsafe titles to `untitled`, and the default chapters template
  (`{filename}_{title}`) has no `{n}`, so any PDF with a repeated TOC title
  (Introduction, Summary, References…) collides and the later chapter clobbers the
  earlier one, while the UI reports success. `output_paths` then lists a duplicate.
- **Impact:** Silent missing-output / miscount. Source PDF untouched; user can add
  `{n}` and re-run.
- **Fix:** Detect duplicate `out_path` within the run and disambiguate (append the
  group index) before saving.
- **Verification:** CONFIRMED.

#### OPS-05 — `images_to_pdf` re-encodes to JPEG q92 despite "preserves quality"
- **Location:** `pdf_ops.py:543` (docstring), `:592-593` / `:606` (behaviour).
- **What:** The docstring says it "preserves image quality", but every input —
  including lossless PNG/TIFF and already-compressed sources — is unconditionally
  re-encoded to lossy JPEG q92 (DCTDecode). No lossless path exists.
- **Impact:** Lossless inputs gain artefacts; JPEGs get a second lossy pass. q92 is
  high, output usable — a quality/expectation mismatch, not a crash.
- **Fix (product call):** Either embed lossless sources losslessly (FlateDecode),
  or correct the docstring to state images are re-encoded as JPEG q92.
- **Verification:** CONFIRMED.

#### CRY-01 — Decrypt raises non-`EPDFError` types on malformed headers
- **Location:** `epdf_crypto.py:421` (`version = int(...)`), `:442-443`
  (`b64decode(metadata["salt"/"nonce"])`).
- **What:** Attacker-controllable header fields are read with unguarded ops: a
  non-numeric `version` → `ValueError`; a missing `salt`/`nonce` key → `KeyError`;
  non-base64 content → `binascii.Error`. None are wrapped as `EPDFError`, unlike
  `epdf_read_metadata` which normalises bad input to `EPDFFormatError` (and a test
  asserts `EPDFError` on a tampered header).
- **Impact:** Callers relying on `except EPDFError` to distinguish "bad file" from
  a programming bug get the wrong type. Not exploitable, no crypto weakness (any
  parsed-but-tampered field still fails AEAD/HMAC); the bridge catches
  `Exception` generically so no crash. Consistency/hardening gap.
- **Fix:** Wrap the salt/nonce lookup+`b64decode` and the `version` parse in
  try/except and re-raise as `EPDFFormatError`.
- **Verification:** CONFIRMED.

#### CRY-02 — Non-dict `kdf_params` bypasses validation → uncaught `TypeError`
- **Location:** `epdf_crypto.py:134` (`_derive_key`), fed from `:444`.
- **What:** `params = _validate_kdf_params({**DEFAULT_KDF_PARAMS, **(kdf_params or
  {})})` — if a crafted header sets `kdf_params` to a non-mapping (JSON list /
  string), the `**` spread raises `TypeError` **before** `_validate_kdf_params`
  runs, escaping its clean `EPDFFormatError` path.
- **Impact:** Same `EPDFError`-contract gap as `CRY-01`; fails fast before Argon2
  (no DoS/OOM), bridge prevents a crash — only a raw error message.
- **Fix:** Assert `isinstance(kdf_params, dict)` (raise `EPDFFormatError`
  otherwise) before the merge.
- **Verification:** CONFIRMED.

#### TRN-02 — Temp PNG leak if `pix.save` fails ✅ Fixed
- **Location:** `pdf_translate.py:450` (`_extract_pages`, OCR fallback).
- **What:** `mkstemp()` creates the file, then `pix.save(tmp)` ran **before** the
  try/finally that guarantees `os.unlink(tmp)`. If `pix.save` raised (disk full,
  PyMuPDF error), the temp file was orphaned; the outer handler logged but didn't
  clean up.
- **Impact:** One leaked temp PNG per failing page; accumulates on a large scanned
  doc where saves repeatedly fail. Offline app, no security angle.
- **Fix (applied):** Moved `pix.save(tmp)` inside the try so the finally always
  runs; `mkstemp`/`os.close(fd)` stay before it so `tmp` is defined for the
  finally. Regression test:
  `tests/test_pdf_translate.py::TestExtractPagesTempCleanup` (monkeypatches
  `fitz.Pixmap.save` to raise and asserts no temp PNG is left behind).
- **Verification:** CONFIRMED; now covered by an automated test.

#### BRG-02 — `deleteFile`/`copyFile` lack workspace-dir path containment ✅ Fixed
- **Location:** `ui/bridge.py:851` (`deleteFile`), `:873` (`copyFile`); helper
  `is_within_directory` at `pdf_ops.py:43`.
- **What:** `deleteFile`'s docstring scoped it to "a workspace-superseded temp
  file", but the body did `if path and os.path.isfile(path): os.remove(path)`
  with no check that `path` was inside `self._workspace_dir`. `copyFile` likewise
  had no containment on its source. `contained_output_path` existed for exactly
  this but wasn't applied.
- **Impact:** Defense-in-depth gap, **not** a demonstrated vuln: the only caller is
  the committed frontend which passes only workspace-dir paths; `os.remove` is
  guarded by `isfile` (no dir/glob); `copyFile`'s dest is a user-chosen export
  path. Unrelated to the network/offline invariant despite the finder's category.
- **Fix (applied):** Added a Qt-free boolean guard `is_within_directory(path,
  base)` in `pdf_ops.py` (realpath + `commonpath`, never `startswith`, returns
  `False` — never raises — on escape or a cross-root `commonpath` ValueError).
  `deleteFile` now refuses (`{"success": False, "error": "refused: path outside
  workspace"}`) unless `self._workspace_dir` is set and `path` is within it — the
  "missing file is not an error" behaviour is kept for in-workspace paths.
  `copyFile` requires `src_path` within the workspace; `dest_path` is left
  deliberately unconstrained (the user's export sink). Tests:
  `tests/test_pdf_ops.py::TestIsWithinDirectory` (incl. the sibling-prefix trap).
- **Verification:** CONFIRMED; helper now covered by automated tests.

#### BRG-03 — Slot param parse runs outside the worker try/except → UI hangs ✅ Fixed
- **Location:** `ui/bridge.py:722` (`startTranslateText`), `:744`
  (`startTranslateImage`).
- **What:** These slots read required keys (`p["text"]`, `p["path"]`,
  `p["target"]`) on the UI thread **before** `_run_in_thread`. Only `_Worker.run`
  wraps the work body in try/except. A missing key → `KeyError` in the slot;
  since no worker started, no `operationDone` was emitted and `useOperation`
  (which has no timeout) left the spinner pending forever.
- **Impact:** Non-normal path (a frontend bug passing a missing key); malformed
  JSON is near-impossible since `JSON.stringify` produces it. Contrast `startMerge`,
  which reads its keys inside `_work` and so reports a failed `operationDone`.
- **Fix (applied):** Moved the required-key extraction (`p["text"]`/`p["path"]`,
  `p["target"]`, and the `.get()` optionals) INSIDE each slot's `_work` closure,
  mirroring `startMerge`. A missing key now raises in the worker, where
  `_Worker.run`'s try/except emits `operationDone(success=False, …)` and the
  frontend resolves with an error instead of hanging. `_normalize_params`,
  `tool_key` (`.get` with default), and `_make_cancel_event` stay at the slot
  head (they don't raise on a missing tool key). `_run_in_thread`,
  `_make_cancel_event`, `startMerge`, and the translate functions are unchanged.
- **Verification:** CONFIRMED by inspection; not unit-tested because importing
  `ui/` pulls the full PySide6 stack (the test suite is kept Qt-free, same
  constraint as BRG-01). The pattern now matches `startMerge`.

#### CLI-02 — Batch: same-basename inputs overwrite each other's output
- **Location:** `compress_pdf.py:128-130`.
- **What:** In `-o <dir>` mode the output name is `<basename>_compressed.pdf`,
  keyed only on basename. `a/report.pdf` and `b/report.pdf` both resolve to
  `out/report_compressed.pdf`; the second silently overwrites the first. No
  collision guard.
- **Impact:** Lost **derived** output (regenerable), not source data (inputs and
  outputs live in different dirs). No backup fires in `-o` mode (backup only on
  in-place).
- **Fix:** Detect collisions across the batch's resolved output paths and
  disambiguate (append a counter) or error before overwriting.
- **Verification:** CONFIRMED. (Finder rated Medium; downgraded to Low.)

#### CLI-03 — `input()` at exit raises `EOFError` on non-interactive stdin
- **Location:** `compress_pdf.py:210-211`.
- **What:** Without `--no-pause`, `main()` ends with a blocking `input("Press
  Enter…")` with no `try/except` or `isatty()` guard. Piped/redirected/CI stdin
  raises `EOFError` → traceback + non-zero exit. Ironically the only path that
  exits non-zero, so a real compression failure exits 0 while an environmental
  non-TTY exits 1.
- **Fix:** Wrap `input()` in `try/except EOFError`, or skip the pause when
  `not sys.stdin.isatty()`.
- **Verification:** CONFIRMED.

#### CLI-04 — Not-found inputs omitted from summary / failure tally
- **Location:** `compress_pdf.py:115-117`.
- **What:** A non-existent input prints a `SKIP` line and `continue`s without
  incrementing `n_ok`/`n_skip`/`n_err` — inconsistent with the invalid-magic case
  just below (which does `n_err += 1`). Missing files never appear in `Summary:`
  and (combined with `CLI-01`) are invisible to any count/exit-status check.
- **Fix:** Increment `n_err` (or a dedicated `n_missing`) for not-found inputs.
- **Verification:** CONFIRMED.

#### FE-02 — Workspace risk badge not refreshed after a transform
- **Location:** `web-react/src/workspace/WorkspaceContext.tsx:123` (`applyResult`).
- **What:** The scan-on-load result (`scan` state) is set only in `load` and reset
  in `clear`. `applyResult` (which repoints `path` to each tool's output) never
  re-scans or resets `scan`. So after any transform (watermark, compress, flatten,
  redact…) the WorkspaceBar still shows the **originally-loaded** document's
  findings.
- **Impact:** A security-advisory surface goes stale — e.g. a Flatten that strips
  JS/forms still shows the old "⚠ risks found" badge, and AnalyzePage (which does
  re-scan) disagrees with the bar. Dominant direction is harmless over-warning.
- **Fix:** Re-run `analyzeDocument` inside `applyResult` (guarded by `scanPathRef`
  like `load`), or reset `scan` to a neutral state. *(Requires a `dist/` rebuild.)*
- **Verification:** CONFIRMED.

#### FE-03 — `RedactPage` advances workspace with an unguarded `output_path` ✅ Fixed
- **Location:** `web-react/src/pages/tools/RedactPage.tsx:181-200` (done handler).
- **What:** The done handler called `workspace.applyResult(output_path, …)` with
  `output_path` read out of the **backend result** (`op.result.results`) — a value
  that round-tripped through the bridge — rather than the known-good path the
  frontend itself computed via `workspaceOutputPath(wsDir, wsPath, opIndex)`. It
  also didn't guard truthiness, unlike every sibling page.
- **Impact:** Defensive-consistency gap; **effectively unreachable** today (the
  backend echoes the caller-supplied path and any write failure raises → status
  'error', not 'done').
- **Fix (applied):** `confirmRedact` now stashes the frontend-computed `outPath`
  in `workspaceOutPathRef` when the workspace run launches; the done handler
  advances the workspace with **that** trusted path (never the backend echo),
  guarded on truthiness (failure toast otherwise). Counts for the toast still
  come from the backend result. The non-workspace file-output branch is
  unchanged. `dist/` rebuilt.
- **Verification:** CONFIRMED. (No unit test on this path — validated by build +
  a real-app redact→advance check.)

#### TST-03 — Password protect/unlock round-trip untested
- **Location:** `pdf_ops.py:408` (`protect_pdf`), `:452` (`unlock_pdf`).
- **What:** Both are wired to the bridge (`:1073`, `:1150`) but have no test. The
  crypto suite covers only the separate `.epdf` format; the pikepdf-based
  protect/unlock path has no round-trip or wrong-password test.
- **Impact:** Pure coverage gap (code reads correct). A silent regression could
  leave a file unencrypted while the UI reports success.
- **Fix:** Integration test: protect with a user password, assert
  `pikepdf.open(out)` raises without it and opens with it; unlock back and assert
  it opens without a password.
- **Verification:** CONFIRMED. (Finder rated Medium; downgraded to Low.)

#### TST-04 — Backup-on-overwrite test asserts nothing when compression skips ✅ Fixed
- **Location:** `tests/test_engine.py:238-241`.
- **What:** The only assertions (`backup_path is not None`; file exists) are inside
  `if not result.skipped:`. If the fixture ever compresses to no gain, the test
  passes verifying nothing. (In practice the backup is created unconditionally
  before the skip decision, so the assertions would actually still hold — the
  guard is both risky and unnecessary.)
- **Fix:** Assert backup behaviour unconditionally (or add an explicit skip-branch
  case).
- **Verification:** CONFIRMED.
- **✅ Fixed** — the `if not result.skipped:` guard is removed; the test now asserts
  `backup_path is not None` and the file exists unconditionally, and additionally
  checks the backup is a real copy (`getsize(backup) == getsize(original)`). This
  is safe because the engine creates the backup unconditionally at
  `engine.py:1763` (before the skip branch) and both return paths carry
  `backup_path`. Verified: the un-guarded assertions pass, confirming the backup
  is genuinely created whether or not compression skips.

#### DOC-01 — README advertises a removed Windows context-menu + About dialog
- **Location:** `README.md:180`.
- **What:** The GUI features list still says: *"Windows context menu — register
  'Compress with PDF Compress' in the Explorer right-click menu (via About
  dialog)"*. Neither exists — both belonged to the native-Qt widget UI deleted in
  v4.21. Repo-wide greps for `winreg`/`HKEY_`/context-menu/About-dialog code
  return nothing.
- **Impact:** A reader looks for a feature the shipping React app doesn't provide.
- **Fix:** Remove the bullet (and the "via About dialog" reference), or
  re-implement if it's meant to ship.
- **Verification:** CONFIRMED. (Finder rated Medium; downgraded to Low — pure
  doc-drift.)

#### DOC-02 — CHANGELOG documents a "stanza" upgrade for a never-present dep
- **Location:** `CHANGELOG.md:115`.
- **What:** The v4.20 entry claims *"Upgraded stanza to resolve CVE-2026-54499…"*.
  The translation stack is Argos + Tesseract + langdetect; `stanza` appears
  nowhere in the code or dependency files — the only repo occurrence is this line.
- **Impact:** A security-relevant changelog line references a dependency the
  project never used — misleading, no runtime impact.
- **Fix:** Correct or remove the stanza/CVE bullet.
- **Verification:** CONFIRMED.

#### PKG-01 — `assets/fonts/DejaVuSans.ttf` not bundled in the spec
- **Location:** `pdf_toolkit.spec:25-27` (`datas`); loader
  `pdf_translate.py:473-523`.
- **What:** `pdf_translate` resolves its font dir relative to `__file__` and
  prefers the committed `DejaVuSans.ttf` for image-preserving translated-PDF
  output. The spec's `datas` bundles only `web-react/dist` — not `assets/fonts/` —
  so in the frozen build the bundled font is absent and the loader falls through
  to OS fonts, then to Latin-only `helv` with only a warning.
- **Impact:** Defeats the advertised "portable across machines" guarantee.
  Overstated in practice on the primary platform: the Windows fallback
  `C:\Windows\Fonts\arial.ttf` covers Latin+Cyrillic+Greek and is present on
  virtually all installs, so output usually still renders — unless arial **and**
  tahoma are both missing.
- **Fix:** Add the fonts dir to `datas`:
  `(os.path.join(PROJECT_ROOT, "assets", "fonts"), os.path.join("assets", "fonts"))`.
- **Verification:** CONFIRMED. (Finder rated Medium; downgraded to Low.)

#### PKG-02 — Spec lists a deleted module `ui.dialogs` as a hidden import
- **Location:** `pdf_toolkit.spec:75`.
- **What:** `hiddenimports` still includes `"ui.dialogs"`, but `ui/dialogs.py` was
  deleted in v4.21. The only repo reference to `ui.dialogs` is this line.
- **Impact:** PyInstaller emits a "hidden import 'ui.dialogs' not found" warning;
  non-fatal, no runtime effect. Stale-config drift (independently surfaced by both
  the docs and deps finders). The app-module block is also somewhat redundant —
  `pdf_analyze`/`pdf_translate`/`net_guard` are pulled in transitively.
- **Fix:** Delete the `"ui.dialogs"` entry (optionally trim the redundant
  app-module block).
- **Verification:** CONFIRMED.

### ⚪ Info

#### TST-05 — Crypto round-trip tests check only the 5-byte `%PDF-` magic
- **Location:** `tests/test_epdf_crypto.py:46/57/69` (round-trip), `:92` (size).
- **What:** The per-cipher round-trip tests assert only that the decrypted file's
  first 5 bytes are `b"%PDF-"`; no test asserts full byte equality vs the original.
- **Impact:** Minimal — content fidelity is already guaranteed by the runtime
  AEAD/HMAC tag (any framing/offset error changes the AAD → auth failure →
  `EPDFPasswordError`, failing the test at decrypt), so a byte-equality assertion
  is largely redundant. Test-hygiene only.
- **Fix:** In each round-trip test, assert
  `open(dec,'rb').read() == open(sample_pdf,'rb').read()` (subsumes magic + size).
- **Verification:** CONFIRMED.

### 🔵 Plausible

#### PKG-03 — UPX enabled for all binaries incl. Qt/WebEngine DLLs
- **Location:** `pdf_toolkit.spec:118` (EXE), `:130-131` (COLLECT,
  `upx_exclude=[]`).
- **What:** `upx=True` with an empty `upx_exclude` compresses every bundled binary,
  including the PySide6 Qt6 DLLs and the QtWebEngine runtime. UPX-compressing
  Qt/WebEngine DLLs is a well-documented PyInstaller failure mode (app crashes on
  launch / "could not load the Qt platform plugin"). This is a QWebEngine app —
  the highest-risk case.
- **Impact:** **Conditional** — `build.bat` never installs UPX and CI has no
  frozen-build job, so when UPX isn't on PATH `upx=True` is a silent no-op. The
  broken build only happens on a dev machine that independently has UPX installed;
  the failure is then version-dependent and hard to reproduce.
- **Fix:** Set `upx=False` for a Qt/WebEngine app, or keep UPX but exclude the Qt
  binaries via `upx_exclude` (e.g. `Qt6WebEngineCore.dll`, `Qt6Core.dll`,
  `QtWebEngineProcess.exe`, `python3*.dll`).
- **Verification:** PLAUSIBLE (real config trap; can't reproduce the crash without
  UPX + a build; downgraded from Medium to Low).

---

## 7. Rejected candidates — do not re-report

These were proposed during the audit and **dismissed on verification.** Recorded
so they aren't rediscovered and re-filed.

| ID | Proposed | Why rejected |
|----|----------|--------------|
| R-01 | Stale image dict keys (`/Decode`, `/Mask`, `/ImageMask`) not cleared on re-encode → color inversion | Mechanism is backwards: PIL decodes JPEG samples and never applies `/Decode`, so **keeping** `/Decode` is correct and the proposed "delete it" fix would *introduce* inversion. The cited 1-bit/stencil examples can't even be opened by the current decode path (`ENG-01`). `/ColorKeyMask` isn't a real PDF key. |
| R-02 | `redact_pdf` user-drawn rects: coordinate space mislabeled / rects outside `pages` filter dropped | Both refuted. The Draw-boxes UI is already wired and coordinate-correct (top-left origin, documented "no axis flip"). The only caller never sends a `pages` subset, so `pages` is always `None` → every page's rects apply. Purely hypothetical for a nonexistent caller. |
| R-03 | README Files table omits `build.bat` / `pdf_toolkit.spec` | The table is a deliberately curated subset that consistently omits build/packaging config and meta-docs (it also omits `pyproject.toml`, `LICENSE`, `.gitignore`, etc.) and never claims to be exhaustive. No drift; a subjective completeness preference. |
| R-04 | `numpy`/`python-docx` "core in requirements.txt but extras in pyproject" mismatch | Misread: `requirements.txt` annotates both lines `# optional`. All files agree in intent (optional); every code path degrades gracefully; the documented build installs both via `requirements.txt`. Cosmetic classification note about a hypothetical non-standard install. |

---

## 8. Appendix — reproducing / extending this audit

- The audit was produced by a multi-agent workflow (12 subsystem finders → per-
  finding adversarial verification). It reads the source; it does not run the app.
- **Gaps to close if repeating:** run the app end-to-end (compression on real
  photo/scan/diagram/transparent PDFs; translation with Argos/Tesseract models
  installed; a frozen PyInstaller build with UPX present) — the static pass could
  not exercise these.
- Findings here are anchored to `331b69f`. Line numbers will drift as the code
  changes; treat the **file + description** as the stable anchor.

---

*This is a point-in-time snapshot. When a finding is fixed, update its Status in
§4 and note the fix in `CHANGELOG.md` per the repo's doc-currency rules.*
