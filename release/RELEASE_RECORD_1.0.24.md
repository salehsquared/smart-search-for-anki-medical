# Release record — v1.0.24

## Status and identity

- Status: public beta
- Published: 2026-08-04 UTC (2026-08-03 America/Phoenix)
- Version: `1.0.24`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.24 updated the existing AnkiWeb item and its single compatibility branch
in place. No duplicate item or overlapping branch was created.

## Frozen artifact

- File: `Smart_Search_Medical_1.0.24.ankiaddon`
- Bytes: `58,094,696`
- Files: `66` unique members
- SHA-256: `a73334c6d6db68525a487c08dfad31584d1b18815a19c0e6b6054dc36cb7c258`

Two independent temporary builds, the frozen distribution artifact, and a
fresh build from the annotated `v1.0.24` tag were byte-identical. Archive CRC,
duplicate-name, path, privacy, runtime-hash, source-version, and manifest checks
passed. The archive contains no profile data, generated indexes, logs, caches,
tests, scripts, or expanded runtime directories; `user_files/README.txt` is the
only packaged `user_files` member.

## Validation

The full source suite passed on every supported Anki version:

| Anki | Passed | Skipped | Failures / errors |
|---|---:|---:|---:|
| 24.11 | 516 | 1 | 0 |
| 25.02.7 | 516 | 1 | 0 |
| 25.07.5 | 516 | 1 | 0 |
| 25.09.4 | 516 | 1 | 0 |
| 25.09.5 | 516 | 1 | 0 |
| 26.05 | 516 | 1 | 0 |
| 26.08 | 516 | 1 | 0 |

Aggregate: `3,612` passed, `7` skipped, and no failures or errors. The sole
skip in each runtime was the opt-in real-model worker integration test because
`SMART_SEARCH_REAL_MODEL_DIR` was not set. The exact frozen archive also passed
disposable local clean-install and v1.0.23-to-v1.0.24 upgrade probes on all
seven versions, preserving customized configuration, `user_files`, and a
synthetic SQLite index.

GitHub Actions run `30871835811` completed all ten Python and Anki jobs
successfully:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/30871835811

Controller and UI tests cover modern and legacy Add Cards openers, saving
pending inline edits first, filtered-deck home routing, stale source IDs,
single-note selection, and the legacy detached-copy identity safeguard. Native
Anki behavior was verified to create fresh IDs and scheduling, generate normal
template/cloze siblings, and leave creation contingent on the user's **Add**
action.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/12
- Merge commit: `5e9a117c0f89079fb6ec0c824f5811e6e5bd97ea`
- Annotated tag: `v1.0.24`
- Release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.24

The release is published as the current stable GitHub release. Fresh public
downloads of the `.ankiaddon` and checksum sidecar were byte-identical to the
frozen files. GitHub reports the archive digest as the expected SHA-256 above,
and `/releases/latest` resolves to v1.0.24.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`

Requests at point versions `241100`, `250207`, `250705`, `250904`, `250905`,
`260500`, and `260800` reached the same compatibility branch. Requests at
`241099`, `260801`, and `260900` returned HTTP 404.

Boundary downloads at `241100` and `260800` each contained `58,094,696` bytes
with SHA-256
`a73334c6d6db68525a487c08dfad31584d1b18815a19c0e6b6054dc36cb7c258`.
Both were byte-identical to the frozen artifact, and the served manifest
reported `human_version=1.0.24`, minimum `241100`, and package maximum
`260800`.

The rendered listing shows the v1.0.24 description, tagged synthetic search
screenshot, 24.11–26.08 range, support link, privacy statement, and install
code.

## Public numeric-code installation

Anki's official `AddonManager` installed code `677438639` into empty disposable
roots on Anki 24.11 and 26.08. Each returned `InstallOk`, selected the expected
compatibility branch, downloaded the exact frozen bytes, installed all 66
members byte-for-byte, and created only the numeric `677438639` folder plus
Anki's expected generated `meta.json`. No named duplicate or backup folder was
created.

The clean-install roots are:

- `/private/tmp/smart-search-v1024-live-clean-anki2411.N9YH7r`
- `/private/tmp/smart-search-v1024-live-clean-anki2608.InQ5pA`

A disposable Anki 26.05 installation then upgraded the certified v1.0.23
archive through the same live numeric-code path. It returned `InstallOk` and
installed the exact v1.0.24 bytes while preserving:

- non-default configuration, including `result_limit=37`, disabled automatic
  preview, Exact mode, and a synthetic release-probe key;
- a `user_files` sentinel byte-for-byte; and
- a synthetic `search.sqlite3` byte-for-byte, with its SQL probe still
  queryable.

The upgrade root is
`/private/tmp/smart-search-v1024-live-upgrade-anki2605.zcAvUw`.

The cached Anki 24.11 test environment emitted its known non-fatal
`macos_helper` missing-library diagnostic after import; the official installer
and every release assertion still completed successfully.

All install and upgrade work used disposable directories under `/private/tmp`.
No real Anki profile was opened or modified by those tests.

## Publication gates

- [x] PR #12 merged at `5e9a117`; annotated tag `v1.0.24` points to the
      certified tree.
- [x] The ten-job GitHub matrix is green.
- [x] The tagged tree reproduces the frozen archive exactly.
- [x] GitHub assets exactly match the frozen archive and checksum.
- [x] GitHub marks v1.0.24 as the current stable release.
- [x] Existing AnkiWeb item `677438639` was updated in place.
- [x] Supported and out-of-range routing behaves correctly.
- [x] Both boundary downloads hash-match the frozen archive.
- [x] Public numeric-code clean installs passed at both support boundaries.
- [x] The live v1.0.23-to-v1.0.24 upgrade preserved configuration, user files,
      and synthetic SQLite data.

First-window support monitoring remains ongoing.
