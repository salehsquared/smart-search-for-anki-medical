#!/usr/bin/env python3
"""Reproduce the ONNX model used by Smart Search semantic retrieval.

The input is an immutable Hugging Face revision of
``abhinand/MedEmbed-small-v0.1``.  The export follows that repository's
Sentence Transformers configuration: take the CLS token and L2-normalize it.
The FP32 graph is then dynamically quantized to signed INT8 weights.

This script is intentionally separate from the add-on runtime.  It is a
release-engineering tool and is not included in the ``.ankiaddon`` package.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
from typing import Any


UPSTREAM_REPOSITORY = "abhinand/MedEmbed-small-v0.1"
UPSTREAM_REVISION = "40a5850d046cfdb56154e332b4d7099b63e8d50e"
UPSTREAM_LICENSE = "Apache-2.0"
BASE_MODEL_REPOSITORY = "BAAI/bge-small-en-v1.5"
BASE_MODEL_LICENSE = "MIT"

EXPECTED_SOURCE_FILES = {
    "1_Pooling/config.json": {
        "bytes": 296,
        "sha256": "83eeb6d0c128b377a452f121f8c5a19bbbf9322092a87b856ef782f4ad2326b9",
    },
    "config.json": {
        "bytes": 711,
        "sha256": "86d5a871bc41599d2e04d7518c6f09cef47800befbe8dace4888f5fa7a9b4333",
    },
    "config_sentence_transformers.json": {
        "bytes": 201,
        "sha256": "de3d0f96c7851b68af569a091ceb9d550e4c9f97e57c3b3e5be0a8cfc7b549cd",
    },
    "model.safetensors": {
        "bytes": 133_462_128,
        "sha256": "67b38b158c6904397d910a724d962d9be85cad9d89ba993ab59ec2b47cb6a3d9",
    },
    "modules.json": {
        "bytes": 349,
        "sha256": "84e40c8e006c9b1d6c122e02cba9b02458120b5fb0c87b746c41e0207cf642cf",
    },
    "sentence_bert_config.json": {
        "bytes": 53,
        "sha256": "ec8e29d6dcb61b611b7d3fdd2982c4524e6ad985959fa7194eacfb655a8d0d51",
    },
    "special_tokens_map.json": {
        "bytes": 695,
        "sha256": "5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
    },
    "tokenizer.json": {
        "bytes": 711_396,
        "sha256": "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
    },
    "tokenizer_config.json": {
        "bytes": 1_242,
        "sha256": "0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
    },
    "vocab.txt": {
        "bytes": 231_508,
        "sha256": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    },
}

EXPECTED_ARTIFACTS = {
    "model_fp32.onnx": {
        "bytes": 133_107_466,
        "sha256": "1c0bb2937cf47eadac726f9399b545c69178c6f31f9bbbe1fcc562f4d12d82a4",
    },
    "model_int8.onnx": {
        "bytes": 34_057_273,
        "sha256": "accb5b9e914356d01190c9f208ac822b345b24daeee4aa0fb345dfcc89a871d5",
    },
}

EXPECTED_TOOLCHAIN = {
    "huggingface-hub": "0.34.4",
    "numpy": "1.26.4",
    "onnx": "1.16.2",
    "onnxruntime": "1.23.2",
    "safetensors": "0.4.5",
    "tokenizers": "0.20.3",
    "torch": "2.4.1",
    "transformers": "4.45.2",
}

VALIDATION_SENTENCES = (
    "Bupropion is a norepinephrine and dopamine reuptake inhibitor.",
    "Wellbutrin is a brand name for bupropion.",
    "Elevated jugular venous pressure may indicate right-sided heart failure.",
    "Inflammatory arthritis can involve morning stiffness and distal interphalangeal joints.",
    "Creatinine-based eGFR estimates kidney filtration from serum creatinine and patient characteristics.",
    "A painless thyroid nodule requires risk stratification.",
    "Sudden dyspnea and pleuritic chest pain can suggest pulmonary embolism.",
    "Vaccination schedules depend on age, risk factors, and prior immunization.",
    "A red bicycle was parked beside the library before sunrise.",
    "The meeting begins at nine o'clock in the east conference room.",
    "Photosynthesis converts light energy into chemical energy in plants.",
    "Careful source attribution makes a technical release easier to audit.",
)

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


@dataclass(frozen=True)
class FileRecord:
    bytes: int
    sha256: str


def file_record(path: Path) -> FileRecord:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return FileRecord(bytes=path.stat().st_size, sha256=digest.hexdigest())


def verify_record(path: Path, expected: dict[str, Any]) -> FileRecord:
    if not path.is_file():
        raise RuntimeError(f"Required file is missing: {path}")
    actual = file_record(path)
    if actual.bytes != expected["bytes"] or actual.sha256 != expected["sha256"]:
        raise RuntimeError(
            f"Integrity check failed for {path}: expected "
            f"{expected['bytes']} bytes / {expected['sha256']}, got "
            f"{actual.bytes} bytes / {actual.sha256}"
        )
    return actual


def require_toolchain() -> dict[str, str]:
    actual = {distribution: version(distribution) for distribution in EXPECTED_TOOLCHAIN}
    mismatches = {
        distribution: {"expected": expected, "actual": actual[distribution]}
        for distribution, expected in EXPECTED_TOOLCHAIN.items()
        if actual[distribution] != expected
    }
    if mismatches:
        raise RuntimeError(
            "The export environment does not match the pinned toolchain: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return actual


def prepare_source(source_dir: Path | None, download_dir: Path) -> Path:
    if source_dir is not None:
        source = source_dir.resolve()
    else:
        from huggingface_hub import snapshot_download

        source = download_dir.resolve()
        source.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=UPSTREAM_REPOSITORY,
            revision=UPSTREAM_REVISION,
            local_dir=source,
            allow_patterns=sorted(EXPECTED_SOURCE_FILES),
        )

    for relative_path, expected in EXPECTED_SOURCE_FILES.items():
        verify_record(source / relative_path, expected)
    modules = json.loads((source / "modules.json").read_text(encoding="utf-8"))
    module_types = [module.get("type") for module in modules]
    if module_types != [
        "sentence_transformers.models.Transformer",
        "sentence_transformers.models.Pooling",
        "sentence_transformers.models.Normalize",
    ]:
        raise RuntimeError(f"Unexpected Sentence Transformers modules: {module_types}")
    pooling = json.loads(
        (source / "1_Pooling" / "config.json").read_text(encoding="utf-8")
    )
    if not (
        pooling.get("pooling_mode_cls_token") is True
        and pooling.get("pooling_mode_mean_tokens") is False
        and pooling.get("pooling_mode_max_tokens") is False
        and pooling.get("pooling_mode_mean_sqrt_len_tokens") is False
        and pooling.get("pooling_mode_weightedmean_tokens") is False
        and pooling.get("pooling_mode_lasttoken") is False
    ):
        raise RuntimeError(f"Unexpected pooling configuration: {pooling}")
    return source


def export_fp32(source: Path, target: Path) -> None:
    import torch
    from transformers import AutoModel, AutoTokenizer

    class SentenceEmbedding(torch.nn.Module):
        def __init__(self, model: torch.nn.Module) -> None:
            super().__init__()
            self.model = model

        def forward(
            self,
            input_ids: torch.Tensor,
            attention_mask: torch.Tensor,
            token_type_ids: torch.Tensor,
        ) -> torch.Tensor:
            hidden = self.model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                return_dict=True,
            ).last_hidden_state
            return torch.nn.functional.normalize(hidden[:, 0, :], p=2, dim=1)

    torch.manual_seed(0)
    tokenizer = AutoTokenizer.from_pretrained(source, local_files_only=True)
    encoder = AutoModel.from_pretrained(source, local_files_only=True).eval()
    wrapper = SentenceEmbedding(encoder).eval()
    sample = tokenizer(
        list(VALIDATION_SENTENCES[:2]),
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode():
        torch.onnx.export(
            wrapper,
            (
                sample["input_ids"],
                sample["attention_mask"],
                sample["token_type_ids"],
            ),
            target,
            export_params=True,
            opset_version=17,
            do_constant_folding=True,
            input_names=("input_ids", "attention_mask", "token_type_ids"),
            output_names=("sentence_embedding",),
            dynamic_axes={
                "input_ids": {0: "batch", 1: "sequence"},
                "attention_mask": {0: "batch", 1: "sequence"},
                "token_type_ids": {0: "batch", 1: "sequence"},
                "sentence_embedding": {0: "batch"},
            },
        )


def quantize_int8(source: Path, target: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(
        source,
        target,
        per_channel=True,
        reduce_range=True,
        weight_type=QuantType.QInt8,
        extra_options={"WeightSymmetric": True},
    )


def validate_models(source: Path, fp32_path: Path, int8_path: Path) -> dict[str, Any]:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from tokenizers import Tokenizer
    from transformers import AutoModel

    for model_path in (fp32_path, int8_path):
        onnx.checker.check_model(onnx.load(model_path, load_external_data=False))

    tokenizer = Tokenizer.from_file(str(source / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding()
    encodings = tokenizer.encode_batch(list(VALIDATION_SENTENCES))
    feeds = {
        "input_ids": np.asarray(
            [encoding.ids for encoding in encodings],
            dtype=np.int64,
        ),
        "attention_mask": np.asarray(
            [encoding.attention_mask for encoding in encodings],
            dtype=np.int64,
        ),
        "token_type_ids": np.asarray(
            [encoding.type_ids for encoding in encodings],
            dtype=np.int64,
        ),
    }
    model = AutoModel.from_pretrained(source, local_files_only=True).eval()
    with torch.inference_mode():
        reference = model(
            **{
                name: torch.from_numpy(value)
                for name, value in feeds.items()
            }
        ).last_hidden_state[:, 0, :]
        reference = torch.nn.functional.normalize(reference, p=2, dim=1)
        reference_array = reference.cpu().numpy().astype(np.float32)
    results: dict[str, Any] = {}
    for label, model_path in (("fp32", fp32_path), ("int8", int8_path)):
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        input_names = {item.name for item in session.get_inputs()}
        output_names = {item.name for item in session.get_outputs()}
        if output_names != {"sentence_embedding"}:
            raise RuntimeError(
                f"{model_path} has unexpected outputs: {sorted(output_names)}"
            )
        output = np.asarray(
            session.run(
                ["sentence_embedding"],
                {name: value for name, value in feeds.items() if name in input_names},
            )[0],
            dtype=np.float32,
        )
        output = output / np.maximum(
            np.linalg.norm(output, axis=1, keepdims=True),
            1e-12,
        )
        expected_shape = (len(VALIDATION_SENTENCES), 384)
        if output.shape != expected_shape:
            raise RuntimeError(
                f"{model_path} returned shape {output.shape}; expected {expected_shape}"
            )
        if not np.all(np.isfinite(output)):
            raise RuntimeError(f"{model_path} returned non-finite values.")
        cosine = np.sum(reference_array * output, axis=1)
        norms = np.linalg.norm(output, axis=1)
        results[label] = {
            "cosine_similarity_by_input": [
                round(float(value), 9) for value in cosine
            ],
            "all_values_finite": True,
            "mean_cosine_similarity": float(np.mean(cosine)),
            "minimum_cosine_similarity": float(np.min(cosine)),
            "maximum_absolute_error": float(
                np.max(np.abs(reference_array - output))
            ),
            "maximum_unit_norm_error": float(
                np.max(np.abs(norms - 1.0))
            ),
            "output_shape": list(output.shape),
        }

    if results["fp32"]["minimum_cosine_similarity"] < 0.999_999:
        raise RuntimeError("FP32 ONNX parity fell below the release threshold.")
    if results["int8"]["minimum_cosine_similarity"] < 0.995:
        raise RuntimeError("INT8 ONNX parity fell below the release threshold.")
    return results


def write_provenance(
    destination: Path,
    source: Path,
    fp32_path: Path,
    int8_path: Path,
    toolchain: dict[str, str],
    parity: dict[str, Any],
) -> None:
    payload = {
        "schema_version": 1,
        "artifact_name": "MedEmbed-small-v0.1 ONNX INT8 (MedBrevia export)",
        "created_utc_date": "2026-07-29",
        "source": {
            "repository": UPSTREAM_REPOSITORY,
            "revision": UPSTREAM_REVISION,
            "declared_license": UPSTREAM_LICENSE,
            "files": {
                relative_path: asdict(file_record(source / relative_path))
                for relative_path in sorted(EXPECTED_SOURCE_FILES)
            },
        },
        "reported_base_model": {
            "repository": BASE_MODEL_REPOSITORY,
            "declared_license": BASE_MODEL_LICENSE,
        },
        "transformation": {
            "pooling": "CLS token (last_hidden_state[:, 0, :])",
            "normalization": "L2 normalization across embedding dimension",
            "onnx_opset": 17,
            "dynamic_axes": ["batch", "sequence"],
            "output": "sentence_embedding",
            "output_dimension": 384,
            "quantization": {
                "implementation": "onnxruntime.quantization.quantize_dynamic",
                "weight_type": "QInt8",
                "per_channel": True,
                "reduce_range": True,
                "weight_symmetric": True,
                "calibration_data": None,
            },
        },
        "artifacts": {
            "model_fp32.onnx": asdict(file_record(fp32_path)),
            "onnx/model_int8.onnx": asdict(file_record(int8_path)),
        },
        "distribution": {
            "included": ["onnx/model_int8.onnx"],
            "omitted_intermediate": [
                "model_fp32.onnx (reproducible, omitted to reduce download size)"
            ],
        },
        "toolchain": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            **toolchain,
        },
        "parity_sanity_check": {
            "purpose": (
                "Numerical export and quantization sanity check only; "
                "not a clinical or retrieval-quality benchmark."
            ),
            "inputs": list(VALIDATION_SENTENCES),
            "reference": (
                "Pinned upstream PyTorch encoder with CLS pooling and "
                "L2 normalization"
            ),
            "runtime_path": (
                "tokenizers.Tokenizer.from_file with truncation at 512 and "
                "dynamic padding, followed by ONNX Runtime CPU inference and "
                "the add-on's final L2 normalization"
            ),
            "results": parity,
            "thresholds": {
                "fp32_minimum_cosine_similarity": 0.999_999,
                "int8_minimum_cosine_similarity": 0.995,
            },
        },
        "reproduction": {
            "script": "export_medembed_onnx.py",
            "requirements": "requirements-model-export.txt",
        },
    }
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        help=(
            "Use an existing pinned upstream snapshot. If omitted, the script "
            "downloads the immutable revision."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for FP32, INT8, and provenance outputs.",
    )
    parser.add_argument(
        "--keep-fp32",
        action="store_true",
        help="Keep the 127 MB FP32 intermediate after verification.",
    )
    arguments = parser.parse_args()

    toolchain = require_toolchain()
    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = prepare_source(arguments.source_dir, output_dir / "upstream")
    fp32_path = output_dir / "model_fp32.onnx"
    int8_path = output_dir / "model_int8.onnx"

    export_fp32(source, fp32_path)
    verify_record(fp32_path, EXPECTED_ARTIFACTS["model_fp32.onnx"])
    quantize_int8(fp32_path, int8_path)
    verify_record(int8_path, EXPECTED_ARTIFACTS["model_int8.onnx"])
    parity = validate_models(source, fp32_path, int8_path)
    write_provenance(
        output_dir / "PROVENANCE.json",
        source,
        fp32_path,
        int8_path,
        toolchain,
        parity,
    )
    if not arguments.keep_fp32:
        fp32_path.unlink()

    print(
        json.dumps(
            {
                "int8": asdict(file_record(int8_path)),
                "parity": parity,
                "provenance": str(output_dir / "PROVENANCE.json"),
                "source_revision": UPSTREAM_REVISION,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
