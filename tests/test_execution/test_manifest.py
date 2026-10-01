"""Manifest contract tests (tasks 4.1 / 4.2).

Integrity is per-file sha256 over the tar.gz archive; structure violations,
path traversal, credential-shaped keys, unlisted members, and digest/size
mismatches are all rejected before anything reaches a loader.
"""

from __future__ import annotations

import io
import json
import tarfile

import pytest

from hecate.contracts.execution.manifest import (
    ArtifactManifest,
    ManifestError,
    sha256_hex,
    validate_manifest_dict,
    verify_archive,
)
from tests.test_execution.conftest import SAMPLES_DIR, load_sample

ARCHIVE_CONTENTS: dict[str, bytes] = {
    "agents/reviewer/main.json": b'{"entry": true, "model": "stub"}',
    "agents/reviewer/graph.json": b'{"nodes": ["draft", "review"], "edges": [["draft", "review"]]}',
}

SAMPLE_MANIFEST = SAMPLES_DIR / "manifest" / "valid-manifest.json"


def build_archive(contents: dict[str, bytes], extra_member: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, content in contents.items():
            info = tarfile.TarInfo(name=path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        if extra_member is not None:
            info = tarfile.TarInfo(name="sneaky/extra.bin")
            info.size = len(extra_member)
            tar.addfile(info, io.BytesIO(extra_member))
    return buffer.getvalue()


def test_sample_manifest_digests_match_the_reference_contents() -> None:
    """The static sample and the test's archive literals cannot drift apart."""

    sample = load_sample(SAMPLE_MANIFEST)
    for entry in sample["files"]:
        assert entry["path"] in ARCHIVE_CONTENTS
        assert entry["sha256"] == sha256_hex(ARCHIVE_CONTENTS[entry["path"]])
        assert entry["size"] == len(ARCHIVE_CONTENTS[entry["path"]])


def test_valid_archive_passes() -> None:
    manifest = verify_archive(load_sample(SAMPLE_MANIFEST), build_archive(ARCHIVE_CONTENTS))
    assert isinstance(manifest, ArtifactManifest)
    assert manifest.entry == "agents/reviewer/main.json"
    assert manifest.backend_ns["pregel"]["graph"]["nodes"] == ["draft", "review"]


def test_tampered_content_fails_digest() -> None:
    tampered = dict(ARCHIVE_CONTENTS)
    tampered["agents/reviewer/main.json"] = b'{"entry": true, "model": "fake"}'
    with pytest.raises(ManifestError, match="sha256 mismatch"):
        verify_archive(load_sample(SAMPLE_MANIFEST), build_archive(tampered))


def test_unlisted_member_is_rejected() -> None:
    with pytest.raises(ManifestError, match="unlisted archive member"):
        verify_archive(
            load_sample(SAMPLE_MANIFEST),
            build_archive(ARCHIVE_CONTENTS, extra_member=b"payload"),
        )


def test_missing_member_is_rejected() -> None:
    partial = {path: content for path, content in ARCHIVE_CONTENTS.items() if "graph" not in path}
    with pytest.raises(ManifestError, match="missing archive member"):
        verify_archive(load_sample(SAMPLE_MANIFEST), build_archive(partial))


def test_path_traversal_is_rejected() -> None:
    data = load_sample(SAMPLE_MANIFEST)
    data["entry"] = "../outside/main.json"
    with pytest.raises(ManifestError, match="escapes the archive"):
        validate_manifest_dict(data)


def test_credential_like_keys_are_rejected() -> None:
    data = load_sample(SAMPLE_MANIFEST)
    data["backend_ns"] = {"pregel": {"api_key": "sk-live-123"}}
    with pytest.raises(ManifestError, match="credential-like key"):
        validate_manifest_dict(data)


def test_structurally_invalid_manifest_is_rejected() -> None:
    data = load_sample(SAMPLE_MANIFEST)
    del data["files"]
    with pytest.raises(ManifestError, match="missing required field: files"):
        validate_manifest_dict(data)


def test_unknown_top_level_field_is_rejected() -> None:
    """additionalProperties=false: installer-script fields cannot even appear."""

    import jsonschema

    from tests.test_execution.conftest import load_registry, schema_uri

    data = load_sample(SAMPLE_MANIFEST)
    data["install_script"] = "setup.sh"
    with pytest.raises(ManifestError, match="unknown manifest field"):
        validate_manifest_dict(data)

    schema = json.loads(
        (SAMPLES_DIR.parents[2] / "src/hecate/contracts/schemas/artifact-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = jsonschema.Draft202012Validator(schema, registry=load_registry())
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(data)

    # The structural validator is a second, schema-independent guard.
    del data["install_script"]
    assert validate_manifest_dict(data) is not None
    assert schema_uri("artifact-manifest").endswith("/execution/0.1/artifact-manifest.schema.json")


@pytest.mark.parametrize("path", [r"..\outside", "C:/outside", "a/./b", "a//b", "/absolute", "a/../b", "a\x00b"])
def test_nonportable_paths_fail_both_schema_and_runtime(path: str) -> None:
    import jsonschema

    from tests.test_execution.conftest import validate_against_schema

    data = load_sample(SAMPLE_MANIFEST)
    data["entry"] = path
    with pytest.raises(ManifestError, match="escapes the archive"):
        validate_manifest_dict(data)
    with pytest.raises(jsonschema.ValidationError):
        validate_against_schema(data, "artifact-manifest")


@pytest.mark.parametrize(
    "key,value",
    [("tools", [None]), ("tools", [{"name": "echo"}]), ("tools", None), ("backend_ns", []), ("evidence_refs", "ref")],
)
def test_invalid_manifest_shapes_raise_contract_error(key, value) -> None:
    data = load_sample(SAMPLE_MANIFEST)
    data[key] = value
    with pytest.raises(ManifestError):
        validate_manifest_dict(data)


def test_entry_must_be_a_verified_member() -> None:
    data = load_sample(SAMPLE_MANIFEST)
    data["entry"] = "not-listed.json"
    with pytest.raises(ManifestError, match="listed file"):
        verify_archive(data, build_archive(ARCHIVE_CONTENTS))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.DIRTYPE])
def test_special_members_are_rejected(kind: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, content in ARCHIVE_CONTENTS.items():
            info = tarfile.TarInfo(path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        link = tarfile.TarInfo("unlisted-link")
        link.type = kind
        link.linkname = "../../outside"
        tar.addfile(link)
    with pytest.raises(ManifestError, match="non-regular archive member"):
        verify_archive(load_sample(SAMPLE_MANIFEST), buffer.getvalue())


def test_duplicate_members_cannot_shadow_verified_content() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, content in [*ARCHIVE_CONTENTS.items(), next(iter(ARCHIVE_CONTENTS.items()))]:
            info = tarfile.TarInfo(path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    with pytest.raises(ManifestError, match="duplicate archive member"):
        verify_archive(load_sample(SAMPLE_MANIFEST), buffer.getvalue())


def test_publisher_metadata_roundtrips_without_granting_trust() -> None:
    data = load_sample(SAMPLE_MANIFEST)
    data.update(
        publisher_ref="local://publisher/acme", license_expression="MIT", signature_ref="local://attestations/1"
    )
    assert validate_manifest_dict(data).to_dict() == data
