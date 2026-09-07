# Offline (Air-Gapped) Deployment — 13.3

For regulated environments (government, defense, finance) where the
deployment host has **no external network access**. Distinct from
[13.2 Self-Hosted](../k8s/) which assumes internet access for image pulls
and model API calls.

The bundle is built on a **connected staging machine**, shipped over
sneakernet / internal mirror, and loaded on the **air-gapped host**.

## What the bundle contains

| Artifact | Produced by | Purpose |
|----------|-------------|---------|
| `images/*.tar` | `bundle.sh` (docker save) | Platform + infrastructure images |
| `wheels/` | `bundle.sh` (pip download) | Python dependency mirror for source builds |
| `models/` | `bundle.sh` (ollama pull) | Local LLM weights served by the bundled Ollama |
| `SHA256SUMS` | `bundle.sh` | Integrity manifest — verify before loading |

## Build (connected machine)

```bash
# 1. Build the app image locally (compose `build:` target)
docker build -f docker/Dockerfile -t hecate-app:offline .

# 2. Optional: browser sandbox image
docker build -f docker/sandbox/Dockerfile -t hecate-browser-sandbox:offline docker/sandbox

# 3. Assemble the bundle
./deploy/offline/bundle.sh -o /tmp/hecate-offline-bundle
# Bundle extras:
#   --models llama3.1:8b,qwen2.5:14b   # models to pull via Ollama
#   --registry mirror.internal:5000    # also retag images for an internal registry
```

## Load (air-gapped host)

```bash
# Verify integrity first
sha256sum -c SHA256SUMS

./deploy/offline/load.sh /tmp/hecate-offline-bundle   # docker load + ollama restore

# Start the stack (base compose + offline overlay)
cp .env.example .env   # no external API keys needed
docker compose -f docker/docker-compose.yml \
               -f deploy/offline/docker-compose.offline.yml up -d
```

## Internal registry mirror (optional)

With `--registry mirror.internal:5000`, `bundle.sh` additionally retags every
saved image as `<registry>/<image>` so the bundle can be pushed to the
internal registry instead of loading tarballs on each host. For the K8s
path, set the same registry prefix in `deploy/k8s/*.yaml` image fields
(see `deploy/k8s/README.md`); with the overlay registry in place the
manifests need no further changes.

## Local LLM wiring

The overlay adds an `ollama` service on the compose network. Register the
local runtime in Model Hub (or provider config) as an OpenAI-compatible
provider:

- **Base URL**: `http://ollama:11434/v1`
- **API key**: any non-empty string (Ollama ignores it)
- **Model names**: exactly the names pulled into the bundle (e.g. `llama3.1:8b`)

## No telemetry / phone-home

- `OTEL_EXPORTER_OTLP_ENDPOINT` defaults to **empty** — no trace export
  leaves the host. Keep it empty offline.
- Hecate has no license beacon or update phone-home; the only outbound
  calls are LLM provider requests, which the overlay re-points at the
  bundled Ollama.
- Verify before going live: `docker compose ... config | grep -i otlp`
  shows no endpoint value.

## Version upgrades

Re-run `bundle.sh` on the connected machine with the new release tag and
ship a fresh bundle; `load.sh` is idempotent (`docker load` overwrites
tags). Database schema moves via the bundled `hecate-migrate` job
(`alembic upgrade head`) which runs before the app starts — see
`deploy/k8s/14-hecate-migrate-job.yaml` for the same pattern on K8s.
