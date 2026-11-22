# Changelog
## Unreleased

### Added
- **Redaction: a result says what the run did not look at (#119).** A
  redaction that verifies has removed what it was asked to remove from
  everything it can read. It has not read a picture. `RedactResult.notes`
  carries the plain facts about that, for the app to word; none is a
  failure. One entry per kind, each a small dictionary with page numbers
  counted from 1: `no_text` — searched pages that draw something, none of it
  text (a scan with no text layer, type drawn as outlines): a search finds
  no words there; `text_layer` — pages that show a picture and whose text is
  mostly drawn in the invisible mode, the way OCR lays what it read over a
  scan, with the number of hits on each: the search read that layer, and a
  word the layer got wrong is still in the picture; `attachments` — the
  attachments in the output by name, and which of them hold their contents
  packed (zip, docx, xlsx, gzip, 7-Zip, another PDF): an attachment is
  searched as stored and never opened; `flattened` — the pages turned into
  pictures and the areas filled on each; `boxes_only` — a run by boxes
  alone, which searched for nothing. A search that finds nothing at all has
  no result to carry a note, so "No matching content found to redact." now
  goes on to name the pages that had no text to search. The bridge sends a
  result as it is, so the notes reach the app with no change there; wording
  them is the app's (next cluster). New: `pdf_analyze.embedded_files`. Not
  noticed: text hidden by a picture drawn over it, rather than by the
  invisible mode. Tests: `tests/test_redact_notes.py`.
- **Redaction: a whole-file search for every target term (#119).** A term
  redaction could finish verified with the term still in the file, in a
  place the verifier's surface checks have no name for: structure-tree
  `/Alt` and `/ActualText`, an application's private data (`/PieceInfo`), a
  layer's name, a named destination, a script in `/Names /JavaScript`, a
  metadata stream on a page, a form XObject no page paints. `verify_redaction`
  now takes a second look, `redaction.term_sweep`: every string in the output
  and the decoded bytes of every stream, wherever it hangs, using the
  whole-file walker the sanitize sweep uses. Bytes are read every way text is
  written in a PDF — Latin-1, PDFDocEncoding, code page 1252, UTF-8, UTF-16
  in both byte orders and at either alignment — and whitespace in a term
  matches any run of whitespace (`pdf_verify.TermFinder`). A hit fails the
  check, with where it is in plain words and the raw object path beside it.
  A search that could not cover the whole file — an object or a stream that
  cannot be read — fails it too, and the refusal then says so instead of
  "still present". A hit on an object a page leads to names the page and
  offers the flatten (which rebuilds the document from its pages, so it
  removes it); any other is document-level. Boxes-only redactions are not
  swept. Not read, and why: images (pixels; that is #128); PDF names; and the
  internals of fonts and colour profiles — embedded font programs, character
  maps, a font descriptor's `/FontFamily`, a character collection's
  `/Registry` and `/Ordering`, ICC profiles. In 521 real PDFs that redact
  today, the word "Adobe" is in a character map or a `/Registry` string in
  249 and "Identity" in 219, and "Copyright" is in a colour profile in 46:
  searching those would refuse the words in every such file. They are exempt
  by what the holder declares itself to be, never by a key name alone. Known
  limits, each needing a file built for it: a term that is (part of) a font's
  or profile's own name, or a PDF name; text behind an object that declares
  itself an image, a font descriptor's program or an ICC profile; a term
  written as a hex string or split across strings inside a stream no page
  paints; and a very short term can be refused because its bytes occur by
  chance in binary data. Cost on the real-file run (554 files with a word to
  redact): 60 s → 72 s in total.
  Tests: `TestTermSweep`, `TestWhatTheSweepLeavesAlone`,
  `TestSweepNeverPassesOnAPartialLook` in `tests/test_redact_completeness.py`.
- **Redaction: one matcher for the text of a page (#119).** Search-term
  redaction found text with PyMuPDF's `search_for`, and the verifier looked
  for what was left with `search_for` and three plain views of the page's
  text. On PyMuPDF 1.27 `search_for` finds no term that is broken across a
  line after a hyphen ("4455-" / "887", "123-45-" / "6789", "Dana Whit-" /
  "field"), in any mode, and no letter-spaced heading; such a term was
  reported as "No matching content", or was left in place beside the
  occurrences that were found, and the output verified. On 2.1 it finds the
  typesetter's hyphenation only, and its hit stops one letter short: "Dana
  Whitfield" came out as a verified file with the "d" still on the page.
  `pdf_verify.PageText` now reads the page's characters with their boxes and
  looks for a term in four views of them: every run of whitespace, a line
  break included, as one space; that, with a hyphen that ends a line dropped
  together with the break; that, with only the break after such a hyphen
  dropped; and no whitespace at all. Both sides are folded first: NFKC (a
  ligature is its letters, a no-break space a space), U+2010 and U+2011 as
  "-", soft hyphens and zero-width characters dropped, case folded unless the
  search is case-sensitive. `redact_pdf` boxes what `pdf_verify.term_boxes`
  returns — the matcher's hits and `search_for`'s, each widened to the whole
  words it touches, one rectangle per line of a hit — and
  `redaction.page_text` uses the same views to confirm nothing is left.
  After a page is redacted it is searched again, up to three times, because
  taking a word out can leave its neighbours reading as the term ("New"
  [removed] "York"); that used to be a refusal.
  **What changes for a user.** A hit takes the whole word: a term that is
  part of a word, an address or a number redacts all of it, punctuation
  attached to it included. `redaction_count` is still the number of
  rectangles; right-to-left and stepped text, where `search_for` gave one
  rectangle per glyph, now counts one per word. The view with no whitespace
  finds a letter-spaced word, a number printed in groups and an address
  broken at a line end with no hyphen — and so also redacts two words that
  spell the term between them ("now here" for "nowhere"). It is accepted
  only as whole words run together: the term "00000" is not found in
  "1.0000 0.2000". An apostrophe may follow the last letter ("W H I T F I E
  L D's file"). The matcher reads text a clipping path hides (a stamp cut
  off at "APPRO" still has "APPROVED" in the file), as `search_for` did.
  Scripts written without spaces (Chinese, Japanese, Thai and others) are not
  widened to "the whole word".
  **Known limits**, each refused or reported as no match, never passed: text
  outside the page's visible box (crop box, or running off the page edge)
  has no characters to box, and the raw-content check refuses the run with
  the flatten offer; what an annotation's appearance draws on a page is not
  page content and cannot be redacted (refused, flatten offered); a word
  that is a PDF name inside a content stream (`/Subtype /Watermark`) is
  still found by the byte-level checks; characters drawn out of reading
  order are not put back in order; slanted text is boxed by its bounding
  rectangle, as before, which takes in what is printed near it.
  **Real files** (1,139 public PDFs, one word of its own text redacted from
  each): 520 verified, 12 refused and 17 "no matching content" before; 542
  verified, 7 refused and none unmatched now. All 17 unmatched files verify
  (ligatures in the word itself, mostly). Of the nine that failed the
  page-text or raw-content checks, three verify and six are still refused:
  three for text outside the visible page, two for an annotation's
  appearance, one for the PDF name. In the 520 files verified both times the
  rectangles went from 6,998 to 6,947: nine more in six files, sixty fewer
  in nine. Every added rectangle holds the term, except the two halves of
  one false match (two separate labels, "su" and "Sin", for the word
  "susin"). Cost: the run's redactions, timed back to back, took 88 s before
  and 125 s now; the largest file, a 352-page book with 563 hits, 9.7 s.
  Tests: `tests/test_redact_matcher.py`.
- **Sanitize proves its output before it commits it (#101).** `sanitize_pdf`
  (`pdf_analyze.py`) writes the cleaned candidate to a temp file beside the
  destination, runs `verify_sanitization` on those exact bytes, and renames
  the candidate onto `output_path` only if it verified. If it did not, it
  raises the new `pdf_analyze.SanitizationVerificationError` — `report` is the
  verification report as a dict — and the run has not created, replaced or
  deleted `output_path`: sanitizing in place leaves the original
  byte-identical, a file already at a separate destination is untouched, and
  no temp file is left behind. Verified means three things. Every finding the
  enabled options promise is absent from a fresh audit of the candidate. The
  audit and the whole-file sweep were able to look everywhere (see the next
  bullet). And the candidate shares no trailer `/ID` element with its source
  (`sanitize.file_id`): `sanitize_pdf` captures the source's identifiers and
  SHA-256 while it has the input open and hands them over as plain values, so
  the verifier never opens the input; an output byte-identical to its source
  is exempt, which is what sanitizing an already-clean file produces.
  Findings no option promised (GPS in an embedded photo, a remote GoTo) are
  residuals on the report, not failures. The return value gains
  `"verification"`; image inputs through `strip_file` carry none. Cost,
  measured on a 600-page file with 7,200 annotations: Sanitize 0.4 s → 1.0 s.
  The gate audits the output, so Sanitize now takes at least as long as
  Analyze does on the same file: on a decompression-bomb test file that
  Analyze needs 74 s for (before and after this change), Sanitize went from
  0.02 s to 80 s.
  What the app shows of this is in the next bullet. Tests: `TestGate` in
  `tests/test_sanitize_gate.py`.
- **Sanitize says what it verified, and why it refused (#101).** The app now
  shows the gate's result. `sanitizeDocument` (`ui/bridge.py`) answers through
  the new Qt-free `pdf_analyze.sanitize_payload`, in one of three shapes:
  success with `verification` (the report the output passed; an image has
  none); a refusal with `error` (the one-sentence reason), `verification`
  (the report) and `refusal`; or a failure with `error` and `errorType`.
  `refusal` is the new `pdf_verify.sanitize_refusal_detail`: what is still
  present (one line per category, with where, in the sweep's plain words;
  three places at most), what could not be checked, whether it is the file
  identifier, whether the output could not be read back. The wording is made
  in Python, in one place, and tested there; the frontend only lays it out.
  On the Analyze page (`AnalyzePage.tsx`, new `SanitizeOutcome.tsx`) a
  verified run reads "Verified — nothing the selected options remove is left
  in the clean copy", with the number and titles of the findings no selected
  option removes, and any check outside the selected options that could not
  be completed. A refused run reads "Sanitize refused — nothing was saved"
  followed by those lists. `audit.incomplete` is listed first, whatever its
  severity, and starts expanded. This is deliberately thin: the exportable
  report and its panel are #100. `SanitizeResponse`, `AnalyzeReport` and the
  mock data (`mockSanitizeResult`, which still used the old counter meanings)
  are updated; the mock refuses when "External links" is ticked so the
  refusal can be reviewed in `vite dev`. Nothing `sanitize_payload` logs
  names a file: the slot used to log the input path, and an unexpected error
  is now logged by its type and where in the code it happened. Frontend
  rebuilt (`web-react/dist/`). Tests: `TestSanitizePayload` and
  `TestRefusalDetail` in `tests/test_bridge.py`.
- **Known limits of the sanitize gate (#101).** Three, each needing a file
  built for the purpose. (1) `metadata` empties the `/Info` dictionary in
  place when it is nothing but document information (see Fixed, below), so
  that a second reference to it does not keep the entries. If the trailer
  points `/Info` at a dictionary that holds only strings, names, numbers and
  booleans, declares no `/Type`, `/Subtype` or `/S`, and has another job — a
  graphics state written without `/Type`, say — that object is emptied too,
  and whatever used it loses it. (2) The other way round: an `/Info` that is
  also held under a second key and is not plain — it declares a `/Type`, or
  one of its values is an array or a dictionary — is only unhooked from the
  trailer. Its entries stay in the file under the other key and the output
  verifies, because once the trailer no longer names it nothing marks it as
  document information. Narrowing either rule widens the other. (3) The
  identifier check compares the elements of the two files' trailer `/ID`. A
  source whose `/ID` holds the output's future identifier inside a nested
  array shares no element with the output, and is not refused.
- **"Could not check" is no longer "nothing there" (#101).** Analyze's
  scanners and walkers are tolerant — one structure they cannot read must not
  end the audit — but they used to swallow the exception and report whatever
  was left, so a scanner that crashed read as `clean`. Every such place now
  records which findings it could not evaluate: `AnalysisResult.unevaluated`,
  `{finding id: [reasons]}`, in `to_dict()` as `unevaluated`. A scanner that
  stops marks every finding it can emit; a walker that cannot read one
  bookmark, field, annotation or name-tree node says so and carries on.
  Analyze never answers `clean` when anything is unevaluated: it emits
  `audit.incomplete` (medium) listing what could not be checked.
  `verify_sanitization` fails a promised finding that could not be evaluated
  (the check is marked `unevaluated`) and lists unpromised ones in the
  report's new `unevaluated` field, so the report never implies they were
  checked. There are no depth caps left to record: `/Next` chains, name trees
  and nested dictionaries are walked iteratively to their end (the
  sanitizer's chain cleaner used to stop 30 links down and drop the rest of
  the chain uncounted). Reasons give the exception's type, never its message —
  library messages quote the path of the file. Tests: `TestCouldNotEvaluate`.
- **Analyze reports what the verifier's sweep can see (#101).** A user must
  never get `clean` from Analyze and then a refusal from Sanitize. After its
  scanners, Analyze now runs the verifier's whole-file sweep twice on its
  in-memory copy (nothing is saved): on the document as it is, and after a
  sanitize with every option on. Whatever is left after that sanitize is added
  to the finding it belongs to, in plain words, with "Sanitize cannot remove
  it from here". A category the sweep finds in the untouched file that no
  scanner listed — it goes only along with something another option removes
  whole — is listed with "Sanitize removes it only together with what holds
  it". Analyze also reports a 3D stream's `/OnInstantiate` script under
  `scripts.js` (Sanitize already removed it), and follows a `/Next` chain to
  its end, where it used to stop at 32 links. Cost on the same 600-page file:
  Analyze 0.6 s → 1.1 s. Tests: `TestAnalyzeSeesWhatTheSweepSees`,
  `TestOneOptionAlone`.
- **Pages outside the page tree (#101).** A page held only by the catalog's
  `/Names /Templates` or `/Names /Pages` name tree (a page template, a "named
  page") can be shown or spawned by a viewer, and nothing looked at it.
  Analyze and Sanitize now take their pages from one generator
  (`_all_pages`), which yields those after the page tree, each page once, so
  page `/AA`, annotations, `/AF` and page metadata there are reported, removed
  and verified like any other. The parity matrix runs every page-based site
  kind in all three homes (294 cases). Tests: `TestPagesOutsideThePageTree`.
- **Refusals and sweep evidence in plain words (#101).** A refused sanitize
  says which of three things happened: something the options promised to
  remove is still there (named: "still present in the output: JavaScript"),
  something could not be checked (named, and said to be unchecked rather than
  present), or the output would still carry its source's identifier
  (`pdf_verify.sanitize_refusal_message`). Sweep evidence reads "a JavaScript
  action on page 1, annotation 2, under /Stash" (`pdf_verify.plain_location`,
  Qt-free); the raw object path is kept beside it in the check's new
  `objectPaths`. What the remover itself could not process is attached to the
  report as `diagnostics` — structure names and exception types only, no file
  paths or names. Tests: `TestRefusalWording`, `TestDiagnostics`.
- **Whole-file sweep in `verify_sanitization` (#101).** The verifier's
  promise checks re-run the analyzer, so alone they were only as good as the
  list of places the analyzer knows to look. `verify_sanitization` now takes
  a second, independent look: `walk_objects` (new, public, in
  `pdf_verify.py`) visits every dictionary and stream reachable from the
  trailer — inline ones included — and for each enabled option a predicate
  asks what the object *is*: an action by its `/S`, script text, an `/AA`
  dictionary holding actions under the standard trigger names, a stream typed
  as metadata, a dictionary whose `/EF` holds a stream, a 3D stream with an
  `/OnInstantiate` script. One `sanitize.sweep_<option>` check per enabled
  option; a hit fails it, with what was found and where as evidence, **even
  at a place the analyzer has no site for**. No predicate fires on a key name
  alone, so
  a font named `/JS`, `/AA` or `/EF` or an image named `/Metadata` is not a
  hit. A stream typed `/EmbeddedFile` is a hit wherever it is, with one
  exception by position: a packet of the catalog's `/AcroForm /XFA` entry.
  Adobe LiveCycle Designer types its XFA packets that way; they are the form
  (`forms.xfa`, which no option removes, so a residual), not attachments, and
  counting them refused every such form under the default options. A file
  specification that embeds a packet is still found and removed. The walk is iterative (tested on a 20,000-link `/Next` chain) and
  cycle-safe, and it never passes on a partial walk: an object that cannot be
  read, an output that cannot be opened, or a walk cut short fails every
  sweep check. `walk_objects(..., strings=True)` also yields every string, for
  a later whole-file term search in `verify_redaction`. `sanitize_pdf` runs
  it on every output before committing (the gate, above). Tests: `TestSweep*`
  in `tests/test_pdf_verify.py`, `TestXfaPackets` in
  `tests/test_sanitize_gate.py`.
- **Analyze/Sanitize parity matrix (#101).** `tests/test_sanitize_parity.py`
  is generated from `pdf_analyze.ACTION_SITE_KINDS` — the one list of places
  an action can hang — crossed with the action types the options remove
  (JavaScript, Launch, SubmitForm, URI), plain and one step down a `/Next`
  chain. Option on: Analyze finds it, the sweep finds it, Sanitize removes it
  and counts it once, the output verifies. Option off: it survives and is
  reported as a residual. A site kind with no way to plant an action fails
  the suite, so a kind cannot be given to the analyzer and not the sanitizer.
- **Fail-closed true redaction (#99).** Redaction now proves its own output
  is clean or refuses to save it. After the real `apply_redactions` pass,
  `redact_pdf` (`pdf_ops.py`) widens the scrub to every document-level
  surface `apply_redactions` never touches — docinfo (all `/Info` keys,
  including custom ones like `/Company`; stripped wholesale), the XMP packet
  (wholesale), bookmark titles, annotation text (`/Contents`,`/T`,`/Subj`,
  all subtypes), form-field values, link URIs, and embedded-file names — each
  removed **whole-value** (a term in "John's Q3 Report" removes the entire
  title, never a broken fragment). A term that lived only in a document-level
  surface (e.g. the `/Title`) now counts as a redaction hit instead of
  raising "no matching content"; the per-surface breakdown is reported in the
  new `RedactResult.surface_counts`. It then re-opens the saved output and
  runs `verify_redaction`; if any target is still extractable it **deletes
  the output and raises `RedactionVerificationError`** (never returns success
  with a leak, never leaves a half-verified file on disk). The error carries
  structured detail (the report, the flatten-eligible pages, whether the
  residual is document-level). **D1 hybrid recovery:** when the only residual
  is page content, the UI offers "Force removal: flatten affected page(s) to
  image" — a second call with `flatten_pages` rasterizes *only* those pages
  (150 DPI, others kept faithfully with selectable text) and re-verifies; a
  document-level residual is a hard refusal with no offer (flatten can't fix
  it). The bridge (`ui/bridge.py`) carries the verification report through on
  success and the structured failure payload (`verification`,
  `flattenablePages`, `documentLevel`) on refusal; `RedactPage.tsx` shows a
  "Verified — 0 occurrences remain" proof line on success and the residual
  detail + flatten offer on refusal (frontend rebuilt, `web-react/dist/`).
  RED-01 (scanned-page image pixel redaction) and the atomic-write pattern
  are unchanged. New coverage in `tests/test_pdf_ops.py` (per-surface scrub
  round-trips, title-only success, fail-closed monkeypatch, the flatten path,
  and error-tail parity: password/cancel/zero-match/out-of-range/empty-terms/
  save-failure) and `tests/test_pdf_verify.py` (D1 flatten targeting).

### Changed
- **Redaction scrubs six more surfaces, and refuses what it has no rule for
  (#119).** So that an ordinary file passes the whole-file search above, a
  term redaction now also: replaces structure-tree text holding a term
  (`/Alt`, `/ActualText`, a title, an attribute) with `[redacted]`, as it
  does a bookmark title; does the same to a layer's name; deletes an
  application's `/PieceInfo` entry that holds a term anywhere under it;
  deletes a metadata stream that holds a term in any encoding wherever it
  hangs, not only the catalog's (one that cannot be decoded is deleted too,
  since it cannot be shown free of the term); removes a script whose text
  holds a term, by the sanitizer's own code and so from every place Sanitize
  removes scripts; and renames a named destination whose name holds a term,
  together with the links, actions and bookmarks that use it, writing the
  name tree back sorted. Each is counted in `surface_counts` under
  `structure`, `layers`, `piece_info`, `metadata` (`xmp` is still the
  catalog's packet), `scripts` and `destinations`, and a term found only
  there is a redaction, not "No matching content". All the document-level
  scrubs now match the way the verifier does, so a term of two words is
  found in a note that breaks the line between them. **A term anywhere else
  is not scrubbed: the run is refused.** Of 524 real PDFs whose redaction of
  one word of their own text used to finish verified, 41 were then refused,
  every one because the word was still in the file; the next entry has the
  rules for the places it was. Tests: `TestScrubRules`,
  `TestWhatHasNoRuleIsRefused`.
- **Redaction: rules for the places real files keep a term (#119).** They
  apply to every annotation and form-field dictionary in the file, not only
  the ones a page lists: redaction takes an annotation under a box off its
  page, but a tagged file's structure tree goes on pointing at it, with its
  text and its link address, and that used to verify. Each rule has its own
  key in `surface_counts`.
  `rich_text`: a rich-text entry holding a term (`/RC` on an annotation,
  `/RV` on a field) is deleted; it is the styled twin of the plain text
  beside it. `field_text`: a field's tooltip (`/TU`), mapping name (`/TM`),
  default value (`/DV`), option list (`/Opt`) and captions (under `/MK`)
  become the marker, wherever the field sits in the field tree.
  `field_names`: a field whose own name (`/T`) holds a term is renamed to a
  neutral name no other field has; on a page widget it used to be deleted,
  which left a field with no name. The appearance streams that drew a
  redacted value, default, option or caption are dropped on the field and on
  every widget below it. `pdftex`: a `/PTEX.*` entry holding a term — the
  name of an included figure's file, or anything under that file's
  `/PTEX.InfoDict` — is deleted. `links`: a URI action whose address holds a
  term is removed the way Sanitize removes that action, wherever it hangs (a
  bookmark, the open action, a field, a chain); **a link annotation whose
  own address held the term now leaves its page, where it used to stay with
  the address deleted.** `page_labels`: a page-label prefix becomes the
  marker. `xfa`: when an XFA packet holds a term the form's `/XFA` entry is
  dropped, so the form falls back to its AcroForm fields; a form that has
  none is refused with a plain message that an XFA-only form cannot be
  term-redacted (`RedactionNotPossibleError`, a `ValueError`; nothing written). `embedded_files`: an
  attachment whose name, description or bytes hold a term is removed whole —
  from the attachment list (written back as one sorted leaf, so no `/Limits`
  keeps the name), from every `/AF` array, and with its `/FileAttachment`
  annotation. **Attachments are searched as their stored bytes only: a zip,
  a docx or an xlsx is compressed inside and is not opened.** One that
  cannot be read is left alone, and the run is then refused. `signatures`: a
  signature holding a term anywhere in it — signer, reason, place,
  certificate bytes — has its value removed, on a signature field or in the
  catalog's `/Perms`, so the field is left unsigned; redaction voids a
  signature in any case. New in `pdf_analyze`, shared with the sanitizer's
  code: `remove_links`, `remove_embedded_files`, `write_name_tree`, and
  `remove_scripts(..., also=)` for objects off the page.
  Still refused, as known limits: web-capture names (`/Names /URLS`), an
  output intent's standard strings, certificates in the document security
  store, and anything else the sweep finds. (Three more were on this list:
  a GoToE action that names a removed attachment, a form stream only the
  structure tree still references, and the appearance of a note that is off
  its page. The next entry and the first under Fixed deal with them.) On
  1,139 real PDFs,
  one word of its own text redacted from each: 524 finished verified before
  any of this, 53 of them with the word still in the file; 483 with the
  sweep and its first rules; 520 now, with 12 refused and 2 XFA-only forms
  refused in plain words. Nine of the 12 fail the page-text or raw-content
  checks, as before; three are the sweep (web-capture names, a GoToE target
  naming a removed attachment, a form stream only the structure tree still
  references).
  Tests: `TestRichText`, `TestFormFieldText`, `TestPdfTexEntries`,
  `TestOffThePage`, `TestLinksEverywhere`, `TestPageLabels`, `TestXfa`,
  `TestAttachments`, `TestSignatures`, `TestStillRefused`.
- **Redaction: what goes with a removed attachment and with text scrubbed off
  the page; neutral names count up (#119).** Three things left over when the
  rules above met real files. (1) A go-to action into an attachment
  (`/GoToE`) names the attachment. When the attachment is removed, the
  action is removed with it, the way Sanitize removes an action — a link
  annotation whose own action it was leaves its page — whether the target
  is given as a name in the attachment list or as the `/FileAttachment`
  annotation that carries the file (by page and position, or page and
  `/NM`). A real file's four links into its one attachment went on naming it
  after it was gone, and the run was refused. Not counted on its own; a
  target counted from another file (`/F`) or from the parent is left.
  `pdf_analyze.remove_embedded_files` gains `also=` and `gone=`. (2) An
  annotation that is on no page — redaction took it off, the structure tree
  still points to it — and has its `/Contents`, `/T`, `/Subj` or `/RC`
  scrubbed now loses its appearance (`/AP`) too: it drew that text, and
  nothing shows it. An annotation that is on a page keeps its appearance; if
  that draws a term the run is refused with the flatten offer, as before.
  (3) The neutral names given to renamed fields and destinations count up
  within one stem — `field-1`, `field-2`, `field-3` — where thirteen sibling
  fields used to come out as `field1`, `item-1`, `item1`, `entry-1` and so
  on. A stem is passed over only when it holds a term itself, the hyphen
  only when it does, and a number only when the name would then hold one.
  Tests: `TestActionsIntoARemovedAttachment`, `TestAnAppearanceOffThePage`,
  `TestNeutralNamesCountUp`.
- **`pdf_analyze`: the sanitizer's removals can be asked for selectively
  (#119).** New `remove_scripts(pdf, wanted)` and
  `remove_metadata_streams(pdf, wanted=None)`, and `_name_tree_items` is now
  the public `name_tree_items`. Sanitize itself goes through the same
  functions; its behaviour is unchanged.
- **Sweep evidence names the same path on every run (#119).**
  `pdf_verify.walk_objects` visits a dictionary's entries in key order. An
  object reachable two ways used to be reported under whichever path the
  hash seed put first. `plain_location` also names the structure tree, the
  layer list and the named destinations.
- **Default Sanitize now removes every additional action (`/AA`) on
  annotations and form fields (#101).** Analyze has always reported any `/AA`
  entry as an auto-run action, whatever it does, but with `auto_actions` on
  (the default) Sanitize removed the whole `/AA` only from the catalog and
  pages; on annotations it removed just the JavaScript, launch and submit
  entries. It now removes every entry there and on form fields too. In
  practice: **format, keystroke, validate and calculate scripts on form
  fields are stripped wherever the field is** (previously only on fields that
  were themselves page annotations), and non-script entries such as a
  pointer-enter `/Hide` go as well. With `auto_actions` off, each other option
  still removes its own action type from `/OpenAction` and every `/AA`, entry
  by entry — before, a script in a page `/AA` survived `javascript` alone.
- **Sanitize: `external_links` removes a URI action wherever it is (#101).**
  It used to remove only a link annotation whose own `/A` was the URI. A URI
  further down a `/Next` chain, in `/OpenAction`, on a bookmark or on a form
  field is now removed too; a harmless action it was chained to stays.
- **Sanitize leaves a destination `/OpenAction` in place (#101).** An
  `/OpenAction` that is an array or a name only says where the document
  opens. It was deleted and counted under `auto_actions`, although Analyze
  never reported it. It is now kept; an `/OpenAction` that is an action is
  still removed.
- **Analyze reports metadata by presence (#101).** `meta.xmp` is reported for
  any stream typed `/Metadata` or `/XML` on any object — the catalog, a page,
  anything else — and for whatever stream sits in the `/Metadata` slot of the
  catalog, a page, or any other object that declares a `/Type`, typed or not
  and whether or not it can be parsed or even decoded; the finding lists
  where each one hangs. (A resource dictionary declares no `/Type`, so an
  image it names `/Metadata` is not metadata.) Sanitize empties such a slot
  where it stands; a stream that declares itself metadata is also unhooked
  from wherever else it is referenced, one that does not is not — a file can
  point a page's `/Contents` at the same stream. An empty packet counts: it is in the file.
  `meta.docinfo` reports every non-empty `/Info` entry, custom keys included,
  except `/Trapped` (a prepress flag). Analyze no longer opens pikepdf's
  metadata editing context to look, which made it report `/Info` entries a
  file did not have.
- **Analyze: a link to an external file is not an embedded file (#101).** A
  file specification is reported under `files.embedded` only when it actually
  carries a payload (`/EF` holding a stream) — and now also when it is
  written inline inside another object, which was missed before. One with no
  payload moves to a new, lower-severity finding, `files.external`
  ("References to external files"): nothing is embedded, but the path
  travels with the document. Sanitize does not remove those, so with
  `embedded_files` on they are residuals, not failures. A font that happens
  to be named `/EF` is no longer reported as an unnamed embedded file.
- **Sanitize counters count what was there (#101).** `document_aa` and
  `page_aa` count entries, not dictionaries. `xmp_metadata` counts metadata
  streams and `doc_info` counts an `/Info` only if it had entries, so a blank
  PDF reports nothing removed (it used to report two). **An embedded file is
  one count however many places list it**: `embedded_files` counts the files
  in the name tree (not the tree), and `associated_file`,
  `file_attachment_annot` and the new `embedded_file` (a payload emptied in
  place) each count only a file nothing counted before — pikepdf 10.16 and
  later registers an attachment in both the name tree and the catalog `/AF`
  as the same object, which used to count twice. Other new keys: `annot_aa`
  and `field_aa` (an `/AA` entry removed only because it fires by itself) and
  `instantiate_script`. Scripts, launches, submits and links removed from
  bookmarks, fields and a link's `/PA` use the existing `annot_javascript`,
  `launch_action`, `submit_action` and `external_link`. Two removals that
  were not counted now are: an `/AA` that is not a dictionary (an array, a
  string, a number, a stream), deleted under `auto_actions`, is one count
  under its owner's `/AA` counter; and an `/Info` that is not a dictionary
  counts as `doc_info` like any other — a number already did, a string, an
  array or a name did not. Analyze lists neither, so the count can be the
  only sign they were there. Tests: `TestCounters` in
  `tests/test_sanitize_gate.py`.
- **Sanitized output is a fixed point with a content-derived `/ID` (#117,
  #101).** `sanitize_pdf` writes through `save_with_content_id`, so the
  output no longer carries the source's trailer `/ID` (the gate's
  `sanitize.file_id` check holds it to that). It writes twice: qpdf packs object
  streams by the numbers objects had on the way in, so only the second write
  is the layout it would give that same file again. Sanitizing a sanitized
  file now changes no byte, and the same input gives the same bytes on every
  run. `fix_metadata_version` is off for these writes. One exception to "the
  same content gives the same bytes": an identifier that is a digest of the
  content is the same whenever the content is, so a source that descends from
  an earlier sanitized copy — re-saved, or edited by a tool that keeps the
  first `/ID` element, as the format asks — can already carry the identifier
  its clean copy would get. The gate would refuse that output, and the user
  could do nothing about it. `sanitize_pdf` then writes the same content in a
  second layout (no object streams), whose digest is a different one; that
  output verifies, and sanitizing it again gives the ordinary layout. A
  source that already carries the identifiers of both layouts is refused, and
  Analyze does not list that beforehand; it takes deliberate construction.
- **Search-term redaction no longer rewrites metadata it was not asked to
  remove (#101).** A consequence of the XMP scrub fix below. Inspecting the
  XMP used to enter pikepdf's metadata editing context, which wrote an empty
  XMP packet into files that had none and re-synchronised docinfo from XMP —
  on a file without XMP that deleted `/Title`, `/Author` and the other
  standard `/Info` keys even though no term appeared in them (box-only
  redaction never did this). Docinfo and XMP are now removed only when a
  term appears in them, as documented, and are otherwise left exactly as
  found. **If you relied on search-term redaction stripping author/title
  metadata as a side effect, it no longer does** — use Sanitize's metadata
  option or the Metadata tool.
- **`pdf_analyze._page_content_blobs` promoted to public
  `page_content_blobs`.** It is now part of the module's public surface
  (consumed by `pdf_verify`'s redaction verifier); behaviour is unchanged and
  both internal call sites were updated.
- **`pdf_verify.py` — the verification loop behind provable removal (#110).**
  New top-level, Qt-free module that re-opens a redacted or sanitized OUTPUT
  file and independently proves the targeted content is gone, instead of
  trusting the transform library. `verify_redaction` searches every
  extractable surface for each redacted term — page text (including
  invisible render-mode-3 text, with whitespace/dehyphenation-normalized
  views so an exact-case term wrapped across a line break is still caught in
  case-sensitive mode), raw decompressed content streams, annotation
  text/titles, form-field values, link URIs, docinfo, XMP, bookmark titles,
  and embedded-file names — and verifies user-drawn redaction rectangles via
  clipped extraction (an invalid/inverted/empty rect is a FAILED check, not
  a silent skip, since those are exactly the rects redaction itself skips).
  Surfaces are read through two independent libraries where possible: the
  PyMuPDF walk plus a pikepdf pass over the raw object graph that also
  covers what PyMuPDF cannot see — custom `/Info` keys, every `/V` field
  value anywhere in the file (values no page widget exposes), and
  annotations whose subtype `page.annots()` skips. `verify_sanitization`
  re-runs the `pdf_analyze` audit and asserts absent exactly the finding
  categories the enabled sanitize options promised to remove; anything else
  still flagged (e.g. EXIF GPS inside an embedded photo, which sanitize has
  no removal path for yet) is reported honestly as a residual finding, not a
  failure. Fail-closed throughout: an output that cannot be opened,
  decrypted, or re-scanned is a FAILED check — and so is any single
  surface, page, stream, or cross-check that cannot be read — never a pass.
  Every failure's evidence names its surface and page (or "document-level")
  — the page-attribution the upcoming fail-closed redaction UX needs to
  offer targeted flatten-to-image. A pure addition when it landed; `redact_pdf`
  (#99) and `sanitize_pdf` (#101) now call it before committing. Covered by
  `tests/test_pdf_verify.py` (37 tests: one per surface, rect mode,
  locked/corrupt outputs, sanitize round-trips, and fail-closed regressions
  from an adversarial review — cross-line case-sensitive terms, multi-page
  attribution, multi-term aggregation, non-stream `/Contents`, unsupported
  annotation subtypes, custom docinfo keys).
- **UI polish: branded sidebar mark + sane Translate default.** The
  sidebar's placeholder "P" chip is now the same file-lock motif as the
  app's window/taskbar icon (lucide-react's `FileLock`, tree-shaken into
  the bundle — new frontend dependency, still fully offline), accent-
  colored on the existing tinted chip. The Translate tool's "To" language
  no longer defaults to English (which, with "From" on auto-detect,
  produced an English→English no-op that read as broken) — it now
  defaults to the first installed non-English language, both on page load
  and right after first-time setup installs packs, and never overrides a
  target the user picked deliberately. Frontend rebuilt
  (`web-react/dist/`).
- **Frozen build: translation is now provisioned on demand instead of
  bundled — fixes the native PyTorch crash and shrinks the installer from
  1.26 GB to ~670 MB.** The packaged app crashed with a native c10 abort
  (inside `torch_python.dll`, no Python traceback) the first time Translate
  was used: `argostranslate.translate` unconditionally imports
  `stanza` → `torch` at import time, and frozen PyTorch does not survive
  PyInstaller's DLL relayout on this stack (Python 3.14 + PyInstaller 6.21;
  a chronic upstream failure class). Rather than fighting frozen-torch,
  `pdf_toolkit.spec` now **excludes the entire ML stack** (torch, spacy,
  thinc, stanza, ctranslate2, onnxruntime, sentencepiece, sacremoses,
  argostranslate, minisbd — ~1 GB for one tool of 22) and the new
  `translate_runtime.py` provisions it at runtime: on first use the
  Translate tool offers a one-time, user-initiated download (~204 MB, 64
  wheels pinned by name/version/URL/sha256 in the new
  `translate_runtime_lock.json`; regenerate with
  `python translate_runtime.py --make-lockfile`), verifies checksums,
  unpacks into `%LOCALAPPDATA%/PDFToolkit/translate-runtime`, and appends
  it to `sys.path` — torch then imports from a real directory layout
  exactly like a source checkout, sidestepping the frozen-DLL problem
  entirely. The Translate page gained a setup flow (language multi-select
  for the 12 supported languages, engine-size notice, live progress with
  cancel, and an "Add languages…" path once ready) backed by a new
  `startSetupTranslation` bridge slot; `pdf_translate.install_languages()`
  is the single language-pack installer shared by the UI and
  `setup_translation.py` (now a thin CLI wrapper around it).
  `translation_status()` now reports the runtime state so the UI can drive
  setup. Source checkouts with argostranslate in the venv are unaffected
  (the runtime activates only when the stack isn't natively importable),
  and the offline invariant holds: the explicit setup download is the only
  network operation, and after it translation runs fully offline (Argos
  language packs live in Argos's own per-user dir, as before). Because the
  ML stack is invisible to PyInstaller's analysis, the bundle now ships the
  **complete Python stdlib** (all top-level modules plus
  `collect_submodules` for every stdlib package, minus POSIX-only/GUI
  corners; `unittest` is no longer excluded) — runtime-provisioned wheels
  import stdlib modules the analyzed app graph never references, and the
  first builds broke exactly that way (torch → `timeit`, requests →
  `http.cookies`); costs ~3 MB. `pdf_translate`'s not-provisioned error now
  carries the underlying exception so real environment failures can't hide
  behind the generic "not set up" message. Added
  Qt-free `tests/test_translate_runtime.py` (lockfile schema/pins,
  download+verify+unpack+activate over `file://` synthetic wheels,
  checksum/zip-slip/cancel failure paths, lock-change invalidation).
  Verified end-to-end on the rebuilt frozen exe: all tools load, the setup
  flow provisions the runtime + German pack in-app, and translation works
  offline afterwards.
- **UI: warm cream light theme with terracotta accent and serif headings;
  cool dark theme with blue accent; refined workspace bar and tool cards.**
  The old near-black "security console" look is replaced across the token
  system (`web-react/src/styles/theme.css`) with a precise, restrained
  design: crisp 1px hairlines and whisper-soft shadows carry surface
  separation, corners at 8/6/4px, spacing on a tight 4px rhythm
  (`--space-4` 16px, `--space-5` 24px), and a single accent that marks
  interaction only. **Light (default, `:root`)**: warm cream/ivory page
  (`#f4f1ea`) with warm near-white panels (`#faf8f3`), warm-greige
  hairlines, warm-tinted text, warm shadows, and a muted terracotta accent
  (`--accent #a85433`, white label text). **Dark (opt-in,
  `[data-theme='dark']`)**: kept cool — deep cool grey (`#0e0f12` page →
  `#16181d` panels → `#1f222a` wells) with the indigo-blue accent
  (`#5964d8`); the warm-light/cool-dark temperature split is deliberate.
  **Typography**: major page headings (the `PageHeader`/Home titles) now
  use an offline-safe serif display face (`--font-serif` Georgia, 22px,
  normal weight, slight letter-spacing) over the system-sans UI text and
  mono machine data. **Accent/severity distinctness** (documented in
  theme.css): in light, terracotta and `--sev-high` are both warm, so they
  separate by saturation/value (vivid bright `#d85a30` badge fill vs
  deeper muted clay), hue of the text variants (red `#9c3414` vs brown
  `#8a4d24`), and context (accent only on interactive elements, sev-high
  only in red-tinted badges/stripes); in dark, indigo (~233°) stays
  hue-separated from steel-blue `--sev-low` (~210°). Light severity
  fills/text were re-deepened for the warm surfaces (`--sev-medium
  #ad7415`, `--sev-low #3384d6`, `--sev-info #178a67`, high/medium text
  darkened). Every color in both themes verified with a WCAG
  relative-luminance calculator (94 checks: text ≥4.5:1, fills/borders/
  focus ≥3:1, including 0.14 badge-tint and 0.10 accent-tint blends).
  Element refinements: active nav items get an accent-tinted fill + thin
  indicator; the sidebar "P" logo is a quiet accent-tinted placeholder
  chip pending the rebrand; the workspace bar's empty state is a compact
  "No document loaded" + quiet hint beside an accent-tinted "Load
  document" button (was one long sentence and a lone bordered pill); tool
  cards group title + description as one anchored block; the DropZone is
  a precise empty state (32px icon chip, dashed border, accent-tinted
  hover/drag-over). Accessibility retained: global accent
  `:focus-visible` ring, `prefers-reduced-motion` guard (loading spinners
  are exempted as essential status — under reduced motion they slow to a
  gentle 1.6s rotation instead of freezing, since Windows maps its
  "Animation effects off" setting — common, and forced in RDP sessions —
  to `reduce`, where a frozen arc would read as a hung app), system font
  stacks only (offline-safe). Also includes the carried-forward
  structural work: light-as-default inversion end-to-end (Sidebar
  boot/persistence read; the bridge's `applyTheme` no longer sets
  `[data-theme]`, so Python's independently-persisted Ctrl+T theme can't
  stomp the restored choice) and the `--accent*` token family decoupling
  interaction color from `--sev-info` across primary buttons, checkboxes/
  sliders, preset selection, tabs, and the progress fill. Frontend rebuilt
  (`web-react/dist/`).

### Removed
- **Dead legacy text-extraction helpers in `pdf_ops.py`.** Deleted
  `_page_text`, `_extract_text_from_stream`, and `_extract_text_pymupdf`
  (~70 lines) — leftovers from the pre-PyMuPDF regex-era redaction/compare
  code with no remaining callers.

### Fixed
- **Redaction: a straight line through a box drawn by hand hides nothing
  (#119).** `redaction.rect_line_art` failed for any drawing with an edge
  inside a box, and so for a rule across the page, the border of a table or
  the edge of a shaded row that a box happened to lie over. Such a line goes
  in one side of the box and out another, and the part under the box is
  fixed by the parts outside it. A straight line both of whose ends are
  outside the box is no longer counted as under it; each edge of a rectangle
  is judged as one line. A line that ends under the box, a corner under it,
  any curve that runs through it and anything wholly inside it still are.
  The line is left in place. It is still never the price of removing
  something else: where a curve under the box could only go by the setting
  that deletes whatever touches the box, the rule would go with it from one
  end of the page to the other, so the default stays and the page is
  refused and offered for flattening, as before. On 554 real files with one
  box each (the first place the file's word stands, grown 3 pt), 35 were
  refused for line art; 19 of them now verify. The check also looks only at
  drawings whose bounding rectangle reaches a box. Tests:
  `TestLineArtUnderABoxDrawnByHand`,
  `TestTheVerifierLooksForLineArtInsideABox`.
- **Redaction: a letter on the edge of a box drawn by hand (#119).** MuPDF
  removes a character when a box reaches a tenth of the way into the letter,
  and leaves it otherwise. The check then asked for no trace of any letter
  in the box (`page.get_text(clip=...)`, which returns a character as soon
  as the clip touches its drawn shape), so a box a point too tall, on the
  tails of the line above, was refused for letters that were rightly left.
  One rule now, for removing and for checking (`pdf_verify.chars_under`): a
  character is under a box when the centre of its text box is inside the
  box, or a fifth or more of the text box is. What MuPDF's pass leaves of
  those is boxed and removed, text only — it happens in fonts whose widths
  in the PDF differ from the font's own, where MuPDF measures a narrower
  letter than the text box: first by the middle half of the text box, and
  if the letter is still there by all of it, and only for a character that
  was under the box before anything was redacted (PyMuPDF 2.1 can move the
  text that follows a removed letter along its line; what slides under a
  box that way is refused, not removed). `redaction.rect_regions` looks for
  exactly the characters of that rule, so nothing half under a box passes.
  On the same 554 boxes, 39 files were refused for a letter on the edge;
  all 39 now pass this check, and with the line-art change the run goes
  from 479 verified and 72 refused to 534 and 17 (PyMuPDF 1.27). The second
  step had letters to remove in three files: twelve, four and one. Tests:
  `TestALetterOnTheEdgeOfABoxDrawnByHand`.
- **Redaction: what leaves a page is counted (#119).** Two things left a
  page uncounted. A form field removed because a box lay over it, drawn by
  hand or a term's: now `fields_removed` in `surface_counts`, one for each
  place a field is shown that left. And a free-text note that any redaction
  rectangle touches, which MuPDF takes off the page itself while it redacts
  (no other kind of annotation): a search that removed such a note with its
  popup and a reply said one annotation was removed, and under a box drawn
  by hand the reply was left on the page, replying to nothing. What MuPDF
  takes off is now counted under `annotations_removed` like what this code
  takes off, its replies go with it and are counted, and annotations under
  a box drawn by hand are taken off before the box is applied. A popup is
  not counted, as before. Tests: `TestWhatLeavesAPageIsCounted`.
- **Redaction: links on a page with a box drawn by hand (#119).** Two
  faults in how links under a box were taken off the page, both from
  earlier in this work and neither in a release. On a page with `/Rotate`,
  the links were compared with the box in the turned page's coordinates and
  the box in the page's own, so links nowhere near the box were deleted (40
  of 200 in the test's page). And PyMuPDF's `get_links()` goes through the
  page's whole list again for every link it returns, and reads the list
  again after each link it deletes, so one box on a page with some
  thousands of links ran for minutes. MuPDF removes the links a box
  overlaps itself, all at once; what is left for this code is a look
  afterwards for any it missed, with the page's list read once and each
  link's rectangle put into the box's coordinates with the page's own
  matrix, as the verifier does it. Tests:
  `test_links_under_the_box_go_and_the_others_stay`.
- **Redaction: a term box takes only its own characters (#119).** PyMuPDF
  removes a character if its box overlaps a redaction rectangle at all, and
  a character's box is as tall as its line. In type set close, the box of a
  found word took letters, or whole words, from the lines above and below:
  "prestations" came out as "prestatio". A term box is now applied in two
  steps. First the text is removed with the box trimmed, top and bottom, to
  where characters that are not part of the hit stop reaching into it —
  never past the middle half of its height, which every character of the
  hit crosses — with nothing filled and no image or drawing touched. Then
  the whole box is applied with the text kept, so the black fill and the
  pixels of a scan under it are covered exactly as before. If a hit
  survives the trimmed step, the page's re-search finds it and applies the
  whole box with the text removed, so trimming cannot cost a removal (on
  the real files that happens only where an annotation draws the word).
  Boxes drawn by hand are not trimmed; nor is a line that does not run
  across the page. On 544 real files verified before and after: word
  instances that left the pages without holding the term went from 1,049 to
  628, leftover fragments from 322 to 214; words holding the term that left
  stayed at 7,570, and no file gets a different number of rectangles or a
  different outcome. What is still lost is mostly not a neighbour at all:
  the other words of an annotation or form field that is removed whole, the
  two halves of a hyphenated hit, and lines printed over one another. New:
  `pdf_verify.term_hits` gives each rectangle with its trimmed one. Tests:
  `TestATermBoxTakesOnlyItsOwnCharacters`.
- **Redaction: line art under a box drawn by hand (#119).** A stroked path
  under a box stayed in the page's content, under the fill, unless it lay
  wholly inside the box as PyMuPDF measures that — by a curve's control
  points, not by where the curve runs. A signature drawn as curves could
  stay. PyMuPDF's other setting removes every drawing whose bounding box
  overlaps the box, a table's frame or a long rule included, so it is used
  only when nothing else would go: when every drawing that reaches a box
  lies wholly inside one by where its path runs. Otherwise the default
  stays, and the new check `redaction.rect_line_art` fails when a drawing
  still has an edge inside a box. That is a page-content failure: the page
  is offered for flattening, and the box is then black on the picture. A
  solid black rectangle is not counted (every box's own fill is one). Box
  mode only; term boxes treat line art as before. Tests:
  `TestLineArtUnderABoxDrawnByHand`,
  `TestTheVerifierLooksForLineArtInsideABox`.
- **Redaction: nothing is drawn above a box any more (#119).** An annotation
  is painted above the page, and PyMuPDF's redaction leaves every annotation
  alone but its own marks and links. A stamp lying over a box drawn by hand
  was still painted over it; a stamp that says the search term went on
  saying it. Both runs were refused and the page offered for flattening,
  and the flattened page showed the stamp (next entry). A box over a
  picture of a signature, which has no text for any check to find, simply
  verified. Now: in a search, an annotation whose appearance draws a term
  is taken off its page. "Draws" is asked of the page-text matcher, on the
  text of each of its appearance streams — normal, rollover and down, every
  state, shown or hidden — so a term broken across two lines of a stamp is
  found as it would be on a page. Under a box drawn by hand, every
  annotation whose rectangle overlaps the box is taken off, whatever it
  draws. With each goes its popup and everything that replies to it,
  however deep. Counted in `surface_counts` as `annotations_removed`;
  `annotations` keeps its meaning, text scrubbed inside an annotation. A
  form field's widget keeps the path it had (value emptied, appearance
  dropped, removed under a box); a popup is judged with its note, not by its
  own rectangle; an annotation that is on no page loses the appearance that
  drew the term (counted as `annotations`: it is scrubbed, not removed). An
  annotation written into the page's `/Annots` array itself, with no object
  number, is taken off the page like any other in both modes: PyMuPDF's
  list of a page's annotations leaves such an entry out, and 1.27 does not
  delete a widget of that kind when asked to. The verifier gains
  `redaction.rect_annotations`: no annotation lies over a redaction
  rectangle (it reads the page's list entry by entry, so it sees those
  too). On the real-file run (1,139
  public PDFs, one word each) the two files that were refused for this now
  verify without flattening — 17 "APPROVED" stamps in one, a Sound
  annotation labelled "SOUND" in the other — which makes 544 verified and 5
  refused, from 542 and 7; nothing else moves, and no file gets a
  different number of rectangles. Tests:
  `TestAnAnnotationThatDrawsATerm`, `TestAnAnnotationUnderABox`,
  `TestTheVerifierLooksForAnnotationsOverABox`, `TestAnnotationCounts`,
  `TestAnAnnotationWrittenIntoThePageList` in
  `tests/test_redact_engine.py`.
- **Redaction: a page made a picture is blacked out first (#119).** The
  flatten fallback rendered the page and put the picture in its place. A
  page is flattened because something on it could not be removed, so that
  something was in the picture — and afterwards no check had text to find:
  the run verified with the word readable. Before a page becomes a picture,
  every place the matcher still finds a term on it (page text, text a clip
  hides, what a shown annotation or form field draws) and every box the
  user drew are filled black on the pixmap itself, where nothing can be
  painted over the fill. `RedactResult.flattened_areas` records how many
  areas per flattened page. Not covered: a residual the matcher cannot put
  anywhere on the page. Tests: `TestAFlattenedPageIsBlackedOutFirst`, with
  removal switched off.
- **Redaction: a tagged file kept its description of what a box removed
  (#119).** A structure element can say in words what its content is: `/Alt`
  on a figure, `/ActualText`, `/E`, a title `/T`. A box over the figure
  removed the picture and left "photo of ...", and the output verified. An
  element whose own content a box removed — a term box or one drawn by
  hand — now loses those four entries, and every element above it loses
  `/Alt`, `/ActualText` and `/E`, which speak for all that is under them.
  "Removed" is decided by comparing what each marked-content sequence
  paints (the text shown, each image's data, each form, the number of paths
  and shadings) in the source and in the redacted page, and for a form
  XObject in the original and in the redacted copy the page now paints; an
  object reference counts when its annotation is on no page any more.
  Whatever cannot be compared counts as removed. On 63 tagged real files
  the comparison marks 150 of 1,438 sequences, only on pages with a box,
  and gives the same answer on PyMuPDF 1.27 and 2.1. Counted as
  `structure_text`. The verifier also gains `redaction.structure_streams`,
  the check for what `structure_refs` fixes: no stream is in the output for
  the structure tree alone. Tests:
  `TestWhatATaggedFileSaysOfRemovedContent`,
  `TestNoStreamIsKeptByTheStructureTreeAlone`.
- **Redaction: page thumbnails are removed (#119).** `/Thumb` is a small
  picture of the page as it was, and it was kept. It is deleted from every
  page of a redacted output (`thumbnails` in `surface_counts`), and
  `redaction.thumbnails` checks that none is left. Tests:
  `TestPageThumbnails`.
- **Redaction: the unredacted original of a form, an image or a page could
  stay in a tagged file (#119).** PyMuPDF does not edit a form XObject, an
  image or a page's content stream in place: the page is given a redacted
  copy, and the original is dropped when the file is written, if nothing
  refers to it any more. In a tagged file the structure tree does — a
  marked-content reference names the stream its content is in (`/Stm`), an
  object reference names the XObject (`/Obj`) — and so the original stayed
  in the output, unredacted, where no page paints it. For a search term the
  whole-file search found it and refused the run. **For a box drawn by hand
  nothing looked, and the output verified**: the text of a form under the
  box, or the pixels of an image the box blacked out, were still in the
  file. `redact_pdf` now removes, on every run, each reference the structure
  tree makes to a stream that nothing else in the file leads to (tested by
  walking the file without entering the structure tree, so a form a page
  still paints keeps its reference). Counted in `surface_counts` as
  `structure_refs`. The redacted copy is not put in the tree in its place:
  that content is no longer tagged, which it already was not, the tree
  having pointed at the original. Both PyMuPDF versions. Tests:
  `TestWhatTheStructureTreeKeepsAlive`.
- **Redaction: a file MuPDF repairs while saving it came out unredacted
  (#119).** MuPDF checks an object when it comes to read it. Finding one
  damaged, it goes back to the file on disk and rebuilds everything from
  there, and whatever had been changed in memory is gone without an error.
  When the damage is somewhere only the save reads, that is the redaction:
  the page was redacted, and the file that was written was not. The verifier
  caught it and the run was refused with the flatten offer — and a
  flattened page would have been drawn from the unredacted file, with the
  word visible and no text left for a check to find. `redact_pdf` now
  notices the repair (`Document.is_repaired` turning true between the first
  page and the end of the save) and does the redaction again on the copy
  MuPDF has just written, which is sound. Three real files were refused or
  unmatched for this, one of them a fuzzed file. Tests:
  `TestAFileRepairedWhileItIsSaved`, on a file built with one wrong entry in
  its cross-reference table.
- **Redaction: a case-sensitive search could take the term in another case
  (#119).** `search_for` ignores case, so each of its hits was confirmed by
  asking for the text inside the hit's rectangle. The rectangle is as tall
  as the line and reaches into the next one, and the answer included the
  next line: "DANA WHITFIELD" was redacted in a case-sensitive search for
  "Dana Whitfield" when that stood on the line below. The characters under
  the hit are now compared themselves. Tests: `TestFolding`.
- **Redaction: the text written in a term's place could hold the term
  (#119).** Bookmark titles, and now structure text, layer names and field
  text, are replaced with `[redacted]` — which contains "Ted", "Red" and
  "act". A title replaced by it for one of those terms was then found by the
  verifier, and the redaction was refused (since #99). The marker is now the
  first of `[redacted]`, `[removed]`, `[hidden]`, `[...]` or nothing that
  holds no term, and the neutral names given to renamed fields and
  destinations are chosen the same way: a real file whose fields are called
  "Field 1" and "Field 2" was being renamed, for the word "Field", to
  `field-1`. Tests: `TestReplacementTextNeverHoldsTheTerm`.
- **A page's content streams were loaded once per reference instead of once
  (#119).** `pdf_analyze.page_content_blobs` — behind Analyze's
  invisible-text check and the redaction verifier's raw-content check —
  loaded a form XObject's stream for every place that draws it, and forms
  draw each other. One real two-page file with 270 forms made 73,172 stream
  loads and handed 157 MB per page to the search; it now makes 542 (each
  stream once per page) and hands over 1 MB, and Analyze on that file goes
  from 11 s to under 2 s. A stream that cannot be read used to be skipped
  when reading it raised, so the verifier could not know it was there; it is
  now returned as `None` in its place, once, and
  `redaction.raw_content` fails on it and counts it once. No size budget is
  added here (#120). Tests: `TestContentStreamsAreReadOnce`.
- **Sanitize: shapes that verified with content still in the file, or were
  refused over something Analyze had not listed (#101).** Found by trying to
  break the gate. (1) An `/Info` dictionary also referenced from another key
  was only unhooked from the trailer, so its entries were still written; it
  is now emptied as well — when it is nothing but document information: a
  dictionary, not a stream, with no `/Type`, `/Subtype` or `/S`, whose every
  value is a string, a name, a number or a boolean. A trailer can point
  `/Info` at an object with another job (one real file has `/Info` and the
  page tree root as the same object); there only the trailer's entry is
  removed and the object is left as it is. (2) An untyped stream in a page's `/Metadata` slot
  was neither reported nor removed (see "metadata by presence" above). (3) An
  `/AA` entry holding an array of actions went whole under `auto_actions` but
  was invisible to the other options, so Analyze said `clean` and
  `javascript` alone was refused; each action in such an array is now a site
  like a lone one, reported, removed and counted. (4) An action chained after
  a named script, or stored in place of one, went with the tree under
  `javascript` but stayed under `launch_actions`, `submit_actions` or
  `external_links` alone; with `javascript` off the named scripts are now
  cleaned entry by entry. (5) A re-saved copy of an already-sanitized file
  read `clean` and was then refused for carrying its source's identifier
  (see the content-derived `/ID` bullet above). Still refused, and listed by
  Analyze beforehand: a script nested under a private key inside an `/AA`
  entry, or an `/AA` that is not a dictionary, when `javascript` is on and
  `auto_actions` is off. Tests: `TestMetadataHeldTwoWays`, `TestOneOptionAlone`,
  `TestIdentifierTheSourceAlreadyCarries`, `TestInfoThatIsAnotherObject`.
- **Analyze: the search for photos inside a PDF did not finish on forms that
  share their contents, and stopped 8 forms deep (#101).** The walk behind
  `location.embedded_image` and `metadata.embedded_image` remembered the
  images it had seen but not the form XObjects, so forms that draw the same
  other forms were walked again for every path that leads to them: on one
  real two-page file with 270 forms Analyze did not finish, and through the
  gate — which audits the output — neither would Sanitize. Each form is now
  entered once per document, by object number. The walk is iterative and has
  no depth limit, so a photo more than 8 forms down is found, where it was
  silently skipped. What it does with an image it cannot read is unchanged
  (#111). Tests: `TestEmbeddedImageWalk`.
- **Sanitize: a damaged source could not be sanitized at all (#101).** A file
  that opens only by being repaired — no cross-reference table, a trailer cut
  off part-way — is given a rebuilt trailer by qpdf, and that trailer can
  lack `/Size`. qpdf writes the true `/Size` over whatever the key holds, but
  only if the key exists; a file written with object streams and no `/Size`
  cannot be opened again, by qpdf or anything else. Sanitize wrote such a
  candidate, failed to read it back, and raised the library's error, naming
  its temp file. Analyze read the same source as `clean` or listed its
  findings as usual, so nothing warned of it. `sanitize_pdf` now gives the
  trailer the key before it writes; those sources sanitize, verify, and the
  output opens with no warnings. Tests: `TestDamagedSource`.
- **Sanitize: a copy that cannot be saved says so in plain words (#101).**
  Writing the cleaned candidate, reading it back and moving it into place can
  each fail for reasons that have nothing to do with the document: the
  destination folder is not there, the disk is full, the destination file is
  open in another program. The library and operating-system errors for these
  quote the path of the temp file. `sanitize_pdf` now raises
  `pdf_analyze.SanitizationOutputError` (a `RuntimeError`) saying which of
  the three it was and that nothing was saved, with no path in it; the
  original error's type is kept in `cause` (and reaches the app as
  `errorType`), its text is not, and it is not chained. As with a refusal,
  the destination is not created, replaced or deleted and no temp file is
  left. Tests: `TestCopyThatCannotBeSaved`.
- **Analyze: the form and layer checks passed over what they could not read
  (#101).** `_scan_forms` and `_scan_optional_content` were the last two
  scanners that swallowed an exception and reported nothing. They now record
  it like the others, so `forms.xfa`, `forms.acro` and `content.ocg` show as
  not checked (`audit.incomplete`) instead of absent; none of the three is
  promised by a Sanitize option, so this refuses nothing — a sanitize report
  lists them under `unevaluated`. A catalog entry that is not a dictionary is
  not a form or a layer list and is not recorded as an unread one. One real
  miss went with it: an XFA form whose `/Fields` was not an array was not
  reported at all, because counting the fields raised and the same handler
  discarded the XFA flag. Tests: `TestFormsAndLayersAreNotSilent`.
- **Redact page: a refusal over the identifier alone no longer says content
  was found (#116, #117).** When the only failed check is
  `redaction.file_id`, the redaction held and the copy was refused because it
  would still carry its source's identifier. The page said "Sensitive content
  was still detectable" for every refusal. It now says which of the two it
  was. A code comment there still said the refused output "was deleted";
  since #116 nothing is written to the destination in the first place.
- **Sanitize and Analyze missed actions on bookmarks and in the form-field
  tree (#101).** JavaScript on a bookmark, or on a form field that no page
  lists as an annotation (a parent field whose widget is a kid, a field with
  no widget), was neither reported nor removed, and the output verified. A
  button widget with a JavaScript `/A` was taken off its page and counted as
  removed while the same dictionary, script included, stayed reachable
  through `/AcroForm`. Both sides now walk one definition of where an action
  can hang (`_action_owners` / `_owner_sites` in `pdf_analyze.py`): the
  catalog, pages, annotations (their `/A`, their `/AA`, and `/PA` — the URI
  action a link keeps from before it was retargeted), bookmarks and form
  fields, each through its `/Next` chain. Outlines and the field tree are
  walked iteratively and cycle-safe. Under `javascript`, Sanitize also
  deletes the `/OnInstantiate` script of a 3D stream, which is JavaScript
  but not an action and so sits at none of those sites. The sanitizer's annotation loop keeps its own page walk because
  it rewrites `/Annots`; a test holds it to the same annotations as the
  shared walk, including an indirect `/Annots` array, a null entry and an
  annotation listed on two pages (dropped from both, counted once). Tests:
  `TestActionSites`, `TestAnnotationWalkParity`, `TestOpenActionDestination`
  in `tests/test_pdf_analyze.py`.
- **Sanitize: metadata removal deleted nothing it could not parse (#101).**
  It cleared pikepdf's parsed view of the XMP and saved, which wrote a
  malformed packet's recovered tree back with its contents, never touched a
  metadata stream on a page or other object, and invented a packet and an
  `/Info` in files that had neither. It now deletes every metadata stream
  outright, by what the stream says it is (`/Type /Metadata` or `/Subtype
  /XML`) wherever it is referenced, never by a key that happens to be called
  `/Metadata`. And `sanitize_pdf` raised `PdfError: While trying to update
  XMP metadata` under any options on a file whose XMP stream could not be
  decoded, because saving with `fix_metadata_version` parses the packet;
  that file now sanitizes. Tests: `TestMetadataPresence` in
  `tests/test_pdf_analyze.py`, `TestVerifySanitizationMetadata` in
  `tests/test_pdf_verify.py`.
- **Sanitize: embedded files outside the name tree, `/AF` and
  `/FileAttachment` annotations survived (#101).** A payload on any other
  holder was reported and kept; one in an inline file specification was not
  even reported, and the output verified with it intact. After the
  structural removals, any dictionary still reachable whose `/EF` holds a
  stream is emptied in place — the payload, and the file name, description
  and every other key that describes it. Tests: `TestEmbeddedFileRule` in
  `tests/test_pdf_analyze.py`.
- **A refused redaction no longer destroys the destination (#116).**
  `redact_pdf` renamed its output onto `output_path`, verified it there, and
  deleted it on refusal. Redacting in place (`output_path == input_path`) and
  being refused therefore deleted the user's only copy, and a refusal with a
  separate destination destroyed whatever file was already at that path. It
  now verifies, then commits: the candidate is written to a temp file in the
  destination's directory, verified there, and `os.replace`d onto the
  destination only if verification passes. A refused run never creates,
  replaces, or deletes `output_path`. The report names the real destination,
  never the temp (`VerificationReport.retarget`, which also rewrites the temp
  name in evidence text). The flatten offer (D1) is unchanged. Tests:
  `TestRedactVerifyThenCommit` in `tests/test_pdf_ops.py`, `TestReportRetarget`
  in `tests/test_pdf_verify.py`.
- **Redacted output no longer carries its source's file identifier (#117).**
  PyMuPDF and qpdf both preserve the trailer `/ID[0]` across a save, so a
  redacted copy could be matched to its original by identifier on the
  search-term and box-only paths (for an encrypted source, both elements
  survived). Every path — search terms, boxes only, flatten — now ends with
  one shared write, `pdf_analyze.save_with_content_id`, which drops the
  trailer `/ID` and has qpdf derive a new one from the output's own content
  (`deterministic_id`; available at the existing `pikepdf>=8.0.0` floor).
  Deleting `/ID` alone would not do: qpdf then mints one from the clock. New
  `redaction.file_id` check: `redact_pdf` captures the source's `/ID`
  elements and SHA-256 while it has the input open and hands them to
  `verify_redaction` as plain values (`source_ids`, `source_sha256`) — the
  verifier still never opens the input and never needs its password. An
  output sharing any element with its source is refused as a document-level
  failure (so flatten is never offered for it), unless it is byte-identical
  to the source. Every shape of `/ID` is captured and none is refused: a
  bare string instead of an array (which MuPDF carries through a save as it
  is) counts as one element, and an element that is not a string is compared
  by its PDF serialization (`pdf_analyze.id_elements`, used on both sides).
  The report's `to_dict()` gains `sourceSha256` and
  `outputSha256`. Sanitize gets the same treatment with #101. Tests:
  `TestRedactFileId` and `TestSourceFileIds` in `tests/test_pdf_ops.py`,
  `TestRedactionFileId` in `tests/test_pdf_verify.py`.
- **The redaction scrub deletes a matching XMP packet instead of clearing and
  re-saving it (#101).** The scrub cleared pikepdf's parsed view of the
  packet and let pikepdf write it back. pikepdf's recovering parser accepts
  malformed XML it cannot then edit, so for a malformed packet the recovered
  tree was re-emitted with the term still in it and the redaction ended in a
  refusal; for a packet that is not XML at all the parsed view was empty, and
  a term living only there was reported as "No matching content found". The
  scrub now reads the stream itself (as UTF-8, as UTF-16, and through
  pikepdf's parsed view — all read-only) and deletes the `/Metadata` stream
  outright on a match. Tests: `TestRedactXmpRemoval` in
  `tests/test_pdf_ops.py`.
- **Pillow 14 deprecation in the test suite.** The images-to-PDF lossless
  round-trip test compared pixels via `Image.getdata()` (removed in
  Pillow 14, 2027); it now compares `tobytes()` — equivalent and faster.
- **Windows installer + real app icon.** New `installer.iss` (Inno Setup
  6.3+, documented in the README) packages the PyInstaller one-dir build
  into `dist/PDFToolkit-Setup-<version>.exe`: all-users install to
  `Program Files\PDF Toolkit` (admin), Start Menu + optional Desktop
  shortcut, automatic uninstaller (per-user data — settings/logs, the
  on-demand translation runtime, Argos language packs — is deliberately
  left in place for upgrades), lzma2/max compression. New
  `build_installer.bat` chains the PyInstaller build and the Inno compile
  (finds `iscc.exe` on PATH or at the default install location). The
  finished `pdf_toolkit.ico` (16–256 px) is now wired everywhere: embedded
  in the built .exe (`pdf_toolkit.spec` `EXE(icon=...)`, for
  Explorer/taskbar/Alt-Tab), bundled as a data file and loaded as the
  window icon at runtime in both source and frozen modes (`app.py`
  `_app_icon()`, replacing the old programmatically-painted placeholder
  icon), and used by the installer for its setup icon and shortcuts. The
  version string now also lives in `installer.iss` (see CLAUDE.md's
  version-bump list, now five places).
- **Protect/Unlock: fix the "where did my file go?" flow — Save-As
  destination + reveal-in-folder, and no more workspace temp-file round-trip.**
  Two problems, one fix. (1) When a workspace document was loaded, Protect and
  Unlock wrote their result into the workspace temp dir and made it the new
  "working document" via `applyResult` — but encryption produces a *terminal
  artifact* (an `.epdf`, or a password-locked PDF) that the workspace's Preview
  (`getPageImages`/`fitz.open`), background scan, and Export then tried to open
  as a plain PDF (Preview failed; Export wrote `.epdf` bytes to a `.pdf`-named
  file). (2) In the ordinary flow the encrypted file was written silently
  (next to the input, or — worse — into the hidden workspace temp dir) with no
  indication of where it landed and no reliable way to open it. Now:
    - For a **single input** (the workspace document, or one dropped file) the
      Output section shows the destination — defaulting to the **same folder as
      the input file** (`<name>_protected.<ext>` / `<name>_unlocked.pdf`) — with
      a **"Choose location…"** button to pick a different file up-front, before
      protecting. The bridge's `startProtect`/`startUnlock` accept an explicit
      `outputPath` for the chosen case (extension still forced to match the
      chosen format, so a mistyped name can't corrupt the type). Multiple
      dropped files keep the folder + naming-template batch flow.
    - On success nothing is auto-opened; instead the results panel shows each
      output's **full path** with a **"Show in folder"** button that reveals the
      file in the OS file manager with it selected (new `revealFile` bridge
      slot — `explorer /select,` on Windows, `open -R` on macOS) plus the
      existing "Open folder". This addresses the original report — open the
      *crypto version* of the file, on demand, not a temp copy.
    - Protect/Unlock no longer touch the workspace working document (they join
      the established **terminal-tool** pattern). The atomic-write temp files
      inside `epdf_crypto.py` were never the issue — those are written and
      immediately `os.replace`d into the real output.

  Frontend rebuilt (`web-react/dist/`).

## v4.23

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
- **Disabled UPX compression in the PyInstaller spec (PKG-03).** `upx=True`
  with an empty `upx_exclude` compressed every bundled binary, including the
  PySide6 Qt6 DLLs and the QtWebEngine runtime -- UPX-compressing Qt/WebEngine
  DLLs is a well-documented PyInstaller failure mode ("could not load the Qt
  platform plugin"), and this is a QWebEngine app, the highest-risk case.
  Conditional in practice (`build.bat` never installs UPX and CI has no
  frozen-build job, so `upx=True` was usually a silent no-op) but a real trap
  on any dev machine that independently has UPX on PATH. Took `upx=False` over
  an ever-growing `upx_exclude` list -- a QWebEngine app's set of Qt-related
  binaries can grow across Qt version bumps, so excluding by name is an
  ongoing maintenance burden with a silent-failure mode if it falls behind;
  disabling UPX removes the risk entirely at the cost of a larger distributed
  binary.
- **PyInstaller spec no longer lists a deleted module as a hidden import
  (PKG-02).** `hiddenimports` still included `"ui.dialogs"`, but `ui/dialogs.py`
  was deleted in v4.21 -- PyInstaller emitted a harmless "hidden import not
  found" warning on every build. Removed the stale entry (re-verified nothing
  else references it first).
- **Frozen (PyInstaller) build now bundles the portable translation font
  (PKG-01).** `pdf_translate` prefers the committed `assets/fonts/DejaVuSans.ttf`
  for image-preserving translated-PDF output, but the spec's `datas` only
  bundled `web-react/dist` -- in a frozen build the font was absent and the
  loader silently fell back to OS fonts, then to Latin-only `helv`, defeating
  the "portable across machines" guarantee (in practice usually masked on
  Windows by the `arial.ttf` fallback, unless that's also missing). Added the
  fonts dir to `datas`, mirroring the existing `web-react/dist` entry.
- **Missing CLI inputs now count toward the failure tally and exit code
  (CLI-04).** A non-existent input printed a `SKIP` line and moved on without
  incrementing any counter, inconsistent with the invalid-magic case right
  below it -- missing files never appeared in the `Summary:` line and
  (combined with CLI-01) were invisible to the exit code. Now increments
  `n_err`, same as the invalid-magic case. Verified: a batch with one valid
  and one missing input now exits 1 and shows `Summary: 1 skipped, 1 failed`;
  pre-fix it exited 0 with no summary line at all. Added
  `tests/test_cli.py::TestCLI::test_missing_input_counts_as_failure`.
- **CLI no longer crashes at exit on non-interactive stdin (CLI-03).** Without
  `--no-pause`, `main()` ended with a blocking `input("Press Enter…")` with no
  `isatty()` guard; piped/redirected/CI stdin raised `EOFError`. Post-CLI-01,
  this crash pre-empted the real `sys.exit(1 if n_err else 0)` a few lines
  later, so even a fully successful run exited 1 due to the crash rather than
  the actual result. `input()` is now skipped entirely when
  `not sys.stdin.isatty()`. Verified empirically with a real closed pipe (note:
  Windows' CRT reports `NUL`/`DEVNULL` as a tty, so that alone doesn't
  reproduce this -- a genuine pipe does). Added
  `tests/test_cli.py::TestCLI::test_no_pause_flag_omitted_on_closed_stdin_does_not_crash`.
- **CLI batch mode no longer overwrites outputs for same-basename inputs
  (CLI-02).** In `-o <dir>` mode the output name was `<basename>_compressed.pdf`
  keyed only on basename, so `a/report.pdf` and `b/report.pdf` both resolved to
  `out/report_compressed.pdf` -- the second silently overwrote the first
  (lost derived output, not source data). Same disambiguation approach as
  OPS-04: a `seen_out_names` map tracks each generated name within the batch;
  the first occurrence is unchanged, later ones get `_2`, `_3`, … inserted
  before the extension. Verified empirically before/after. Added
  `tests/test_cli.py::TestCLI::test_same_basename_batch_produces_distinct_outputs`.
- **Non-dict `kdf_params` in an `.epdf` header now raises `EPDFError` instead
  of a raw `TypeError` (CRY-02).** `_derive_key` merged `kdf_params` via
  `{**DEFAULT_KDF_PARAMS, **(kdf_params or {})}`; a crafted header setting
  `kdf_params` to a non-mapping (a JSON list or string) made the `**` spread
  raise `TypeError` before `_validate_kdf_params` ever ran, escaping its
  `EPDFFormatError` contract (same gap as CRY-01, fed from the same decrypt
  path). `_derive_key` now asserts `isinstance(kdf_params, dict)` before the
  merge and raises `EPDFFormatError` otherwise. Verified empirically
  before/after against two tampered-header cases. Added
  `tests/test_epdf_crypto.py::TestDecryptNonDictKdfParams`.
- **`.epdf` decrypt now raises `EPDFError` for malformed headers instead of a
  raw exception (CRY-01).** Attacker-controllable header fields were read
  unguarded: a non-numeric `version` raised `ValueError`; a missing
  `salt`/`nonce` raised `KeyError`; non-base64 content raised
  `binascii.Error`. None were wrapped, unlike `epdf_read_metadata`, which
  breaks callers relying on `except EPDFError` to distinguish "bad file" from
  a programming bug (not exploitable -- any tampered field still fails
  AEAD/HMAC regardless). Both reads now catch their respective exception
  types and re-raise as `EPDFFormatError`. Verified against three tampered-
  header cases empirically before/after. Added
  `tests/test_epdf_crypto.py::TestDecryptMalformedHeader`.
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
- **Crypto round-trip tests now assert full byte equality, not just the 5-byte
  `%PDF-` magic (TST-05).** `tests/test_epdf_crypto.py`'s per-cipher round-trip
  tests (ChaCha20-Poly1305, AES-256-GCM, Camellia-256-CBC, Argon2d KDF) only
  checked that the decrypted file started with `%PDF-`; a separate
  `test_file_sizes_match` checked size alone. Both are now one assertion per
  test — `open(dec, "rb").read() == open(sample_pdf, "rb").read()` — which
  subsumes magic bytes and size and catches any content-fidelity regression the
  narrower checks would miss. `test_file_sizes_match` removed as redundant.
  Flips TST-05 to Fixed — closes out every finding in `AUDIT.md`.

### Docs
- **Removed a fabricated "stanza" security-upgrade line from the v4.20 section
  (DOC-02).** It claimed *"Upgraded stanza to resolve CVE-2026-54499…"*, but
  the translation stack is Argos + Tesseract + langdetect and `stanza` never
  appeared anywhere in code, dependency files, or git history outside this one
  line — nothing to correct it *to*, so it's removed rather than replaced with
  a fabricated event.
- **README no longer advertises a removed Windows context-menu + About dialog
  (DOC-01).** Both belonged to the native-Qt widget UI deleted in v4.21; a
  repo-wide grep for `winreg`/`HKEY_`/About-dialog code returns nothing.
  Removed the stale bullet from the GUI features list.
- **Added `AUDIT.md`** — a point-in-time code-audit snapshot of v4.22 (~40 open
  findings across the engine, PDF ops, crypto, analyze, translate, bridge, CLI,
  frontend, tests, docs, and packaging), each adversarially verified against the
  source, with severity, evidence, and a suggested fix. Nothing is fixed yet — it's
  the shared "where we stand" reference. Linked from `README.md` (Development
  section + Files table) and folded into `CLAUDE.md`'s doc-currency rules.
- **`CLAUDE.md` / `README.md`** — contributors now consult the relevant `AUDIT.md`
  findings before touching a subsystem and flip a finding's **Status** when they
  fix it (recording the fix in this changelog too).
- **Removed `AUDIT.md`.** Every finding in its master list is now `✅ Fixed` (the
  last, `TST-05`, closes above) — the snapshot has fully served its purpose and
  each fix is already recorded here in detail, so the standalone doc is now
  redundant rather than current. Removed the doc-currency bullet and the "consult
  before touching a subsystem" workflow it described from `CLAUDE.md`, and the
  "Known issues" pointer + Files-table row from `README.md`.

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
