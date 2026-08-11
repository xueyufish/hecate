# Changelog

All notable changes to Hecate will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

For **human-readable highlights** of each release, see [Release Notes](docs/about/release-notes.md). For **the full commit history** of every published version, see [GitHub Releases](https://github.com/xueyufish/hecate/releases).

---

## [Unreleased]

### Added
- Initial public alpha release of Hecate
- Pregel/BSP execution engine with checkpoint persistence
- 11 core extension points + 4 SPI extension points (Plugin / Evaluator / Channel / Auth / Notifier)
- MCP server + client (bidirectional, Streamable HTTP)
- A2A Protocol server + client (Linux Foundation v1.0 GA)
- OpenAI-compatible API at `/v1/chat/completions` (drop-in replacement)
- Multi-tenant architecture: Organization → Workspace → RBAC
- 4-level memory architecture (L1 working / L2 compressed / L3 user / L4 knowledge)
- RAG pipeline with hybrid dense+sparse retrieval (BGE-M3 + Qdrant)
- Visual canvas at `web/` (Next.js + React Flow + JSON DSL bidirectional sync)
- 3 CLI entry points: `hecate`, `hecate-migrate`, `hecate-flag-audit`
- 6 multi-agent collaboration patterns (Hierarchical / Handoff / Pipeline / Broadcast / Negotiation / Debate)
- Observability stack: OpenTelemetry traces, Prometheus metrics, structured logs, audit pipeline
- Outbound DLP engine for content scanning
- Guardrail hooks (Pre/Post LLM/Tool) for PII masking, injection defense, audit
- OpenSpec-driven development workflow (based on PEPs / KEPs / Rust RFCs)

### Security
- 3-Layer rate limiting (waf + token bucket + workspace quota)
- All PII anonymized before storage (LLM context + log + audit)
- Workspace-scoped JWT + API key auth
- OIDC / SAML / LDAP SSO support
- SIEM pipeline for compliance audit log forwarding

---

## Release history

Hecate is in **alpha** (0.1.x). APIs may change between minor versions. Detailed release history:

- **0.1.x** — alpha (current). See [GitHub Releases](https://github.com/xueyufish/hecate/releases) for the full list.
- **0.2.x** — Beta (planned for 2026 Q4). See [Migration Guide](docs/migrations/v0.1-to-v0.2.md).
- **1.0.0** — GA (planned for 2027 Q2). See Positioning.

---

## How to read this changelog

Each entry follows the format:

```
### Added         — new features
### Changed       — changes in existing functionality
### Deprecated    — soon-to-be removed features
### Removed       — removed features
### Fixed         — bug fixes
### Security      — security fixes / improvements
```

For the per-PR detail, see [GitHub Releases](https://github.com/xueyufish/hecate/releases).

---

## Categories used in this changelog

### `Added` — new features

A new feature that users can opt into. Always backward-compatible.

### `Changed` — changes in existing functionality

A change to behavior that users may notice. May be backward-compatible or breaking — if breaking, called out in [Migration Guide](docs/migrations/).

### `Deprecated` — soon-to-be removed features

A feature that still works but will be removed in a future release. Usually gives 1-2 minor versions of notice.

### `Removed` — removed features

A feature that no longer works. Migration path should be documented.

### `Fixed` — bug fixes

A behavior that was wrong and is now correct.

### `Security` — security fixes / improvements

A vulnerability fix or security hardening. May be called out separately for CVEs.

---

## Related documents

- [Release Notes](docs/about/release-notes.md) — human-readable highlights
- Positioning — what's coming
- [Migration Guide](docs/migrations/) — version upgrade procedures
- [Contributing Guide](CONTRIBUTING.md) — how to add entries
- [GitHub Releases](https://github.com/xueyufish/hecate/releases) — full release history

---

## Contributing to this changelog

This file is updated by maintainers during release. To add an entry:

1. Make your PR with the conventional commit format (`feat:`, `fix:`, `chore:`, etc.)
2. The release script will:
   - Scan merged PRs since the last release
   - Categorize them by conventional commit type
   - Add them to the new version section
3. The maintainer reviews and merges

Don't manually edit `CHANGELOG.md` for your PR — it will be overwritten at release time.