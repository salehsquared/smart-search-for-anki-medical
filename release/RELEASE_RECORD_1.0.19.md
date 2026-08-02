# Release record — v1.0.19

## Candidate identity

- Version: `1.0.19`
- Prepared: 2026-08-01
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

Publication to the existing AnkiWeb item and the GitHub v1.0.19 prerelease is
pending. After publication, record the served metadata, artifact comparison,
numeric-code clean installs, and real v1.0.15 → v1.0.19 button-driven upgrade
below before declaring the release complete.
