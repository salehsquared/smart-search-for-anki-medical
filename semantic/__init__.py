"""Optional, fully local semantic retrieval for Smart Search.

This package deliberately has no Anki or Qt imports.  The add-on can therefore
load and use lexical search even when the optional native inference runtime is
not installed for the current platform.
"""

from .model_manager import (
    ModelInstallError,
    ModelManager,
    RuntimeInstallError,
    UnsupportedRuntimeError,
)
from .service import (
    SemanticDocument,
    SemanticHit,
    SemanticService,
    SemanticStatus,
    semantic_text_hash,
)

__all__ = [
    "ModelInstallError",
    "ModelManager",
    "RuntimeInstallError",
    "SemanticDocument",
    "SemanticHit",
    "SemanticService",
    "SemanticStatus",
    "UnsupportedRuntimeError",
    "semantic_text_hash",
]
