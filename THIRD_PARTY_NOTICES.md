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

The following wheels are distributed only to run semantic search locally on a
supported Mac. Smart and Exact search do not depend on them.

| Bundled wheel | SHA-256 |
| --- | --- |
| `onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl` | `31410f544674f534c2f27348af52ef81682ca9c8719154bf4d48f0ef23823b1e` |
| `numpy-2.5.1-cp313-cp313-macosx_14_0_arm64.whl` | `6165343f81b56ef8f514f396989e529b61d9dc709b99421b07e9f3e698e2287d` |
| `tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl` | `e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e` |
| `flatbuffers-25.12.19-py2.py3-none-any.whl` | `7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4` |

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

### Python 3.9 compatibility runtime

The compatibility runtime uses older, separately pinned wheels for Anki builds
that embed Python 3.9. They are not installed on newer Python runtimes.

| Bundled wheel | SHA-256 |
| --- | --- |
| `onnxruntime-1.19.2-cp39-cp39-macosx_11_0_universal2.whl` | `006c8d326835c017a9e9f74c9c77ebb570a71174a1e89fe078b29a557d9c3848` |
| `numpy-2.0.2-cp39-cp39-macosx_14_0_arm64.whl` | `2b2955fa6f11907cf7a70dab0d0755159bca87755e831e47932367fc8f2f2d0b` |
| `tokenizers-0.20.3-cp39-cp39-macosx_11_0_arm64.whl` | `f4cb0c614b0135e781de96c2af87e73da0389ac1458e2a97562ed26e29490d8d` |
| `flatbuffers-24.3.25-py2.py3-none-any.whl` | `8dbdec58f935f3765e4f7f3cf635ac3a77f83568138d6a2311f524ec96364812` |

#### ONNX Runtime 1.19.2

- Project: Microsoft ONNX Runtime
- License: MIT
- Source: <https://github.com/microsoft/onnxruntime/tree/v1.19.2>
- License extracted from the exact wheel:
  `licenses/onnxruntime-1.19.2-LICENSE.txt`
- Required upstream component notices extracted from the exact wheel:
  `licenses/onnxruntime-1.19.2-ThirdPartyNotices.txt`

The complete third-party notice distributed inside this ONNX Runtime wheel is
retained without localization or abridgment.

#### NumPy 2.0.2

- Project: NumPy
- Project license: BSD-3-Clause
- Source: <https://github.com/numpy/numpy/tree/v2.0.2>
- NumPy license and the notices for software included in its exact binary
  wheel: `licenses/numpy-2.0.2-LICENSE.txt`

The retained wheel license includes the component-level terms supplied by
NumPy for libraries incorporated into that binary distribution.

#### Hugging Face tokenizers 0.20.3

- Project: Hugging Face tokenizers
- License: Apache-2.0
- Source: <https://github.com/huggingface/tokenizers/tree/v0.20.3>
- Version-matched PyPI source distribution:
  <https://files.pythonhosted.org/packages/da/25/b1681c1c30ea3ea6e584ae3fffd552430b12faa599b558c4c4783f56d7ff/tokenizers-0.20.3.tar.gz>
- Source distribution SHA-256:
  `2278b34c5d0dd78e087e1ca7f9b1dcbf129d80211afa645f214bd6e051037539`
- Upstream license from that source distribution:
  `licenses/tokenizers-0.20.3-LICENSE.txt`

This older wheel does not embed a software bill of materials or license file.
The exact, version-matched PyPI source distribution and its Cargo lockfile are
therefore used as the dependency authority for the reproducible component
notice at `licenses/tokenizers-0.20.3-NOTICES.txt`. The bundle covers the
reviewed non-development dependency closure: **120** packages, including
**118** checksum-verified crates.io archives and two local tokenizers packages.
Because a Cargo lockfile can retain target-specific packages, the bundle may
conservatively include an attribution that was not linked into this particular
Mac wheel; it does not silently omit such an attribution.

Every license, licence, copying, copyright, notice, and unlicense file found in
those exact sources is retained. The `number_prefix` crate intentionally
excludes its `LICENCE` from its crates.io archive, so that one notice is fetched
from the exact repository commit recorded in the crate's VCS metadata and is
separately checksum-verified. The checked-in bundle can be reproduced from the
source repository with:

`python3 scripts/build_tokenizers_0203_notices.py`

#### Google FlatBuffers 24.3.25

- Project: Google FlatBuffers
- License: Apache-2.0
- Source: <https://github.com/google/flatbuffers/tree/v24.3.25>
- License from the immutable release commit:
  <https://github.com/google/flatbuffers/blob/595bf0007ab1929570c7671f091313c8fc20644e/LICENSE>
- Retained license text: `licenses/flatbuffers-24.3.25-LICENSE.txt`

The Python wheel declares Apache-2.0 but does not itself contain a license
file, so the retained version-specific text comes from the corresponding
upstream release commit.

## Host-provided software

Anki, Qt/PyQt, Python's standard library, and SQLite are supplied by Anki or
the host system; they are not redistributed by this add-on. Their names are
used only to describe compatibility and integration.

## No endorsement

No third-party project named above endorses Smart Search, MedBrevia, or Saleh
Mostafa. Product and project names remain the property of their respective
owners.
