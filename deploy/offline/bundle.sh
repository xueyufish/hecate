#!/usr/bin/env bash
# Assemble the Hecate offline deployment bundle (13.3).
#
# Produces a directory containing:
#   images/*.tar   — docker-saved platform + infrastructure images
#   wheels/        — pip-download'ed Python dependencies
#   models/        — Ollama model blobs (optional, --models)
#   SHA256SUMS     — integrity manifest for everything above
#
# Usage: bundle.sh [-o OUT_DIR] [--models LIST] [--registry HOST:PORT]
# Run on a machine with network access and a local Docker daemon.

set -euo pipefail

OUT_DIR="hecate-offline-bundle"
MODELS=""
REGISTRY=""

APP_IMAGE="hecate-app:offline"
SANDBOX_IMAGE="hecate-browser-sandbox:offline"
INFRA_IMAGES=(
  "postgres:16"
  "qdrant/qdrant:latest"
  "minio/minio:latest"
  "temporalio/auto-setup:latest"
  "temporalio/ui:latest"
  "ollama/ollama:latest"
)

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUT_DIR="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$OUT_DIR/images" "$OUT_DIR/wheels" "$OUT_DIR/models"

echo "==> Saving images"
ALL_IMAGES=("$APP_IMAGE" "$SANDBOX_IMAGE" "${INFRA_IMAGES[@]}")
for img in "${ALL_IMAGES[@]}"; do
  tar_name="$(echo "$img" | tr '/:' '__').tar"
  echo "    $img -> images/$tar_name"
  docker save --output "$OUT_DIR/images/$tar_name" "$img"
  if [[ -n "$REGISTRY" ]]; then
    docker tag "$img" "$REGISTRY/$img"
  fi
done

echo "==> Downloading Python wheels (all extras)"
pip download \
  --dest "$OUT_DIR/wheels" \
  --pre \
  ".[dev]" \
  "fastmcp (>=4.0a0)" >/dev/null
if [[ -d packages/hecate-memory ]]; then
  pip download --dest "$OUT_DIR/wheels" "./packages/hecate-memory[rag]" >/dev/null || \
    echo "    (hecate-memory wheel download skipped)"
fi

echo "==> Pulling Ollama models"
if [[ -n "$MODELS" ]]; then
  IFS=',' read -ra MODEL_LIST <<< "$MODELS"
  for model in "${MODEL_LIST[@]}"; do
    echo "    ollama pull $model"
    ollama pull "$model"
    # Export via a throwaway server-side copy is not supported by the Ollama
    # CLI; bundle the on-disk blobs instead so load.sh can restore them.
    done
  if [[ -d ~/.ollama/models ]]; then
    tar -czf "$OUT_DIR/models/ollama-models.tar.gz" -C "$HOME/.ollama" models
  else
    echo "    WARNING: ~/.ollama/models not found — no model blobs bundled" >&2
  fi
fi

if [[ -n "$REGISTRY" ]]; then
  echo "==> Pushing retagged images to $REGISTRY"
  for img in "${ALL_IMAGES[@]}"; do
    docker push "$REGISTRY/$img"
  done
fi

echo "==> Generating checksum manifest"
( cd "$OUT_DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z \
  | xargs -0 sha256sum > SHA256SUMS )

echo "==> Bundle ready: $OUT_DIR"
du -sh "$OUT_DIR"
echo "Ship the directory (and verify SHA256SUMS on the target) — see deploy/offline/README.md"
