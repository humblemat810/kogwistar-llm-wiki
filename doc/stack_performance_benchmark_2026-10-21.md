# Cross-Stack Performance Benchmark (2026-10-21)

## Scope

This pass broadens performance evidence beyond document parsing to Kogwistar
graph operations, LLM-Wiki materialization, and the MCP adapter. It combines
existing reproducible harnesses with small local probes. Mock and in-memory
results are labeled explicitly; they are not production-backend or model
throughput claims.

## Results

### Kogwistar persistent SQLite capability facade

Ran `kogwistar/scripts/rust_port_persistent_benchmark.py` on CPython 3.13.3,
with 30 items, 5 read cycles, and 3 samples. It compares shipped Python and Rust
facades, including their serialization and SQLite boundaries.

| Operation | Python median | Rust median | Rust/Python latency | Rust/Python throughput |
| --- | ---: | ---: | ---: | ---: |
| Event append, 30 events | 562 ms | 1,191 ms | 2.15x | 0.47x |
| Event replay, 150 reads | 140 ms | 175 ms | 1.22x | 0.80x |
| Projection list, 150 reads | 62 ms | 173 ms | 2.74x | 0.36x |
| Projection replace, 30 writes | 568 ms | 1,156 ms | 2.03x | 0.49x |

Both implementations produced the same state digest. The benchmark gate failed
for Rust latency and throughput; peak RSS passed. For these operations, another
Rust cutover is not supported by the measurements. The raw report is in the
ignored scratch directory `.cache/perf-bench/persistent-sqlite` in the vendored
Kogwistar checkout. This rerun used fresh processes, but did not flush OS or
SQLite page caches; it is not a cold-cache comparison.

### Kogwistar hypergraph traversal

Built an in-memory graph with 100 nodes, 50 four-participant edges (200 endpoint
participations), then measured 25 warm calls after 3 warmups.

| Query | Median | p95 | Backend |
| --- | ---: | ---: | --- |
| `neighbors` | 11.29 ms | 11.98 ms | Kogwistar in-memory |
| `k_hop`, 2 hops | 11.63 ms | 12.85 ms | Kogwistar in-memory |

This is a useful baseline, not a scaling result: only one graph size and one
degree distribution were measured. It does not yet compare SQLite/Postgres or
ACL/as-of filtering, and graph construction time is excluded.

### Kogwistar in-memory Rust shadow-read path

Ran `kogwistar/scripts/rust_port_phase2_benchmark.py` on CPython 3.13.3 with 32
dataset items, two warmups, three fresh-process samples, and ten measured cycles
per sample. Each cycle covers graph records, vector query, event replay, and
named-projection listing. Output digests matched; no external DB queries or
authoritative writes occurred.

| Path | p95 cycle latency | Throughput | Peak RSS |
| --- | ---: | ---: | ---: |
| Python | 2.31 ms | 1,800 ops/s | 81.9 MB |
| Rust shadow | 5.34 ms | 822 ops/s | 82.9 MB |

The Rust path was 2.31x slower by p95 latency and delivered 0.46x throughput.
This is not evidence that Rust graph kernels are slower: the measured shadow
path rebuilds an isolated store from a JSON snapshot for each cycle. Its purpose
is safety parity, not persistent request serving.

### LLM-Wiki deferred indexing / embedding

Ran `scripts/benchmark_conversation_two_stage.py` with 12 entities and a
deterministic embedding provider that waits 10 ms per call. The backend is
in-memory; no real embedding model or database was involved.

| Path | Admission | Promotion | Total | Provider calls | Promoted |
| --- | ---: | ---: | ---: | ---: | ---: |
| Single-stage | 3,982 ms | N/A | 3,982 ms | 96 | N/A |
| Two-stage, batched | 762 ms | 523 ms | 1,285 ms | 48 | 12 / 12 |

Two-stage admission was 5.23x faster; admission plus promotion was 3.10x faster
for this fixture. This validates that deferred batched work is not free but can
reduce total provider calls and elapsed time in this harness. It does not
predict production behavior until repeated with the deployed embedding service
and Postgres/Chroma backend.

### MCP result encoding

Measured the synchronous dispatch and JSON formatting path using a fake gateway
returning graph-shaped node/edge payloads. This isolates adapter serialization;
it does not include MCP transport, authentication middleware, actual graph
queries, or concurrent requests.

| Payload | Compact JSON | Pretty `TextContent` JSON | Pretty dump median | Compact dump median |
| --- | ---: | ---: | ---: | ---: |
| 10 nodes / 5 edges | 2,931 chars | 3,816 chars | 0.047 ms | 0.042 ms |
| 100 nodes / 50 edges | 29,171 chars | 37,886 chars | 0.360 ms | 0.322 ms |
| 1,000 nodes / 500 edges | 292,471 chars | 379,486 chars | 4.413 ms | 3.574 ms |

