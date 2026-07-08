from __future__ import annotations

import os

from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    ProviderEndpointConfig,
    WorkflowProviderSettings,
)


_PROVIDER_ALIASES = {
    "azure_openai": "azure",
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value in {None, ""}:
        return default
    return value


def _first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value not in {None, ""}:
            return value
    return default


def _first_env_name(*names: str) -> str | None:
    for name in names:
        if os.getenv(name) not in {None, ""}:
            return name
    return None


def normalize_provider_name(provider: str | None) -> str | None:
    if provider is None:
        return None
    normalized = str(provider).strip().lower()
    if not normalized:
        return None
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _role_prefix(role: str) -> str:
    return f"KOGWISTAR_{role.upper()}"


def _legacy_prefix(role: str) -> str:
    return f"KG_DOC_{role.upper()}"


def _openai_env_suffix_for_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = str(model).strip().lower().replace(" ", "")
    if not normalized:
        return None
    compact = normalized.replace("-", "").replace("_", "").replace(".", "")
    if compact in {"gpt4o", "gpt4omini"}:
        return "GPT4O" if compact == "gpt4o" else "GPT4O_MINI"
    if compact == "gpt41":
        return "GPT4_1"
    if normalized in {"gpt-4.1", "gpt4.1"}:
        return "GPT4_1"
    if normalized in {"gpt-4.1-mini", "gpt4.1-mini"}:
        return "GPT4_1_MINI"
    if normalized in {"gpt-4.1-nano", "gpt4.1-nano"}:
        return "GPT4_1_NANO"
    if normalized in {"gpt-5-mini", "gpt5-mini"}:
        return "GPT5_MINI"
    if normalized in {"gpt-5-nano", "gpt5-nano"}:
        return "GPT5_NANO"
    if normalized in {"gpt-5-chat", "gpt5-chat"}:
        return "GPT5_CHAT"
    return None


def _role_provider_defaults(role: str) -> tuple[str | None, str | None]:
    if role == "parser":
        return "ollama", "gemma4:e2b"
    if role == "maintenance":
        return "ollama", "gemma4:e2b"
    return None, None


def build_provider_endpoint_config(
    role: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_version: str | None = None,
    project: str | None = None,
    location: str | None = None,
    max_retries: int | None = None,
) -> ProviderEndpointConfig:
    role_prefix = _role_prefix(role)
    legacy_prefix = _legacy_prefix(role)
    default_provider, default_model = _role_provider_defaults(role)

    provider_env_names = [f"{role_prefix}_PROVIDER"]
    model_env_names = [f"{role_prefix}_MODEL"]
    base_url_env_names = [f"{role_prefix}_BASE_URL"]
    api_key_env_names = [f"{role_prefix}_API_KEY_ENV"]
    api_version_env_names = [f"{role_prefix}_API_VERSION"]
    project_env_names = [f"{role_prefix}_PROJECT"]
    location_env_names = [f"{role_prefix}_LOCATION"]
    max_retries_env_names = [f"{role_prefix}_MAX_RETRIES"]
    if role == "maintenance":
        provider_env_names.extend(["KOGWISTAR_PARSER_PROVIDER", "KG_DOC_PARSER_PROVIDER"])
        model_env_names.extend(
            [
                "KOGWISTAR_PARSER_MODEL",
                "KG_DOC_PARSER_MODEL",
                "OLLAMA_MODEL",
                "GEMINI_MODEL",
            ]
        )
        base_url_env_names.extend(["KOGWISTAR_PARSER_BASE_URL", "KG_DOC_PARSER_BASE_URL"])
        api_key_env_names.extend(["KOGWISTAR_PARSER_API_KEY_ENV", "KG_DOC_PARSER_API_KEY_ENV"])
        api_version_env_names.extend(["KOGWISTAR_PARSER_API_VERSION", "KG_DOC_PARSER_API_VERSION"])
        project_env_names.extend(["KOGWISTAR_PARSER_PROJECT", "KG_DOC_PARSER_PROJECT"])
        location_env_names.extend(["KOGWISTAR_PARSER_LOCATION", "KG_DOC_PARSER_LOCATION"])
        max_retries_env_names.extend(["KOGWISTAR_PARSER_MAX_RETRIES", "KG_DOC_PARSER_MAX_RETRIES"])
    else:
        model_env_names.extend(["OLLAMA_MODEL", "GEMINI_MODEL"])

    resolved_provider = normalize_provider_name(
        provider
        or _first_env(*provider_env_names, f"{legacy_prefix}_PROVIDER", default=default_provider)
    )
    resolved_model = (
        model
        or _first_env(*model_env_names, f"{legacy_prefix}_MODEL", default=default_model)
        or default_model
        or "gemini-2.5-flash"
    )
    if resolved_provider in {"openai", "azure"}:
        openai_suffix = _openai_env_suffix_for_model(resolved_model)
        endpoint_env_names = [f"{role_prefix}_BASE_URL", f"{legacy_prefix}_BASE_URL"]
        api_key_candidate_env_names = [f"{role_prefix}_API_KEY_ENV", f"{legacy_prefix}_API_KEY_ENV"]
        api_version_candidate_env_names = [f"{role_prefix}_API_VERSION", f"{legacy_prefix}_API_VERSION"]
        if openai_suffix:
            endpoint_env_names = [
                f"OPENAI_DEPLOYMENT_ENDPOINT_{openai_suffix}",
                f"OPENAI_ENDPOINT_{openai_suffix}",
                f"AZURE_OPENAI_ENDPOINT_{openai_suffix}",
                *endpoint_env_names,
            ]
            api_key_candidate_env_names = [
                f"OPENAI_API_KEY_{openai_suffix}",
                f"AZURE_OPENAI_API_KEY_{openai_suffix}",
                *api_key_candidate_env_names,
            ]
            api_version_candidate_env_names = [
                f"OPENAI_DEPLOYMENT_VERSION_{openai_suffix}",
                f"AZURE_OPENAI_API_VERSION_{openai_suffix}",
                *api_version_candidate_env_names,
            ]
        endpoint_env_names.extend(
            [
                "OPENAI_DEPLOYMENT_ENDPOINT",
                "OPENAI_API_BASE",
                "AZURE_OPENAI_ENDPOINT",
                "OPENAI_ENDPOINT",
            ]
        )
        api_key_candidate_env_names.extend(["OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"])
        api_version_candidate_env_names.extend(["OPENAI_API_VERSION", "AZURE_OPENAI_API_VERSION"])
        base_url_env_names = endpoint_env_names + base_url_env_names
        api_key_env_names = api_key_candidate_env_names + api_key_env_names
        api_version_env_names = api_version_candidate_env_names + api_version_env_names

    resolved_temperature = float(
        temperature
        if temperature is not None
        else _first_env(f"{role_prefix}_TEMPERATURE", f"{legacy_prefix}_TEMPERATURE", default="0.1")
        or "0.1"
    )
    resolved_base_url = base_url or _first_env(*base_url_env_names, f"{legacy_prefix}_BASE_URL")
    resolved_api_key_env = (
        api_key_env
        or _first_env(f"{role_prefix}_API_KEY_ENV", f"{legacy_prefix}_API_KEY_ENV")
        or _first_env_name(*api_key_env_names)
    )
    resolved_api_version = (
        api_version
        or _first_env(*api_version_env_names, f"{legacy_prefix}_API_VERSION")
        or _first_env("OPENAI_API_VERSION", "AZURE_OPENAI_API_VERSION")
    )
    resolved_project = project or _first_env(*project_env_names, f"{legacy_prefix}_PROJECT")
    resolved_location = location or _first_env(*location_env_names, f"{legacy_prefix}_LOCATION")
    resolved_max_retries = int(
        max_retries
        if max_retries is not None
        else _first_env(*max_retries_env_names, f"{legacy_prefix}_MAX_RETRIES", default="2")
        or "2"
    )

    return ProviderEndpointConfig(
        provider=str(resolved_provider or "ollama"),
        model=str(resolved_model),
        temperature=resolved_temperature,
        base_url=resolved_base_url,
        api_key_env=resolved_api_key_env,
        api_version=resolved_api_version,
        project=resolved_project,
        location=resolved_location,
        max_retries=resolved_max_retries,
    )


def build_workflow_provider_settings(
    *,
    proposal_mode: str | None = None,
    parser: ProviderEndpointConfig | None = None,
    ocr: ProviderEndpointConfig | None = None,
    embedding: EmbeddingProviderConfig | None = None,
) -> WorkflowProviderSettings:
    parser_spec = parser or build_provider_endpoint_config("parser")
    ocr_spec = ocr or ProviderEndpointConfig()
    embedding_spec = embedding or EmbeddingProviderConfig()
    settings_kwargs: dict[str, object] = {"parser": parser_spec, "ocr": ocr_spec, "embedding": embedding_spec}
    if proposal_mode is not None:
        settings_kwargs["proposal_mode"] = proposal_mode
    return WorkflowProviderSettings(**settings_kwargs)


def resolve_parser_provider_settings(
    *,
    proposal_mode: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_version: str | None = None,
    project: str | None = None,
    location: str | None = None,
    max_retries: int | None = None,
) -> WorkflowProviderSettings:
    resolved_proposal_mode = proposal_mode or _first_env(
        "KOGWISTAR_PARSER_PROPOSAL_MODE",
        "KG_DOC_PARSER_PROPOSAL_MODE",
        default="children",
    )
    return build_workflow_provider_settings(
        proposal_mode=str(resolved_proposal_mode or "children"),
        parser=build_provider_endpoint_config(
            "parser",
            provider=provider,
            model=model,
            temperature=temperature,
            base_url=base_url,
            api_key_env=api_key_env,
            api_version=api_version,
            project=project,
            location=location,
            max_retries=max_retries,
        )
    )


def resolve_maintenance_provider_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_version: str | None = None,
    project: str | None = None,
    location: str | None = None,
    max_retries: int | None = None,
) -> WorkflowProviderSettings:
    return build_workflow_provider_settings(
        parser=build_provider_endpoint_config(
            "maintenance",
            provider=provider,
            model=model,
            temperature=temperature,
            base_url=base_url,
            api_key_env=api_key_env,
            api_version=api_version,
            project=project,
            location=location,
            max_retries=max_retries,
        )
    )


def provider_config_summary(settings: WorkflowProviderSettings | ProviderEndpointConfig) -> dict[str, object]:
    parser = settings.parser if hasattr(settings, "parser") else settings
    proposal_mode = getattr(settings, "proposal_mode", None)
    return {
        "proposal_mode": proposal_mode,
        "provider": getattr(parser, "provider", None),
        "model": getattr(parser, "model", None),
        "temperature": getattr(parser, "temperature", None),
        "base_url": getattr(parser, "base_url", None),
        "api_key_env": getattr(parser, "api_key_env", None),
        "api_version": getattr(parser, "api_version", None),
        "project": getattr(parser, "project", None),
        "location": getattr(parser, "location", None),
        "max_retries": getattr(parser, "max_retries", None),
    }
