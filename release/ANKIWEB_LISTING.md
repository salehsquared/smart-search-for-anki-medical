# AnkiWeb listing record — v1.0.19

## Live listing

- **Status:** publication candidate; existing v1.0.15 listing remains public
- **Prepared:** 2026-08-01
- **AnkiWeb code:** `677438639`
- **Public URL:** https://ankiweb.net/shared/info/677438639
- **Intended supported Anki versions:** 24.11 through hard maximum 26.08

## Listing fields

**Title**

Smart Search for Anki — Medical

**One-line summary**

Fast, typo-tolerant medical search for Anki, with native filters, local semantic search, and safe bulk actions.

**Support contact**

product@medbrevia.com

**Required support URL**

https://github.com/salehsquared/smart-search-for-anki-medical/issues/new/choose

**Project and issue tracker**

https://github.com/salehsquared/smart-search-for-anki-medical

**Privacy notice**

https://medbrevia.com/legal/smart-search-privacy

**Model provenance**

https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8

**Mobile app**

https://medbrevia.com/app

**License**

Use the license declared in `LICENSE.txt`. The add-on also contains third-party
components and terminology covered by `THIRD_PARTY_NOTICES.md` and `licenses/`.

## Canonical listing description

Install from AnkiWeb with code **677438639**:
https://ankiweb.net/shared/info/677438639

### Find the card you meant

Smart Search for Anki — Medical is a keyboard-first search palette for large
medical collections. Press **Command-K on macOS** or **Ctrl-K on Windows/Linux**
from Anki and start typing.

**Public beta compatibility:** v1.0.19 supports **Anki Desktop 24.11 through
26.08** and was integration-tested on macOS with Apple silicon. Semantic Search
requires **macOS 14 or later on Apple silicon**. Windows, Linux, and Intel Mac
integration testing is not part of this release's support claim.

- **Smart** search is case-insensitive, tolerates misspellings, and recognizes
  common medical and medication aliases. For example, `buproprion` can find
  `bupropion`, and a brand name can find its generic equivalent.
- **Exact** search keeps your wording literal while supporting Anki's native
  search syntax.
- **Semantic** search finds cards by clinical meaning using a model that runs
  locally on your computer.

Structured searches such as `deck:`, `tag:`, `note:`, `is:`, `flag:`, `prop:`,
`rated:`, field searches, Boolean groups, and wildcards are delegated to Anki.
Card-specific filters return only the sibling cards that actually match.

### Designed for real collections

- Use the searchable hierarchical deck picker to select several decks or keep
  specific subdeck branches excluded from a selected parent.
- Preview and edit the selected card in a resizable pane without leaving the
  search window. The pane can be expanded or temporarily dismissed.
- Open the selected result—or exactly the checked results—in Anki's Browser.
- Shift-click to select a range; use **All shown**, **None**, or **Invert** for
  quick bulk selection.
- Flag, suspend, unsuspend, add tags, or remove tags without leaving search.
- See compact suspension and flag indicators directly on each result.
- Weak matches are removed with adaptive relevance cutoffs instead of filling
  the list to an arbitrary maximum.
- Adds, edits, and deletes update the disposable search data in the background.
- Public AnkiWeb copies offer **Check & Update** in About and delegate the
  installation to Anki's native updater.

Tags apply to notes. Flags and suspension apply to cards. Changes use Anki's
supported, undoable collection operations and form one clean Undo step per
action. The add-on never writes directly to `collection.anki2`.

### Private by design

Searches, card text, spelling data, and search indexes stay on your computer.
There is no analytics or telemetry.

Smart and Exact search require no model download. Semantic Search has a separate
one-time setup that downloads its required files only after you choose to
enable it. The download is retrieved from the disclosed upstream model source
and verified before use.

### Semantic compatibility

**Semantic Search currently supports macOS 14 or later on Apple-silicon Macs
only.** Smart and Exact remain available when Semantic is unsupported, not yet
set up, or still preparing its separate index.

The initial Smart/Exact setup must finish before those modes can search.
Semantic preparation is separate and may take several minutes on a large
collection. You can continue using Smart and Exact while Semantic indexes.

### Shortcuts

- **Command/Ctrl-K:** open or focus Smart Search
- **Up/Down** or **Control-J/Control-K:** move through results
- **Return:** open the highlighted result in Anki's Browser
- **Control-Shift-P:** open or close the inline card preview
- **Command/Ctrl-Return:** open checked results, or all shown results when
  nothing is checked
- **Space:** check or uncheck the focused result
- **Shift-click:** select a continuous range
- **Command/Ctrl-1, 2, or 3:** switch between Smart, Exact, and Semantic
- **Escape:** clear the query or close the palette

### Important limitations

- This is an Anki Desktop add-on. It does not run inside AnkiMobile or AnkiDroid.
- v1.0.19 supports Anki Desktop 24.11 through 26.08 only.
- Search indexes are local to each desktop profile and do not sync through
  AnkiWeb.
- Semantic results are relevance suggestions, not clinical guidance. Always
  verify medical information against the card source and current references.
- See the Known Limitations and Support documents linked from the project page
  before reporting an issue.

### About

Created by **Saleh Mostafa** with **MedBrevia**. Smart Search is an independent
Anki add-on and is not affiliated with or endorsed by Anki or AnkiWeb.

Explore the MedBrevia mobile app: https://medbrevia.com/app

Questions or feedback: product@medbrevia.com

Privacy notice: https://medbrevia.com/legal/smart-search-privacy

Project, support, and complete source:
https://github.com/salehsquared/smart-search-for-anki-medical

### Existing manual-install users

Do not load a manually installed development copy and the AnkiWeb copy at the
same time. Remove or disable the manual copy before installing the AnkiWeb
code. Local search indexes are disposable and can be rebuilt safely.

## Suggested screenshot order

1. `assets/screenshots/01-smart-search.png` — typo recovery, filter chip, and
   ranked medical results
2. `assets/screenshots/02-semantic-setup.png` — separate Semantic preparation
   with Smart/Exact availability made explicit
3. `assets/screenshots/03-bulk-actions.png` — multi-select, Browser, flags,
   suspension, and tags
4. `assets/screenshots/04-about-privacy.png` — creator credit, privacy promise,
   mobile-app link, and native update control
5. `assets/screenshots/05-inline-editor.png` — real clean-profile capture of
   the inline editor handling an incomplete native filter without losing the
   active search

All screenshots use synthetic medical study content. Screenshots 01 and 05 are
clean-profile captures of the frozen add-on. Screenshots 02–04 are deterministic
release compositions reviewed against that build; they are illustrative and
contain no real collection data.

## Publication record

- v1.0.15 was published on 2026-07-31 as AnkiWeb item `677438639`; it remains
  the live version until this candidate is uploaded.
- v1.0.19 must update the existing item with `min_point_version=241100` and
  hard `max_point_version=-260800`; do not create a second listing or branch.
- `release/ANKIWEB_DESCRIPTION_1.0.19.md` is the canonical description source.
- Final server metadata, served-archive comparison, and numeric-code QA belong
  in `release/RELEASE_RECORD_1.0.19.md` after upload.

## Listing maintenance notes

- Do not paste internal engine names, model identifiers, filesystem paths,
  profile names, or index implementation details into the listing.
- Do not promise support for an Anki version or operating system until it has
  passed the compatibility matrix in `RELEASE_CHECKLIST.md`.
- Update the declared version, package size, Semantic setup size, and supported
  Anki range before each future publication.
- If AnkiWeb strips or changes Markdown formatting, preserve the headings and
  paragraph order rather than adding decorative HTML.
