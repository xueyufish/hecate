"""Runner artifact signing and trusted-publisher admission tests.

Covers the strict-admission contract: a real signed artifact starts under
``--artifact-trust``; every refusal path (missing signature, unknown or
revoked publisher/key, wrong key, algorithm/version mismatch, tampered
manifest or files, path escape) refuses before any serving; the verified
bytes are the consumed bytes; and profiles without the flag keep their
existing digest-only behavior.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from conftest_runner import ENTRY_NAME, write_profile
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hecate_runner.artifact_signing import (
    ArtifactAdmissionError,
    canonical_manifest_bytes,
    load_trust_policy,
    sign_manifest,
)
from hecate_runner.profile import ProfileError, load_profile
from hecate_runner.sign import main as sign_main
from hecate_runtime.manifest import sha256_hex

PUBLISHER = "publisher:acme"
KEY_ID = "acme-2026-01"
ENVELOPE_NAME = "artifact-signature.json"


def _keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_key, public_pem


def _policy(
    tmp_path: Path,
    public_pem: str,
    *,
    publisher_ref: str = PUBLISHER,
    key_id: str = KEY_ID,
    revoked: bool = False,
    revoked_key_ids: list[str] | None = None,
) -> Path:
    policy = {
        "artifact_trust_version": "1",
        "publishers": {
            publisher_ref: {
                "keys": [{"key_id": key_id, "public_key": public_pem}],
                "revoked": revoked,
                **({"revoked_key_ids": revoked_key_ids} if revoked_key_ids else {}),
            }
        },
    }
    path = tmp_path / "artifact-trust.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    return path


def _declare_and_sign(
    profile_dir: Path,
    private_key: Ed25519PrivateKey,
    *,
    publisher_ref: str | None = PUBLISHER,
    key_id: str = KEY_ID,
    signature_ref: str | None = ENVELOPE_NAME,
    mutate_envelope: object = None,
) -> Path:
    """Add the publisher/signature references to the manifest and sign it."""

    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if publisher_ref is not None:
        raw["publisher_ref"] = publisher_ref
    if signature_ref is not None:
        raw["signature_ref"] = signature_ref
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    envelope = sign_manifest(
        raw,
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ),
        publisher_ref=raw["publisher_ref"],
        key_id=key_id,
    )
    if mutate_envelope is not None:
        assert isinstance(mutate_envelope, dict)
        envelope = {**envelope, **mutate_envelope}
    out = profile_dir / (signature_ref or ENVELOPE_NAME)
    try:
        # Invalid references need only be declared, never materialized outside
        # this fixture's profile (especially drive-qualified Windows paths).
        if not out.resolve().is_relative_to(profile_dir.resolve()):
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope), encoding="utf-8")
    except (OSError, ValueError):
        # Escape targets may be unwritable or invalid on the host OS; the
        # manifest still declares the reference and admission must refuse it.
        pass
    return out


@pytest.fixture()
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


def _signed_profile(tmp_path: Path, private_key: Ed25519PrivateKey, **sign_kwargs) -> Path:
    profile_dir = write_profile(tmp_path)
    _declare_and_sign(profile_dir, private_key, **sign_kwargs)
    return profile_dir


def test_signed_profile_passes_strict_admission(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy = load_trust_policy(_policy(tmp_path, public_pem))

    profile = load_profile(profile_dir, artifact_trust=policy)
    assert profile.artifact_admission is not None
    assert profile.artifact_admission.publisher_ref == PUBLISHER
    assert profile.artifact_admission.key_id == KEY_ID


def test_without_policy_keeps_digest_only_behavior(tmp_path, _shutdown_env) -> None:
    private_key, _public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    profile = load_profile(profile_dir)
    # A digest pass or a present signature_ref is never publisher authentication.
    assert profile.artifact_admission is None


def test_strict_mode_refuses_unsigned_profile(tmp_path, _shutdown_env) -> None:
    _private_key, public_pem = _keypair()
    profile_dir = write_profile(tmp_path)
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="publisher_ref"):
        load_profile(profile_dir, artifact_trust=policy)


def test_strict_mode_refuses_missing_envelope_file(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = write_profile(tmp_path)
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = PUBLISHER
    raw["signature_ref"] = ENVELOPE_NAME
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="signature_ref"):
        load_profile(profile_dir, artifact_trust=policy)


def test_unknown_publisher_and_key_are_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    other_public = _keypair()[1]
    policy = load_trust_policy(_policy(tmp_path, other_public, publisher_ref="publisher:other"))
    with pytest.raises(ProfileError, match="not in the artifact trust policy"):
        load_profile(profile_dir, artifact_trust=policy)

    # Publisher trusted, but the signing key is not the trusted one.
    policy = load_trust_policy(_policy(tmp_path, other_public, key_id="other-key"))
    with pytest.raises(ProfileError, match="not trusted for publisher"):
        load_profile(profile_dir, artifact_trust=policy)


def test_revoked_publisher_and_revoked_key_are_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy = load_trust_policy(_policy(tmp_path, public_pem, revoked=True))
    with pytest.raises(ProfileError, match="revoked"):
        load_profile(profile_dir, artifact_trust=policy)

    profile_dir = _signed_profile(tmp_path, private_key)
    policy = load_trust_policy(_policy(tmp_path, public_pem, revoked_key_ids=[KEY_ID]))
    with pytest.raises(ProfileError, match="signing key.*revoked"):
        load_profile(profile_dir, artifact_trust=policy)


def test_signature_from_wrong_key_is_refused(tmp_path, _shutdown_env) -> None:
    impostor, _impostor_public = _keypair()
    _honest_key, honest_public = _keypair()
    # The impostor signs, the policy trusts only the honest key.
    profile_dir = _signed_profile(tmp_path, impostor)
    policy = load_trust_policy(_policy(tmp_path, honest_public))
    with pytest.raises(ProfileError, match="signature verification failed"):
        load_profile(profile_dir, artifact_trust=policy)


def test_tampered_envelope_signature_is_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key, mutate_envelope={"key_id": KEY_ID})
    envelope = json.loads((profile_dir / ENVELOPE_NAME).read_text(encoding="utf-8"))
    raw_sig = bytearray(base64.b64decode(envelope["signature"]))
    raw_sig[0] ^= 0xFF
    envelope["signature"] = base64.b64encode(bytes(raw_sig)).decode("ascii")
    (profile_dir / ENVELOPE_NAME).write_text(json.dumps(envelope), encoding="utf-8")
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="signature verification failed"):
        load_profile(profile_dir, artifact_trust=policy)


def test_envelope_publisher_mismatch_is_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key, mutate_envelope={"publisher_ref": "publisher:impostor"})
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="does not match manifest publisher_ref"):
        load_profile(profile_dir, artifact_trust=policy)


@pytest.mark.parametrize(
    "mutate, match",
    [
        ({"signature_version": 2}, "signature_version"),
        ({"algorithm": "rsa-sha256"}, "algorithm"),
    ],
)
def test_unknown_signature_version_or_algorithm_is_refused(tmp_path, _shutdown_env, mutate: dict, match: str) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key, mutate_envelope=mutate)
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match=match):
        load_profile(profile_dir, artifact_trust=policy)


def test_manifest_changed_after_signing_is_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["required_capabilities"] = ["cancel"]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="manifest changed after signing"):
        load_profile(profile_dir, artifact_trust=policy)


def test_envelope_reused_for_another_manifest_is_refused(tmp_path, _shutdown_env) -> None:
    """A valid signature over manifest A must not admit manifest B even when
    the envelope's digest field is updated to point at B."""

    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["required_capabilities"] = ["cancel"]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    envelope = json.loads((profile_dir / ENVELOPE_NAME).read_text(encoding="utf-8"))
    envelope["manifest_sha256"] = sha256_hex(canonical_manifest_bytes(raw))
    (profile_dir / ENVELOPE_NAME).write_text(json.dumps(envelope), encoding="utf-8")
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="signature verification failed"):
        load_profile(profile_dir, artifact_trust=policy)


