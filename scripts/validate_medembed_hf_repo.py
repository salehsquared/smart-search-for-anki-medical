#!/usr/bin/env python3
"""Validate the staged Hugging Face repository for the MedEmbed ONNX export."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


EXPECTED_FILES = {
    ".gitattributes",
    "1_Pooling/config.json",
    "LICENSE",
    "LICENSE-BGE-MIT.txt",
    "NOTICE.md",
    "PROVENANCE.json",
    "README.md",
    "config.json",
    "config_sentence_transformers.json",
    "export_medembed_onnx.py",
    "modules.json",
    "onnx/model_int8.onnx",
    "requirements-model-export.txt",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "validate_repository.py",
    "vocab.txt",
}

SENSITIVE_PATTERNS = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Hugging Face access token": re.compile(r"\bhf_[A-Za-z0-9]{24,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "OpenAI API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}


def file_record(path: Path) -> dict[str, Any]:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def repository_files(root: Path) -> set[str]:
    paths: set[str] = set()
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise RuntimeError(f"Symlinks are not allowed in the staged repo: {path}")
        if path.is_file():
            paths.add(path.relative_to(root).as_posix())
    return paths


def require_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected!r}, got {actual!r}")


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise RuntimeError(f"Repository directory does not exist: {root}")

    actual_files = repository_files(root)
    require_equal("repository file allowlist", actual_files, EXPECTED_FILES)

    provenance = json.loads((root / "PROVENANCE.json").read_text(encoding="utf-8"))
    require_equal("provenance schema", provenance.get("schema_version"), 1)
    require_equal(
        "source repository",
        provenance["source"]["repository"],
        "abhinand/MedEmbed-small-v0.1",
    )
    require_equal(
        "source revision",
        provenance["source"]["revision"],
        "40a5850d046cfdb56154e332b4d7099b63e8d50e",
    )
    require_equal(
        "published artifact list",
        provenance["distribution"]["included"],
        ["onnx/model_int8.onnx"],
    )

    model_record = file_record(root / "onnx" / "model_int8.onnx")
    require_equal(
        "model integrity",
        model_record,
        provenance["artifacts"]["onnx/model_int8.onnx"],
    )
    require_equal(
        "model integrity",
        model_record,
        {
            "bytes": 34_057_273,
            "sha256": "accb5b9e914356d01190c9f208ac822b345b24daeee4aa0fb345dfcc89a871d5",
        },
    )

    source_records = provenance["source"]["files"]
    for relative_path in (
        "1_Pooling/config.json",
        "config.json",
        "config_sentence_transformers.json",
        "modules.json",
        "sentence_bert_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    ):
        require_equal(
            f"source-derived file {relative_path}",
            file_record(root / relative_path),
            source_records[relative_path],
        )

    readme = (root / "README.md").read_text(encoding="utf-8")
    if not readme.startswith("---\n") or "\nlicense: apache-2.0\n" not in readme:
        raise RuntimeError("README.md is missing valid Apache-2.0 model metadata.")
    for required_text in (
        "40a5850d046cfdb56154e332b4d7099b63e8d50e",
        model_record["sha256"],
        "not a clinical evaluation",
        "not an official export",
    ):
        if required_text.casefold() not in readme.casefold():
            raise RuntimeError(f"README.md is missing required disclosure: {required_text}")

    apache_license = (root / "LICENSE").read_text(encoding="utf-8")
    if "Apache License" not in apache_license or "Version 2.0" not in apache_license:
        raise RuntimeError("LICENSE is not the Apache License 2.0 text.")
    bge_license = (root / "LICENSE-BGE-MIT.txt").read_text(encoding="utf-8")
    if "MIT License" not in bge_license or "Copyright (c) 2022 staoxiao" not in bge_license:
        raise RuntimeError("The BGE MIT license and copyright notice are incomplete.")

    for relative_path in sorted(actual_files):
        path = root / relative_path
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                raise RuntimeError(f"Possible {label} found in {relative_path}")

    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    # Use ONNX Runtime here rather than importing ``onnx``. The public model
    # repository intentionally contains an ``onnx/`` directory, which would
    # otherwise shadow the Python package when this validator is run from the
    # repository root.
    session = ort.InferenceSession(
        str(root / "onnx" / "model_int8.onnx"),
        providers=["CPUExecutionProvider"],
    )
    require_equal(
        "ONNX inputs",
        [item.name for item in session.get_inputs()],
        ["input_ids", "attention_mask", "token_type_ids"],
    )
    require_equal(
        "ONNX outputs",
        [item.name for item in session.get_outputs()],
        ["sentence_embedding"],
    )
    require_equal(
        "recorded ONNX opset",
        provenance["transformation"]["onnx_opset"],
        17,
    )

    parity = provenance["parity_sanity_check"]["results"]
    if parity["fp32"]["minimum_cosine_similarity"] < 0.999_999:
        raise RuntimeError("Recorded FP32 parity is below its declared threshold.")
    if parity["int8"]["minimum_cosine_similarity"] < 0.995:
        raise RuntimeError("Recorded INT8 parity is below its declared threshold.")

    tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding()
    encoded = tokenizer.encode_batch(
        [
            "Bupropion is a norepinephrine and dopamine reuptake inhibitor.",
            "A red bicycle was parked beside the library before sunrise.",
        ]
    )
    runtime_output = np.asarray(
        session.run(
            ["sentence_embedding"],
            {
                "input_ids": np.asarray(
                    [item.ids for item in encoded],
                    dtype=np.int64,
                ),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encoded],
                    dtype=np.int64,
                ),
                "token_type_ids": np.asarray(
                    [item.type_ids for item in encoded],
                    dtype=np.int64,
                ),
            },
        )[0],
        dtype=np.float32,
    )
    require_equal("runtime smoke-test shape", runtime_output.shape, (2, 384))
    if not np.all(np.isfinite(runtime_output)):
        raise RuntimeError("Runtime smoke test returned non-finite values.")
    norms = np.linalg.norm(runtime_output, axis=1)
    if float(np.max(np.abs(norms - 1.0))) > 1e-5:
        raise RuntimeError(f"Runtime smoke-test vectors are not unit length: {norms}")

    return {
        "files": len(actual_files),
        "model": model_record,
        "repository_bytes": sum(
            (root / relative_path).stat().st_size for relative_path in actual_files
        ),
        "source_revision": provenance["source"]["revision"],
        "runtime_smoke_test": {
            "all_values_finite": True,
            "maximum_unit_norm_error": float(np.max(np.abs(norms - 1.0))),
            "output_shape": list(runtime_output.shape),
        },
        "validation_inputs": len(provenance["parity_sanity_check"]["inputs"]),
        "validation_minimum_int8_cosine": parity["int8"][
            "minimum_cosine_similarity"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(validate(arguments.repository), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
