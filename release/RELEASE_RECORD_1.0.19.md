# Release record — v1.0.19

## Candidate identity

- Version: `1.0.19`
- Prepared: 2026-08-01
- Published: 2026-08-02
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon

## Frozen public artifact

- File: `Smart_Search_Medical_1.0.19.ankiaddon`
- Bytes: `52,244,211`
- Files: `67`
- SHA-256: `dd36b476b4dc3513e408f114b580c0e0db92adead7b3025496edcf8cc4852de8`

The builder produced the same checksum twice. Archive CRC validation passed,
and the archive contains no profile directory, collection/index database,
expanded runtime, model download, cache, log, temporary file, or Python
bytecode.

## Compatibility matrix

The exact frozen source passed all 352 tests and a native-updater API probe on
every supported target below, with zero failures.

| Anki | Python / Qt | Full suite | Native updater probe |
| --- | --- | ---: | ---: |
| 24.11 | Python 3.9 / Qt 6.6 | 352 pass | Pass |
| 25.02.7 | Python 3.9 / Qt 6.6 | 352 pass | Pass |
| 25.07.5 | Python 3.13 / Qt 6.9 | 352 pass | Pass |
| 25.09.4 | Python 3.13 / Qt 6.9 | 352 pass | Pass |
| 25.09.5 | Python 3.13 / Qt 6.9 | 352 pass | Pass |
| 26.05 | Python 3.13 / Qt 6.11 | 352 pass | Pass |
| 26.08 | Python 3.13 / Qt 6.11 | 352 pass | Pass |

## Native-upgrade safety

The real `AddonManager.install()` implementation from every target was used to
simulate an AnkiWeb numeric-folder upgrade from the frozen public v1.0.15
archive to v1.0.19. Every target preserved:

- a `user_files` persistence sentinel;
- a synthetic profile index file;
- customized add-on configuration; and
- exactly one installed directory, `677438639`, with no named development copy
  and no stranded `files_backup` directory.

The About update control is enabled only when the running module and Anki
metadata both identify public item `677438639`. Focused tests cover targeted
installation, missing-helper failure, repeated-click coalescing, up-to-date,
successful-install, synchronous-error, active-maintenance deferral, exclusive
operation admission, and profile-handle quiescence paths.

Before invoking Anki's installer, the controller atomically blocks new local
work, stops deferred launchers, and closes the active lexical and semantic
index handles on Anki's background worker. A successful replacement remains
quiesced until restart; a no-update or failed result releases the gate and
reopens the existing profile data.

## Publication record

- Status: public beta
- AnkiWeb code: `677438639`
- AnkiWeb listing: https://ankiweb.net/shared/info/677438639
- GitHub prerelease:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.19
- Privacy notice: https://medbrevia.com/legal/smart-search-privacy

The existing AnkiWeb item was updated in place; no duplicate listing or branch
was created. The rendered listing shows **24.11–26.08**, all three publication
images render, the support link targets the prepared issue forms, and the
privacy page returns HTTP 200.

### Served artifact and compatibility boundary

- Supported requests at `p=241100` and `p=260800` each downloaded exactly
  `52,244,211` bytes with SHA-256
  `dd36b476b4dc3513e408f114b580c0e0db92adead7b3025496edcf8cc4852de8`.
- Both downloads were byte-identical to the frozen release artifact.
- AnkiWeb's redirect metadata reports `minpt=241100`,
  `maxpt=-260800`, and `bidx=0`.
- Requests immediately outside the range, at `p=241099` and `p=260900`, return
  HTTP 404 with `Add-on not available for your Anki version.`
- The served manifest reports `human_version=1.0.19`,
  `min_point_version=241100`, and `max_point_version=260800`.

The initial archive upload retained the prior listing's 26.05-only server
range. The mandatory boundary download caught that mismatch before completion;
the listing was corrected and the full boundary/hash validation was rerun
successfully.

### GitHub distribution

The annotated `v1.0.19` tag resolves to merge commit
`c4819569a80108c3bee3a2c905934edd4f0650db`. The public prerelease contains the
`.ankiaddon` and checksum sidecar. A fresh unauthenticated download reproduced
the frozen size and SHA-256 above, and the sidecar declares the same digest.

### Public numeric-code upgrade

An isolated Anki 26.05 environment installed the preserved public v1.0.15
archive under numeric folder `677438639`. The test then added a `user_files`
sentinel, a synthetic `search.sqlite3` database with a probe row, and customized
add-on configuration before calling Anki's official
`aqt.addons.download_and_install_addon()` path for code `677438639`.

The live update returned `InstallOk` and installed v1.0.19 with branch metadata
`min=241100`, `max=-260800`, and `bidx=0`. The sentinel and SQLite hashes were
unchanged at
`d6f126e9e323a8352290a39ce5c1b382522d308a3ee422493cf0eccac3e6ac44`
and
`5d454efc71659c92c5ccc5bfab8a6f2c3bad05ec39d1e65bc5f0b22e35ecafcf`,
respectively. The probe row and customized configuration survived, and the
final add-ons directory contained only `677438639`: no
`smart_search_medical` development duplicate and no stranded `files_backup`
directory.

A second empty temporary add-ons folder then performed an independent clean
install through the same official API. It returned `InstallOk`; the downloaded
bytes matched the frozen archive, all 67 archive members matched the installed
tree byte-for-byte, and the only additional file was Anki-managed `meta.json`.
The result again contained only `677438639`, with no duplicate named folder or
`files_backup`.

No real Anki profile was opened or modified. The test changed only the
temporary environment's trash callback so replacement of the disposable old
install would not place test files in the user's Trash; the network download,
compatibility decision, backup, installation, and restoration logic were
Anki's official `AddonManager` implementation.
