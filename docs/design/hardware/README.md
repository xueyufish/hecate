# Hardware (L2/L3 Architecture Diagrams)

Level-2 subsystem diagrams and Level-3 detail diagrams. Each `.drawio` file has a PNG export in [`docs/design/images/`](../images/) that is embedded in the docs. These diagrams use a status legend so the reader can see, at a glance, what is implemented today versus planned.

## Legend

| Marker | Meaning |
|--------|---------|
| `✅ Implemented` | Shipped and running in the codebase |
| `📋 Planned` | Design intent, not yet implemented |
| `⚠️` / red fill | Gap, caveat, or partial implementation |

Green fill = implemented; yellow dashed = planned; red = gap/caveat.

## Diagram index

| File | PNG | Covers |
|------|-----|--------|
| [`hardware_architecture.drawio`](hardware_architecture.drawio) | [`hardware_architecture.png`](../images/hardware_architecture.png) | The platform map: all subsystems (engine, services, API, models, core) and the extension-point surface. |
| [`agent-engine-l2.drawio`](agent-engine-l2.drawio) | [`agent-engine-l2.png`](../images/agent-engine-l2.png) | Engine internals: Pregel runtime, channels, workers, checkpoints, event store, guardrail hooks, collaboration patterns. |
| [`access-channel-l2.drawio`](access-channel-l2.drawio) | [`access-channel-l2.png`](../images/access-channel-l2.png) | Access surfaces: OpenAI-compatible API, management API, dashboard, IM channels (Feishu/Slack), embeddable web widget. |
| [`agent-studio-l2.drawio`](agent-studio-l2.drawio) | [`agent-studio-l2.png`](../images/agent-studio-l2.png) | Visual development environment: canvas, agent configurator, prompt management, testing tools. |
| [`tool-platform-l2.drawio`](tool-platform-l2.drawio) | [`tool-platform-l2.png`](../images/tool-platform-l2.png) | Tool platform: MCP integration, plugin system, tool operations, security, observability. |
| [`model-hub-l2.drawio`](model-hub-l2.drawio) | [`model-hub-l2.png`](../images/model-hub-l2.png) | Model hub: LLM integration (LiteLLM), model catalog, routing, lifecycle, governance, monitoring. |
| [`knowledge-memory-l2.drawio`](knowledge-memory-l2.drawio) | [`knowledge-memory-l2.png`](../images/knowledge-memory-l2.png) | Knowledge management and the four-level memory architecture. |
| [`rag-pipeline-l3.drawio`](rag-pipeline-l3.drawio) | [`rag-pipeline-l3.png`](../images/rag-pipeline-l3.png) | RAG pipeline detail (L3): ingestion, chunking, embedding, retrieval, citation. |
| [`security-l2.drawio`](security-l2.drawio) | [`security-l2.png`](../images/security-l2.png) | Security architecture: guardrail hooks, auth, audit, sandboxing, OWASP coverage. |
| [`security-l3.drawio`](security-l3.drawio) | [`security-l3.png`](../images/security-l3.png) | Security L3 internal flow: LLM execution path with guardrail hook interception. |
| [`ops-center-l2.drawio`](ops-center-l2.drawio) | [`ops-center-l2.png`](../images/ops-center-l2.png) | Ops center: observability, monitoring, conversation analytics, testing, budget governance. |
| [`enterprise-foundation-l2.drawio`](enterprise-foundation-l2.drawio) | [`enterprise-foundation-l2.png`](../images/enterprise-foundation-l2.png) | Enterprise foundation: multi-tenancy, SSO, SCIM, compliance, deployment, secrets. |
| [`ecosystem-l2.drawio`](ecosystem-l2.drawio) | [`ecosystem-l2.png`](../images/ecosystem-l2.png) | Ecosystem: MCP/A2A protocols, plugin marketplace, agent discovery, partner integration. |

## Editing and re-exporting

Edit the `.drawio` files in the draw.io desktop app or the VSCode draw.io extension, then re-export the PNG with the draw.io CLI (installed at `/usr/local/bin/drawio`):

```bash
drawio -x -f png -o docs/design/images/<name>.png docs/design/hardware/<name>.drawio
```

Keep the legend and status markers accurate when you edit — they are the primary signal readers use to distinguish shipped features from planned ones.