# Design

## Context

`AGENTS.md` currently makes `spec-driven` mandatory. The installed OpenSpec CLI supports repository-local schemas, while `openspec-propose` currently selects a non-default schema only when the user names it.

## Goals / Non-Goals

**Goals:** Preserve full product-change planning while making low-risk changes require fewer artifacts. Make the risk decision available both to contributors and to the proposal skill. Preserve focused verification and CI for both tiers.

**Non-Goals:** Change CI gates, automatically infer affected tests, skip tests, remove OpenSpec from changes, or alter product behavior.

## Decisions

- Keep `spec-driven` as the project default. The lightweight schema is opt-in at change creation, selected by the repository proposal skill only when all lightweight criteria hold. This avoids weakening the default when a change is ambiguous.
- Define the lightweight schema with `proposal.md` and `tasks.md` only. The proposal records scope, existing contract, and why the change is low risk; tasks list implementation and targeted verification. Product delta specs and design documents are unnecessary only for changes that do not add or change a product requirement or architectural decision.
- Set `skip_specs: true` in lightweight change metadata because OpenSpec schemas without a `specs` artifact must declare the intentional absence of product deltas.
- Update the repository's `openspec-propose` skill to classify before creating the change and honor an explicitly requested schema. The existing apply skill already reads schema-specific context and tasks, so no apply-skill change is needed.
- Keep post-merge archival for both tiers, but make its review proportional: the archive skill already skips spec synchronization when no delta exists; `positioning.md` needs review only when product positioning or catalog text changed, while `AGENTS.md` needs pruning only when repository rules changed.
- When any lightweight criterion fails or scope expands during implementation, stop and move the change to the full schema before proceeding.

## Risks / Trade-offs

- A contributor may misclassify a behavior change as low risk → Use conservative explicit criteria and escalate uncertainty or scope expansion to `spec-driven`.
- A small workflow may omit useful design context → Require a concise proposal with acceptance criteria and a verification task; CI and review gates remain unchanged.

## Migration Plan

Keep existing changes and the default schema unchanged. Apply the lightweight schema only to new qualifying changes after this policy merges. To roll back, remove the lightweight schema and restore the full-process requirement in `AGENTS.md` and the proposal skill.
