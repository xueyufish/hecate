# Getting Started

Get Hecate running locally in about five minutes, then send your first chat request through the OpenAI-compatible API.

If you want to understand the ideas before installing, read the [Concepts](../concepts/) first. For hands-on, end-to-end examples after the Quickstart, move on to the [Tutorials](../tutorials/).

---

## Choose your path

Different readers want different starting points:

| If you want to... | Start here |
|---|---|
| **Get Hecate running locally** | [Quickstart](quickstart.md) below |
| **Understand Hecate's architecture first** | [Concepts > Overview](../concepts/overview.md) → [Architecture Center](../design/) |
| **Evaluate Hecate for production** | [Positioning](../design/positioning.md) → [Reference Architectures](../design/reference-architectures.md) → [Threat Model](../design/threat-model.md) |
| **Build a multi-agent workflow** | [Quickstart](quickstart.md) → [Tutorial 04: Multi-Agent](../tutorials/04-multi-agent.md) |
| **Integrate Hecate with your existing OpenAI client** | [Quickstart](quickstart.md) → [Tutorial 10: OpenAI SDK Compatibility](../tutorials/10-openai-compatibility.md) |
| **See real-world examples** | [Use Cases: customer support bot / code review / research team](../use-cases/README.md) |
| **Extend Hecate with custom plugins** | [Concepts > Plugins](../concepts/plugins.md) → [Extension SPI Architecture](../design/extension-architecture.md) |
| **Deploy to production** | [Reference Architectures](../design/reference-architectures.md) → [How-to: Deploy to Production](../how-to/deploy-production.md) |
| **Contribute code or docs** | [Contributing Guide](../../CONTRIBUTING.md) → [OpenSpec workflow](https://github.com/xueyufish/hecate/tree/main/openspec) |

---

## System requirements

| Component | Minimum | Recommended |
|---|---|---|
| **CPU** | 2 vCPU | 4+ vCPU |
| **RAM** | 4 GB | 8+ GB |
| **Disk** | 10 GB | 50+ GB SSD |
| **Python** | 3.12+ | 3.13 |
| **Docker** | 24+ | Latest |
| **Docker Compose** | v2 | Latest |

### Operating system

| OS | Status |
|---|---|
| **Linux** (Ubuntu 22.04+, Debian 12+, RHEL 9+) | Fully supported — primary development target |
| **macOS** (13+ Apple Silicon, 13+ Intel) | Fully supported — recommended for development |
| **Windows** (native) | Experimental — use WSL2 |
| **WSL2** | Fully supported — recommended for Windows developers |

### Cloud platforms

Hecate runs on any cloud that supports Docker / Kubernetes:

- AWS (ECS, EKS, Fargate)
- GCP (Cloud Run, GKE)
- Azure (AKS, Container Apps)
- Alibaba Cloud (ACK)
- Huawei Cloud (CCE)
- Self-hosted (bare metal, VMs)

See [Reference Architectures](../design/reference-architectures.md) for deployment patterns.

---

## Guides

- **[Quickstart](quickstart.md)** — clone the repo, start PostgreSQL / Qdrant / MinIO via Docker Compose, run migrations, start the server, and verify with a `curl` request. Covers OpenAI, Anthropic, DeepSeek, Qwen, GLM, and Ollama providers.

---

## Next steps

After the Quickstart, continue with:

### For users

- **[Build Your First Agent](../tutorials/01-first-agent.md)** — go deeper into tool binding, sessions, and the CLI workflow.
- **[Use Cases](../use-cases/README.md)** — customer support bot, code review agent, research team.
- **[OpenAI SDK Compatibility](../tutorials/10-openai-compatibility.md)** — use Hecate as a drop-in OpenAI replacement.

### For operators

- **[Deploy to Production](../how-to/deploy-production.md)** — Docker Compose, blue-green, Kubernetes.
- **[Monitor with OpenTelemetry](../how-to/monitor-opentelemetry.md)** — tracing, metrics, structured logging.
- **[Configure Budget and Cost Tracking](../how-to/configure-budget.md)** — per-workspace budgets, degradation profiles.
- **[Set Up Webhooks](../how-to/set-up-webhooks.md)** — receive events from GitHub / Slack.

### For architects and evaluators

- **[Architecture Overview](../concepts/overview.md)** — five-layer architecture, ten modules, key concepts.
- **[Positioning](../design/positioning.md)** — where Hecate fits among Dify, LangGraph, Agentforce, etc.
- **[Roadmap](../features/roadmap.md)** — phases from Alpha to 1.0 GA and beyond.
- **[Threat Model](../design/threat-model.md)** — STRIDE analysis of Hecate's security posture.
- **[Reference Architectures](../design/reference-architectures.md)** — deployment patterns and sizing.

### For developers

- **[Plugins](../concepts/plugins.md)** — extend Hecate with custom code.
- **[Visual Canvas](../tutorials/11-visual-canvas.md)** — drag-and-drop workflow design.
- **[A2A Protocol](../concepts/a2a-protocol.md)** — cross-framework agent communication.
- **[Extension Points](../reference/extension-points.md)** — the 26 engine extension interfaces + 8 plugin SPI types.

### For contributors

- **[Contributing Guide](../../CONTRIBUTING.md)** — how to contribute code and docs.
- **[About > Inspired by](../about/inspired-by.md)** — the projects Hecate builds on.
- **[CHANGELOG.md](../../CHANGELOG.md)** — what's been shipped.
- **GitHub Issues** — coming soon; in the meantime, post questions in the project README or release notes.

---

## Help and support

- **GitHub Issues** — coming soon; check release notes and the changelog for now.
- **Documentation** — start at [Architecture Center](../design/README.md)

---

## Current status

**Hecate is in alpha (0.1.x).** APIs and config schemas may change before 1.0. Pin your version (`hecate==0.1.x`) in production deployments.

### Recent releases

- **0.1.x** — current alpha (see [GitHub Releases](https://github.com/xueyufish/hecate/releases))
- **0.2.x** — Beta (planned for 2026 Q4) — see [Migrating from 0.1.x to 0.2.x](../migrations/v0.1-to-v0.2.md)
- **1.0.0** — GA (planned for 2027 Q2)