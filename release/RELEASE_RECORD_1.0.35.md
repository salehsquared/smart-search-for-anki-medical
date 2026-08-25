# Release record — v1.0.35

## Status and identity

- Status: public beta
- Published: 2026-08-25 UTC (2026-08-25 America/Phoenix)
- Version: `1.0.35`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.35 updated the existing AnkiWeb item and its single compatibility branch
in place. No duplicate item or overlapping branch was created.

## Frozen artifact

- File: `Smart_Search_Medical_1.0.35.ankiaddon`
- Bytes: `52,924,796`
- Files: `68` unique members
- SHA-256: `befcbabb81b74b09f2b89fa129ac9f2c912c88fceec7a6234e2974a73c29df1b`
- Checksum-sidecar SHA-256:
  `2bfcfc0476cbf1f300fd318084a1316164214e17567dbd2cbbbcc7936cb71fc8`

Two fresh builds from the exact merged `main` tree were byte-identical to the
frozen candidate. Archive CRC, duplicate-name, path, privacy, runtime-hash,
source-version, and manifest checks passed. The archive contains no profile
data, collection databases, generated indexes, models, logs, caches, or
expanded runtimes. `user_files/README.txt` is its only packaged `user_files`
member.

## Validation

The exact candidate passed the local source suite: `720` tests, `712` passed,
`8` expected skips, and no failures or errors. The three source-runtime and
seven Anki-runtime jobs in each GitHub Actions run reported:

| Runtime | Passed | Skipped | Failures / errors |
|---|---:|---:|---:|
| Python 3.9 | 712 | 8 | 0 |
| Python 3.11 | 712 | 8 | 0 |
| Python 3.13 | 712 | 8 | 0 |
| Anki 24.11 | 719 | 1 | 0 |
| Anki 25.02.7 | 719 | 1 | 0 |
| Anki 25.07.5 | 719 | 1 | 0 |
| Anki 25.09.4 | 719 | 1 | 0 |
| Anki 25.09.5 | 719 | 1 | 0 |
| Anki 26.05 | 719 | 1 | 0 |
| Anki 26.08 | 719 | 1 | 0 |

Each ten-job run therefore completed `7,169` passes, `31` expected skips, and
no failures or errors. Source-runtime skips are optional Anki integration
probes; the sole skip under each Anki runtime is the opt-in real-model worker
test because `SMART_SEARCH_REAL_MODEL_DIR` was not set.

The reviewed pull-request run was `32888327226`:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/32888327226

The post-merge `main` run `32888615008` repeated all ten jobs successfully on
merge commit `59874c4683856834764e83517cd4d324709f3849`:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/32888615008

The release tests cover the collection lifecycle guard before collection or
media sync, cancellation rechecks for queued work, process-wide containment of
PyO3-wrapped backend panics, Semantic model and vector work in the disposable
helper process, Browser-to-Smart-Search and Smart-Search-to-Browser query
handoffs, profile-scoped default-deck behavior, and the safe suspended-only
filter. They also retain the prior coverage for native search semantics,
reviewer-safe Semantic work, bounded timeouts, latest-request-wins behavior,
safe card actions, guarded Undo, and inline preview/editing.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/18
- Merge commit: `59874c4683856834764e83517cd4d324709f3849`
- Annotated tag object: `d787f6fedb248e42860053fc8e81b8e0b916453b`
- Annotated tag: `v1.0.35`
- Release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.35
- Published: `2026-08-25T19:18:48Z`

