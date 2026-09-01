# hecate-llm

Hecate's LLM gateway and model management domain, extracted from the core
package as part of the package-split plan (PR4b).

## Contents

- **gateway** — `LLMGateway` Protocol (`chat` / `chat_stream` / `list_models`
  / `test_connection`); the single front door for every LLM call.
- **service** — `LLMService` (the default gateway implementation) plus the
  module-level `llm_service` singleton; the only `import litellm` site in
  the platform (PR4a acceptance).
- **routing** — `ModelRouter`, `ModelInfo`, `RoutingStrategy` primitives
  for capability-based model selection.
- **tool_calling** — pure-Python helpers for tool-call formatting,
  parsing, and result injection (the dict shape litellm expects).
- **circuit_breaker** / **gray_release** / **ab_testing** — cross-cutting
  policies around the gateway's chat call. No production consumer today
  (latent capabilities tracked in the research doc); shipped unchanged for
  PR4b so a follow-up can wire them into `LLMService.chat`.
- **hub** — management plane: catalog, lifecycle, cost budgets,
  fine-tuning, OpenAI-compatible inference endpoints, performance
  monitoring, intelligent routing (cache-aware wrapper over routing).
- **api/management** — six CRUD routers wholesale-moved from core's
  `api/management/` (cost_management, fine_tuning, inference,
  model_catalog, model_lifecycle, monitoring_models).

## Relationship to core

`hecate-llm` is a **required** dependency of the core `hecate` package:
LLM-backed chat is platform capability, not an optional extra. The
`engine/` layer is unaffected — it consumes only the abstract
`RuntimePort.llm_invoke` and never imports anything from `hecate_llm`
(PR0.x self-sufficiency rule, enforced by `test_engine_self_sufficiency`).

`services/model_provider/crypto.py` (Fernet encryption of stored provider
API keys) deliberately **stays in core**: the gateway receives plaintext
API keys as call-site kwargs from the core admin endpoint, so decryption
stays where the `ModelProviderModel` row is owned.

## Install

```bash
# As part of the uv workspace (recommended for development)
uv sync --package hecate --package hecate-llm --extra dev --prerelease=allow

# litellm runtime (used by the default gateway adapter)
pip install 'hecate-llm[llm]'
```