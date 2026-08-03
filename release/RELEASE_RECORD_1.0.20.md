# Release record — v1.0.20

## Status and identity

- Status: public beta
- Prepared: 2026-08-02
- Published: 2026-08-03 UTC (2026-08-02 America/Phoenix)
- Version: `1.0.20`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon

v1.0.20 is live under the existing AnkiWeb item `677438639` and GitHub tag
`v1.0.20`. No duplicate listing or compatibility branch was created.

## Frozen public artifact

- File: `Smart_Search_Medical_1.0.20.ankiaddon`
- Bytes: `52,275,482`
- Files: `68`
- SHA-256: `dca1f90d52b3a4d64c86108547a7178108a6a55ddcb1d435a976fa9f8415cf8f`

Two independent builds were byte-identical. Archive CRC validation passed;
paths are unique; the only `user_files` member is its README. The archive has
no profile data, collection or search database, vector data, expanded runtime,
model download, cache, log, temporary file, test, development script, source
control metadata, or Python bytecode.

## Test and compatibility evidence

The complete suite passed with `431/431` tests and zero skips in every target
environment below. The native updater probe passed in each environment.

| Anki | Python / Qt | Full suite | Native updater probe |
| --- | --- | ---: | ---: |
| 24.11 | Python 3.9 / Qt 6.6 | 431 pass | Pass |
| 25.02.7 | Python 3.9 / Qt 6.6 | 431 pass | Pass |
| 25.07.5 | Python 3.13 / Qt 6.9 | 431 pass | Pass |
| 25.09.4 | Python 3.13 / Qt 6.9 | 431 pass | Pass |
| 25.09.5 | Python 3.13 / Qt 6.9 | 431 pass | Pass |
| 26.05 | Python 3.13 / Qt 6.11 | 431 pass | Pass |
| 26.08 | Python 3.13 / Qt 6.11 | 431 pass | Pass |

The local Python 3.11 suite also passed with `431` tests and `78` intentional
no-Qt skips, including a run that treats `ResourceWarning` as an error. Python
3.9 compilation passed, and the focused compatibility suite passed `176/176`.

## Official installer validation

Anki's real `AddonManager` implementation completed a disposable clean install
and a v1.0.19-to-v1.0.20 upgrade in all seven environments. Each upgrade
preserved customized configuration, a persistence sentinel, and a synthetic
`search.sqlite3` file. Every final installation contained only numeric folder
`677438639`, with no named duplicate and no stranded `files_backup` directory.

No real Anki profile or installed add-on was changed during these tests.

## Performance and lifecycle evidence

- Reviewer answers invoke no indexing, collection reads, fuzzy refresh, UI
  refresh, or Semantic maintenance.
- Review mode pauses and cooperatively cancels background maintenance; work
  resumes only after the reviewer has settled.
- Exact note edits enter a crash-safe profile-local journal and are applied in
  bounded targeted batches rather than rebuilding the collection.
- Sync, import, undo/redo, and unknown bulk changes use compact manifests to
  identify affected notes and cards before scheduling repairs.
- Filtered-deck scheduling changes are normalized to each card's home deck.
- Semantic inference is lazy, cooperatively cancellable, and released after
  use. Vector scans, deltas, and rebuild reads are bounded.
- Lexical and Semantic generations, durable work, and vector counts are checked
  before a Semantic index can be published as ready.

## Remaining operational limits

The first full Smart/Exact setup and first full Semantic preparation still
require CPU, disk I/O, and memory, but they run outside review mode and are
bounded/cancellable. Native inference libraries may retain allocator caches
until Anki exits even after the active Semantic model is released.

## GitHub distribution

- Pull request: https://github.com/salehsquared/smart-search-for-anki-medical/pull/4
- Merge commit: `625b3506765ea074eea8542ac616f3cc5011f10e`
- Annotated tag: `v1.0.20`
- Public prerelease:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.20
- Final `main` validation:
  https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/30777389049

The public release contains the `.ankiaddon` and checksum sidecar. Fresh
unauthenticated downloads were byte-identical to the frozen local files, and
both tagged screenshot URLs used by AnkiWeb returned HTTP 200.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`
- Listing date: 2026-08-03 UTC (2026-08-02 America/Phoenix)

The existing item and its single branch were updated in place. The rendered
listing shows the v1.0.20 review-isolation description, both publication
images, the expected support contact, and Anki 24.11–26.08.

Requests at point versions `241100` and `260800` each downloaded exactly
`52,275,482` bytes with SHA-256
`dca1f90d52b3a4d64c86108547a7178108a6a55ddcb1d435a976fa9f8415cf8f`.
Both archives were byte-identical to the frozen artifact and reported
`human_version=1.0.20`. Out-of-range requests at `241099`, `260801`, and
`260900` returned HTTP 404.

## Public numeric-code installation

Anki's official `download_and_install_addon()` path completed clean installs
from code `677438639` in disposable Anki 24.11 and 26.08 environments. Both
returned `InstallOk`; all 68 archive members matched the frozen artifact
byte-for-byte, and the final directory contained only numeric folder
`677438639` plus Anki-managed `meta.json`.

A disposable Anki 26.05 environment then installed v1.0.19, added customized
configuration, a `user_files` persistence sentinel, and a synthetic
`search.sqlite3` database with a probe row before updating through the same
live numeric-code path. The update returned `InstallOk`, installed v1.0.20,
preserved the exact configuration values and both files byte-for-byte, and left
no named duplicate or `files_backup` directory.

No real Anki profile or installed add-on was opened or modified by these
public-route tests. First-window support monitoring remains ongoing.