def test_tampered_artifact_file_refuses_before_admission(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    (profile_dir / "files" / ENTRY_NAME).write_bytes(b'{"kind": "entry", "tools": ["query_inventory", "extra"]}')
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="digest mismatch"):
        load_profile(profile_dir, artifact_trust=policy)


@pytest.mark.parametrize(
    "signature_ref",
    ["../outside.json", "C:/evil/signature.json", "https://evil.example/sig.json", "files/../signature.json"],
)
def test_signature_ref_path_escape_is_refused(tmp_path, _shutdown_env, signature_ref: str) -> None:
    private_key, public_pem = _keypair()
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    profile_dir = _signed_profile(tmp_path, private_key, signature_ref=signature_ref)
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="signature_ref"):
        load_profile(profile_dir, artifact_trust=policy)


def test_signature_ref_symlink_escape_is_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    profile_dir = write_profile(tmp_path)
    link = profile_dir / "linked-signature.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform/account")
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = PUBLISHER
    raw["signature_ref"] = "linked-signature.json"
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    with pytest.raises(ProfileError, match="signature_ref"):
        load_profile(profile_dir, artifact_trust=policy)


def test_invalid_trust_policy_is_refused(tmp_path, _shutdown_env) -> None:
    private_key, public_pem = _keypair()
    _signed_profile(tmp_path, private_key)

    bad_version = tmp_path / "bad-version.json"
    bad_version.write_text(json.dumps({"artifact_trust_version": "2", "publishers": {}}), encoding="utf-8")
    with pytest.raises(ArtifactAdmissionError, match="artifact_trust_version"):
        load_trust_policy(bad_version)

    rsa_public = tmp_path / "rsa.pem"
    rsa_public.write_text(public_pem.replace("PUBLIC KEY", "NOT-A-KEY"), encoding="utf-8")
    bad_key = {
        "artifact_trust_version": "1",
        "publishers": {PUBLISHER: {"keys": [{"key_id": KEY_ID, "public_key": rsa_public.read_text()}]}},
    }
    bad_key_path = tmp_path / "bad-key.json"
    bad_key_path.write_text(json.dumps(bad_key), encoding="utf-8")
    with pytest.raises(ArtifactAdmissionError, match="public_key"):
        load_trust_policy(bad_key_path)

    policy = _policy(tmp_path, public_pem, revoked_key_ids=["unknown-key"])
    with pytest.raises(ArtifactAdmissionError, match="revokes key ids not present"):
        load_trust_policy(policy)


