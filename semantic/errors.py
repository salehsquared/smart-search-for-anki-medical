"""Semantic runtime errors shared by the host and isolated worker."""

from __future__ import annotations


class SemanticRuntimeError(RuntimeError):
    """The optional Semantic runtime could not complete an operation."""


class SemanticWorkerError(SemanticRuntimeError):
    """The isolated Semantic worker failed or violated its protocol."""