The pretty text copy is about 30% larger than compact JSON for this payload,
and took about 1.23x as long to encode at the largest size. The MCP response
also carries the same result as structured content, so large calls can transmit
both structured data and a textual JSON representation. This is a real adapter
cost but a modest CPU cost in this isolated probe; do not change the contract
without checking client behavior and transport-level impact.

An attempted async concurrency probe could not initialize the Windows asyncio
event loop in this sandbox: it blocked in the Proactor loop's local socket-pair
setup before the request ran. Therefore no concurrency or transport-throughput
claim is made here. This is an environment limitation, not evidence of an MCP
server defect.

## Existing Related Evidence

The parser algorithm and validation measurements, plus text and multimodal
embedding measurements, remain documented separately in:

- `kg-doc-parser/doc/benchmarks/parser_hotpath_assessment.md`
- `doc/text_embedding_benchmark.md`
- `doc/multimodal_retrieval_experiment.md`

Those results are not combined numerically with this report because their
workloads, hardware, runtimes, and measurement methods differ.

## Conclusions And Next Measurements

1. Keep the current Python implementations for the measured persistent SQLite
   capability operations; the Rust facade is currently slower.
2. Measure hypergraph traversal at multiple graph sizes and degree profiles,
   on in-memory and production storage backends, including endpoint-query
   counts and ACL/as-of filters.
3. Re-run two-stage indexing against a real embedding endpoint and Postgres or
   Chroma, recording stage latency, provider batch sizes, retries, and index
   backlog.
4. Run MCP HTTP/stdio concurrency tests outside the restricted Windows socket
   environment. Record tool dispatch, graph service, auth, and response-encoding
   times separately at 1, 4, and 8 concurrent clients.
5. Consider compact MCP text JSON only after confirming client compatibility;
   the measured saving is mostly response bytes, not a large CPU win.

### Rust And Python PostgreSQL Comparison

The benchmark runner was executed against a disposable local Postgres 16
Docker Desktop container on 2026-10-21, using 30 items per workload, five
samples, precomputed JSON, no embedding model, and warm local database state.
After capturing a baseline, a persistent, explicitly scoped Rust store context
was implemented and measured with the same runner and host. Python used its
persistent SQLAlchemy pool in both runs. These compare end-to-end facade paths,
not language runtimes in isolation.

| Workload (30 items) | Python baseline | Rust before | Rust after | Rust change | Python after | Rust/Python after latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Individual event append | 472.9 ms | 1,156.1 ms | 612.2 ms | 47.0% faster | 463.1 ms | 1.32x |
| Grouped event append | 286.5 ms | 402.8 ms | 372.6 ms | 7.5% faster | 285.3 ms | 1.31x |
| Atomic event + projection per record | 757.9 ms | 1,305.7 ms | 728.2 ms | 44.2% faster | 730.8 ms | 1.00x |
| Grouped conversation records | 445.1 ms | 514.6 ms | 477.6 ms | 7.2% faster | 443.4 ms | 1.08x |
| Event replay batch | 8.1 ms | 21.5 ms | 5.0 ms | 76.8% faster | 7.9 ms | 0.63x |
| Projection listing | 8.1 ms | 22.9 ms | 4.5 ms | 80.2% faster | 7.8 ms | 0.58x |

Persistent Rust context improves Rust materially, especially replay/list reads.
After the change, Rust reads are about 1.7x faster than Python in this small
local case; event append remains slower in Rust; per-record atomic writes are
near parity but still slightly slower. The grouping cases alter commit
boundaries, so they are sensitivity probes, not recommendations for user
conversation durability. The run excludes embeddings, remote network latency,
production-scale data, and cold-cache behavior.

The Rust prototype gives each explicit `RustPostgresSession` a worker thread,
Tokio runtime, and persistent Postgres pool; it is bounded to 64 contexts per
process, supports explicit close, and leaves contextless legacy calls on the
old path. A first after-run deadlocked during transaction dispatch; database
activity showed an idle open transaction. That run was discarded, the receiver
mutex lifetime corrected, and the full benchmark then passed. Do not use the
discarded run as performance or correctness evidence.

The runner creates uniquely named `gke_perf_*` schemas, verifies read counts,
and removes only those schemas. Focused verification passed: Rust formatting,
`cargo check -p kogwistar-python`, the Rust worker cache lifecycle unit test,
and the Python Rust-Postgres facade tests (6 passed). The successful live
Postgres benchmark is the integration smoke evidence; it is not a concurrency
stress test.
