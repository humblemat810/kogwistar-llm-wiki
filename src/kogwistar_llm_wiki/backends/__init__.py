"""Optional vector backend integration for LLM-Wiki."""

from .registry import SUPPORTED_BACKENDS, VectorBackendSettings, build_backend_factory

__all__ = ["SUPPORTED_BACKENDS", "VectorBackendSettings", "build_backend_factory"]
