# Data sources and model provenance

This file describes the medical terminology and model artifacts used by Smart
Search for Anki — Medical. It is a provenance record, not a claim of clinical
validation.

> **Snapshot freshness:** the bundled RxTerms alias data is the **July 2026
> release (`RxTerms202607`)**. It is a fixed snapshot and does not update
> itself. NLM publishes RxTerms monthly, so a newer source release may exist
> after this add-on build. Search aliases can therefore be incomplete,
> outdated, or wrong for a user's intended meaning.

## RxTerms drug-name aliases

### Source

- Publisher: U.S. National Library of Medicine, Lister Hill National Center
  for Biomedical Communications
- Source product: RxTerms
- Source release: `RxTerms202607`
- Source archive:
  <https://data.lhncbc.nlm.nih.gov/public/rxterms/release/RxTerms202607.zip>
- Release documentation: <https://lhncbc.nlm.nih.gov/MOR/RxTerms/>
- NLM terms: <https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html>

### Bundled derived file

- Path: `resources/medical_vocab/rxterms_202607.json.gz`
- SHA-256:
  `fb45614effd4abccbb3e4cec36b256e1919c41ab301499457615f609fd0b7f9e`
- Alias records: 5,979
- Builder: `scripts/build_rxterms_aliases.py`

### Transformations

The deterministic builder reads the RxTerms main and ingredients files and:

1. excludes rows marked suppressed or retired;
2. derives candidate aliases from brand names, display names, and display-name
   synonyms;
3. removes a trailing parenthetical route/dose-form suffix from display names;
4. maps an alias to the ingredient or ingredient combination shared by its
   source rows;
5. discards ambiguous aliases that do not resolve to one shared or consistent
   ingredient set; and
6. stores normalized lookup strings, display strings, source kind, and a count
   of associated RxCUIs.

The add-on does not bundle full RxTerms records, dosing, prescribing
instructions, interaction data, or an RxTerms API mirror.

### Important limitations

- An alias match is a search expansion, not a statement that two products,
  formulations, doses, routes, or therapies are clinically interchangeable.
- RxTerms is oriented toward U.S. prescribing and may not represent drugs or
  naming conventions in other countries.
- Brand names, abbreviations, combination products, and ingredient mappings
  can change.
- The alias builder intentionally removes clinical-product detail to keep
  search expansion compact; it must not be used for medication reconciliation,
  prescribing, dispensing, interaction checking, or patient care decisions.

The exact NLM-requested attribution appears in `THIRD_PARTY_NOTICES.md`.

## Optional semantic-search model

Semantic Search uses a local 384-dimensional English text-embedding model. It
compares mathematical representations of note text and a search query. It is
not a clinical knowledge base, diagnostic model, prescribing system, or
substitute for reviewing the actual card.

### Reported lineage

1. `BAAI/bge-small-en-v1.5`
   - Publisher: Beijing Academy of Artificial Intelligence (BAAI)
   - Model card: <https://huggingface.co/BAAI/bge-small-en-v1.5>
   - Declared license: MIT
2. `abhinand/MedEmbed-small-v0.1`
   - Publisher: Abhinand Balachandran
   - Model card:
     <https://huggingface.co/abhinand/MedEmbed-small-v0.1>
   - The model card identifies BGE small English v1.5 as the base model.
   - Declared license: Apache-2.0
3. `medbrevia/medembed-small-v0.1-onnx-int8`
   - Export and quantization: Saleh Mostafa / MedBrevia
   - Repository:
     <https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8>
   - Pinned distribution revision:
     `6cbe4664f1e0067da935f5abc24e4f8b5406b13f`
   - Pinned MedEmbed source revision:
     `40a5850d046cfdb56154e332b4d7099b63e8d50e`
   - This is an independently produced derivative, not an official export from
     the upstream author.

The MedEmbed model card reports use of MedicalQARetrieval, NFCorpus,
PublicHealthQA, TRECCOVID, and ArguAna. Those datasets are not distributed by
this add-on.

### Upstream research credit

- Balachandran, Abhinand. *MedEmbed: Medical-Focused Embedding Models* (2024).
  <https://github.com/abhinand5/MedEmbed>