def test_verified_content_is_what_startup_consumes(tmp_path, _shutdown_env, monkeypatch) -> None:
    """The engine consumes the digest-verified bytes, not a fresh disk read.

    The entry file is read exactly once: a tampered file after that read
    changes nothing the runner executes (the pre-admission re-read this
    test forbids is the TOCTOU window).
    """

    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    entry_path = profile_dir / "files" / ENTRY_NAME
    original = entry_path.read_bytes()
    tampered = b'{"kind": "entry", "tools": ["submit_inventory_update"]}'
    reads = {"count": 0}
    real_read_bytes = Path.read_bytes

    def read_bytes_then_tamper(path: Path) -> bytes:
        content = real_read_bytes(path)
        if path.resolve() == entry_path.resolve():
            reads["count"] += 1
            if reads["count"] > 1:
                return tampered
        return content

    monkeypatch.setattr(Path, "read_bytes", read_bytes_then_tamper)
    try:
        profile = load_profile(profile_dir, artifact_trust=policy)
    finally:
        monkeypatch.undo()
    assert reads["count"] == 1
    assert profile.entry_content == original
    assert b"submit_inventory_update" not in profile.entry_content


def test_strict_runner_main_refuses_before_serving(tmp_path, _shutdown_env, capsys) -> None:
    from hecate_runner.__main__ import main

    private_key, public_pem = _keypair()
    unsigned = write_profile(tmp_path / "unsigned")
    policy_path = _policy(tmp_path, public_pem)
    evidence_dir = unsigned / "evidence"

    unsigned_argv = ["--profile", str(unsigned), "--business-api", "http://127.0.0.1:9"]
    assert main([*unsigned_argv, "--artifact-trust", str(policy_path)]) == 2
    captured = capsys.readouterr()
    assert "artifact admission refused" in captured.err
    # Refusal happens before any resource initialization: no evidence
    # directory, no service bind, no model or tool call.
    assert not evidence_dir.exists()
    assert "serving on" not in captured.out

    # A malformed trust policy also refuses before the profile is even read.
    broken_policy = tmp_path / "broken.json"
    broken_policy.write_text("{ not json", encoding="utf-8")
    assert main([*unsigned_argv, "--artifact-trust", str(broken_policy)]) == 2
    assert "artifact trust policy error" in capsys.readouterr().err


def test_sign_cli_roundtrip_and_preflight(tmp_path) -> None:
    private_key, public_pem = _keypair()
    key_path = tmp_path / "signing-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    profile_dir = write_profile(tmp_path / "cli")
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = PUBLISHER
    raw["signature_ref"] = ENVELOPE_NAME
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")

    assert (
        sign_main(
            [
                "sign",
                "--manifest",
                str(manifest_path),
                "--key",
                str(key_path),
                "--publisher-ref",
                PUBLISHER,
                "--key-id",
                KEY_ID,
            ]
        )
        == 0
    )
    policy_path = _policy(tmp_path, public_pem)
    assert sign_main(["verify", "--profile-dir", str(profile_dir), "--trust-policy", str(policy_path)]) == 0

    # Tampered manifest flips the preflight to a refusal.
    raw["required_capabilities"] = ["cancel"]
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    assert sign_main(["verify", "--profile-dir", str(profile_dir), "--trust-policy", str(policy_path)]) == 2


