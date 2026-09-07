#!/usr/bin/env bash
# Load a Hecate offline bundle on the air-gapped host (13.3).
#
# Usage: load.sh <bundle_dir>
# Idempotent: `docker load` overwrites existing tags; models restore over
# the existing Ollama store.

set -euo pipefail

BUNDLE_DIR="${1:-}"
if [[ -z "$BUNDLE_DIR" || ! -d "$BUNDLE_DIR" ]]; then
  echo "Usage: load.sh <bundle_dir>" >&2
  exit 1
fi

echo "==> Verifying checksums"
( cd "$BUNDLE_DIR" && sha256sum -c SHA256SUMS )

echo "==> Loading container images"
for tar in "$BUNDLE_DIR"/images/*.tar; do
  echo "    docker load < $(basename "$tar")"
  docker load --input "$tar"
done

if [[ -f "$BUNDLE_DIR/models/ollama-models.tar.gz" ]]; then
  echo "==> Restoring Ollama models"
  mkdir -p "$HOME/.ollama"
  tar -xzf "$BUNDLE_DIR/models/ollama-models.tar.gz" -C "$HOME/.ollama"
  echo "    Restart the ollama service/container to pick up restored blobs."
fi

echo "==> Done. Start the stack:"
echo "  docker compose -f docker/docker-compose.yml -f deploy/offline/docker-compose.offline.yml up -d"