The release is the current stable/latest GitHub release, not a draft or
prerelease. The annotated tag dereferences to the PR #18 merge commit. Fresh
public downloads of both assets were byte-identical to the frozen files.
GitHub reports `52,924,796` bytes and the frozen SHA-256 for the archive; the
104-byte checksum sidecar has the SHA-256 recorded above.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`
- Server modification timestamp: `1787686396`
- Server modification time: `2026-08-25T19:33:16Z`
- Package manifest modification timestamp: `1786909246`

Requests at point versions `241100`, `250207`, `250705`, `250904`, `250905`,
`260500`, and `260800` reached the same compatibility branch. Requests at
`241099`, `260801`, and `260900` returned HTTP 404 with
`Add-on not available for your Anki version.`

The minimum- and maximum-version downloads each contained `52,924,796` bytes
and `68` unique archive members with SHA-256
`befcbabb81b74b09f2b89fa129ac9f2c912c88fceec7a6234e2974a73c29df1b`.
Both files were byte-identical to the frozen GitHub artifact, passed ZIP
integrity checks, and reported `human_version=1.0.35`, minimum `241100`, and
package maximum `260800` in the manifest.

The rendered listing shows the v1.0.35 safety and Browser-integration notes,
the Anki 24.11–26.08 range, install code, and local-privacy statement. Its
synthetic screenshot loaded from the tagged v1.0.35 GitHub path at `1040×732`
pixels. The **Contact Author** control targets the GitHub issue chooser.

AnkiWeb renders the bare project, mobile-app, and feedback strings in the
description as text rather than links. The local-privacy statement renders,
but the separate privacy URL is not present as a link on the listing. The
privacy endpoint itself returns HTTP 200. These presentation details do not
affect package distribution.

The tagged hero image is the privacy-safe v1.0.15 clean-profile capture already
identified in `release/assets/screenshots/README.md`. It remains a generic
illustration of search results and is not used as v1.0.35 interface-parity or
compatibility evidence. The new v1.0.35 controls and behaviors are covered by
the automated tests and release validation above; visual parity is not claimed
from the older image.

## Public numeric-code installation

Anki's real `aqt.addons.download_and_install_addon()` path installed code
`677438639` into fresh disposable roots under Anki 24.11 and 26.08. Each
selected branch 0 with minimum `241100` and hard maximum `-260800`, downloaded
the exact frozen bytes, returned `InstallOk` with `compatible=True`, and
created exactly one numeric `677438639` add-on folder.

Both installations contained all `68` archive members byte-for-byte plus only
Anki's expected generated `meta.json`. There were no missing or mismatched
members, named duplicates, disabled copies, or backup paths. Generated metadata
on both boundaries reported `human_version=1.0.35`, branch 0, minimum `241100`,
hard maximum `-260800`, and server timestamp `1787686396`.

The isolated evidence root was
`/private/tmp/smart-search-v1035-boundary.fFdJVy`. It did not instantiate or
open a personal profile or collection. Anki 24.11 emitted only its known
non-fatal missing `macos_helper` diagnostic; all download, install, archive,
metadata, and containment assertions passed at both boundaries.

The same Anki 24.11 and 26.08 disposable environments then installed the exact
frozen public v1.0.26 archive and upgraded it through the live numeric-code
path. Both upgrades returned `InstallOk` with `compatible=True` and installed
the exact v1.0.35 public bytes. Each case preserved a non-default ten-key
configuration, disabled state, custom top-level metadata, a binary sentinel,
nested settings, and a synthetic SQLite index byte-for-byte. SQLite
`quick_check` returned `ok` before and after, and its rows were unchanged.
There was no duplicate or backup add-on path.

The harness kept all destructive containment inside the asserted disposable
root. Its manager subclass overrode only `deleteAddon()` so synthetic old files
were removed inside that root instead of being sent to the user's Trash.
Anki's official backup/restore and live download/install logic remained in use.

## Personal-installation safety

Publication and public-serving checks did not open or modify the personal Anki
profile, collection, configuration, indexes, or sync state. They did not
restart the running Anki app. Package and installation checks used public
downloads, archive inspection, CI, and disposable roots only.

## Publication gates

- [x] PR #18 merged at `59874c46`; annotated tag `v1.0.35` points to the
      certified tree.
- [x] Both ten-job GitHub matrices are green.
- [x] The merged tree reproduces the frozen archive exactly.
- [x] GitHub assets exactly match the frozen archive and checksum.
- [x] GitHub marks v1.0.35 as the current stable/latest release.
- [x] Existing AnkiWeb item `677438639` was updated in place.
- [x] Supported and out-of-range routing behaves correctly.
- [x] The public boundary downloads hash-match the frozen archive.
- [x] Disposable public numeric-code installations pass at both support
      boundaries.
- [x] Public v1.0.26-to-v1.0.35 upgrades at both support boundaries preserve
      configuration, metadata, `user_files`, and synthetic SQLite data.
- [ ] Monitor the support channel during the first release window.

First-window support monitoring remains ongoing.