def test_sign_cli_refuses_publisher_mismatch(tmp_path) -> None:
    private_key, _public_pem = _keypair()
    key_path = tmp_path / "signing-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    profile_dir = write_profile(tmp_path / "cli")
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = "publisher:other"
    raw["signature_ref"] = ENVELOPE_NAME
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    assert (
        sign_main(
            [
                "sign",
                "--manifest",
                str(manifest_path),
                "--key",
                str(key_path),
                "--publisher-ref",
                PUBLISHER,
                "--key-id",
                KEY_ID,
            ]
        )
        == 2
    )


def test_private_key_material_never_reaches_envelope_or_stderr(tmp_path, capsys) -> None:
    private_key, public_pem = _keypair()
    key_path = tmp_path / "signing-key.pem"
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path.write_bytes(key_pem)
    profile_dir = write_profile(tmp_path / "cli")
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw["publisher_ref"] = PUBLISHER
    raw["signature_ref"] = ENVELOPE_NAME
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    sign_argv = [
        "sign",
        "--manifest",
        str(manifest_path),
        "--key",
        str(key_path),
        "--publisher-ref",
        PUBLISHER,
        "--key-id",
        KEY_ID,
    ]
    assert sign_main(sign_argv) == 0
    envelope_text = (profile_dir / ENVELOPE_NAME).read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in envelope_text
    captured = capsys.readouterr()
    assert key_pem.decode("ascii").strip() not in captured.out + captured.err


@pytest.mark.parametrize("change", ["tamper", "remove"])
def test_verify_cli_checks_manifest_file_content(tmp_path, change) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy_path = _policy(tmp_path, public_pem)
    entry_path = profile_dir / "files" / ENTRY_NAME
    if change == "tamper":
        entry_path.write_bytes(b"changed after signing")
    else:
        entry_path.unlink()
    assert sign_main(["verify", "--profile-dir", str(profile_dir), "--trust-policy", str(policy_path)]) == 2


def _sign_args(tmp_path: Path, *, signature_ref: str = ENVELOPE_NAME) -> tuple[list[str], Path, Path]:
    private_key, _public_pem = _keypair()
    key_path = tmp_path / "private.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    profile_dir = write_profile(tmp_path / "sign-cli")
    manifest_path = profile_dir / "agent-manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw.update(publisher_ref=PUBLISHER, signature_ref=signature_ref)
    manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    return (
        [
            "sign",
            "--manifest",
            str(manifest_path),
            "--key",
            str(key_path),
            "--publisher-ref",
            PUBLISHER,
            "--key-id",
            KEY_ID,
        ],
        profile_dir,
        manifest_path,
    )


def test_sign_cli_uses_declared_signature_ref(tmp_path) -> None:
    args, profile_dir, _manifest_path = _sign_args(tmp_path, signature_ref="signatures/current.json")
    (profile_dir / "signatures").mkdir()
    assert sign_main(args) == 0
    assert (profile_dir / "signatures/current.json").is_file()
    assert not (profile_dir / ENVELOPE_NAME).exists()


def test_sign_cli_rejects_mismatched_output(tmp_path) -> None:
    args, profile_dir, _manifest_path = _sign_args(tmp_path)
    unrelated = tmp_path / "unrelated.json"
    assert sign_main([*args, "--out", str(unrelated)]) == 2
    assert not unrelated.exists()
    assert not (profile_dir / ENVELOPE_NAME).exists()


def test_sign_cli_cannot_replace_the_manifest(tmp_path) -> None:
    args, _profile_dir, manifest_path = _sign_args(tmp_path, signature_ref="agent-manifest.json")
    original = manifest_path.read_bytes()
    assert sign_main(args) == 2
    assert manifest_path.read_bytes() == original


@pytest.mark.parametrize("existing", ["private", "public"])
def test_generate_cli_preserves_existing_key_files(tmp_path, existing) -> None:
    private_path, public_path = tmp_path / "private.pem", tmp_path / "public.pem"
    protected = private_path if existing == "private" else public_path
    protected.write_bytes(b"existing key material")
    assert sign_main(["generate", "--out-private-key", str(private_path), "--out-public-key", str(public_path)]) == 2
    assert protected.read_bytes() == b"existing key material"
    assert not (public_path if existing == "private" else private_path).exists()


