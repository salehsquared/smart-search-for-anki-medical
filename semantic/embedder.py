"""ONNX medical text embedding with lazy optional native imports."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import threading
from typing import Any

from .manifest import MODEL_DIMENSION, MODEL_MAX_LENGTH
from .model_manager import ModelManager


class SemanticRuntimeError(RuntimeError):
    pass


class OnnxMedicalEmbedder:
    """Generate normalized MedEmbed vectors.

    Imports of NumPy, ONNX Runtime, and tokenizers are intentionally delayed
    until construction.  The main add-on remains importable when semantic
    dependencies are unavailable.
    """

    def __init__(self, manager: ModelManager, *, intra_op_threads: int = 2) -> None:
        if not manager.model_ready():
            raise SemanticRuntimeError("The medical embedding model is not installed.")
        manager.activate_runtime()
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except Exception as error:
            raise SemanticRuntimeError(
                f"The local semantic runtime could not be loaded: {error}"
            ) from error

        self._np = np
        self._ort = ort
        self._lock = threading.RLock()
        try:
            self._tokenizer = Tokenizer.from_file(str(manager.tokenizer_path))
            self._tokenizer.enable_truncation(max_length=MODEL_MAX_LENGTH)
            self._tokenizer.enable_padding()
        except Exception as error:
            raise SemanticRuntimeError(
                f"Could not open the local semantic tokenizer: {error}"
            ) from error

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, intra_op_threads)
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            self._session = ort.InferenceSession(
                str(manager.model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise SemanticRuntimeError(f"Could not open the ONNX model: {error}") from error

        self._input_names = {item.name for item in self._session.get_inputs()}
        self._output_names = {item.name for item in self._session.get_outputs()}
        if "sentence_embedding" not in self._output_names:
            raise SemanticRuntimeError(
                "The ONNX model does not provide a sentence_embedding output."
            )

    @property
    def dimension(self) -> int:
        return MODEL_DIMENSION

    def embed(
        self,
        texts: Sequence[str],
        *,
        batch_size: int = 16,
        progress: Callable[[int, int], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> Any:
        if not texts:
            return self._np.empty((0, MODEL_DIMENSION), dtype=self._np.float32)

        vectors: list[Any] = []
        total = len(texts)
        with self._lock:
            for start in range(0, total, max(1, batch_size)):
                if cancel_check is not None:
                    cancel_check()
                batch = [str(text or "") for text in texts[start : start + batch_size]]
                encodings = self._tokenizer.encode_batch(batch)
                feeds = {
                    "input_ids": self._np.asarray(
                        [encoding.ids for encoding in encodings], dtype=self._np.int64
                    ),
                    "attention_mask": self._np.asarray(
                        [encoding.attention_mask for encoding in encodings],
                        dtype=self._np.int64,
                    ),
                    "token_type_ids": self._np.asarray(
                        [encoding.type_ids for encoding in encodings],
                        dtype=self._np.int64,
                    ),
                }
                active_feeds = {
                    name: value for name, value in feeds.items() if name in self._input_names
                }
                try:
                    output = self._session.run(["sentence_embedding"], active_feeds)[0]
                except Exception as error:
                    raise SemanticRuntimeError(f"Embedding inference failed: {error}") from error

                output = self._np.asarray(output, dtype=self._np.float32)
                norms = self._np.linalg.norm(output, axis=1, keepdims=True)
                output = output / self._np.maximum(norms, 1e-12)
                vectors.append(output)
                if progress:
                    progress(min(start + len(batch), total), total)
            if cancel_check is not None:
                cancel_check()

        return self._np.concatenate(vectors, axis=0)
