# Third-party notices

Smart Search for Anki — Medical includes or can install the components listed
below. The add-on's MIT License in `LICENSE.txt` applies only to the original
add-on code and documentation; it does not replace any third-party terms.

The words “bundled” and “downloaded” describe delivery only. They do not imply
that MedBrevia, Saleh Mostafa, or the add-on authors created, sponsor, or
endorse the named projects.

## National Library of Medicine RxTerms

The add-on bundles a transformed alias snapshot made from **RxTerms 202607**,
produced and maintained by the U.S. National Library of Medicine (NLM). The
snapshot is used only to expand local drug-name searches. Its source,
transformations, limitations, version, and checksum are documented in
`DATA_SOURCES.md`.

NLM requests that applications using its data include this statement:

> This product uses publicly available data from the U.S. National Library of
> Medicine (NLM), National Institutes of Health, Department of Health and Human
> Services; NLM is not responsible for the product and does not endorse or
> recommend this or any other product.

NLM's terms also prohibit using the NLM name or logo in conjunction with an
application. This add-on uses the name only for attribution and does not use
the NLM logo.

- Source: <https://lhncbc.nlm.nih.gov/MOR/RxTerms/>
- Terms: <https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html>
- Snapshot source:
  <https://data.lhncbc.nlm.nih.gov/public/rxterms/release/RxTerms202607.zip>

## Optional semantic model

### MedEmbed-small-v0.1

The optional semantic-search model is based on
**abhinand/MedEmbed-small-v0.1**, created by Abhinand Balachandran. The
upstream model card identifies `BAAI/bge-small-en-v1.5` as its base model and
declares the MedEmbed model under the **Apache License 2.0**.

- Upstream model: <https://huggingface.co/abhinand/MedEmbed-small-v0.1>
- Declared license: Apache-2.0
- License text: `licenses/Apache-2.0.txt`

### BAAI BGE small English v1.5

**BAAI/bge-small-en-v1.5**, from the Beijing Academy of Artificial
Intelligence BGE project, is the base model reported by the MedEmbed model
card. Its Hugging Face model card declares the model under the **MIT License**.

- Base model: <https://huggingface.co/BAAI/bge-small-en-v1.5>
- Project: <https://github.com/FlagOpen/FlagEmbedding>
- Declared license: MIT
- License text and copyright notice:
  `licenses/bge-small-en-v1.5-MIT.txt`

### Reproducible INT8 ONNX derivative used by this add-on

Saleh Mostafa / MedBrevia independently exported and quantized the pinned
`abhinand/MedEmbed-small-v0.1` model for compact local inference. The
distributed repository is **medbrevia/medembed-small-v0.1-onnx-int8**, pinned
to immutable revision
`6cbe4664f1e0067da935f5abc24e4f8b5406b13f`. The add-on downloads
`model_int8.onnx` plus unmodified tokenizer and configuration files and
verifies every file against its expected byte size and SHA-256 digest.

- Conversion repository:
  <https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8>
- Pinned distribution revision:
  <https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8/tree/6cbe4664f1e0067da935f5abc24e4f8b5406b13f>
- Pinned upstream source revision:
  <https://huggingface.co/abhinand/MedEmbed-small-v0.1/tree/40a5850d046cfdb56154e332b4d7099b63e8d50e>
- Public provenance record:
  <https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8/blob/6cbe4664f1e0067da935f5abc24e4f8b5406b13f/PROVENANCE.json>

The repository retains the Apache License 2.0 text, the BGE MIT license and
copyright notice, and a prominent modification notice. It also publishes the
complete export script, pinned toolchain, source and artifact hashes,
quantization parameters, and numerical parity sanity check.

The modifications are: ONNX opset 17 export; CLS pooling and L2 normalization
matching the upstream Sentence Transformers configuration; dynamic batch and
sequence axes; and ONNX Runtime dynamic, per-channel, symmetric signed-INT8
weight quantization with reduced range. The reproducible FP32 intermediate is
omitted from distribution to reduce download size.

This derivative is not an official export from the upstream author. Its small
synthetic parity check is an export-integrity check, not clinical validation or
a guarantee that quantization preserves every retrieval ranking. Model
training was not reproduced, and upstream training-dataset provenance and
licensing were not independently audited. No training dataset is distributed
by this add-on or conversion repository. See `DATA_SOURCES.md` for exact
artifact hashes and validation results.

## Optional local semantic runtime

Semantic inference runs in a short-lived, standalone worker process on a
supported Mac. This lets the operating system reclaim the model and native
inference libraries when the worker exits instead of retaining them inside
Anki. Smart and Exact search do not launch or depend on that worker.

### Standalone CPython 3.13.14 worker

The add-on redistributes this pinned, official
`astral-sh/python-build-standalone` release artifact:

| Bundled artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `cpython-3.13.14+20260728-aarch64-apple-darwin-install_only_stripped.tar.gz` | 25,121,759 | `aa2a054f5e04bde63ae199e3bb6bbb634e457423efd294842deeb1299e7e5932` |

- Release: <https://github.com/astral-sh/python-build-standalone/releases/tag/20260728>
- Exact artifact:
  <https://github.com/astral-sh/python-build-standalone/releases/download/20260728/cpython-3.13.14%2B20260728-aarch64-apple-darwin-install_only_stripped.tar.gz>
- Project source at the release tag:
  <https://github.com/astral-sh/python-build-standalone/tree/20260728>
