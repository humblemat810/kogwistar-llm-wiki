"""Backend construction for application graph-space bundles.

This module owns engine wiring; ingestion orchestration stays in the
parent pipeline module.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from kg_doc_parser.workflow_ingest.providers import EmbeddingProviderConfig
from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.embedding_profile import EmbeddingProfile
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.typing_interfaces import EmbeddingFunctionLike

from ..backends import VectorBackendSettings, build_backend_factory
from ..embeddings.embedding_config_resolver import (
    EMBEDDING_SPACES,
    validate_shared_postgres_embedding_profile,
)
from ..models import NamespaceEngines

EmbeddingResolver = Callable[..., tuple[dict[str, EmbeddingFunctionLike], dict[str, EmbeddingProviderConfig]]]
ProfileResolver = Callable[[EmbeddingProviderConfig], EmbeddingProfile | None]

def build_in_memory_namespace_engines(
    base_dir: str | Path | None = None,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    embedding_resolver: EmbeddingResolver,
    profile_resolver: ProfileResolver,
) -> NamespaceEngines:
    root = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="kogwistar-llm-wiki-"))
    embeddings, configs = embedding_resolver(
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
    )
    
    # Shared conversation engine (fg/bg lanes)
    conversation = _build_engine(
        root / "conversation",
        kg_graph_type="conversation",
        embedding_function=embeddings["conversation"],
        embedding_profile=profile_resolver(configs["conversation"]),
        embedding_profile_mode=embedding_profile_mode,
        persistence_mode=conversation_persistence_mode,
    )
    
    derived_engine = _build_engine(root / "derived_knowledge", kg_graph_type="derived_knowledge", embedding_function=embeddings["knowledge"], embedding_profile=profile_resolver(configs["knowledge"]), embedding_profile_mode=embedding_profile_mode) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=conversation,
        workflow=_build_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embeddings["workflow"], embedding_profile=profile_resolver(configs["workflow"]), embedding_profile_mode=embedding_profile_mode),
        kg=_build_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embeddings["knowledge"], embedding_profile=profile_resolver(configs["knowledge"]), embedding_profile_mode=embedding_profile_mode),
        wisdom=_build_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embeddings["wisdom"], embedding_profile=profile_resolver(configs["wisdom"]), embedding_profile_mode=embedding_profile_mode),
        derived_knowledge=derived_engine,
    )


def build_persistent_namespace_engines(
    base_dir: str | Path,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    embedding_resolver: EmbeddingResolver,
    profile_resolver: ProfileResolver,
    vector_backend: str = "chroma",
) -> NamespaceEngines:
    root = Path(base_dir)
    embeddings, resolved_embedding_configs = embedding_resolver(
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
    )
    def build_space(space: str, graph_type: str, *, persistence_mode: Literal["single_stage", "two_stage"] = "single_stage") -> GraphKnowledgeEngine:
        profile = profile_resolver(resolved_embedding_configs[space])
        if vector_backend in {"chroma", "postgres"}:
            return _build_persistent_engine(
                root / graph_type,
                kg_graph_type=graph_type,
                embedding_function=embeddings[space],
                embedding_profile=profile,
                embedding_profile_mode=embedding_profile_mode,
                persistence_mode=persistence_mode,
            )
        settings = VectorBackendSettings.from_env(
            vector_backend, dimension=resolved_embedding_configs[space].dimension
        )
        return _build_external_engine(
            root / graph_type,
            kg_graph_type=graph_type,
            embedding_function=embeddings[space],
            embedding_profile=profile,
            embedding_profile_mode=embedding_profile_mode,
            persistence_mode=persistence_mode,
            backend_factory=build_backend_factory(settings),
        )

    derived_engine = build_space("knowledge", "derived_knowledge") if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=build_space("conversation", "conversation", persistence_mode=conversation_persistence_mode),
        workflow=build_space("workflow", "workflow"),
        kg=build_space("knowledge", "knowledge"),
        wisdom=build_space("wisdom", "wisdom"),
        derived_knowledge=derived_engine,
    )


def build_postgres_namespace_engines(
    *,
    base_dir: str | Path,
    dsn: str,
    embedding_dim: int | None = None,
    schema: str = "public",
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    embedding_resolver: EmbeddingResolver,
    profile_resolver: ProfileResolver,
    engine_builder: Callable[..., GraphKnowledgeEngine] | None = None,
) -> NamespaceEngines:
    root = Path(base_dir)
    embeddings, resolved_embedding_configs = embedding_resolver(
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension or embedding_dim,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
    )
    # A global ``embedding_dim`` remains a fallback. The PostgreSQL bundle
    # validates the resolved profiles below because its graph spaces share the
    # same physical vector tables.
    embedding_dimensions = {
        space: (
            (embedding_configs or {}).get(space).dimension
            if (embedding_configs or {}).get(space) is not None
            else embedding_dim or resolved_embedding_configs[space].dimension
        )
        for space in EMBEDDING_SPACES
    }
    validate_shared_postgres_embedding_profile(resolved_embedding_configs)
    root.mkdir(parents=True, exist_ok=True)
    engine_builder = engine_builder or _build_postgres_engine
    derived_engine = engine_builder(
        root / "derived_knowledge",
        kg_graph_type="derived_knowledge",
        embedding_function=embeddings["knowledge"],
        dsn=dsn,
        embedding_dim=embedding_dimensions["knowledge"],
        schema=schema,
        embedding_profile=profile_resolver(resolved_embedding_configs["knowledge"]),
        embedding_profile_mode=embedding_profile_mode,
    ) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=engine_builder(
            root / "conversation",
            kg_graph_type="conversation",
            embedding_function=embeddings["conversation"],
            dsn=dsn,
            embedding_dim=embedding_dimensions["conversation"],
            schema=schema,
            embedding_profile=profile_resolver(resolved_embedding_configs["conversation"]),
            embedding_profile_mode=embedding_profile_mode,
            persistence_mode=conversation_persistence_mode,
        ),
        workflow=engine_builder(
            root / "workflow",
            kg_graph_type="workflow",
            embedding_function=embeddings["workflow"],
            dsn=dsn,
            embedding_dim=embedding_dimensions["workflow"],
            schema=schema,
            embedding_profile=profile_resolver(resolved_embedding_configs["workflow"]),
            embedding_profile_mode=embedding_profile_mode,
        ),
        kg=engine_builder(
            root / "kg",
            kg_graph_type="knowledge",
            embedding_function=embeddings["knowledge"],
            dsn=dsn,
            embedding_dim=embedding_dimensions["knowledge"],
            schema=schema,
            embedding_profile=profile_resolver(resolved_embedding_configs["knowledge"]),
            embedding_profile_mode=embedding_profile_mode,
        ),
        wisdom=engine_builder(
            root / "wisdom",
            kg_graph_type="wisdom",
            embedding_function=embeddings["wisdom"],
            dsn=dsn,
            embedding_dim=embedding_dimensions["wisdom"],
            schema=schema,
            embedding_profile=profile_resolver(resolved_embedding_configs["wisdom"]),
            embedding_profile_mode=embedding_profile_mode,
        ),
        derived_knowledge=derived_engine,
    )


def _build_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
) -> GraphKnowledgeEngine:
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        embedding_profile=embedding_profile,
        embedding_profile_mode=embedding_profile_mode,
        backend_factory=build_in_memory_backend,
        namespace=kg_graph_type,
        persistence_mode=persistence_mode,
    )


def _build_persistent_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
) -> GraphKnowledgeEngine:
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        embedding_profile=embedding_profile,
        embedding_profile_mode=embedding_profile_mode,
        namespace=kg_graph_type,
        persistence_mode=persistence_mode,
    )


def _build_external_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    embedding_profile: EmbeddingProfile | None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"],
    persistence_mode: Literal["single_stage", "two_stage"],
    backend_factory: Callable[[GraphKnowledgeEngine], object] | None,
) -> GraphKnowledgeEngine:
    if backend_factory is None:
        raise ValueError("An external vector backend requires a backend factory")
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        embedding_profile=embedding_profile,
        embedding_profile_mode=embedding_profile_mode,
        backend_factory=backend_factory,
        namespace=kg_graph_type,
        persistence_mode=persistence_mode,
    )


def _build_postgres_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    dsn: str,
    embedding_dim: int,
    schema: str,
    embedding_profile: EmbeddingProfile | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    graph_engine_factory: Callable[..., GraphKnowledgeEngine] = GraphKnowledgeEngine,
) -> GraphKnowledgeEngine:
    from kogwistar.engine_core.engine_postgres import (
        EnginePostgresConfig,
        build_postgres_backend,
    )

    persist_directory.mkdir(parents=True, exist_ok=True)
    backend, _ = build_postgres_backend(
        EnginePostgresConfig(
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
            application_name=f"kogwistar-llm-wiki-{kg_graph_type}",
        )
    )
    return graph_engine_factory(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        backend=backend,
        embedding_profile=embedding_profile,
        embedding_profile_mode=embedding_profile_mode,
        namespace=kg_graph_type,
        persistence_mode=persistence_mode,
    )
