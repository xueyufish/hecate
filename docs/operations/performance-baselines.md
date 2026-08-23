# Performance Baselines

Reference performance numbers for Hecate deployments. **This document is currently a methodology + estimate template** — actual measured numbers will be added as they're collected. We do not fabricate benchmarks.

> **Status**: Methodology + estimates. Real numbers are being collected. See [GitHub issue for benchmark tracking](https://github.com/xueyufish/hecate/issues) (search for `benchmark`).

For deployment sizing estimates, see [Reference Architectures](../design/reference-architectures.md#sizing-calculator). For tuning tips, see [Operations Runbook](../operations/README.md) (when it exists).

---

## How to use this document

This document serves three audiences:

1. **Evaluators comparing platforms** — use the estimates as order-of-magnitude sanity checks
2. **Operators sizing deployments** — use the methodology to run your own benchmarks
3. **Engineers optimizing Hecate** — find the bottleneck component before tuning

---

## Test methodology

Every number in this document is measured (or estimable) using the **same methodology**:

### Hardware baseline

| Component | Spec |
|---|---|
| CPU | 4 vCPU (Intel Xeon or AMD EPYC, x86_64) |
| RAM | 16 GB |
| Disk | 100 GB NVMe SSD |
| Network | 1 Gbps internal, 100 Mbps internet |
| OS | Linux kernel 5.15+ |

### Software baseline

| Component | Version |
|---|---|
| Hecate | 0.2.x |
| PostgreSQL | 16 |
| Qdrant | 1.7+ |
| Redis | 7+ |
| Python | 3.12 |

### Workload pattern

A "standard request" consists of:

```
1. Chat completion with a single agent
2. Agent has the 11 built-in tools (web_search, read_file, write_file, list_files, execute_code + 6 browser_* tools)
3. Agent makes exactly 2 LLM calls (initial + tool execution)
4. Average conversation: 10 messages total
5. No knowledge base (RAG disabled)
6. No MCP servers attached
7. Input tokens: ~500
8. Output tokens: ~300
```

### Measurement method

```bash
# Install hey or wrk for HTTP load generation
# https://github.com/wg/wrk

# Example: 30 concurrent sessions, 1000 requests, 60 second timeout
hey -c 30 -n 1000 -z 60s \
  -H "Authorization: Bearer $HECATE_API_KEYS" \
  -H "Content-Type: application/json" \
  -d @standard-request.json \
  http://localhost:8000/v1/chat/completions
```

Metrics collected:

| Metric | Tool |
|---|---|
| p50 / p95 / p99 latency | `hey` output |
| Requests/sec | `hey` output |
| Error rate | `hey` output + Hecate audit |
| CPU usage | `mpstat` / `pidstat` |
| Memory usage | `pidstat -r` |
| Postgres query latency | `pg_stat_statements` |
| LLM provider latency | Hecate traces (`hecate_llm_duration_seconds`) |

---

## Estimates (not measurements)

These are **estimates based on architecture**, not actual measurements. Treat them as order-of-magnitude sanity checks.

### Single-node baseline (Pattern 1)

**Setup**: 1 Hecate pod + 1 Postgres + 1 Qdrant + 1 Redis + 1 MinIO. LLM provider: OpenAI GPT-4o-mini.

| Metric | Estimate | Notes |
|---|---|---|
| **Latency p50** | ~800ms | LLM call dominates (~600ms + ~200ms overhead) |
| **Latency p95** | ~1.5s | Tail latency on LLM + DB |
| **Latency p99** | ~3s | Cold cache + slow LLM |
| **Throughput** | ~10 req/s | Limited by single Postgres connection pool (default 20) |
| **Concurrent sessions** | ~30 | Limited by Hecate pod RAM |
| **Token throughput** | ~8K tokens/s | Bound by LLM provider rate limit (OpenAI tier) |
| **Error rate** | <0.1% | Excluding LLM provider failures |

**Caveats**: these assume warm caches, no MCP, no RAG, OpenAI provider. Real numbers will differ.

### Multi-replica (Pattern 3, 3 pods)

**Setup**: 3 Hecate pods + 1 Postgres primary + 1 replica + Qdrant cluster + Redis Cluster + S3.

| Metric | Estimate | Notes |
|---|---|---|
| **Latency p50** | ~700ms | Less queue time, same LLM dominance |
| **Latency p95** | ~1.2s | Better than single-node due to load distribution |
| **Throughput** | ~25 req/s | 3x pods, ~80% efficiency due to DB bottleneck |
| **Concurrent sessions** | ~90 | 3x pods, same per-pod limit |
| **Error rate** | <0.1% | Same as single-node |

### LLM-dominated latency breakdown

For a single chat completion:

```
Total latency ~800ms
├── Client → Hecate: ~5ms
├── Auth + RBAC check: ~5ms
├── Load agent config: ~10ms (DB)
├── Session state lookup: ~2ms (Redis, cached) or ~20ms (cold)
├── Tool binding resolution: ~5ms
├── LLM call (OpenAI): ~600ms ← 75% of total
├── Tool execution (if any): ~50-200ms
├── Context engine assembly: ~20ms
├── Checkpoint save: ~30ms (async, doesn't block response)
└── Response serialization: ~5ms
```

**Key insight**: LLM provider latency dominates. Optimizing anything else (caching, DB indexes) yields marginal gains unless you're hitting your LLM provider's rate limit.

---

## Component bottlenecks

Each component has a different bottleneck. **Find yours before optimizing.**

### Hecate app

- **Bottleneck**: LLM provider rate limit (NOT CPU)
- **Signal**: `hecate_llm_requests_total{status="429"}` rate
- **Mitigation**: request multiple LLM providers, use cheaper model tier

### PostgreSQL

- **Bottleneck**: Write throughput / connection count
- **Signal**: `pg_stat_activity.waiting > 5` (active lock waits)
- **Mitigation**: increase `POSTGRES_POOL_SIZE`, add read replicas for read-heavy workloads

### Qdrant

- **Bottleneck**: RAM (vector indices are memory-resident)
- **Signal**: OOM errors or search latency p95 > 100ms
- **Mitigation**: scale vertically first, then cluster mode

### Redis

- **Bottleneck**: Memory
- **Signal**: `used_memory > maxmemory` (evictions)
- **Mitigation**: increase memory; switch to Cluster mode

### MinIO

- **Bottleneck**: Disk I/O
- **Signal**: high `s3_requests_5xx` rate
- **Mitigation**: faster disks (NVMe), distributed mode

---

## Measurement cookbook

### Scenario 1: "How fast is Hecate?"

```bash
# Terminal 1: Start load
hey -c 10 -n 100 -m POST \
  -H "Authorization: Bearer $HECATE_API_KEYS" \
  -H "Content-Type: application/json" \
  -d @standard-request.json \
  http://localhost:8000/v1/chat/completions

# Terminal 2: Watch Hecate metrics
watch -n 1 'curl -s http://localhost:8000/metrics | grep hecate_request_duration'

# Expected output:
# p50 ~800ms, p95 ~1500ms, p99 ~3000ms
# requests/s ~10, error rate < 0.1%
```

### Scenario 2: "What happens at 100 concurrent?"

```bash
hey -c 100 -n 5000 -z 120s \
  -H "Authorization: Bearer $HECATE_API_KEYS" \
  -d @standard-request.json \
  http://localhost:8000/v1/chat/completions
```

Watch for:
- Latency p99 climbing above 10s (suggests queue/timeout)
- Error rate > 1% (suggests saturation)
- Postgres connection count maxing out

### Scenario 3: "How does multi-agent perform?"

Use a workflow with 3 sequential agents:

```yaml
# workflow.json
name: research-team
nodes:
  - planner
  - researcher
  - writer
edges:
  - planner → researcher
  - researcher → writer
```

Expected latency: **3× single-agent** (LLM calls multiply).

---

## Optimization checklist

When performance is insufficient:

| Optimization | Effort | Expected gain |
|---|---|---|
| Switch to cheaper LLM (gpt-4o-mini vs gpt-4o) | Trivial | 30-60% latency reduction |
| Enable response streaming (SSE) | Trivial | TTFT drops to ~200ms |
| Add Redis for session state | Low | 10-20% p95 reduction (avoid cold reads) |
| Add Postgres read replica | Medium | 30-50% read query latency reduction |
| Increase Hecate pod replicas | Medium | Linear throughput increase (until DB bottleneck) |
| Switch to managed Postgres | High | Removes operational bottleneck |
| Switch Qdrant to cluster mode | High | 10x vector search throughput |
| Enable context offloading | Low | Reduces token cost for long conversations |

**Rule**: optimize the slowest component first. Measure before tuning.

---

## Real measurements (placeholder)

This section will be populated as the project collects real measurements.

### Test environment A: developer laptop (4 vCPU, 16 GB RAM)

| Metric | Measurement | Date | Commit |
|---|---|---|---|
| Latency p50 | TBD | TBD | TBD |
| Latency p95 | TBD | TBD | TBD |
| Throughput | TBD | TBD | TBD |

### Test environment B: production-shape single-node (8 vCPU, 32 GB RAM)

| Metric | Measurement | Date | Commit |
|---|---|---|---|
| Latency p50 | TBD | TBD | TBD |
| Latency p95 | TBD | TBD | TBD |
| Throughput | TBD | TBD | TBD |

### Test environment C: 3-replica production (32 vCPU, 96 GB RAM)

| Metric | Measurement | Date | Commit |
|---|---|---|---|
| Latency p50 | TBD | TBD | TBD |
| Latency p95 | TBD | TBD | TBD |
| Throughput | TBD | TBD | TBD |

---

## Contributing measurements

If you run benchmarks in your environment, please contribute back:

1. Use the methodology above (hardware + software baseline + workload)
2. Run 3 trials; report median and p95
3. Open a PR to update the "Real measurements" section
4. Include: hardware, software, workload, results, graph screenshot if applicable

We aggregate community measurements in this document so future users can see real-world numbers across diverse deployments.

---

## Related documents

- [Reference Architectures](../design/reference-architectures.md#sizing-calculator) — sizing formulas
- [Observability Architecture](../design/observability-architecture.md) — what to measure in production
- [Health Checks](../operations/health-checks.md) — probe configuration
-  — performance improvements in upcoming phases