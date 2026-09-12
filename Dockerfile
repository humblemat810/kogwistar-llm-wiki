# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

FROM ${PYTHON_IMAGE} AS app-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY docker/container-constraints.txt ./docker/container-constraints.txt

# This toolchain is confined to the builder. BuildKit caches make Rust and
# Python dependency downloads reusable without copying them to the runtime.
RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl gcc libc6-dev \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/root/.cargo/bin:/opt/venv/bin:$PATH

RUN python -m venv /opt/venv \
    && python -m pip install --upgrade pip setuptools wheel \
       "maturin>=1.8,<2" "poetry-core>=2.0,<3"

# Kogwistar owns the Rust extension and is intentionally the first source
# layer. Changes to downstream packages do not rebuild this layer.
COPY kogwistar ./kogwistar
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cargo/registry \
    --mount=type=cache,target=/root/.cargo/git \
    --mount=type=cache,target=/app/kogwistar/rust/target \
    python -m pip install --no-build-isolation \
    --constraint /app/docker/container-constraints.txt "./kogwistar[full]"

# The sink is small and depends on Kogwistar, so install it before the more
# frequently changing parser and product application layers.
COPY kogwistar-obsidian-sink ./kogwistar-obsidian-sink
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-build-isolation --no-deps \
    --constraint /app/docker/container-constraints.txt ./kogwistar-obsidian-sink

COPY kg-doc-parser ./kg-doc-parser
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-build-isolation \
    --constraint /app/docker/container-constraints.txt ./kg-doc-parser

# These external runtime dependencies are installed before root source so
# application-only changes do not rebuild the expensive dependency layer.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --constraint /app/docker/container-constraints.txt \
       "opentelemetry-api>=1.25" \
       "opentelemetry-sdk>=1.25" \
       "opentelemetry-exporter-otlp-proto-http>=1.25" \
       "chromadb>=0.6" \
       "Pillow>=10"

COPY pyproject.toml README.md .env.example ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-build-isolation --no-deps \
    --constraint /app/docker/container-constraints.txt ".[agent]"

# Do not carry packaging/build tools into the runtime artifact. pip remains
# available for diagnostics, but no compiler, Rust toolchain, or build backend
# is copied into the final stage.
RUN python -m pip uninstall -y maturin poetry-core wheel setuptools

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KOGWISTAR_DATA_DIR=/var/lib/llm-wiki \
    KG_DOC_PARSER_JOBLIB_CACHE_DIR=/var/lib/llm-wiki/parser-cache \
    GKE_JOBLIB_CACHE_DIR=/var/lib/llm-wiki/kogwistar-cache \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app
COPY --from=app-builder --chown=10001:10001 /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 llmwiki \
    && mkdir -p /app/.cache /app/.version_chain /app/.llm_cache /app/.kg_extract \
       /var/lib/llm-wiki/logs /var/lib/llm-wiki/parser-cache \
       /var/lib/llm-wiki/kogwistar-cache \
    && ln -s /var/lib/llm-wiki/logs /app/logs \
    && touch /app/application_logs.db \
    && chown 10001:10001 /app/.cache /app/.version_chain /app/.llm_cache \
       /app/.kg_extract /app/application_logs.db /var/lib/llm-wiki/logs \
       /var/lib/llm-wiki/parser-cache /var/lib/llm-wiki/kogwistar-cache
USER llmwiki

# Catch namespace-package/split-install failures before the container enters
# a restart loop. This also verifies the pinned FastMCP server contract.
RUN python -c "from fastmcp import FastMCP; from fastmcp.server.auth import StaticTokenVerifier, require_scopes; FastMCP('build-import-check'); print('FastMCP import check passed')"

EXPOSE 8765 8780

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"

ENTRYPOINT ["python", "-m", "kogwistar_llm_wiki.container_entrypoint"]
CMD ["llm-wiki", "--help"]
