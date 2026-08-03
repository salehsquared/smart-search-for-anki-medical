# Release record — v1.0.21

## Status and identity

- Status: public beta
- Prepared: 2026-08-02 America/Phoenix
- Published: 2026-08-03 UTC (2026-08-02 America/Phoenix)
- Version: `1.0.21`
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon
- Existing AnkiWeb item: `677438639`

v1.0.21 is live under the existing AnkiWeb item `677438639` and GitHub tag
`v1.0.21`. The existing listing and its single compatibility branch were
updated in place; no duplicate item or overlapping branch was created.

## Frozen candidate artifact

- File: `Smart_Search_Medical_1.0.21.ankiaddon`
- Bytes: `58,084,711`
- Files: `66` unique members
- SHA-256: `9f3640fffaa964cd2fd1b530d6626ba7ce2fe32c1f07c4b572a731b2dc95375a`
- Independent deterministic rebuild: three byte-identical builds
- Archive validation: CRC, duplicate-name, path, privacy, runtime-hash,
  executable-mode, and source/manifest parity checks passed

## Test and compatibility matrix

Final results from the frozen bytes:

| Anki | Python / Qt | Full suite | Clean install | v1.0.20 upgrade |
| --- | --- | ---: | ---: | ---: |
| 24.11 | Python 3.9 / Qt 6.6 | 483 pass / 1 opt-in skip | Pass | Pass |
| 25.02.7 | Python 3.9 / Qt 6.6 | 483 pass / 1 opt-in skip | Pass | Pass |
| 25.07.5 | Python 3.13 / Qt 6.9 | 483 pass / 1 opt-in skip | Pass | Pass |
| 25.09.4 | Python 3.13 / Qt 6.9 | 483 pass / 1 opt-in skip | Pass | Pass |
| 25.09.5 | Python 3.13 / Qt 6.9 | 483 pass / 1 opt-in skip | Pass | Pass |
| 26.05 | Python 3.13 / Qt 6.11 | 483 pass / 1 opt-in skip | Pass | Pass |
| 26.08 | Python 3.13 / Qt 6.11 | 483 pass / 1 opt-in skip | Pass | Pass |

Upgrade validation must preserve customized configuration, `user_files`, the
downloaded model, profile-local indexes, and a persistence sentinel. The final
installation must contain one add-on folder with no stranded backup or named
development duplicate.

The exact suite completed `3,381` passing test executions across the matrix.
Official AddonManager installs preserved customized configuration,
`user_files`, and synthetic SQLite data. Python 3.9 and 3.13 migration probes
also preserved semantic SQLite bytes and sentinel, `vectors.f16`, and unrelated
runtime data while replacing only the obsolete native runtimes. A real
normalized 384-dimensional embedding completed on both host generations;
ONNX Runtime and Tokenizers stayed outside the host and every worker was
reaped.

## Confirmed performance evidence

These measurements were collected from the current v1.0.21 implementation on
an Apple-silicon Mac running macOS 26.3. The existing personal model and vector
index were copied to temporary directories; the collection, review data,
configuration, indexes, installed source, and add-on metadata were not changed.
A separate legacy-runtime parity probe imported the old runtime in place and
therefore generated ordinary `__pycache__/*.pyc` files inside that disabled
runtime. Those derived caches were left alone rather than deleted.

### Workload

- Live Anki physical-footprint baseline with the add-on disabled: `274.03 MiB`
- Copied vector index: `40,666` notes, capacity `40,960`, dimension `384`
- Worker cycles: `25` unique fresh processes
- Stress input: `16 KiB` per text, truncated by the tokenizer to its supported
  512-token maximum
- Production constraints: 512-row vector scans, one ONNX thread,
  single-sequence inference, and a 256 MiB macOS worker memory ceiling
- Kernel metric: `proc_pid_rusage(RUSAGE_INFO_V0).ri_phys_footprint`

### Results

| Measurement | Result |
| --- | ---: |
| Host helper before NumPy | 22.51 MiB |
| Host helper after isolated NumPy | 34.77 MiB |
| Host immediately before real vector search | 39.36 MiB |
| Host real vector-search peak | 39.91 MiB |
| Host after mmap release | 39.84 MiB |
| Host after 25 worker cycles and idle settle | 34.97 MiB |
| Worst projected growth vs live Anki baseline | 17.40 MiB / 6.35% |
| Final retained growth vs live Anki baseline | 12.46 MiB / 4.55% |
| Worker cold-launch peak | 190.50 MiB |
| Worker steady-state median | 182.77 MiB |
| Worker steady peak slope | +0.00265 MiB per cycle |
| Worker idle CPU, maximum | 0.00% of one core |
| Worker exit time, median / maximum | 20 ms / 78 ms |
| Worker processes reaped | 25 of 25 |

The host process imported NumPy from the dedicated vector runtime and never
imported ONNX Runtime or Tokenizers. Repeated embeddings were bit-identical;
the related-query cosine was `0.810` versus `0.482` for the unrelated control.
No worker remained after completion, and the performance harness cleaned its
temporary directory.

Machine-readable result:
`/tmp/smart-search-production-combined-25.json` (local, not a release asset).

### Interpretation

The persistent host-side overhead remained below the 15% target, including a
real unfiltered vector search. Active Semantic inference still temporarily
adds approximately 183–191 MiB in a separate process; a combined-process 15%
ceiling is not feasible with the current model. The release guarantee is that
inference is bounded, absent during review and idle periods, and reclaimed by
terminating the disposable worker.

