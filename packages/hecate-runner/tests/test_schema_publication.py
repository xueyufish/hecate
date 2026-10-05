"""Runner wheel resources must match authoritative contract schemas."""

from __future__ import annotations

import hashlib
from pathlib import Path


def test_packaged_schemas_match_authoritative_source() -> None:
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parents[1]
    source_dir = repo_root / "src" / "hecate" / "contracts" / "schemas"
    target_dir = package_root / "src" / "hecate_runner" / "_contract_schemas"
    source_files = {path.name: path for path in source_dir.glob("*.schema.json")}
    target_files = {path.name: path for path in target_dir.glob("*.schema.json")}

    assert set(target_files) == set(source_files)
    for name, source in source_files.items():
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target_digest = hashlib.sha256(target_files[name].read_bytes()).hexdigest()
        assert target_digest == source_digest, name
