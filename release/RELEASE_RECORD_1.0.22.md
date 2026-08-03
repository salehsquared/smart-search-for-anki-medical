# Release record — v1.0.22

## Status and identity

- Status: public beta
- Published: 2026-08-03 UTC (2026-08-03 America/Phoenix)
- Version: `1.0.22`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.22 updated the existing AnkiWeb item and its single compatibility branch
in place. No duplicate item or overlapping branch was created.

## Frozen artifact

- File: `Smart_Search_Medical_1.0.22.ankiaddon`
- Bytes: `58,086,845`
- Files: `66` unique members
- SHA-256: `d80048e692e0776b03e1b2a43e5ba3d10bbc006d57a499ac226d32793088eec0`

Two independent temporary builds, the frozen distribution artifact, and a
fresh build from the annotated `v1.0.22` tag were byte-identical. Archive CRC,
duplicate-name, path, privacy, runtime-hash, source-version, and manifest checks
passed. The archive contains no profile data, generated indexes, logs, caches,
tests, scripts, or expanded runtime directories; `user_files/README.txt` is the
only packaged `user_files` member.

## Validation

The full source suite passed on every supported Anki version:

| Anki | Passed | Skipped | Failures / errors |
|---|---:|---:|---:|
| 24.11 | 494 | 1 | 0 |
| 25.02.7 | 494 | 1 | 0 |
| 25.07.5 | 494 | 1 | 0 |
| 25.09.4 | 494 | 1 | 0 |
| 25.09.5 | 494 | 1 | 0 |
| 26.05 | 494 | 1 | 0 |
| 26.08 | 494 | 1 | 0 |

Aggregate: `3,458` passed, `7` skipped, and no failures or errors. The exact
frozen archive also passed disposable local clean-install and
v1.0.21-to-v1.0.22 upgrade probes on all seven versions.

GitHub Actions run `30846204892` completed all ten Python and Anki jobs
successfully:

https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/30846204892

A real-model probe extracted the frozen archive, installed its pinned isolated
runtime in a disposable directory, produced deterministic normalized 384-D
embeddings, reused the same warm worker for a second query, and terminated and
reaped that worker successfully.

The release adds reviewer-style preview controls, keeps repeated Semantic
searches warm during an active Search session, discards superseded searches
without restarting the worker, and updates visible card state after flag,
suspension, and tag actions without rerunning the search.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/8
- Merge commit: `69e32b0eba2bc2c427f221c638a11788c48b6368`
- Annotated tag: `v1.0.22`
- Release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.22

The release is published as the current stable GitHub release. Fresh public
downloads of the `.ankiaddon` and checksum sidecar were byte-identical to the
frozen files. GitHub reports the archive digest as the expected SHA-256 above.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`

Requests at point versions `241100`, `250207`, `250705`, `250904`, `250905`,
`260500`, and `260800` reached the same compatibility branch. Requests at
`241099`, `260801`, and `260900` returned HTTP 404.

Boundary downloads at `241100` and `260800` each contained `58,086,845` bytes
with SHA-256
`d80048e692e0776b03e1b2a43e5ba3d10bbc006d57a499ac226d32793088eec0`.
Both were byte-identical to the frozen artifact, and the served manifest
reported `human_version=1.0.22`, minimum `241100`, and package maximum
`260800`.

The rendered listing shows the v1.0.22 description, both tagged screenshots,
the 24.11–26.08 range, support links, privacy statement, and install code. Both
screenshots loaded successfully from the `v1.0.22` GitHub tag.

## Public numeric-code installation

Anki's official `AddonManager` installed code `677438639` into empty disposable
roots on Anki 24.11 and 26.08. Each returned `InstallOk`, selected the expected
compatibility branch, downloaded the exact frozen bytes, installed all 66
members byte-for-byte, and created only the numeric `677438639` folder plus
Anki's expected generated `meta.json`. No named duplicate or backup folder was
created.

A disposable Anki 26.05 installation then upgraded the certified v1.0.21
archive through the same live numeric-code path. It returned `InstallOk` and
installed the exact v1.0.22 bytes while preserving:

- non-default configuration, including `result_limit=37`, disabled automatic
  preview, and a synthetic release-probe key;
- a `user_files` sentinel byte-for-byte; and
- a synthetic `search.sqlite3` byte-for-byte, with both SQL probes still
  queryable.

The cached Anki 24.11 test environment emitted its known non-fatal
`macos_helper` missing-library diagnostic after import; the official installer
and every release assertion still completed successfully.

All install and upgrade work used disposable directories under `/private/tmp`.
No real Anki profile or installed add-on was opened or modified by these tests.

## Publication gates

- [x] PR #8 merged at `69e32b0`; annotated tag `v1.0.22` points to the certified
      tree.
- [x] The ten-job GitHub matrix is green.
- [x] The tagged tree reproduces the frozen archive exactly.
- [x] GitHub assets exactly match the frozen archive and checksum.
- [x] GitHub marks v1.0.22 as the current stable release.
- [x] Existing AnkiWeb item `677438639` was updated in place.
- [x] Supported and out-of-range routing behaves correctly.
- [x] Both boundary downloads hash-match the frozen archive.
- [x] Public numeric-code clean installs passed at both support boundaries.
- [x] The live v1.0.21-to-v1.0.22 upgrade preserved configuration, user files,
      and synthetic SQLite data.

First-window support monitoring remains ongoing.