def test_generate_cli_rejects_identical_output_paths(tmp_path) -> None:
    path = tmp_path / "same.pem"
    assert sign_main(["generate", "--out-private-key", str(path), "--out-public-key", str(path)]) == 2
    assert not path.exists()


@pytest.mark.parametrize("document", ["manifest", "signature", "policy"])
def test_duplicate_json_members_refuse_strict_startup(tmp_path, _shutdown_env, document) -> None:
    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy_path = _policy(tmp_path, public_pem)
    if document == "policy":
        text = policy_path.read_text(encoding="utf-8").replace('"revoked": false', '"revoked": true, "revoked": false')
        policy_path.write_text(text, encoding="utf-8")
        with pytest.raises(ArtifactAdmissionError, match="duplicate"):
            load_trust_policy(policy_path)
    else:
        path = profile_dir / ("agent-manifest.json" if document == "manifest" else ENVELOPE_NAME)
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:-1] + ', "publisher_ref": ' + json.dumps(PUBLISHER) + "}", encoding="utf-8")
        policy = load_trust_policy(policy_path)
        with pytest.raises(ProfileError, match="duplicate"):
            load_profile(profile_dir, artifact_trust=policy)


def test_nonfinite_manifest_is_not_signable(tmp_path) -> None:
    private_key, _public_pem = _keypair()
    profile_dir = write_profile(tmp_path)
    raw = json.loads((profile_dir / "agent-manifest.json").read_text(encoding="utf-8"))
    raw.update(publisher_ref=PUBLISHER, signature_ref=ENVELOPE_NAME, backend_ns={"invalid": float("nan")})
    with pytest.raises(ArtifactAdmissionError, match="finite"):
        sign_manifest(
            raw,
            private_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
            publisher_ref=PUBLISHER,
            key_id=KEY_ID,
        )


@pytest.mark.parametrize("unreadable", ["policy", "signature", "entry"])
def test_unreadable_artifact_inputs_return_refusal_before_initialization(
    tmp_path, _shutdown_env, monkeypatch, unreadable
) -> None:
    from hecate_runner import __main__ as runner_main

    private_key, public_pem = _keypair()
    profile_dir = _signed_profile(tmp_path, private_key)
    policy_path = _policy(tmp_path, public_pem)
    blocked_path = {
        "policy": policy_path,
        "signature": profile_dir / ENVELOPE_NAME,
        "entry": profile_dir / "files" / ENTRY_NAME,
    }[unreadable]
    real_read_bytes, real_read_text = Path.read_bytes, Path.read_text

    def read_bytes(path, *args, **kwargs):
        if path == blocked_path:
            raise PermissionError("unreadable test file")
        return real_read_bytes(path, *args, **kwargs)

    def read_text(path, *args, **kwargs):
        if path == blocked_path:
            raise PermissionError("unreadable test file")
        return real_read_text(path, *args, **kwargs)

    def initialization_forbidden(*_args, **_kwargs):
        pytest.fail("refusal must precede execution resource initialization")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)
    for resource in ("RunnerServer", "ExecutionEngine", "EvidenceStore", "BusinessApiToolDispatcher", "DurableRuntime"):
        monkeypatch.setattr(runner_main, resource, initialization_forbidden)
    assert runner_main.main(["--profile", str(profile_dir), "--artifact-trust", str(policy_path)]) == 2


def test_tool_schema_uses_the_verified_file_snapshot(tmp_path, _shutdown_env, monkeypatch) -> None:
    private_key, public_pem = _keypair()
    profile_dir = write_profile(tmp_path)
    schema_name = "query.schema.json"
    schema_path = profile_dir / "files" / schema_name
    schema = {"type": "object", "required": ["sku"]}
    schema_bytes = json.dumps(schema).encode("utf-8")
    schema_path.write_bytes(schema_bytes)
    manifest_path = profile_dir / "agent-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append({"path": schema_name, "sha256": sha256_hex(schema_bytes), "size": len(schema_bytes)})
    manifest["tools"] = [{"name": "query_inventory", "permission": "read", "schema_ref": schema_name}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _declare_and_sign(profile_dir, private_key)
    policy = load_trust_policy(_policy(tmp_path, public_pem))
    reads = 0
    real_read_bytes = Path.read_bytes

    def read_then_replace(path):
        nonlocal reads
        content = real_read_bytes(path)
        if path == schema_path:
            reads += 1
            path.write_text('{"type":"object"}', encoding="utf-8")
        return content

    monkeypatch.setattr(Path, "read_bytes", read_then_replace)
    profile = load_profile(profile_dir, artifact_trust=policy)
    assert reads == 1
    assert profile.tool_schemas["query_inventory"] == schema
