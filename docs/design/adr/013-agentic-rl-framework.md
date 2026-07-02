# ADR-013: Agentic RL Framework for Agent Self-Optimization

> **Status**: Proposed
> **Date**: 2026-06-30

## Context

Enterprise AI agents deployed in production accumulate valuable operational data: which tasks succeed, which fail, which tools work best for which scenarios, and how users respond to agent outputs. Currently this data is logged but not used to systematically improve agent performance. Competitors like Huawei AgentArts have implemented "data flywheel" systems where production traces feed back into model training, enabling agents to improve autonomously over time.

The question is whether Hecate should implement an Agentic RL framework that closes the loop between production operation and model/prompt optimization.

## Decision

Implement an **Agentic RL Framework** with four core components:

1. **Data Flywheel**: Production traces → labeling → training data
2. **Reward Mechanisms**: Rule-based, generative, and credit assignment rewards
3. **Interaction Environments**: Runtime cloning for safe RL training
4. **Optimization Algorithms**: Verifiable reward RL, multi-turn optimization, preference alignment

## Architecture

```
Production Agent
       │
       ▼
┌──────────────────────────────────────┐
│  Trace Collection (via EventStore)   │
│  • Conversation traces               │
│  • Tool call traces                  │
│  • User feedback (thumbs up/down)    │
│  • Execution outcomes                │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  Trace Annotation & Labeling         │
│  • Auto-labeling (LLM-as-Judge)     │
│  • Human annotation                  │
│  • Multi-dimensional tags            │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  Training Data Construction          │
│  • Prompt optimization datasets      │
│  • RL training datasets              │
│  • Evaluation test sets              │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  Optimization Engine                 │
│  • Prompt self-optimization (ACE)    │
│  • Model fine-tuning (optional)      │
│  • Policy evolution                  │
└──────────────┬───────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│  Validation & Deployment             │
│  • A/B testing against baseline      │
│  • Canary release                    │
│  • Rollback on degradation           │
└──────────────────────────────────────┘
```

## Rationale

- **Self-improving agents** are the key differentiator in enterprise AI
- **Data flywheel** leverages existing observability infrastructure (EventStore, Trace, Audit)
- **Prompt optimization** is the lowest-risk, highest-impact optimization (vs model fine-tuning)
- **A/B testing** ensures safe deployment of optimized agents

## Consequences

- AgentOps needs new Trace annotation capabilities (6.18)
- Evaluation engine needs integration with training data construction
- Prompt management needs versioning for optimized prompts
- Security: RL training must not leak PII from production traces
