"""Pinned artifacts used by the optional semantic search runtime."""

from __future__ import annotations

from dataclasses import dataclass
import platform


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
WORKER_RUNTIME_TAG = "darwin-arm64-worker-py313"
WORKER_PYTHON_VERSION = "3.13.14"
WORKER_PYTHON_BUILD = "20260728"
WORKER_PYTHON_FILENAME = (
    "cpython-3.13.14+20260728-aarch64-apple-darwin-"
    "install_only_stripped.tar.gz"
)
WORKER_PYTHON_SHA256 = (
    "aa2a054f5e04bde63ae199e3bb6bbb634e457423efd294842deeb1299e7e5932"
)
WORKER_PYTHON_SIZE = 25_121_759
WORKER_PYTHON_SOURCE = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20260728/cpython-3.13.14%2B20260728-aarch64-apple-darwin-"
    "install_only_stripped.tar.gz"
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


DARWIN_ARM64_PY39_NUMPY = RuntimeWheel(
    filename="numpy-2.0.2-cp39-cp39-macosx_14_0_arm64.whl",
    sha256="2b2955fa6f11907cf7a70dab0d0755159bca87755e831e47932367fc8f2f2d0b",
)

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

WORKER_RUNTIME_WHEELS = DARWIN_ARM64_PY313
RUNTIME_WHEELS = {WORKER_RUNTIME_TAG: WORKER_RUNTIME_WHEELS}
HOST_VECTOR_WHEELS = {
    # The host process receives NumPy only. Inference libraries are confined
    # to the standalone worker so they can be fully reclaimed after use.
    "darwin-arm64-py39": (DARWIN_ARM64_PY39_NUMPY,),
    "darwin-arm64-py313": (DARWIN_ARM64_PY313[1],),
}


def runtime_tag() -> str:
    """Return the standalone Semantic worker tag for this platform."""

    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        machine = "arm64"
    if system == "darwin" and machine == "arm64":
        return WORKER_RUNTIME_TAG
    return f"{system}-{machine}-worker"
