"""Standard bundle archive tests: deterministic pack, bounded install.

Covers the transport contract: the same artifact always packs to the same
bytes; a packed bundle installs, activates, starts and rolls back exactly
like its directory; every malicious or malformed archive is refused before
anything leaves staging; and the extraction bounds hold without allocating
real resources.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest
from conftest_runner import ENTRY_NAME
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hecate_runner import bundle as bundle_module
from hecate_runner.artifact_signing import (
    canonical_manifest_bytes,
    load_trust_policy,
    sign_manifest,
)
from hecate_runner.release import (
    ACTIVE_POINTER_NAME,
    ReleaseError,
    activate_release,
    install_bundle,
    install_release,
    load_host_selection,
    read_active_selection,
    rollback_release,
)
from hecate_runtime.manifest import sha256_hex

PUBLISHER = "publisher:acme"
KEY_ID = "acme-2026-01"
ENVELOPE_NAME = "artifact-signature.json"


def _keypair(tmp_path: Path) -> tuple[bytes, str]:
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def _policy(tmp_path: Path, public_pem: str, *, revoked: bool = False):
    policy = {
        "artifact_trust_version": "1",
        "publishers": {PUBLISHER: {"keys": [{"key_id": KEY_ID, "public_key": public_pem}], "revoked": revoked}},
    }
    path = tmp_path / "artifact-trust.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return load_trust_policy(path)


def _make_artifact(
    tmp_path: Path, name: str, marker: str, private_pem: bytes, *, signature_ref: str = ENVELOPE_NAME
) -> tuple[Path, str]:
    artifact = tmp_path / name
    (artifact / "files" / "agents/summary").mkdir(parents=True)
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"], "marker": marker}).encode()
    (artifact / "files" / ENTRY_NAME).write_bytes(entry)
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [{"path": ENTRY_NAME, "sha256": sha256_hex(entry), "size": len(entry)}],
        "publisher_ref": PUBLISHER,
        "signature_ref": signature_ref,
    }
    (artifact / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    raw = json.loads((artifact / "agent-manifest.json").read_text(encoding="utf-8"))
    envelope = sign_manifest(raw, private_pem, publisher_ref=PUBLISHER, key_id=KEY_ID)
    envelope_path = artifact / signature_ref
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return artifact, sha256_hex(canonical_manifest_bytes(raw))


def _write_host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    host.mkdir()
    (host / "runner.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 0,
                "evidence_dir": "evidence",
                "business_api_base": "http://127.0.0.1:59999",
                "model": {"backend": "stub"},
                "tool_allowlist": ["query_inventory"],
                "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
            }
        ),
        encoding="utf-8",
    )
    (host / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": "app-reader",
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (host / "secrets").mkdir()
    (host / "secrets/app-reader-token").write_text("reader-secret-token", encoding="utf-8")
    return host


@pytest.fixture()
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def _pack(artifact: Path, out: Path, policy) -> str:
    from hecate_runner.bundle import pack_bundle

    return pack_bundle(artifact, out, policy)


def test_pack_is_deterministic_and_matches_the_directory_digest(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)

    out_a, out_b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    assert _pack(artifact, out_a, policy) == sha
    assert _pack(artifact, out_b, policy) == sha
    assert out_a.read_bytes() == out_b.read_bytes()

    # The bundle digest is the release id a directory install produces.
    host = _write_host(tmp_path)
    assert install_release(host, artifact, policy) == sha
    assert install_bundle(host, out_a, policy) == sha  # idempotent across formats


def test_packed_bundle_installs_and_runs_like_the_directory(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)
    bundle_path = tmp_path / "release.tar.gz"
    _pack(artifact, bundle_path, policy)

    host = _write_host(tmp_path)
    install_bundle(host, bundle_path, policy)
    activate_release(host, sha, policy, expected_generation=0)
    profile, lock = load_host_selection(host, policy)
    assert profile.artifact_directory.name == sha
    assert b"version-a" in profile.entry_content
    lock.release()

    # Switch and rollback run through the same machinery.
    artifact_b, sha_b = _make_artifact(tmp_path, "artifact-b", "version-b", private_pem)
    bundle_b = tmp_path / "release-b.tar.gz"
    _pack(artifact_b, bundle_b, policy)
    install_bundle(host, bundle_b, policy)
    activate_release(host, sha_b, policy, expected_generation=1)
    profile_b, lock = load_host_selection(host, policy)
    assert b"version-b" in profile_b.entry_content
    lock.release()
    rollback_release(host, policy, expected_generation=2)
    assert read_active_selection(host).manifest_sha256 == sha


def test_pack_carries_only_whitelisted_members(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    # Decoys that must never travel: host config, secrets, run data.
    (artifact / "runner.json").write_text("{}", encoding="utf-8")
    (artifact / "identity.json").write_text("{}", encoding="utf-8")
    (artifact / "secrets").mkdir()
    (artifact / "secrets/token").write_text("secret", encoding="utf-8")
    (artifact / "evidence").mkdir()
    (artifact / "evidence/run.jsonl").write_text("{}", encoding="utf-8")
    policy = _policy(tmp_path, public_pem)

    bundle_path = tmp_path / "release.tar.gz"
    _pack(artifact, bundle_path, policy)
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        names = sorted(member.name for member in tar.getmembers())
    assert names == sorted(
        [
            "agent-manifest.json",
            ENVELOPE_NAME,
            f"files/{ENTRY_NAME}",
        ]
    )


def test_pack_refusals_leave_the_artifact_and_output_untouched(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)

    existing = tmp_path / "exists.tar.gz"
    existing.write_bytes(b"previous bundle")
    with pytest.raises(ReleaseError, match="already exists"):
        _pack(artifact, existing, policy)
    assert existing.read_bytes() == b"previous bundle"

    inside = artifact / "nested.tar.gz"
    with pytest.raises(ReleaseError, match="inside the artifact"):
        _pack(artifact, inside, policy)
    assert not inside.exists()

    unsigned, _sha = _make_artifact(tmp_path, "artifact-u", "unsigned", private_pem)
    (unsigned / ENVELOPE_NAME).unlink()
    with pytest.raises(ReleaseError, match="admission refused"):
        _pack(unsigned, tmp_path / "unsigned.tar.gz", policy)
    assert not (tmp_path / "unsigned.tar.gz").exists()

    tampered, _sha = _make_artifact(tmp_path, "artifact-t", "tampered", private_pem)
    (tampered / "files" / ENTRY_NAME).write_bytes(b'{"kind": "entry", "tools": ["query_inventory"], "marker": "evil"}')
    with pytest.raises(ReleaseError, match="does not match the manifest"):
        _pack(tampered, tmp_path / "tampered.tar.gz", policy)
    assert not (tmp_path / "tampered.tar.gz").exists()

    revoked = _policy(tmp_path, public_pem, revoked=True)
    fine, _sha = _make_artifact(tmp_path, "artifact-f", "fine", private_pem)
    with pytest.raises(ReleaseError, match="admission refused"):
        _pack(fine, tmp_path / "revoked.tar.gz", revoked)
    assert not (tmp_path / "revoked.tar.gz").exists()


def test_pack_refuses_symlinked_source_file(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact-a", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)
    outside = tmp_path / "outside-entry.json"
    outside.write_bytes(b'{"kind": "entry", "tools": ["query_inventory"]}')
    link = artifact / "files" / ENTRY_NAME
    link.unlink()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform/account")
    with pytest.raises(ReleaseError, match="symlink"):
        _pack(artifact, tmp_path / "release.tar.gz", policy)


def _gz(tar_bytes: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as gz:
        gz.write(tar_bytes)
    return buffer.getvalue()


def _regular(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.type = tarfile.REGTYPE
    return info, content


def _valid_manifest_member(signature_ref: str = ENVELOPE_NAME) -> tuple[tarfile.TarInfo, bytes]:
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [{"path": ENTRY_NAME, "sha256": sha256_hex(entry), "size": len(entry)}],
        "publisher_ref": PUBLISHER,
        "signature_ref": signature_ref,
    }
    return _regular("agent-manifest.json", json.dumps(manifest).encode())


def _craft_bundle(tmp_path: Path, members: list) -> Path:
    """Write a hand-crafted bundle with arbitrary member descriptors."""

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tar.addfile(member)
            else:
                info, content = member
                tar.addfile(info, io.BytesIO(content))
    bundle_path = tmp_path / f"craft-{abs(hash(tuple(str(m) for m in members)))}.tar.gz"
    bundle_path.write_bytes(_gz(payload.getvalue()))
    return bundle_path


def _install_crafted(tmp_path: Path, host: Path, policy, members: list) -> None:
    bundle_path = _craft_bundle(tmp_path, members)
    with pytest.raises(ReleaseError):
        install_bundle(host, bundle_path, policy)


@pytest.mark.parametrize(
    "evil",
    [
        "files/../evil.json",
        "/abs/evil.json",
        "files\\..\\evil.json",
        "files/x.",
        "files/con",
        "evidence/run.jsonl",
    ],
)
def test_bundle_refuses_malicious_member_names(tmp_path, _shutdown_env, evil: str) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    _policy_public = public_pem
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, json.dumps({"signature_version": 1}).encode()),
        _regular(f"files/{ENTRY_NAME}", entry),
        _regular(evil, b"payload"),
    ]
    with pytest.raises(ReleaseError):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)
    assert not (host / "releases").exists() or not any((host / "releases").iterdir())
    assert not (host / ACTIVE_POINTER_NAME).exists()


@pytest.mark.parametrize(
    "member",
    [
        pytest.param(("symlink", tarfile.SYMTYPE), id="symlink-member"),
        pytest.param(("hardlink", tarfile.LNKTYPE), id="hardlink-member"),
        pytest.param(("device", tarfile.CHRTYPE), id="device-member"),
        pytest.param(("sparse", tarfile.GNUTYPE_SPARSE), id="sparse-member"),
        pytest.param(("dir", tarfile.DIRTYPE), id="directory-member"),
    ],
)
def test_bundle_refuses_non_regular_members(tmp_path, _shutdown_env, member: tuple) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()

    evil = tarfile.TarInfo(name="files/agents/summary/extra.json")
    evil.type = member[1]
    evil.size = 0
    evil.linkname = "somewhere" if member[1] in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, json.dumps({"signature_version": 1}).encode()),
        _regular(f"files/{ENTRY_NAME}", entry),
        evil,
    ]
    with pytest.raises(ReleaseError):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)


def test_bundle_refuses_duplicate_undeclared_and_missing_members(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()

    # Duplicate member.
    members = [(manifest_info, manifest_bytes), _regular(ENVELOPE_NAME, b"{}"), _regular(f"files/{ENTRY_NAME}", entry)]
    members.append(_regular(f"files/{ENTRY_NAME}", entry))
    with pytest.raises(ReleaseError, match="duplicate|collide|too many"):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)

    # Undeclared member alongside a complete set.
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, b"{}"),
        _regular(f"files/{ENTRY_NAME}", entry),
        _regular("extra.txt", b"undeclared"),
    ]
    with pytest.raises(ReleaseError, match="undeclared"):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)

    # Missing declared file (the manifest lists the entry, bundle omits it).
    members = [(manifest_info, manifest_bytes), _regular(ENVELOPE_NAME, b"{}")]
    with pytest.raises(ReleaseError, match="missing declared members"):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)


def test_bundle_refuses_case_alias_members(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, b"{}"),
        _regular(f"files/{ENTRY_NAME}", entry),
        _regular("FILES/" + ENTRY_NAME.upper(), b"alias"),
    ]
    with pytest.raises(ReleaseError, match="path aliases"):
        install_bundle(host, _craft_bundle(tmp_path, members), policy)


def test_bundle_refuses_corrupted_and_truncated_archives(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, json.dumps({"signature_version": 1}).encode()),
        _regular(f"files/{ENTRY_NAME}", entry),
    ]
    good = _gz(_tar_bytes(members))

    corrupted = tmp_path / "corrupted.tar.gz"
    payload = bytearray(good)
    payload[len(payload) // 2] ^= 0xFF
    corrupted.write_bytes(bytes(payload))
    with pytest.raises(ReleaseError, match="unreadable|corrupted"):
        install_bundle(host, corrupted, policy)

    truncated = tmp_path / "truncated.tar.gz"
    truncated.write_bytes(good[: len(good) // 3])
    with pytest.raises(ReleaseError):
        install_bundle(host, truncated, policy)

    not_gzip = tmp_path / "plain.tar"
    not_gzip.write_bytes(_tar_bytes(members))
    with pytest.raises(ReleaseError, match="unreadable"):
        install_bundle(host, not_gzip, policy)

    empty = tmp_path / "empty.tar.gz"
    empty.write_bytes(b"")
    with pytest.raises(ReleaseError):
        install_bundle(host, empty, policy)

    # The host is untouched by every refusal.
    assert not (host / "releases").exists() or not any((host / "releases").iterdir())
    assert not (host / ACTIVE_POINTER_NAME).exists()


def _tar_bytes(members: list) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tar.addfile(member)
            else:
                info, content = member
                tar.addfile(info, io.BytesIO(content))
    return payload.getvalue()


def test_bundle_size_limits_refuse_without_extraction(tmp_path, _shutdown_env, monkeypatch) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    policy = _policy(tmp_path, public_pem)
    host = _write_host(tmp_path)
    manifest_info, manifest_bytes = _valid_manifest_member()
    entry = json.dumps({"kind": "entry", "tools": ["query_inventory"]}).encode()
    members = [
        (manifest_info, manifest_bytes),
        _regular(ENVELOPE_NAME, b"{}"),
        _regular(f"files/{ENTRY_NAME}", entry),
    ]
    bundle_path = _craft_bundle(tmp_path, members)

    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_MEMBERS", 2)
    with pytest.raises(ReleaseError, match="too many members"):
        install_bundle(host, bundle_path, policy)

    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_MEMBERS", 512)
    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_FILE_BYTES", 8)
    with pytest.raises(ReleaseError, match="per-file size limit"):
        install_bundle(host, bundle_path, policy)

    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_FILE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_TOTAL_BYTES", 16)
    with pytest.raises(ReleaseError, match="total .*limit|size limit"):
        install_bundle(host, bundle_path, policy)
    monkeypatch.undo()
    # A refusal never publishes and never leaves a host-side release behind.
    assert not (host / "releases").exists() or not any((host / "releases").iterdir())


def test_bundle_failure_retry_and_nested_signature(tmp_path, _shutdown_env) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, sha = _make_artifact(
        tmp_path, "artifact-a", "version-a", private_pem, signature_ref="signatures/current.json"
    )
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public_pem)

    revoked = _policy(tmp_path, public_pem, revoked=True)
    bundle_path = tmp_path / "release.tar.gz"
    _pack(artifact, bundle_path, policy)
    with pytest.raises(ReleaseError, match="admission refused"):
        install_bundle(host, bundle_path, revoked)
    assert not (host / "releases").exists() or not any((host / "releases").iterdir())
    # Staging is cleaned: nothing outside staging, nothing half-written inside.
    assert not (host / "staging").exists() or not any((host / "staging").iterdir())

    # Failure retry with a valid policy succeeds, including the nested
    # signature reference and idempotent re-install.
    assert install_bundle(host, bundle_path, policy) == sha
    assert install_bundle(host, bundle_path, policy) == sha
    activate_release(host, sha, policy, expected_generation=0)
    assert read_active_selection(host).manifest_sha256 == sha
    installed_release = host / "releases" / sha
    assert (installed_release / "signatures" / "current.json").is_file()
    assert not (installed_release / "runner.json").exists()


@pytest.mark.parametrize("change", ["truncated-trailer", "bad-crc", "trailing-member"])
def test_valid_signed_bundle_requires_a_complete_gzip_stream(tmp_path, _shutdown_env, change) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)
    path = tmp_path / "release.tar.gz"
    _pack(artifact, path, policy)
    data = path.read_bytes()
    if change == "truncated-trailer":
        data = data[:-4]
    elif change == "bad-crc":
        data = data[:-8] + bytes([data[-8] ^ 0xFF]) + data[-7:]
    else:
        data += _gz(_tar_bytes([_regular("hidden", b"undeclared")]))
    path.write_bytes(data)
    host = _write_host(tmp_path)
    with pytest.raises(ReleaseError):
        install_bundle(host, path, policy)
    assert not (host / ACTIVE_POINTER_NAME).exists()
    assert not (host / "releases").exists()


def test_pack_never_overwrites_an_output_created_during_verification(tmp_path, _shutdown_env, monkeypatch) -> None:
    private_pem, public_pem = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private_pem)
    policy = _policy(tmp_path, public_pem)
    out = tmp_path / "release.tar.gz"
    extract = bundle_module.extract_bundle

    def concurrent_output(*args):
        result = extract(*args)
        out.write_bytes(b"concurrent publisher's output")
        return result

    monkeypatch.setattr(bundle_module, "extract_bundle", concurrent_output)
    with pytest.raises(ReleaseError, match="exists"):
        _pack(artifact, out, policy)
    assert out.read_bytes() == b"concurrent publisher's output"


def test_member_limit_stops_parsing_before_the_rest_of_the_archive(tmp_path, _shutdown_env, monkeypatch) -> None:
    host = _write_host(tmp_path)
    _private, public = _keypair(tmp_path)
    path = _craft_bundle(tmp_path, [_regular(f"item-{index}", b"") for index in range(20)])
    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_MEMBERS", 2)
    parsed = 0
    original = tarfile.TarInfo.frombuf.__func__

    def counted(cls, *args, **kwargs):
        nonlocal parsed
        parsed += 1
        assert parsed <= 3, "member limit was checked after parsing the whole archive"
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarInfo, "frombuf", classmethod(counted))
    with pytest.raises(ReleaseError, match="too many members"):
        install_bundle(host, path, _policy(tmp_path, public))


@pytest.mark.parametrize("name", ["files/con.txt", "files/COM1.json", "files/lpt\u00b2.log", "files/a?b"])
def test_windows_device_extensions_and_invalid_characters_are_refused(name) -> None:
    with pytest.raises(ReleaseError):
        bundle_module._validate_member_name(name)


def test_bundle_bounds_extension_headers_and_rejects_sparse_pax(tmp_path, _shutdown_env, monkeypatch) -> None:
    _private, public = _keypair(tmp_path)
    host = _write_host(tmp_path)
    policy = _policy(tmp_path, public)
    header = tarfile.TarInfo("././@LongLink")
    header.type = tarfile.GNUTYPE_LONGNAME
    header.size = 65
    path = _craft_bundle(tmp_path, [(header, b"x" * 65)])
    monkeypatch.setattr(bundle_module, "BUNDLE_MAX_METADATA_BYTES", 64)
    with pytest.raises(ReleaseError, match="metadata exceeds"):
        install_bundle(host, path, policy)

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as tar:
        member = tarfile.TarInfo("file")
        member.pax_headers = {"GNU.sparse.map": "0,0", "GNU.sparse.realsize": "1"}
        tar.addfile(member, io.BytesIO())
    monkeypatch.undo()
    path.write_bytes(_gz(payload.getvalue()))
    with pytest.raises(ReleaseError, match="sparse"):
        install_bundle(host, path, policy)


def test_bundle_accepts_bounded_pax_and_gnu_long_names(tmp_path, _shutdown_env) -> None:
    # Keep the host root short enough for Windows' legacy total-path limit;
    # the archive name itself must still exercise GNU/PAX long-name records.
    with tempfile.TemporaryDirectory(prefix="hb-") as directory:
        _check_long_names(Path(directory))


def _check_long_names(tmp_path: Path) -> None:
    private, public = _keypair(tmp_path)
    artifact, sha = _make_artifact(
        tmp_path, "artifact", "version-a", private, signature_ref="signatures/" + "s" * 105 + ".json"
    )
    policy = _policy(tmp_path, public)
    path = tmp_path / "release.tar.gz"
    _pack(artifact, path, policy)
    host = _write_host(tmp_path)
    assert install_bundle(host, path, policy) == sha
    # An external standard tar producer may use bounded PAX path records.
    payload = io.BytesIO()
    with (
        tarfile.open(path, mode="r:gz") as original,
        tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for member in original:
            member.pax_headers = {"comment": "external producer"}
            tar.addfile(member, original.extractfile(member))
    path.write_bytes(_gz(payload.getvalue()))
    assert install_bundle(host, path, policy) == sha


@pytest.mark.parametrize("limit", ["BUNDLE_MAX_MEMBERS", "BUNDLE_MAX_FILE_BYTES", "BUNDLE_MAX_TOTAL_BYTES"])
def test_pack_applies_limits_before_constructing_an_archive(tmp_path, _shutdown_env, monkeypatch, limit) -> None:
    private, public = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private)
    policy = _policy(tmp_path, public)
    monkeypatch.setattr(bundle_module, limit, 2)

    def unexpected_archive(*args, **kwargs):
        raise AssertionError("packing began before source limits were checked")

    monkeypatch.setattr(tarfile, "open", unexpected_archive)
    out = tmp_path / "release.tar.gz"
    with pytest.raises(ReleaseError, match="limit|too many"):
        _pack(artifact, out, policy)
    assert not out.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_pack_refuses_a_junction_as_the_source_root(tmp_path, _shutdown_env) -> None:
    private, public = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private)
    policy = _policy(tmp_path, public)
    junction = tmp_path / "source-link"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(artifact)], capture_output=True)
    assert result.returncode == 0
    assert junction.is_junction()
    with pytest.raises(ReleaseError, match="junction"):
        _pack(junction, tmp_path / "release.tar.gz", policy)


def test_valid_bundle_refuses_excessive_padding_and_repeated_metadata(tmp_path, _shutdown_env) -> None:
    private, public = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private)
    policy = _policy(tmp_path, public)
    path = tmp_path / "release.tar.gz"
    _pack(artifact, path, policy)
    path.write_bytes(path.read_bytes() + _gz(bytes(bundle_module.BUNDLE_MAX_TRAILER_BYTES)))
    host = _write_host(tmp_path)
    with pytest.raises(ReleaseError, match="padding"):
        install_bundle(host, path, policy)

    header = tarfile.TarInfo("././@LongLink")
    header.type = tarfile.GNUTYPE_LONGNAME
    header.size = 2
    path = _craft_bundle(tmp_path, [(header, b"x\x00"), (header, b"x\x00")])
    with pytest.raises(ReleaseError, match="repeats metadata"):
        install_bundle(host, path, policy)


@pytest.mark.parametrize("names", [["files/Dir/a", "files/dir/b"], ["files/a", "files/a/b"]])
def test_bundle_refuses_conflicting_parent_paths(names) -> None:
    with pytest.raises(ReleaseError):
        bundle_module._check_alias_conflicts(names)


@pytest.mark.parametrize("action", ["pack", "install"])
def test_module_cli_reports_bundle_refusals_without_a_traceback(tmp_path, _shutdown_env, action) -> None:
    private, public = _keypair(tmp_path)
    artifact, _sha = _make_artifact(tmp_path, "artifact", "version-a", private)
    _policy(tmp_path, public)
    out = tmp_path / "release.tar.gz"
    out.write_bytes(b"existing output or malformed input")
    if action == "pack":
        args = ["pack", "--artifact", str(artifact), "--out", str(out)]
    else:
        host = _write_host(tmp_path)
        args = ["install", "--host", str(host), "--bundle", str(out)]
    result = subprocess.run(
        [sys.executable, "-m", "hecate_runner.release", *args, "--trust-policy", str(tmp_path / "artifact-trust.json")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, result.stderr
    assert "hecate-runner-release:" in result.stderr
    assert "Traceback" not in result.stderr
    assert out.read_bytes() == b"existing output or malformed input"
