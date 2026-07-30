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
from .service import SemanticHit, SemanticService, SemanticStatus

__all__ = [
    "ModelInstallError",
    "ModelManager",
    "RuntimeInstallError",
    "SemanticHit",
    "SemanticService",
    "SemanticStatus",
    "UnsupportedRuntimeError",
]
