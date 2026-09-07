# How to Deploy Without Internet Access (Air-Gapped)

For regulated environments where the deployment host has no outbound
network. Produces a verifiable offline bundle on a connected machine and
loads it on the target host. The compose-level mechanics live in
[`deploy/offline/README.md`](../../deploy/offline/README.md); this page
walks through the operator workflow.

## Prerequisites

- Connected staging machine: Docker, Python 3.12+, Ollama (only if bundling
  local models), and this repository checked out at the release tag.
- Air-gapped host: Docker Engine with Compose v2.24+, enough disk for the
  bundle (images ≈ several GB, models add more).

## Steps

1. **Build images on the staging machine** (the app image is built from
   source, infrastructure images are pulled from upstream registries):

   ```bash
   docker build -f docker/Dockerfile -t hecate-app:offline .
   ```

2. **Assemble the bundle**:

   ```bash
   ./deploy/offline/bundle.sh -o hecate-offline-bundle --models llama3.1:8b
   ```

   This saves all container images to `images/*.tar`, downloads the Python
   dependency wheels to `wheels/`, exports the pulled Ollama models to
   `models/`, and writes a `SHA256SUMS` manifest.

3. **Ship the bundle** over your approved transfer channel (sneakernet,
   internal mirror). Verify on arrival: `sha256sum -c SHA256SUMS`.

4. **Load on the air-gapped host**:

   ```bash
   ./deploy/offline/load.sh hecate-offline-bundle
   ```

5. **Start the stack** with the offline overlay (adds the bundled Ollama
   runtime and pins the pre-built app image):

   ```bash
   docker compose -f docker/docker-compose.yml \
                  -f deploy/offline/docker-compose.offline.yml up -d
   ```

6. **Register the local LLM** in Model Hub as an OpenAI-compatible provider
   with base URL `http://ollama:11434/v1` (any API key value) and point your
   agents at the bundled model names.

7. **Verify** health and silence of outbound connections:

   ```bash
   curl -s http://localhost:8000/health/ready
   docker compose ... config | grep -i otlp   # no endpoint value expected
   ```

## Troubleshooting

- **`docker compose` rejects `!reset`** — your Compose version is older
  than 2.24; remove the `build: !reset null` line from the overlay and keep
  the source tree reachable, or upgrade Compose.
- **Models missing after load** — restore is volume-based; see the copy
  recipe in `deploy/offline/docker-compose.offline.yml`.
- **Schema migrations** run automatically via the bundled `hecate-migrate`
  job before the app starts; check its logs if `/health/ready` fails.
