"""Pinned artifacts used by the optional semantic search runtime."""

from __future__ import annotations

from dataclasses import dataclass
import platform
import sys


MODEL_NAME = "MedEmbed-small-v0.1-int8-medbrevia-6cbe4664"
MODEL_REPOSITORY = "medbrevia/medembed-small-v0.1-onnx-int8"
MODEL_UPSTREAM = "abhinand/MedEmbed-small-v0.1"
MODEL_REVISION = "6cbe4664f1e0067da935f5abc24e4f8b5406b13f"
MODEL_LICENSE = "Apache-2.0"
MODEL_DIMENSION = 384
MODEL_MAX_LENGTH = 512
MODEL_BASE_URL = (
    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}"
)


@dataclass(frozen=True)
class Artifact:
    remote_path: str
    local_path: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"{MODEL_BASE_URL}/{self.remote_path}"


MODEL_ARTIFACTS = (
    Artifact(
        remote_path="onnx/model_int8.onnx",
        local_path="model_int8.onnx",
        sha256="accb5b9e914356d01190c9f208ac822b345b24daeee4aa0fb345dfcc89a871d5",
        size=34_057_273,
    ),
    Artifact(
        remote_path="tokenizer.json",
        local_path="tokenizer.json",
        sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        size=711_396,
    ),
    Artifact(
        remote_path="tokenizer_config.json",
        local_path="tokenizer_config.json",
        sha256="0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53",
        size=1_242,
    ),
    Artifact(
        remote_path="special_tokens_map.json",
        local_path="special_tokens_map.json",
        sha256="5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a",
        size=695,
    ),
    Artifact(
        remote_path="vocab.txt",
        local_path="vocab.txt",
        sha256="07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
        size=231_508,
    ),
    Artifact(
        remote_path="config.json",
        local_path="config.json",
        sha256="86d5a871bc41599d2e04d7518c6f09cef47800befbe8dace4888f5fa7a9b4333",
        size=711,
    ),
)


@dataclass(frozen=True)
class RuntimeWheel:
    filename: str
    sha256: str


DARWIN_ARM64_PY313 = (
    RuntimeWheel(
        filename="onnxruntime-1.28.0-cp313-cp313-macosx_14_0_arm64.whl",
        sha256="31410f544674f534c2f27348af52ef81682ca9c8719154bf4d48f0ef23823b1e",
    ),
    RuntimeWheel(
        filename="numpy-2.5.1-cp313-cp313-macosx_14_0_arm64.whl",
        sha256="6165343f81b56ef8f514f396989e529b61d9dc709b99421b07e9f3e698e2287d",
    ),
    RuntimeWheel(
        filename="tokenizers-0.23.1-cp310-abi3-macosx_11_0_arm64.whl",
        sha256="e0948bbb1ac1d7cdfc9fb6d62c596e3b7550036ad60ecd654a66ad273326324e",
    ),
    RuntimeWheel(
        filename="flatbuffers-25.12.19-py2.py3-none-any.whl",
        sha256="7634f50c427838bb021c2d66a3d1168e9d199b0607e6329399f04846d42e20b4",
    ),
)

RUNTIME_WHEELS = {
    "darwin-arm64-py313": DARWIN_ARM64_PY313,
}


def runtime_tag() -> str:
    """Return the native-runtime bundle tag for this Python process."""

    system = platform.system().casefold()
    machine = platform.machine().casefold()
    python_tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        machine = "arm64"
    return f"{system}-{machine}-{python_tag}"
