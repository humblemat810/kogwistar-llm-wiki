"""Compatibility façade for provider-role configuration."""

from .providers.role_config import (
    EmbeddingProviderConfig,
    ProviderEndpointConfig,
    WorkflowProviderSettings,
    build_provider_endpoint_config,
    build_workflow_provider_settings,
    normalize_provider_name,
    provider_config_summary,
    resolve_maintenance_provider_settings,
    resolve_parser_provider_settings,
)

__all__ = [
    "EmbeddingProviderConfig",
    "ProviderEndpointConfig",
    "WorkflowProviderSettings",
    "build_provider_endpoint_config",
    "build_workflow_provider_settings",
    "normalize_provider_name",
    "provider_config_summary",
    "resolve_maintenance_provider_settings",
    "resolve_parser_provider_settings",
]
