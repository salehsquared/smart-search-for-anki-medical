# Release record — v1.0.18

> **Superseded before publication:** v1.0.18 was a validated local candidate
> but was never uploaded to GitHub Releases or AnkiWeb. Its compatibility work
> is included in v1.0.19; do not publish this artifact.

## Candidate identity

- Version: `1.0.18`
- Prepared: 2026-08-01
- Supported Anki range: 24.11 through 26.08
- AnkiWeb point-version range: minimum `241100`, hard maximum `-260800`
- Semantic Search: macOS 14 or later on Apple silicon

## Frozen public artifact

- File: `Smart_Search_Medical_1.0.18.ankiaddon`
- Bytes: `52,239,972`
- Files: `66`
- SHA-256: `364b20361d9706249b12f2d257c8f11514bbc518007844034118ccacdfeaba77`

The builder produced the same checksum twice. Archive CRC validation passed,
and the archive contains no profile directory, collection/index database,
expanded runtime, model download, cache, log, temporary file, or Python
bytecode.

## Compatibility matrix

The product snapshot passed the complete 336-test suite and the explicit
76-test Qt-offscreen subset on every target below, with zero skips or failures.
After the final attribution, package, and quoted-field deck-filter checks were
added, the complete 338-test suite was repeated successfully on the exact final
source across every target.

| Anki | Python / Qt | Full suite | Qt subset | Startup/API |
| --- | --- | ---: | ---: | ---: |
| 24.11 | Python 3.9 / Qt 6.6 | Pass | Pass | Pass |
| 25.02.7 | Python 3.9 / Qt 6.6 | Pass | Pass | Pass |
| 25.07.5 | Python 3.13 / Qt 6.9 | Pass | Pass | Pass |
| 25.09.4 | Python 3.13 / Qt 6.9 | Pass | Pass | Pass |
| 25.09.5 | Python 3.13 / Qt 6.9 | Pass | Pass | Pass |
| 26.05 | Python 3.13 / Qt 6.11 | Pass | Pass | Pass |
| 26.08 | Python 3.13 / Qt 6.11 | Pass | Pass | Pass |

The signed Apple-silicon Anki 24.11 and 25.02.7 applications also loaded the
packaged add-on in disposable profiles. The controller started, the menu action
was registered, and shutdown completed without an add-on traceback.

## Semantic runtime validation

- Python 3.9: ONNX Runtime 1.19.2, NumPy 2.0.2, Tokenizers 0.20.3
- Python 3.13: ONNX Runtime 1.28.0, NumPy 2.5.1, Tokenizers 0.23.1

For both runtimes, installation from the exact bundled wheels, model digest
verification, ONNX inference, vector indexing, and semantic retrieval passed.
Inference produced finite, normalized 384-dimensional vectors.

## Attribution validation

The Python 3.9 runtime includes exact version-specific ONNX Runtime and NumPy
notices, the Apache notices for Tokenizers and FlatBuffers, and a deterministic
120-component Tokenizers notice bundle reconstructed from the checksum-pinned
0.20.3 source distribution and Cargo lockfile. Its clean regeneration check
passed.

## Publication boundary

No AnkiWeb or GitHub release upload is recorded here. Publication requires
updating existing AnkiWeb item `677438639` with version `1.0.18`, minimum
`241100`, and hard maximum `-260800`, then verifying installation by numeric
code on both sides of the supported range.
