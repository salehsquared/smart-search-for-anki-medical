# Release record — v1.0.25

## Status and identity

- Status: public beta
- Published: 2026-08-07 UTC (2026-08-07 America/Phoenix)
- Version: `1.0.25`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.25 updated the existing AnkiWeb item and its single compatibility branch
in place. No duplicate item or overlapping branch was created.

## Frozen artifact

- File: `Smart_Search_Medical_1.0.25.ankiaddon`
- Bytes: `58,113,662`
- Files: `67` unique members
- SHA-256: `f80d3eb17082c3c2574f5efb3a2ec90be6cc6ceb6d6c76aa1257e41f4c956119`

Two clean local builds, the frozen distribution artifact, and a fresh build
from the merged `main` tree were byte-identical. Archive CRC, duplicate-name,
path, privacy, runtime-hash, source-version, and manifest checks passed. The
archive contains no profile data, generated indexes, logs, caches, tests,
scripts, or expanded runtime directories; `user_files/README.txt` is the only
packaged `user_files` member.

## Validation

The full source suite passed on every supported Anki version:

| Anki | Passed | Skipped | Failures / errors |
|---|---:|---:|---:|
| 24.11 | 610 | 1 | 0 |
| 25.02.7 | 610 | 1 | 0 |
| 25.07.5 | 610 | 1 | 0 |
| 25.09.4 | 610 | 1 | 0 |
| 25.09.5 | 610 | 1 | 0 |
| 26.05 | 610 | 1 | 0 |
| 26.08 | 610 | 1 | 0 |

Aggregate: `4,270` passed, `7` skipped, and no failures or errors. The sole
skip in each runtime was the opt-in real-model worker integration test because
`SMART_SEARCH_REAL_MODEL_DIR` was not set. A local run against Anki's bundled
Python/Qt runtime also ran all `611` tests successfully with the same one skip.
The exact frozen v1.0.24 archive then upgraded through the live numeric-code
path on all seven supported Anki versions with every persistence assertion
passing.

GitHub Actions run `31202686961` completed all ten Python and Anki jobs
successfully:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/31202686961

The post-merge `main` run `31202896095` repeated the same ten-job validation
successfully on merge commit `586a66f`:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/31202896095

The release tests cover compact field-aware rows, Extra-only supporting
snippets, automatic first-result preview without stealing query focus, stale
preview cancellation, Related-result restoration, filter-invariant ranking,
guarded Undo, preview defaults, and the native Exact-search guidance. The
Python 3.13 CI job also rebuilt the reviewed archive twice, proved deterministic
output, and audited the package paths.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/14
- Merge commit: `586a66fe7f986257b102fcbfa63e3ca59a650459`
- Annotated tag: `v1.0.25`
- Release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.25

The release is published as the current stable GitHub release. Fresh public
downloads of the `.ankiaddon` and checksum sidecar were byte-identical to the
frozen files. GitHub reports the archive digest and byte size shown above, and
`/releases/latest` resolves to v1.0.25. The annotated tag dereferences to the
PR #14 merge commit.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`
- Server modification timestamp: `1786124333`

Requests at point versions `241100`, `250207`, `250705`, `250904`, `250905`,
`260500`, and `260800` reached the same compatibility branch. Requests at
`241099`, `260801`, and `260900` returned HTTP 404.

All seven supported downloads contained `58,113,662` bytes and `67` archive
members with SHA-256
`f80d3eb17082c3c2574f5efb3a2ec90be6cc6ceb6d6c76aa1257e41f4c956119`.
Every served file was byte-identical to the frozen artifact, passed its ZIP
integrity check, and reported `human_version=1.0.25`, minimum `241100`, and
package maximum `260800` in the manifest.

The rendered listing shows the v1.0.25 description, automatic first-result
preview, compact Extra-only snippets, native Exact semantics, the tagged
synthetic screenshot, 24.11–26.08 range, support link, privacy statement, and
install code. The tagged screenshot and privacy URL returned HTTP 200.

## Public numeric-code installation

Anki's official `AddonManager` installed code `677438639` into empty disposable
roots on Anki 24.11 and 26.08. Each selected the expected compatibility branch,
downloaded the exact frozen bytes, installed all 67 members byte-for-byte, and
created only the numeric `677438639` folder plus Anki's expected generated
`meta.json`. No named duplicate or backup folder was created.

The clean-install roots are:

- `/private/tmp/smart-search-v1025-live-verify.y7qvbl/clean-anki2411`
- `/private/tmp/smart-search-v1025-live-verify.y7qvbl/clean-anki2608`

Disposable Anki 24.11, 25.02.7, 25.07.5, 25.09.4, 25.09.5, 26.05, and 26.08
installations then upgraded the certified v1.0.24 archive through the same
live numeric-code path. Every official installer returned `InstallOk` and
installed the exact v1.0.25 bytes while preserving customized metadata and
configuration, a sentinel, the complete `user_files` tree, and a synthetic
SQLite index byte-for-byte. SQLite `quick_check` and the probe query passed on
every version; no `files_backup` or named duplicate was created.

The upgrade evidence is under
`/private/tmp/smart-search-v1025-live-verify.y7qvbl`, in the version-specific
`upgrade-anki*-matrix` directories plus `upgrade-anki2605` and
`upgrade-anki2608`.

The cached Anki 24.11 and 25.02.7 test environments emitted their known
non-fatal `macos_helper` missing-library diagnostic after import, and the older
runtimes logged non-fatal `pip_system_certs` warnings. Every installer and
release assertion still completed successfully.

All install and upgrade work used disposable directories under `/private/tmp`.
No real Anki profile was opened or modified by those tests.

## Publication gates

- [x] PR #14 merged at `586a66f`; annotated tag `v1.0.25` points to the
      certified tree.
- [x] The ten-job GitHub matrix is green.
- [x] The merged tree reproduces the frozen archive exactly.
- [x] GitHub assets exactly match the frozen archive and checksum.
- [x] GitHub marks v1.0.25 as the current stable release.
- [x] Existing AnkiWeb item `677438639` was updated in place.
- [x] Supported and out-of-range routing behaves correctly.
- [x] All seven supported downloads hash-match the frozen archive.
- [x] Public numeric-code clean installs passed at both support boundaries.
- [x] The live v1.0.24-to-v1.0.25 upgrade preserved configuration, user files,
      and synthetic SQLite data on all seven supported Anki versions.

First-window support monitoring remains ongoing.
