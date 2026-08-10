# Release record — v1.0.26

## Status and identity

- Status: public beta
- Published: 2026-08-10 UTC (2026-08-10 America/Phoenix)
- Version: `1.0.26`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.26 updated the existing AnkiWeb item and its single compatibility branch
in place. No duplicate item or overlapping branch was created.

## Frozen artifact

- File: `Smart_Search_Medical_1.0.26.ankiaddon`
- Bytes: `58,116,764`
- Files: `67` unique members
- SHA-256: `07b96d826d88280355babf70ad8311dbac3a2fb3a84c95fbf0b2642586fb027c`

Two clean pre-merge builds, the frozen candidate, and two fresh builds from the
exact merged `main` tree were byte-identical. Archive CRC, duplicate-name,
path, privacy, runtime-hash, source-version, and manifest checks passed. The
archive contains no profile data, generated indexes, logs, caches, tests,
scripts, or expanded runtime directories; `user_files/README.txt` is its only
packaged `user_files` member.

## Validation

The exact candidate passed the local source suite: `627` tests, `619` passed,
`8` expected skips, and no failures or errors. The three source-runtime and
seven Anki-runtime jobs in each GitHub Actions run reported:

| Runtime | Passed | Skipped | Failures / errors |
|---|---:|---:|---:|
| Python 3.9 | 619 | 8 | 0 |
| Python 3.11 | 619 | 8 | 0 |
| Python 3.13 | 619 | 8 | 0 |
| Anki 24.11 | 626 | 1 | 0 |
| Anki 25.02.7 | 626 | 1 | 0 |
| Anki 25.07.5 | 626 | 1 | 0 |
| Anki 25.09.4 | 626 | 1 | 0 |
| Anki 25.09.5 | 626 | 1 | 0 |
| Anki 26.05 | 626 | 1 | 0 |
| Anki 26.08 | 626 | 1 | 0 |

Each ten-job run therefore completed `6,239` passes, `31` expected skips, and
no failures or errors. Source-runtime skips are optional Anki integration
probes; the sole skip under each Anki runtime is the opt-in real-model worker
test because `SMART_SEARCH_REAL_MODEL_DIR` was not set.

The reviewed pull-request run was `31405413911`:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/31405413911

The post-merge `main` run `31406285072` repeated all ten jobs successfully on
merge commit `4e2ecc17fafaaa6c22a5c8414d4156a33d9b207e`:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/31406285072

The release tests cover request-driven Semantic searches during review,
reviewer-paused background maintenance, pre-dispatch cancellation, worker
crash and model/index failures, the 45-second end-to-end watchdog,
latest-request-wins supersession, prompt result rendering before best-effort
mutable card-state refresh, first-card preview after that refresh, and warm
helper cleanup after close or the bounded idle lease. They also retain the
v1.0.25 coverage for compact Extra-only rows, Related-result restoration,
native Exact semantics, filters, guarded Undo, and automatic preview.

Before publication, the exact v1.0.25 archive upgraded to the exact v1.0.26
candidate in disposable Anki 24.11, 25.02.7, 25.07.5, 25.09.4, 25.09.5,
26.05, and 26.08 environments. Every installer returned `InstallOk` and
preserved customized configuration, top-level metadata, the complete
`user_files` tree, sentinels, and a synthetic SQLite index byte-for-byte;
SQLite `quick_check` and the probe query passed. Clean local candidate installs
also passed at the 24.11 and 26.08 boundaries. Evidence for this release run
was preserved under `/private/tmp/smart-search-v1026-matrix.TU9pBy`.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/16
- Merge commit: `4e2ecc17fafaaa6c22a5c8414d4156a33d9b207e`
- Annotated tag object: `dd07531288055a125064f3882a40bc9eafca12ff`
- Annotated tag: `v1.0.26`
- Release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.26
- Published: `2026-08-10T16:00:47Z`

