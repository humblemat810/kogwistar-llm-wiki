FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KOGWISTAR_DATA_DIR=/var/lib/llm-wiki

WORKDIR /app

# The local Kogwistar checkout contains a Rust-backed extension. Build all
# sibling packages from this source tree so the container uses one coherent
# cross-repository version set.
RUN apt-get update \
       && apt-get install --no-install-recommends -y ca-certificates curl gcc libc6-dev \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:${PATH}"

COPY pyproject.toml README.md .env.example ./
COPY kogwistar ./kogwistar
COPY kg-doc-parser ./kg-doc-parser
COPY kogwistar-obsidian-sink ./kogwistar-obsidian-sink
COPY src ./src

   RUN python -m pip install --upgrade pip \
       && python -m pip install --no-cache-dir -e "./kogwistar[full]" \
       && python -m pip install --no-cache-dir -e ./kg-doc-parser \
       && python -m pip install --no-cache-dir -e ./kogwistar-obsidian-sink \
       && python -m pip install --no-cache-dir fastmcp "chromadb>=0.6" \
          opentelemetry-api "opentelemetry-sdk>=1.25" \
          "opentelemetry-exporter-otlp-proto-http>=1.25" \
       && python -m pip install --no-cache-dir --no-deps -e .

RUN useradd --create-home --uid 10001 llmwiki \
    && mkdir -p /var/lib/llm-wiki \
    && chown -R llmwiki:llmwiki /app /var/lib/llm-wiki
USER llmwiki

EXPOSE 8765 8780

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3)"

CMD ["llm-wiki", "--help"]