- Xiao, Shitao; Liu, Zheng; Zhang, Peitian; and Muennighoff, Niklas.
  *C-Pack: Packaged Resources To Advance General Chinese Embedding* (2023).
  arXiv:2309.07597. <https://arxiv.org/abs/2309.07597>

### Downloaded artifacts

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `onnx/model_int8.onnx` | 34,057,273 | `accb5b9e914356d01190c9f208ac822b345b24daeee4aa0fb345dfcc89a871d5` |
| `tokenizer.json` | 711,396 | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `tokenizer_config.json` | 1,242 | `0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53` |
| `special_tokens_map.json` | 695 | `5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a` |
| `vocab.txt` | 231,508 | `07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3` |
| `config.json` | 711 | `86d5a871bc41599d2e04d7518c6f09cef47800befbe8dace4888f5fa7a9b4333` |

All downloads use immutable revision URLs and must pass the expected byte-size
and SHA-256 checks before installation.

### Local inference execution

The model is executed in a short-lived local worker instead of loading ONNX
Runtime and Tokenizers into Anki's process. The worker uses the official
`astral-sh/python-build-standalone` CPython 3.13.14 release artifact from build
`20260728`, pinned to 25,121,759 bytes and SHA-256
`aa2a054f5e04bde63ae199e3bb6bbb634e457423efd294842deeb1299e7e5932`.
Inference uses one text sequence per batch and one ONNX intra/inter-op thread,
which bounds transient padding and CPU use. On macOS, the helper also receives
a 256 MiB process-memory ceiling. The 384-dimensional output is sent back over
a local operating-system pipe; the worker is then eligible to exit so the
operating system can reclaim the model and native-library memory.

Vector-index arithmetic in Anki uses a separate NumPy-only runtime. It does
not expose the worker's ONNX Runtime, Tokenizers, or FlatBuffers packages to
Anki. Vector scans use bounded 512-row chunks to avoid retaining a large native
allocator high-water mark. These implementation boundaries do not change the model, tokenizer,
pooling, normalization, index format, or similarity calculation described
here. Exact runtime artifacts, checksums, and licenses are recorded in
`THIRD_PARTY_NOTICES.md`.

### Reproducible conversion record

The public conversion repository includes:

- `PROVENANCE.json`, recording every required input and output size and
  SHA-256 digest;
- `export_medembed_onnx.py`, the complete deterministic export, quantization,
  integrity-check, and parity-check script;
- `requirements-model-export.txt`, the pinned reproduction toolchain;
- the Apache-2.0 license, retained BGE MIT license and copyright notice, and a
  prominent modification notice; and
- a model card that describes intended use, limitations, privacy, lineage, and
  exact reproduction commands.

Public provenance:
<https://huggingface.co/medbrevia/medembed-small-v0.1-onnx-int8/blob/6cbe4664f1e0067da935f5abc24e4f8b5406b13f/PROVENANCE.json>

The conversion verifies the upstream Sentence Transformers configuration,
exports ONNX opset 17 with dynamic batch and sequence axes, applies CLS pooling
and L2 normalization, and uses ONNX Runtime dynamic quantization with signed
INT8, per-channel weights, reduced range, and symmetric weights. No calibration
dataset is used by this dynamic quantization method.

Both the FP32 intermediate and distributed INT8 model were reproduced
bit-for-bit in the pinned environment. An exact `tokenizers` plus ONNX Runtime
sanity check used 12 synthetic medical and general-language inputs. The INT8
embeddings were finite 384-dimensional unit vectors, with minimum cosine
similarity `0.995368898` and mean cosine similarity `0.997062981` versus the
pinned PyTorch source pipeline.

This is a small numerical export-integrity check, not a clinical evaluation or
retrieval benchmark. The conversion does not claim to be an official upstream
export, does not establish that quantization preserves every close ranking,
and did not reproduce model training. The exact upstream training-dataset
snapshots, provenance, and license chain were not independently audited; none
of those datasets is distributed by the add-on or conversion repository.

## Clinical-use boundary

Search rankings and aliases are convenience features for finding the user's
own Anki material. They may miss relevant cards or return irrelevant cards.
Neither the terminology snapshot nor the embedding model should be used as a
source of medical advice, diagnosis, treatment, dosing, or emergency guidance.

For licenses and required notices, see `THIRD_PARTY_NOTICES.md`. For local-data
handling, see `PRIVACY.md`.
