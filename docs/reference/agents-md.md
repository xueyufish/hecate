# AGENTS.md Convention

`AGENTS.md` is a community convention — adopted by Claude Code, OpenAI Codex, OpenClaw, Hermes Agent, ByteDance deer-flow, and others — for giving coding agents persistent, project-scoped instructions. A coding agent (Claude Code, Codex, Cursor, etc.) reads `AGENTS.md` files before doing work, so the repository's conventions, commands, and constraints are applied on every run without re-explaining them.

Hecate's repository ships an `AGENTS.md` at the root. This page documents what it is, how Hecate uses it, and how to leverage it when working on Hecate with a coding agent.

---

## What AGENTS.md is (and is not)

- **Is**: a plain-Markdown instructions file read by coding agents. It carries the project's "how to work here" — setup commands, architecture rules, coding conventions, gotchas, and what *not* to do.
- **Is not**: user-facing product documentation, nor a replacement for the docs you are reading now. It is contributor/agent-facing.

The convention is deliberately simple: one file, Markdown, no schema. Agents discover it by filename.

---

## How Hecate's AGENTS.md is structured

Hecate's root `AGENTS.md` captures the operational knowledge an agent (or a new contributor) needs to make correct changes:

- **What the repo is** — one-paragraph context (enterprise multi-tenant agent platform).
- **Commands** — install, test, lint, typecheck, run. The exact commands an agent should execute, not paraphrases.
- **Architecture layers** — the `engine → services → api → models → core` dependency rule, plus known legacy violations to avoid replicating.
- **Engine extension point inventory** — the many engine extension interfaces + multiple plugin SPI types, so an agent proposing a change knows where the seams are.
- **Key files to read first** — the design docs, the Graph DSL schema, the openspec specs.
- **Gotchas and non-obvious facts** — the `model_config` alias, `metadata_` alias, the empty `engine/__init__.py`, ChannelManager semantics, deprecated `PERSISTENT_TOPIC`, etc.
- **Conventions** — naming, workflow (OpenSpec is mandatory for feature changes), coding rules enforced by ruff, testing rules.
- **What to do / what not to do** — a concrete allow/deny list.

This is the file that keeps coding-agent sessions from making beginner mistakes (wrong layer imports, `as any`/`@ts-ignore`, bare `pip install`, direct commits to `main`, etc.).

---

## The discovery chain

Coding agents discover instruction files in a layered precedence. Codex, for example, resolves in this order (highest precedence first):

1. CLI flags / `--config` overrides
2. Project files `.codex/config.toml`, root → cwd (closest wins)
3. Profile files selected with `--profile`
4. User config `~/.codex/config.toml`
5. System config `/etc/codex/config.toml`
6. Built-in defaults

For `AGENTS.md` specifically, the typical chain is:

1. **Global** — `~/.codex/AGENTS.md` (or `~/.claude/CLAUDE.md`) for personal defaults across all repos.
2. **Project** — the repository root `AGENTS.md`, plus nested `AGENTS.md` files walking down to the working directory. Closer files override broader ones because they appear later in the combined prompt.

Files are concatenated root-down; the combined size is capped (32 KiB by default in Codex). Empty files are skipped; the search stops at the current directory.

---

## Working on Hecate with a coding agent

If you use Claude Code, Codex, Cursor, Windsurf, or another coding agent on this repository, point it at the docs:

```
Help me work on Hecate. Read AGENTS.md and docs/design/README.md first,
then <your task>.
```

The agent will pick up the architecture layering, the OpenSpec workflow, the test commands, and the gotchas — so it will not, for example, import from `engine/` in `api/`, or try to commit directly to `main`.

For a one-shot setup (common in the coding-agent ecosystem), you can hand the agent the Quickstart URL:

```
Bootstrap Hecate for local development by following
https://github.com/xueyufish/hecate/blob/main/docs/getting-started/quickstart.md
```

---

## When to update AGENTS.md

- You added a new architecture rule or extension point → add it to the inventory.
- You hit a non-obvious footgun that cost you time → add it to **Gotchas** so the next agent (or human) does not repeat it.
- You changed the install / test / lint commands → update **Commands** immediately; stale commands waste every agent session.
- You added a new forbidden pattern → add it to **What not to do**.

The file is intentionally curated — it is not a dump of everything. Keep it to the rules an agent must follow to make correct changes.

---

## Related

- [Contributing](../../CONTRIBUTING.md) — the human-facing contribution workflow
- [Architecture Center](../design/) — the design docs and ADRs an agent should read for deeper context
- The root [`AGENTS.md`](../../AGENTS.md) itself — the canonical instructions file
