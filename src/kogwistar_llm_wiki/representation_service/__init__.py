"""Optional FastAPI service for isolated multimodal representation inference."""

from .app import create_app
from .config import RepresentationServiceConfig, load_config

__all__ = ["RepresentationServiceConfig", "create_app", "load_config"]
