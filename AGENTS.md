# AGENTS.md — Hecate

## What this repo is

Hecate is an **enterprise-grade Agent platform** in early design/research phase. This repo contains **documentation only** — no source code, no build system, no tests. It serves as the working knowledge base for architecture decisions and feature planning.

## Key files (read these first on any new session)

| File | Purpose |
|------|---------|
| `docs/features/feature-catalog.md` | **Authoritative feature list** — 156 features across 14 capability domains, prioritized P1→P4 |
| `docs/research/research-tracker.md` | **Single source of truth for research progress** — 5 phases, 80 research items, status of every step. Read this to recover context after session loss |
| `docs/research/reports/00-architecture-decisions.md` | 5 core architecture decisions (D1 decided: self-built engine; D2–D5 pending) |
| `docs/research/reports/01-execution-engine-decision.md` | Full 6-round discussion record for Decision 1 |

## Directory structure

```
docs/
├── features/
│   └── feature-catalog.md          # 156 features, P1-P4
├── research/
│   ├── research-tracker.md         # Progress tracker (source of truth)
│   ├── research-checklist.md       # Original 80-item checklist
│   ├── notes/                      # 25 project analysis notes + 6 comparison groups
│   └── reports/                    # 7 synthesis reports (00-05)
└── refs/
    ├── pdf/                        # 9 AgentArts reference PDFs
    └── md/                         # Reference markdown docs
```

## Conventions

- Feature IDs use the pattern `X.Y.Z` (e.g., `1.3.1`, `9.4a`). New features appended to existing IDs use letter suffixes (`9.4a`, `9.4b`).
- Research notes follow a consistent format per project: overview → architecture → key findings → conclusion.
- Reports are numbered `00`–`05` with descriptive suffixes. `00` is the master decision summary.
- The research tracker's phase-level status is the authority — always update it when completing research steps.

## Critical context for design discussions

- **Decision 1 (settled)**: Self-built execution engine borrowing LangGraph design patterns (Channel, Checkpoint, Pregel, interrupt, subgraph), ~5000 LOC, NOT using LangGraph code.
- **Decisions 2–5 (pending)**: Multi-Agent orchestration, frontend canvas tech, unified data model, target user & GTM.
- **Product positioning**: Open-source, self-hosted, model-agnostic, MCP-first enterprise Agent platform. "拒绝供应商锁定" — the key differentiator vs commercial Chinese platforms.
- **Reference projects**: Analyzed 26 projects. Primary competitive benchmarks are AgentArts (Huawei), Coze (ByteDance), Dify, and OpenClaw. `relay-agent/` (sibling repo at `../relay-agent/`) provides additional reference.
- **Stack decisions**: PostgreSQL + Qdrant persistence, LiteLLM model routing, LangFuse observability, React Flow canvas, Python runtime + TypeScript API/UI.
- **P1 gaps still unresearched**: LLM Guard, OWASP LLM Top 10, BGE embedding models.

## What to do vs. what not to do

- **Do** update `research-tracker.md` whenever research items change status.
- **Do** add AgentArts as a reference project when new features originate from it.
- **Do** maintain the P1→P4 priority ordering and update counts in the statistics table when features change.
- **Don't** create source code or implementation files — this repo is design/documentation only.
- **Don't** renumber existing feature IDs — use letter suffixes for additions.
- **Don't** commit PDF files or large binary assets (they already exist in `refs/pdf/`).
