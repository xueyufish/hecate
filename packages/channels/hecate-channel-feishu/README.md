# hecate-channel-feishu

Feishu (Lark) IM channel adapter for Hecate — one of the channel plugin
packages (`packages/channels/*`), extracted from core as part of the
package-split plan (PR5b).

## Contents

- **channel** — `FeishuChannel` (the `ChannelBase` implementation wrapping
  `lark_oapi.channel.FeishuChannel`) plus the `create_feishu_channel`
  factory. Includes the `verify_webhook` override delegating signature
  verification, payload decryption, and URL-verification challenges to
  the SDK's `handle_webhook_request` — the webhook route stays free of
  Feishu-specific knowledge.
- **provider** — the zero-arg `provider()` entry-point factory. Reads its
  own `HECATE_IM_FEISHU_*` env vars and returns `None` when unconfigured
  (the resolver skips `None` without blocking boot).

## Relationship to core

`hecate-channel-feishu` is an **optional** plugin package: core never
imports it structurally — discovery goes through the
`hecate.channel_providers` entry-point group (the in-core registration
that preceded this package was retired when the package was extracted).
Uninstalling the package removes the Feishu channel; `hecate.main` stays
importable and boots without it (enforced by the layering guards and the
core-only self-sufficiency test).

The adapter depends only on core's channel contract modules
(`hecate.channel.adapter` / `capabilities` / `types`); the Feishu SDK
(`lark-oapi[aiohttp]`) ships as this package's main dependency.

## Install

```bash
# As part of the uv workspace (recommended for development)
uv sync --package hecate --package hecate-channel-feishu --extra dev --prerelease=allow

# Standalone
pip install hecate-channel-feishu
```

Configuration: `HECATE_IM_FEISHU_APP_ID`, `HECATE_IM_FEISHU_APP_SECRET`,
optional `HECATE_IM_FEISHU_ENCRYPT_KEY`, `HECATE_IM_FEISHU_VERIFICATION_TOKEN`,
and `HECATE_IM_FEISHU_TRANSPORT` (`webhook` default, `ws` for local dev).
See `docs/how-to/configure-feishu-slack.md`.