- python-build-standalone project license: MPL-2.0

The compact archive retains CPython's license at
`python/lib/python3.13/LICENSE.txt` and the license files shipped with its pip
installation. Its matching full release artifact also provides a top-level
`python/licenses/` directory for CPython and compiled dependencies. Every one
of those **19** exact release-supplied texts, plus the project's MPL-2.0 text,
is preserved in
`licenses/python-build-standalone-20260728-NOTICES.txt`.

That notice bundle is generated from the exact matching full artifact,
`cpython-3.13.14+20260728-aarch64-apple-darwin-pgo+lto-full.tar.zst`
(58,335,956 bytes; SHA-256
`85c6b957a86e44ec423e57b7af1b5abdffe33107bc951297a7a88eb46361d20b`).
It covers the release texts for bdb, bzip2, CPython, Expat, libX11, libXau,
libedit, libffi, liblzma, libuuid, libxcb, mpdecimal, ncurses, OpenSSL 1.1 and
3, SQLite, Tcl, Tix, and zlib. Reproduce it with:

`python3 scripts/build_python_runtime_notices.py`

### Bundled Python wheels

The Python 3.13 wheels below are installed only inside the isolated worker,
except NumPy, which is also installed in a separate NumPy-only host directory
for vector-index arithmetic. Anki versions that embed Python 3.9 receive only
the compatible NumPy 2.0.2 wheel; the old Python 3.9 ONNX Runtime, Tokenizers,
and FlatBuffers wheels are no longer distributed.

| Bundled wheel | SHA-256 |
| --- | --- |
| `onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl` | `31410f544674f534c2f27348af52ef81682ca9c8719154bf4d48f0ef23823b1e` |
| `numpy-2.5.1-cp313-cp313-macosx_14_0_arm64.whl` | `6165343f81b56ef8f514f396989e529b61d9dc709b99421b07e9f3e698e2287d` |
| `tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl` | `e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e` |
| `flatbuffers-25.12.19-py2.py3-none-any.whl` | `7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4` |
| `numpy-2.0.2-cp39-cp39-macosx_14_0_arm64.whl` | `2b2955fa6f11907cf7a70dab0d0755159bca87755e831e47932367fc8f2f2d0b` |

### ONNX Runtime 1.28.0

- Project: Microsoft ONNX Runtime
- License: MIT
- Homepage: <https://onnxruntime.ai/>
- License: `licenses/onnxruntime-LICENSE.txt`
- Required upstream component notices:
  `licenses/onnxruntime-ThirdPartyNotices.txt`

The complete ONNX Runtime third-party notice is retained without
localization or abridgment.

### NumPy 2.5.1

- Project: NumPy
- License expression reported by the wheel:
  `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`
- Homepage: <https://numpy.org/>
- NumPy license and the notices for software included in its binary wheel:
  `licenses/numpy-LICENSE.txt`

The installed wheel also retains its component-level license files under
`numpy-2.5.1.dist-info/licenses/`.

### NumPy 2.0.2

- Project: NumPy
- Project license: BSD-3-Clause
- Source: <https://github.com/numpy/numpy/tree/v2.0.2>
- NumPy license and the notices supplied with the exact binary wheel:
  `licenses/numpy-2.0.2-LICENSE.txt`

This wheel provides vector-index arithmetic to supported Anki releases that
embed Python 3.9. It is not an inference runtime and does not load ONNX
Runtime or Tokenizers into Anki.

### Hugging Face tokenizers 0.23.1

- Project: Hugging Face tokenizers
- License: Apache-2.0
- Source: <https://github.com/huggingface/tokenizers/tree/v0.23.1>
- License text: `licenses/Apache-2.0.txt`

The binary wheel includes a CycloneDX software bill of materials at
`tokenizers-0.23.1.dist-info/sboms/tokenizers-python.cyclonedx.json`. It
enumerates the Rust components compiled into the wheel and their license
expressions. The wheel and the extracted runtime retain that SBOM. A
human-readable rendering is included at
`licenses/tokenizers-0.23.1-transitive-inventory.md`.

The complete component-level license and copyright bundle is included at
`licenses/tokenizers-0.23.1-NOTICES.txt`. It covers all **120** components in
the wheel's exact SBOM. The deterministic generator checksum-verifies each of
the **119** crates.io source archives against the digest in the SBOM, resolves
the one local-path `tokenizers` component to upstream v0.23.1 commit
`7f1623b90b5adfb9bc327d4c3468d2f70bbce262`, and preserves every license,
licence, copying, copyright, notice, and unlicense file found in those exact
sources. Identical texts are deduplicated byte-for-byte and cross-referenced
to every supplying component. The release bundle can be reproduced with:

`python3 scripts/build_tokenizers_notices.py`

### Google FlatBuffers 25.12.19

- Project: Google FlatBuffers
- License: Apache-2.0
- Source: <https://github.com/google/flatbuffers/tree/v25.12.19>
- License text: `licenses/Apache-2.0.txt`

## Host-provided software

Anki, Qt/PyQt, Anki's embedded Python runtime, and the host copy of SQLite are
supplied by Anki or the operating system; they are not redistributed by this
add-on. The standalone Python worker described above is redistributed and is
covered by its separate notices.

## No endorsement

No third-party project named above endorses Smart Search, MedBrevia, or Saleh
Mostafa. Product and project names remain the property of their respective
owners.
