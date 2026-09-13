"""Standalone Qwen3-VL embedding service package."""

from .app import create_app
from .config import EmbeddingServiceConfig, load_config

__all__ = ["EmbeddingServiceConfig", "create_app", "load_config"]
