# hecate-channel-slack

Slack IM channel adapter for Hecate — one of the channel plugin packages
(`packages/channels/*`), extracted from core as part of the package-split
plan (PR5b).

## Contents

- **channel** — `SlackChannel` (the `ChannelBase` implementation wrapping
  `slack_bolt.App`) plus the `create_slack_channel` factory. Includes the
  `verify_webhook` override implementing Slack's signing-secrets `v0`
  scheme (HMAC-SHA256 over `v0:<timestamp>:<body>` with a five-minute
  replay window) — webhook POSTs terminate at Hecate's FastAPI endpoint
  and never flow through Bolt's `RequestVerification` middleware, so the
  adapter verifies signatures itself.
- **provider** — the zero-arg `provider()` entry-point factory. Reads its
  own `HECATE_IM_SLACK_*` env vars and returns `None` when unconfigured
  (the resolver skips `None` without blocking boot).

## Relationship to core

`hecate-channel-slack` is an **optional** plugin package: core never
imports it structurally — discovery goes through the
`hecate.channel_providers` entry-point group (the in-core registration
that preceded this package was retired when the package was extracted).
Uninstalling the package removes the Slack channel; `hecate.main` stays
importable and boots without it (enforced by the layering guards and the
core-only self-sufficiency test).

The adapter depends only on core's channel contract modules
(`hecate.channel.adapter` / `capabilities` / `types`); the Slack SDK
(`slack-bolt`) ships as this package's main dependency.

## Install

```bash
# As part of the uv workspace (recommended for development)
uv sync --package hecate --package hecate-channel-slack --extra dev --prerelease=allow

# Standalone
pip install hecate-channel-slack
```

Configuration: `HECATE_IM_SLACK_BOT_TOKEN`, `HECATE_IM_SLACK_SIGNING_SECRET`,
optional `HECATE_IM_SLACK_APP_TOKEN` (Socket Mode) and
`HECATE_IM_SLACK_TEST_MODE=1` (skip the auth.test round-trip; tests only).
See `docs/how-to/configure-feishu-slack.md`.