### Isolated Anki GUI comparison

The packaged implementation was also launched in an isolated Anki 26.05 base
with a synthetic one-note profile and compared with that exact profile in safe
mode using `ri_phys_footprint`:

| Run | Add-on | Median physical footprint | Increase vs paired safe mode |
| --- | --- | ---: | ---: |
| Fresh initial setup | v1.0.21 code | 201.14 MiB | 24.28 MiB / 13.73% |
| Warm reopen | v1.0.21 code | 192.42 MiB | 14.39 MiB / 8.08% |

Idle CPU was effectively zero in both add-on runs (approximately
`0.003–0.004%` of one core). For a direct root-cause control, the installed
v1.0.17 code and its legacy in-process runtime were copied into another
disposable profile. After automatic Semantic warmup, its main-process physical
footprint remained at 285.75 MiB, approximately 108.89 MiB / 61.6% above the
paired 176.86 MiB safe-mode baseline despite the collection containing only
one synthetic note. This confirms that the old native runtime lifetime, not
reviewing or deck filtering, caused the persistent idle increase.

## Local release gates

- [x] All source-version declarations equal `1.0.21`.
- [x] Complete source suite passes on all seven supported Anki environments.
- [x] Real-runtime integration test passes with `ResourceWarning` promoted to
  an error.
- [x] Three clean builds are byte-identical.
- [x] Archive remains below the 64 MiB ceiling and contains no profile/model
  data, expanded runtime, database, cache, log, or bytecode.
- [x] Official Anki installer clean-install and v1.0.20-upgrade probes pass in
  every supported environment.
- [x] Quarantine-style clean and upgrade launches pass with the frozen bytes.

The actual quarantined `.ankiaddon` install path strips quarantine before the
worker executes, and that path passed. The embedded Python also passes
`codesign --verify --deep --strict`, but standalone `spctl --assess` rejects
it. If a future Anki/Python extraction path begins preserving quarantine, the
worker launch must be recertified.

## GitHub distribution

- Pull request:
  https://github.com/salehsquared/smart-search-for-anki-medical/pull/6
- Merge commit: `d345015772aa9178154634f728fa6cbe25e89b63`
- Annotated tag: `v1.0.21`
- Public release:
  https://github.com/salehsquared/smart-search-for-anki-medical/releases/tag/v1.0.21
- Release validation:
  https://github.com/salehsquared/smart-search-for-anki-medical/actions/runs/30791610404

The ten-job GitHub matrix passed on Python 3.9, 3.11, and 3.13 and Anki 24.11,
25.02.7, 25.07.5, 25.09.4, 25.09.5, 26.05, and 26.08. The public release
contains the frozen `.ankiaddon` and its checksum sidecar. A fresh
unauthenticated GitHub download was byte-identical to the frozen artifact, and
the tagged screenshots used by AnkiWeb returned HTTP 200.

## AnkiWeb distribution

- Add-on code: `677438639`
- Public listing: https://ankiweb.net/shared/info/677438639
- Server range: `minpt=241100`, hard `maxpt=-260800`, `bidx=0`
- Listing date: 2026-08-03 UTC (2026-08-02 America/Phoenix)

The rendered listing shows v1.0.21, the memory-lifecycle change, both
publication images, the expected support link, and Anki 24.11–26.08. Requests
at point versions `241100`, `250207`, `250705`, `250904`, `250905`, `260500`,
and `260800` reached the same compatibility branch. Requests at `241099`,
`260801`, and `260900` returned HTTP 404.

Boundary downloads at `241100` and `260800` each contained `58,084,711` bytes
with SHA-256
`9f3640fffaa964cd2fd1b530d6626ba7ce2fe32c1f07c4b572a731b2dc95375a`.
Both were byte-identical to the frozen artifact, and the served manifest
reported `human_version=1.0.21`, minimum `241100`, and package maximum
`260800`.

## Public numeric-code installation

Anki's official `download_and_install_addon()` path completed clean installs
from code `677438639` in disposable Anki 24.11 and 26.08 environments. Both
returned `InstallOk`; all 66 archive members matched the frozen artifact
byte-for-byte, the server metadata reported minimum `241100`, hard maximum
`-260800`, and branch `0`, and each install contained only numeric folder
`677438639` plus Anki-managed `meta.json`.

A disposable Anki 26.05 environment then installed v1.0.20, added customized
configuration, a `user_files` persistence sentinel, and a synthetic
`search.sqlite3` database before updating through the same live numeric-code
path. The update returned `InstallOk`, installed v1.0.21, preserved the stored
configuration values and both files byte-for-byte, retained the database probe
row, and left no named duplicate or `files_backup` directory. Anki correctly
merged the preserved user configuration with defaults newly supplied by the
release.

No real Anki profile or installed add-on was opened or modified by these
public-route tests.

## Publication gates

- [x] Commit and annotated tag `v1.0.21` point to the certified tree.
- [x] GitHub release assets exactly match the frozen archive and checksum.
- [x] Existing AnkiWeb item `677438639` was updated in place with the certified
  archive and the existing compatibility branch.
- [x] Boundary downloads at `241100` and `260800` hash-match the frozen file;
  `241099`, `260801`, and `260900` remain unavailable.
- [x] Numeric-code clean installs and a live v1.0.20-to-v1.0.21 upgrade preserve
  configuration and `user_files` without a duplicate add-on folder.

First-window support monitoring remains ongoing.