The release is the current stable/latest GitHub release, not a draft or
prerelease. The annotated tag dereferences to the PR #16 merge commit. Fresh
public downloads of both assets were byte-identical to the frozen files.
GitHub reports `58,116,764` bytes and the frozen SHA-256 for the archive; the
104-byte checksum sidecar has SHA-256
`055cbb903f384f7256fffa96d22500883c1724ea33a001105a5479f7141b93cb`.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`
- Server modification timestamp: `1786378023`
- Server modification time: `2026-08-10T16:07:03Z`

Requests at point versions `241100`, `250207`, `250705`, `250904`, `250905`,
`260500`, and `260800` reached the same compatibility branch. Requests at
`241099`, `260801`, and `260900` returned HTTP 404.

All seven supported downloads contained `58,116,764` bytes and `67` archive
members with SHA-256
`07b96d826d88280355babf70ad8311dbac3a2fb3a84c95fbf0b2642586fb027c`.
Every served file was byte-identical to the frozen artifact, passed its ZIP
integrity check, and reported `human_version=1.0.26`, minimum `241100`, and
package maximum `260800` in the manifest.

The rendered listing shows the v1.0.26 Semantic-review lifecycle, bounded
timeout and visible errors, request supersession, prompt result rendering,
90-second idle lease, Anki 24.11–26.08 range, install code, support link, and
privacy statement. Its synthetic screenshot loaded from the tagged v1.0.26
GitHub path at `1040×732` pixels. A cache-busted fresh listing showed the new
content immediately after upload; an already-open pre-publication view retained
the prior cached text until refreshed.

AnkiWeb renders the bare project, mobile-app, and feedback strings in the
description as text rather than links. The separate **Contact Author** control
correctly targets the GitHub issue chooser, and the privacy statement renders.
The intended privacy endpoint returns HTTP 200 but is not linked from the
listing; neither presentation detail affects package distribution.

## Public numeric-code installation

Anki's official `aqt.addons.download_and_install_addon()` path installed code
`677438639` into empty disposable roots under Anki 24.11 and 26.08. Each
selected branch 0 with minimum `241100` and hard maximum `-260800`, downloaded
the exact frozen bytes, returned `InstallOk`, installed all 67 members exactly,
and created only the numeric `677438639` folder plus Anki's expected
`meta.json`. No named duplicate, disabled copy, or `files_backup` appeared.

Disposable Anki 24.11, 25.02.7, 25.07.5, 25.09.4, 25.09.5, 26.05, and 26.08
installations then installed the frozen public v1.0.25 archive and upgraded it
through the same live numeric-code path. All seven public downloads reported
server timestamp `1786378023`, returned `InstallOk`, and installed the exact
v1.0.26 public bytes. Every case preserved customized configuration and
top-level metadata, the complete synthetic `user_files` tree, sentinels, probe
notes, and a synthetic SQLite index byte-for-byte. SQLite `quick_check` and its
probe query passed; no duplicate or backup folder was created.

The isolated evidence root was preserved at
`/private/tmp/smart-search-v1026-matrix.TU9pBy/public-numeric.ZowAFe`; each of
its seven `upgrade-anki*/result.json` and two boundary
`clean-anki*/result.json` files records the exact request URL, redirect
metadata, payload hash, installer result, installed metadata, containment
guards, and persistence assertions. The harness never instantiated the real
profile manager and did not open or modify the personal Anki profile.

The cached 24.11 and 25.02.7 runtimes emitted their known non-fatal missing
`pip_system_certs` and `macos_helper` diagnostics; 25.07.5, 25.09.4, and
25.09.5 emitted only the `pip_system_certs` diagnostic. The 26.05 and 26.08
cases were clean, and every TLS, download, install, and persistence assertion
passed in all nine cases.

## Personal installation

Before publication, the personal Anki 26.05 installation was upgraded to the
v1.0.26 candidate through Anki's supported installer. A reviewer-mode Semantic
query completed normally with automatic first-card preview, and a rapid
replacement query returned the newest result in 642 ms. No cards were answered
or rescheduled; configuration, the complete `user_files` tree, and the active
40,683-note generation-1130 lexical and Semantic indexes were preserved.

After the Add-note work was saved and Anki had quit cleanly, the complete
named add-on folder was backed up to
`/Users/saleh/Documents/Personal/anki-smart-search-backups/2026-08-10-092940-pre-public-v1.0.26`.
The literal AnkiWeb-served `p=241100` archive was then installed through Anki's
supported add-on installer and Anki was restarted. All 67 public archive
members matched the installed bytes, only the named `smart_search_medical`
folder was present, and no numeric duplicate or `files_backup` appeared.

The canonical configuration and complete `meta.json` hashes were unchanged.
The persistent `user_files` tree retained 4,516 files and 514,374,014 bytes;
its active external index files reconciled normally from generation 1139 with
40,691 notes to aligned lexical and Semantic generation 1141 with 40,690
notes. Search, maintenance, and Semantic SQLite `quick_check` each returned
`ok`, the dirty-note queue was empty, and vector metadata matched the active
lexical index. Smart Search opened normally and reported
`Smart & Exact ready — 40,690 notes ready`. No direct writes were made to the
Anki collection; external-index changes occurred only through the add-on's
supported reconciliation path, and no cards were answered or rescheduled.

## Publication gates

- [x] PR #16 merged at `4e2ecc17`; annotated tag `v1.0.26` points to the
      certified tree.
- [x] Both ten-job GitHub matrices are green.
- [x] The merged tree reproduces the frozen archive exactly.
- [x] GitHub assets exactly match the frozen archive and checksum.
- [x] GitHub marks v1.0.26 as the current stable/latest release.
- [x] Existing AnkiWeb item `677438639` was updated in place.
- [x] Supported and out-of-range routing behaves correctly.
- [x] All seven supported downloads hash-match the frozen archive.
- [x] Public numeric-code clean installs pass at both support boundaries.
- [x] The public v1.0.25-to-v1.0.26 upgrade preserves configuration,
      `user_files`, and synthetic SQLite data on all seven supported Anki
      versions.
- [x] The exact AnkiWeb-served v1.0.26 archive is installed in the personal
      Anki installation; configuration and persistent files were preserved,
      the external indexes reconciled cleanly, and the pre-publication
      reviewer-mode smoke and post-install exact-archive smoke passed.

First-window support monitoring remains ongoing.
