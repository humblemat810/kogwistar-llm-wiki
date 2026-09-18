"""Embedding provider resolution kept separate from ingestion orchestration."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    build_embedding_function,
)
from kogwistar.engine_core.embedding_profile import (
    EmbeddingProfile,
    endpoint_fingerprint,
)
from kogwistar.typing_interfaces import EmbeddingFunctionLike

EMBEDDING_SPACES = ("conversation", "workflow", "knowledge", "wisdom")


class TinyEmbeddingFunction:
    """Deterministic in-memory fallback used by provider-free tests."""

    _name = "kogwistar-llm-wiki-embedding-v1"

    def name(self) -> str:
        return self._name

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for value in input:
            text = str(value or "")
            checksum = float((sum(ord(ch) for ch in text) % 97) + 1)
            vectors.append([float(len(text) + 1), checksum])
        return vectors


def resolve_embedding_function(
    *,
    namespace: str = "global",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_max_sequence_length: int | None = None,
    embedding_crop_token_budget: int | None = None,
    embedding_tokenizer_fingerprint: str | None = None,
    embedding_factory: Callable[[EmbeddingProviderConfig], EmbeddingFunctionLike] | None = None,
) -> tuple[EmbeddingFunctionLike, EmbeddingProviderConfig]:
    """Resolve one embedding provider without creating a second factory."""

    def embedding_env(suffix: str, default: str | None = None) -> str | None:
        namespace_prefix = (
            "KOGWISTAR_LLM_WIKI_"
            + namespace.upper().replace("-", "_")
            + "_EMBED_"
        )
        for prefix in (
            namespace_prefix,
            "KOGWISTAR_LLM_WIKI_EMBED_",
            "KOGWISTAR_EMBED_",
            "KG_DOC_EMBED_",
        ):
            value = os.getenv(prefix + suffix)
            if value not in {None, ""}:
                return value
        return default

    if embedding_function is not None and embedding_config is not None:
        raise ValueError("pass either embedding_function or embedding_config, not both")

    configured = any(
        value is not None
        for value in (
            embedding_provider,
            embedding_model,
            embedding_dimension,
            embedding_base_url,
            embedding_api_key_env,
            embedding_max_sequence_length,
            embedding_crop_token_budget,
            embedding_tokenizer_fingerprint,
        )
    ) or any(
        embedding_env(suffix) is not None
        for suffix in (
            "PROVIDER",
            "MODEL",
            "DIMENSION",
            "BASE_URL",
            "API_KEY_ENV",
            "MAX_SEQUENCE_LENGTH",
            "CROP_TOKEN_BUDGET",
            "TOKENIZER_FINGERPRINT",
        )
    )

    if embedding_function is not None:
        config = embedding_config or EmbeddingProviderConfig(
            provider="fake", model=embedding_function.name(), dimension=2
        )
        return embedding_function, config
    if embedding_config is None and not configured:
        tiny = TinyEmbeddingFunction()
        return tiny, EmbeddingProviderConfig(provider="fake", model=tiny.name(), dimension=2)

    config = embedding_config or EmbeddingProviderConfig(
        provider=embedding_provider or embedding_env("PROVIDER", "fake"),
        model=embedding_model or embedding_env("MODEL", "kg-doc-parser-workflow-embedding-v1"),
        dimension=int(embedding_dimension or embedding_env("DIMENSION", "2")),
        base_url=embedding_base_url or embedding_env("BASE_URL"),
        api_key_env=embedding_api_key_env or embedding_env("API_KEY_ENV"),
        max_sequence_length=(
            embedding_max_sequence_length
            if embedding_max_sequence_length is not None
            else (
                int(value)
                if (value := embedding_env("MAX_SEQUENCE_LENGTH")) is not None
                else None
            )
        ),
        crop_token_budget=(
            embedding_crop_token_budget
            if embedding_crop_token_budget is not None
            else (
                int(value)
                if (value := embedding_env("CROP_TOKEN_BUDGET")) is not None
                else None
            )
        ),
        tokenizer_fingerprint=(
            embedding_tokenizer_fingerprint or embedding_env("TOKENIZER_FINGERPRINT")
        ),
    )
    factory = embedding_factory or build_embedding_function
    return factory(config), config


def resolve_embedding_functions(
    *,
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_factory: Callable[[EmbeddingProviderConfig], EmbeddingFunctionLike] | None = None,
) -> tuple[dict[str, EmbeddingFunctionLike], dict[str, EmbeddingProviderConfig]]:
    """Resolve one embedding function/config per graph space."""

    scoped_prefix = "KOGWISTAR_LLM_WIKI_"
    supported_scopes = {value.upper() for value in EMBEDDING_SPACES} | {"EMBED"}
    for env_name in os.environ:
        if not env_name.startswith(scoped_prefix) or "_EMBED_" not in env_name:
            continue
        rest = env_name[len(scoped_prefix) :]
        scope = "EMBED" if rest.startswith("EMBED_") else rest.split("_EMBED_", 1)[0]
        if scope not in supported_scopes:
            raise ValueError(
                f"unsupported llm-wiki embedding scope {scope.lower()!r}; "
                "use conversation, workflow, knowledge, or wisdom"
            )

    if embedding_functions and embedding_function is not None:
        raise ValueError("pass either embedding_function or embedding_functions, not both")
    if embedding_configs and embedding_config is not None:
        raise ValueError("pass either embedding_config or embedding_configs, not both")

    shared_function, shared_config = resolve_embedding_function(
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
        embedding_factory=embedding_factory,
    )
    functions: dict[str, EmbeddingFunctionLike] = {}
    configs: dict[str, EmbeddingProviderConfig] = {}
    for space in EMBEDDING_SPACES:
        space_function = (embedding_functions or {}).get(space)
        space_config = (embedding_configs or {}).get(space)
        if space_function is None and space_config is None:
            namespace_setting = any(
                os.getenv(
                    "KOGWISTAR_LLM_WIKI_" + space.upper() + "_EMBED_" + suffix
                )
                not in {None, ""}
                for suffix in (
                    "PROVIDER",
                    "MODEL",
                    "DIMENSION",
                    "BASE_URL",
                    "API_KEY_ENV",
                    "MAX_SEQUENCE_LENGTH",
                    "CROP_TOKEN_BUDGET",
                    "TOKENIZER_FINGERPRINT",
                )
            )
            if not namespace_setting:
                functions[space] = shared_function
                configs[space] = shared_config
                continue
        resolved_function, resolved_config = resolve_embedding_function(
            namespace=space,
            embedding_function=space_function,
            embedding_config=space_config,
            embedding_provider=None if space_function or space_config else embedding_provider,
            embedding_model=None if space_function or space_config else embedding_model,
            embedding_dimension=None if space_function or space_config else embedding_dimension,
            embedding_base_url=None if space_function or space_config else embedding_base_url,
            embedding_api_key_env=None if space_function or space_config else embedding_api_key_env,
            embedding_factory=embedding_factory,
        )
        functions[space] = resolved_function
        configs[space] = resolved_config

    for space, config in configs.items():
        space_prefix = f"KOGWISTAR_LLM_WIKI_{space.upper()}_EMBED_"
        has_declared_dimension = (
            embedding_dimension is not None
            or embedding_config is not None
            or space in (embedding_configs or {})
            or any(
                os.getenv(prefix + "DIMENSION") not in {None, ""}
                for prefix in (
                    space_prefix,
                    "KOGWISTAR_LLM_WIKI_EMBED_",
                    "KOGWISTAR_EMBED_",
                    "KG_DOC_EMBED_",
                )
            )
        )
        if config.provider != "fake" and not has_declared_dimension:
            raise ValueError(
                f"embedding dimension is required for the real {space} provider; "
                "set the scoped dimension or a global embedding dimension"
            )
    return functions, configs


def embedding_profile(config: EmbeddingProviderConfig) -> EmbeddingProfile:
    """Translate parser-owned provider settings into the core profile."""

    return EmbeddingProfile(
        provider=config.provider,
        model=config.model,
        dimension=config.dimension,
        similarity_metric="cosine",
        endpoint_fingerprint=endpoint_fingerprint(config.base_url),
        max_sequence_length=config.max_sequence_length,
        crop_token_budget=config.crop_token_budget,
        tokenizer_fingerprint=config.tokenizer_fingerprint,
        crop_policy="token_prefix" if config.crop_token_budget is not None else None,
    )


def validate_shared_postgres_embedding_profile(
    configs: Mapping[str, EmbeddingProviderConfig],
) -> None:
    """Reject mixed profiles sharing one physical PostgreSQL vector table."""

    profiles = {space: embedding_profile(config) for space, config in configs.items()}
    if len(set(profiles.values())) <= 1:
        return
    rendered = ", ".join(
        f"{space}={profile.provider}/{profile.model}/{profile.dimension}D"
        for space, profile in sorted(profiles.items())
    )
    raise ValueError(
        "PostgreSQL LLM-Wiki namespace engines currently share physical "
        f"pgvector tables, but their embedding profiles differ: {rendered}. "
        "Configure one provider/model/dimension for all graph spaces, or use "
        "separate physical PostgreSQL schemas or databases per embedding space. "
        "Existing vectors are not migrated automatically; archive, replay, "
        "and re-embed into the isolated target before cutover."
    )


__all__ = [
    "EMBEDDING_SPACES",
    "TinyEmbeddingFunction",
    "embedding_profile",
    "resolve_embedding_function",
    "resolve_embedding_functions",
    "validate_shared_postgres_embedding_profile",
]
