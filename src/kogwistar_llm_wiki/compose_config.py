"""Safe Compose bundle generation and validation for local operators.

This module deliberately emits configuration, not secrets.  The checked-in
Compose files remain authoritative for advanced deployments; generated files
are a small, reproducible starting point for the common stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


class ComposeConfigurationError(ValueError):
    """The requested deployment cannot be generated safely."""


@dataclass(frozen=True)
class ComposeOptions:
    backend: str = "postgres"
    workspace: str = "default"
    project_name: str = "llm-wiki"
    mode: str = "gpu"
    embedding_backend: str = "auto"
    with_otel: bool = False
    with_oauth: bool = False
    auth_mode: str = "disabled"
    model_revision: str = ""
    embedding_dimension: int = 1024
    embedding_max_model_len: int = 8192
    embedding_crop_token_budget: int = 7680
    embedding_gpu_memory_utilization: float = 0.86
    embedding_vllm_enforce_eager: bool = True
    embedding_vllm_max_num_seqs: int = 1
    postgres_password: str = "change-this-development-password"


def validate_options(options: ComposeOptions) -> list[str]:
    errors: list[str] = []
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", options.workspace):
        errors.append("workspace must contain only letters, numbers, '.', '_', or '-'")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", options.project_name):
        errors.append("project_name must contain only letters, numbers, '.', '_', or '-'")
    if options.mode not in {"cpu", "gpu", "text-only"}:
        errors.append("mode must be cpu, gpu, or text-only")
    if options.embedding_backend not in {"auto", "vllm", "transformers"}:
        errors.append("embedding_backend must be auto, vllm, or transformers")
    if options.mode == "text-only" and options.embedding_backend != "auto":
        errors.append("embedding_backend is only valid for cpu or gpu multimodal modes")
    if options.mode == "cpu" and options.embedding_backend == "vllm":
        errors.append("vllm embedding_backend is GPU-only; use transformers for CPU")
    if options.backend not in {"postgres", "chroma"}:
        errors.append("backend must be postgres or chroma")
    elif options.backend == "chroma":
        errors.append(
            "embedded Chroma cannot be generated for the multi-process Compose bundle; "
            "use PostgreSQL or the single-process demo"
        )
    if options.auth_mode not in {"disabled", "static_token", "kogwistar_jwt"}:
        errors.append("auth_mode must be disabled, static_token, or kogwistar_jwt")
    if options.mode in {"cpu", "gpu"} and not options.model_revision.strip():
        errors.append("model_revision is required for the multimodal service")
    if not 64 <= options.embedding_dimension <= 2048:
        errors.append("embedding_dimension must be between 64 and 2048")
    if options.backend == "postgres" and options.mode == "gpu" and options.embedding_dimension > 1536:
        errors.append("GPU pgvector default profile cannot use dimensions above 1536")
    if options.embedding_max_model_len <= 0:
        errors.append("embedding_max_model_len must be positive")
    elif options.embedding_max_model_len > 8192:
        errors.append("embedding_max_model_len cannot exceed 8192")
    if options.embedding_crop_token_budget <= 0:
        errors.append("embedding_crop_token_budget must be positive")
    elif options.embedding_crop_token_budget > options.embedding_max_model_len:
        errors.append("embedding_crop_token_budget cannot exceed embedding_max_model_len")
    if not 0 < options.embedding_gpu_memory_utilization <= 1:
        errors.append("embedding_gpu_memory_utilization must be between 0 and 1")
    if options.embedding_vllm_max_num_seqs <= 0:
        errors.append("embedding_vllm_max_num_seqs must be positive")
    return errors


def render_compose(options: ComposeOptions) -> str:
    errors = validate_options(options)
    if errors:
        raise ComposeConfigurationError("; ".join(errors))
    embedding_backend = (
        options.embedding_backend
        if options.embedding_backend != "auto"
        else ("vllm" if options.mode == "gpu" else "transformers")
    )
    auth = options.auth_mode
    embedding_url = (
        "http://embedding:8000"
        if options.mode == "gpu" and embedding_backend == "vllm"
        else "http://embedding:8790"
        if options.mode in {"cpu", "gpu"}
        else ""
    )
    lines = [
        f"name: {options.project_name}",
        "services:",
        "  postgres:",
        "    image: pgvector/pgvector:pg17",
        "    environment:",
        "      POSTGRES_DB: llm_wiki",
        "      POSTGRES_USER: llm_wiki",
        "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD}",
        "    volumes:",
        "      - postgres_data:/var/lib/postgresql/data",
        "    healthcheck:",
        '      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]',
        "      interval: 5s",
        "      timeout: 5s",
        "      retries: 12",
        "  llm-wiki:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        "    depends_on:",
        "      postgres:",
        "        condition: service_healthy",
        "    environment:",
        "      KOGWISTAR_DATA_DIR: /var/lib/llm-wiki",
        "      KOGWISTAR_POSTGRES_DSN: postgresql+psycopg://llm_wiki:${POSTGRES_PASSWORD}@postgres:5432/llm_wiki",
        f"      LLM_WIKI_WORKSPACE: {options.workspace}",
        f"      LLM_WIKI_AUTH_MODE: \"{auth}\"",
        f"      LLM_WIKI_AUTH_REQUIRED: \"${{LLM_WIKI_AUTH_REQUIRED:-{'true' if options.auth_mode != 'disabled' else 'false'}}}\"",
        "      LLM_WIKI_API_TOKEN: \"${LLM_WIKI_API_TOKEN:-}\"",
        "      KOGWISTAR_PARSER_PROVIDER: ${KOGWISTAR_PARSER_PROVIDER:-ollama}",
        "      KOGWISTAR_PARSER_MODEL: ${KOGWISTAR_PARSER_MODEL:-gemma4:e2b}",
        "      KOGWISTAR_PARSER_BASE_URL: ${KOGWISTAR_PARSER_BASE_URL:-http://host.docker.internal:11434}",
        "      KOGWISTAR_MAINTENANCE_PROVIDER: ${KOGWISTAR_MAINTENANCE_PROVIDER:-ollama}",
        "      KOGWISTAR_MAINTENANCE_MODEL: ${KOGWISTAR_MAINTENANCE_MODEL:-gemma4:e2b}",
        "      KOGWISTAR_MAINTENANCE_BASE_URL: ${KOGWISTAR_MAINTENANCE_BASE_URL:-http://host.docker.internal:11434}",
        f"      LLM_WIKI_OTEL_ENABLED: \"${{LLM_WIKI_OTEL_ENABLED:-{'true' if options.with_otel else 'false'}}}\"",
        "      OTEL_EXPORTER_OTLP_ENDPOINT: \"${OTEL_EXPORTER_OTLP_ENDPOINT:-http://grafana:4318}\"",
        f"      LLM_WIKI_MULTIMODAL_BACKEND: \"${{LLM_WIKI_MULTIMODAL_BACKEND:-{embedding_backend if embedding_url else 'none'}}}\"",
        f"      LLM_WIKI_EMBEDDING_SERVICE_URL: \"${{LLM_WIKI_EMBEDDING_SERVICE_URL:-{embedding_url if embedding_backend == 'transformers' else ''}}}\"",
        "      LLM_WIKI_EMBEDDING_SERVICE_TOKEN: \"${LLM_WIKI_EMBEDDING_SERVICE_TOKEN:-}\"",
        f"      LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS: \"${{LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS:-{'embedding' if embedding_backend == 'transformers' and embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_URL: \"${{LLM_WIKI_EMBEDDING_VLLM_URL:-{embedding_url if embedding_backend == 'vllm' else ''}}}\"",
        "      LLM_WIKI_EMBEDDING_VLLM_TOKEN: \"${LLM_WIKI_EMBEDDING_VLLM_TOKEN:-}\"",
        "      LLM_WIKI_EMBEDDING_VLLM_IMAGE: \"${LLM_WIKI_EMBEDDING_VLLM_IMAGE:-}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS: \"${{LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS:-{'embedding' if embedding_backend == 'vllm' else ''}}}\"",
        f"      LLM_WIKI_MULTIMODAL_MODEL: \"${{LLM_WIKI_MULTIMODAL_MODEL:-{'Qwen/Qwen3-VL-Embedding-2B' if embedding_url else ''}}}\"",
        f"      LLM_WIKI_MULTIMODAL_DIMENSION: \"${{LLM_WIKI_MULTIMODAL_DIMENSION:-{options.embedding_dimension if embedding_url else ''}}}\"",
        f"      LLM_WIKI_MULTIMODAL_MODEL_REVISION: \"${{LLM_WIKI_MULTIMODAL_MODEL_REVISION:-{options.model_revision if embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_MAX_MODEL_LEN: \"${{LLM_WIKI_EMBEDDING_MAX_MODEL_LEN:-{options.embedding_max_model_len if embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET: \"${{LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET:-{options.embedding_crop_token_budget if embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER: \"${{LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER:-{'1' if options.embedding_vllm_enforce_eager else '0'}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS: \"${{LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS:-{options.embedding_vllm_max_num_seqs}}}\"",
        f"    command: [llm-wiki, --backend, {options.backend}, workbench, --workspace, \"${{LLM_WIKI_WORKSPACE:-default}}\", --host, 0.0.0.0, --port, '8765']",
        "    ports: ['127.0.0.1:${LLM_WIKI_REST_PORT:-8765}:8765']",
        "    extra_hosts: ['host.docker.internal:host-gateway']",
        "    volumes: [app_data:/var/lib/llm-wiki]",
        "  mcp:",
        "    build:",
        "      context: .",
        "      dockerfile: Dockerfile",
        "    depends_on:",
        "      postgres:",
        "        condition: service_healthy",
        "    environment:",
        "      KOGWISTAR_DATA_DIR: /var/lib/llm-wiki",
        "      KOGWISTAR_POSTGRES_DSN: postgresql+psycopg://llm_wiki:${POSTGRES_PASSWORD}@postgres:5432/llm_wiki",
        f"      LLM_WIKI_OTEL_ENABLED: \"${{LLM_WIKI_OTEL_ENABLED:-{'true' if options.with_otel else 'false'}}}\"",
        "      OTEL_EXPORTER_OTLP_ENDPOINT: \"${OTEL_EXPORTER_OTLP_ENDPOINT:-http://grafana:4318}\"",
        f"      LLM_WIKI_AUTH_MODE: \"{auth}\"",
        f"      LLM_WIKI_MCP_AUTH_REQUIRED: \"${{LLM_WIKI_MCP_AUTH_REQUIRED:-{'true' if options.auth_mode != 'disabled' else 'false'}}}\"",
        "      LLM_WIKI_MCP_TOKEN: \"${LLM_WIKI_MCP_TOKEN:-}\"",
        f"      LLM_WIKI_EMBEDDING_MAX_MODEL_LEN: \"${{LLM_WIKI_EMBEDDING_MAX_MODEL_LEN:-{options.embedding_max_model_len if embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET: \"${{LLM_WIKI_EMBEDDING_CROP_TOKEN_BUDGET:-{options.embedding_crop_token_budget if embedding_url else ''}}}\"",
        f"      LLM_WIKI_MULTIMODAL_BACKEND: \"${{LLM_WIKI_MULTIMODAL_BACKEND:-{embedding_backend if embedding_url else 'none'}}}\"",
        f"      LLM_WIKI_EMBEDDING_SERVICE_URL: \"${{LLM_WIKI_EMBEDDING_SERVICE_URL:-{embedding_url if embedding_backend == 'transformers' else ''}}}\"",
        "      LLM_WIKI_EMBEDDING_SERVICE_TOKEN: \"${LLM_WIKI_EMBEDDING_SERVICE_TOKEN:-}\"",
        f"      LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS: \"${{LLM_WIKI_EMBEDDING_SERVICE_ALLOWED_HOSTS:-{'embedding' if embedding_backend == 'transformers' and embedding_url else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_URL: \"${{LLM_WIKI_EMBEDDING_VLLM_URL:-{embedding_url if embedding_backend == 'vllm' else ''}}}\"",
        "      LLM_WIKI_EMBEDDING_VLLM_TOKEN: \"${LLM_WIKI_EMBEDDING_VLLM_TOKEN:-}\"",
        "      LLM_WIKI_EMBEDDING_VLLM_IMAGE: \"${LLM_WIKI_EMBEDDING_VLLM_IMAGE:-}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS: \"${{LLM_WIKI_EMBEDDING_VLLM_ALLOWED_HOSTS:-{'embedding' if embedding_backend == 'vllm' else ''}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER: \"${{LLM_WIKI_EMBEDDING_VLLM_ENFORCE_EAGER:-{'1' if options.embedding_vllm_enforce_eager else '0'}}}\"",
        f"      LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS: \"${{LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS:-{options.embedding_vllm_max_num_seqs}}}\"",
        f"    command: [llm-wiki, --backend, {options.backend}, mcp, --transport, streamable-http, --host, 0.0.0.0, --port, '8780', --path, /mcp]",
        "    ports: ['127.0.0.1:${LLM_WIKI_MCP_PORT:-8780}:8780']",
        "    extra_hosts: ['host.docker.internal:host-gateway']",
        "    volumes: [app_data:/var/lib/llm-wiki]",
    ]
    if embedding_url:
        healthcheck_positions = [index for index, line in enumerate(lines) if line == "        condition: service_healthy"]
        for offset, position in enumerate(healthcheck_positions):
            insert_at = position + 1 + offset * 2
            lines[insert_at:insert_at] = ["      embedding:", "        condition: service_started"]
    if options.mode in {"cpu", "gpu"} and embedding_backend == "transformers":
        lines.extend([
            "  embedding:",
            "    build:",
            "      context: .",
            "      dockerfile: Dockerfile.embedding-service",
            f"      args: {{LLM_WIKI_EMBEDDING_TORCH_BACKEND: {'cu128' if options.mode == 'gpu' else 'cpu'}}}",
            "    environment:",
            "      LLM_WIKI_EMBEDDING_MODEL: Qwen/Qwen3-VL-Embedding-2B",
            f"      LLM_WIKI_EMBEDDING_MODEL_REVISION: {options.model_revision}",
            f"      LLM_WIKI_EMBEDDING_DIMENSION: {options.embedding_dimension}",
            f"      LLM_WIKI_EMBEDDING_DEVICE: {'cuda' if options.mode == 'gpu' else 'cpu'}",
            f"      LLM_WIKI_EMBEDDING_TORCH_BACKEND: {'cu128' if options.mode == 'gpu' else 'cpu'}",
            "      LLM_WIKI_EMBEDDING_TOKEN: \"${LLM_WIKI_EMBEDDING_TOKEN:-}\"",
            "    expose: ['8790']",
            "    healthcheck:",
            '      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8790/readyz\', timeout=3)"]',
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 12",
            "      start_period: 30s",
            "    volumes: [embedding_hf_cache:/var/lib/huggingface]",
        ])
    elif options.mode == "gpu" and embedding_backend == "vllm":
        lines.extend([
            "  embedding:",
            "    image: ${LLM_WIKI_EMBEDDING_VLLM_IMAGE:?Set LLM_WIKI_EMBEDDING_VLLM_IMAGE to a pinned vllm/vllm-openai@sha256 digest}",
            "    command:",
            "      - vllm",
            "      - serve",
            "      - Qwen/Qwen3-VL-Embedding-2B",
            "      - --served-model-name",
            "      - Qwen/Qwen3-VL-Embedding-2B",
            "      - --revision",
            "      - ${LLM_WIKI_MULTIMODAL_MODEL_REVISION:?Set LLM_WIKI_MULTIMODAL_MODEL_REVISION}",
            "      - --runner",
            "      - pooling",
            "      - --convert",
            "      - embed",
            "      - --max-model-len",
            f"      - ${{LLM_WIKI_EMBEDDING_MAX_MODEL_LEN:-{options.embedding_max_model_len}}}",
            "      - --gpu-memory-utilization",
            f"      - ${{LLM_WIKI_EMBEDDING_GPU_MEMORY_UTILIZATION:-{options.embedding_gpu_memory_utilization}}}",
            *(["      - --enforce-eager"] if options.embedding_vllm_enforce_eager else []),
            "      - --max-num-seqs",
            f"      - ${{LLM_WIKI_EMBEDDING_VLLM_MAX_NUM_SEQS:-{options.embedding_vllm_max_num_seqs}}}",
            "      - --api-key",
            "      - ${LLM_WIKI_EMBEDDING_VLLM_TOKEN:?Set LLM_WIKI_EMBEDDING_VLLM_TOKEN}",
            "    environment: [HF_HOME=/root/.cache/huggingface]",
            "    expose: ['8000']",
            "    healthcheck:",
            '      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8000/health\', timeout=3)"]',
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 30",
            "      start_period: 60s",
            "    volumes: [embedding_vllm_hf_cache:/root/.cache/huggingface]",
            "    deploy:",
            "      resources:",
            "        reservations:",
            "          devices:",
            "            - driver: nvidia",
            "              count: all",
            "              capabilities: [gpu]",
        ])
    if options.mode == "gpu" and embedding_backend == "transformers":
        lines.extend([
            "    deploy:",
            "      resources:",
            "        reservations:",
            "          devices:",
            "            - driver: nvidia",
            "              count: all",
            "              capabilities: [gpu]",
        ])
    if options.with_otel:
        lines.extend([
            "  grafana:",
            "    image: grafana/otel-lgtm:latest",
            "    ports: ['127.0.0.1:${GRAFANA_PORT:-3000}:3000']",
        ])
    if options.with_oauth:
        lines.extend([
            "  # Optional OAuth/OIDC provider; configure issuer and client secrets externally.",
            "  oauth:",
            "    image: ghcr.io/navikt/mock-oauth2-server:2.2.1",
            "    profiles: [oauth]",
            "    expose: ['8080']",
        ])
    lines.extend(["volumes:", "  app_data:"])
    if options.backend == "postgres":
        lines.insert(-1, "  postgres_data:")
    if options.mode in {"cpu", "gpu"} and embedding_backend == "transformers":
        lines.append("  embedding_hf_cache:")
    if options.mode == "gpu" and embedding_backend == "vllm":
        lines.append("  embedding_vllm_hf_cache:")
    return "\n".join(lines) + "\n"


def check_compose_text(text: str) -> dict[str, object]:
    """Perform dependency-free structural checks on generated or hand-written YAML."""
    checks: dict[str, str] = {}
    errors: list[str] = []
    required = ("services:", "llm-wiki:")
    for token in required:
        checks[token] = "ok" if token in text else "missing"
        if token not in text:
            errors.append(f"missing required Compose element: {token}")
    if "postgres:" in text:
        for token in ("KOGWISTAR_POSTGRES_DSN", "healthcheck:"):
            checks[token] = "ok" if token in text else "missing"
            if token not in text:
                errors.append(f"missing required PostgreSQL element: {token}")
    if "LLM_WIKI_EMBEDDING_DEVICE: cuda" in text and "driver: nvidia" not in text:
        errors.append("CUDA embedding service requires an NVIDIA device reservation")
    if "Qwen3-VL-Embedding-2B" in text and not any(
        name in text
        for name in ("LLM_WIKI_MULTIMODAL_MODEL_REVISION", "LLM_WIKI_EMBEDDING_MODEL_REVISION")
    ):
        errors.append("Qwen3-VL embedding requires an immutable model revision")
    return {"valid": not errors, "errors": errors, "checks": checks}


def write_compose(path: str | Path, options: ComposeOptions) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_compose(options), encoding="utf-8")
    return destination
