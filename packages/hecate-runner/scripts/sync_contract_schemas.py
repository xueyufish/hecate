"""Publish authoritative execution-contract schemas into the runner wheel."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SOURCE_SCHEMA_DIR = REPO_ROOT / "src" / "hecate" / "contracts" / "schemas"
TARGET_SCHEMA_DIR = PACKAGE_ROOT / "src" / "hecate_runner" / "_contract_schemas"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync() -> None:
    TARGET_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    source_files = sorted(SOURCE_SCHEMA_DIR.glob("*.schema.json"))
    if not source_files:
        raise SystemExit(f"no authoritative schemas found under {SOURCE_SCHEMA_DIR}")
    expected = {path.name for path in source_files}
    for path in TARGET_SCHEMA_DIR.glob("*.schema.json"):
        if path.name not in expected:
            path.unlink()
    for source in source_files:
        target = TARGET_SCHEMA_DIR / source.name
        if not target.exists() or sha256(target) != sha256(source):
            shutil.copyfile(source, target)
            print(f"synced {source.name}")


if __name__ == "__main__":
    sync()
