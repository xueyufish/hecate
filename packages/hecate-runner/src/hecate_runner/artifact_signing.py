"""Runner artifact signing and trusted-publisher admission (offline, Ed25519).

The execution manifest may declare a ``publisher_ref`` and a
``signature_ref``. The signature reference names a constrained local file
(inside the profile directory, no symlinks, no URL) holding a versioned
signature envelope over the canonical manifest bytes. Trust is NEVER
established from the artifact itself: the operator supplies an
``--artifact-trust`` policy naming the trusted publishers, their Ed25519
public keys and any revocations, and the runner refuses to start unless the
declared publisher and key are present and unrevoked and the signature
verifies over the exact manifest content.

Fixed first-version parameters: Ed25519 signatures, signature envelope
version 1, and a deterministic signature payload (canonical JSON). The
signature binds the complete manifest — file digests, tool permissions,
entry and publisher — through the manifest's canonical form.

Boundaries: only the manifest and its listed files are covered. The host
configuration (``runner.json``), the identity trust material
(``identity.json``) and the artifact trust policy remain operator-owned
host configuration outside the signature. Admission is a startup-time
decision; it grants no runtime privilege and does not re-evaluate while
the runner is serving.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from hecate_runtime.manifest import ManifestError, sha256_hex, validate_manifest_dict

SIGNATURE_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
_SIGNATURE_CONTEXT = "hecate-runner/artifact-signature"
_MAX_IDENTIFIER = 256


class ArtifactAdmissionError(Exception):
    """Strict artifact admission refused, or signing/trust-policy input invalid."""


@dataclass(frozen=True)
class SignatureEnvelope:
    """Parsed signature envelope (schema fixed by ``SIGNATURE_VERSION``)."""

    version: int
    algorithm: str
    publisher_ref: str
    key_id: str
    manifest_sha256: str
    signature: bytes


@dataclass(frozen=True)
class PublisherTrust:
    """One publisher's trusted keys and revocation facts (operator-owned)."""

    keys: dict[str, Ed25519PublicKey]
    revoked: bool
    revoked_key_ids: frozenset[str]


@dataclass(frozen=True)
class ArtifactTrustPolicy:
    """The operator-provided trust root for strict artifact admission."""

    path: Path
    publishers: dict[str, PublisherTrust]


@dataclass(frozen=True)
class VerifiedArtifact:
    """The recorded admission decision facts for one startup."""

    publisher_ref: str
    key_id: str
    manifest_sha256: str


def _valid_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_IDENTIFIER
        and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        and value == value.strip()
    )


def _identifier_error(value: object, label: str) -> str | None:
    if _valid_identifier(value):
        return None
    return f"{label} must be a non-empty printable identifier without control characters"


def canonical_manifest_bytes(raw_manifest: dict) -> bytes:
    """The deterministic byte form a signature is computed over."""

    try:
        return json.dumps(
            raw_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ArtifactAdmissionError("manifest must contain only finite JSON values and valid Unicode") from exc


def read_artifact_json(path: Path, label: str) -> dict:
    """Read an unambiguous UTF-8 object for signatures, manifests or trust policies."""

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ArtifactAdmissionError(f"{label} contains duplicate JSON members")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ArtifactAdmissionError(f"{label} contains a non-finite JSON number")

    try:
        data = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except (OSError, ValueError, UnicodeError) as exc:
        raise ArtifactAdmissionError(f"unreadable {label}") from exc
    if not isinstance(data, dict):
        raise ArtifactAdmissionError(f"{label} must be a JSON object")
    return data


def signature_payload(
    *,
    version: int,
    algorithm: str,
    publisher_ref: str,
    key_id: str,
    manifest_sha256: str,
) -> bytes:
    """The exact bytes an Ed25519 key signs: canonical JSON of the binding block.

    Binding the envelope metadata (version, algorithm, publisher, key id)
    next to the manifest digest makes every envelope field tamper-evident.
    """

    signed = {
        "context": _SIGNATURE_CONTEXT,
        "version": version,
        "algorithm": algorithm,
        "publisher_ref": publisher_ref,
        "key_id": key_id,
        "manifest_sha256": manifest_sha256,
    }
    return json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_signature_envelope(data: object) -> SignatureEnvelope:
    """Validate the envelope structure strictly; unknown fields are refused."""

    if not isinstance(data, dict):
        raise ArtifactAdmissionError("signature envelope must be a JSON object")
    allowed = {"signature_version", "algorithm", "publisher_ref", "key_id", "manifest_sha256", "signature"}
    unknown = data.keys() - allowed
    if unknown:
        raise ArtifactAdmissionError(f"signature envelope has unknown fields: {sorted(unknown)}")
    missing = allowed - data.keys()
    if missing:
        raise ArtifactAdmissionError(f"signature envelope is missing fields: {sorted(missing)}")
    version = data["signature_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ArtifactAdmissionError("signature_version must be a positive integer")
    if not isinstance(data["algorithm"], str) or not data["algorithm"]:
        raise ArtifactAdmissionError("algorithm must be a non-empty string")
    for label in ("publisher_ref", "key_id"):
        error = _identifier_error(data[label], f"signature envelope {label}")
        if error:
            raise ArtifactAdmissionError(error)
    if not isinstance(data["manifest_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["manifest_sha256"]):
        raise ArtifactAdmissionError("signature envelope manifest_sha256 must be a lowercase hex sha256")
    if not isinstance(data["signature"], str):
        raise ArtifactAdmissionError("signature must be a base64 string")
    try:
        signature = base64.b64decode(data["signature"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtifactAdmissionError("signature must be valid base64") from exc
    if len(signature) != 64:
        raise ArtifactAdmissionError("ed25519 signature must be 64 bytes")
    return SignatureEnvelope(
        version=version,
        algorithm=data["algorithm"],
        publisher_ref=data["publisher_ref"],
        key_id=data["key_id"],
        manifest_sha256=data["manifest_sha256"],
        signature=signature,
    )


def resolve_signature_path(profile_dir: Path, signature_ref: str, *, require_file: bool = True) -> Path:
    """Resolve ``signature_ref`` to a constrained local file under the profile.

    Absolute paths, drive letters, backslashes, NUL bytes, empty/``.``/``..``
    segments and any symlink component are refused — the reference can only
    name a regular file inside the profile directory, never a URL or a path
    that escapes it.
    """

    if (
        not isinstance(signature_ref, str)
        or not signature_ref
        or signature_ref.startswith("/")
        or "\\" in signature_ref
        or ":" in signature_ref
        or "\x00" in signature_ref
    ):
        raise ArtifactAdmissionError(
            f"signature_ref must be a profile-relative path without a URL scheme: {signature_ref!r}"
        )
    segments = signature_ref.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ArtifactAdmissionError(f"signature_ref must not traverse directories: {signature_ref!r}")
    current = profile_dir.resolve()
    for segment in segments:
        current = current / segment
        if current.is_symlink() or current.is_junction():
            raise ArtifactAdmissionError(f"signature_ref must not traverse symlinks: {signature_ref!r}")
    # Keep a containment check in addition to refusing known link types.
    if not current.resolve().is_relative_to(profile_dir.resolve()):
        raise ArtifactAdmissionError(f"signature_ref resolves outside the profile directory: {signature_ref!r}")
    if (require_file or current.exists()) and not current.is_file():
        raise ArtifactAdmissionError(f"signature_ref does not resolve to a file: {signature_ref!r}")
    return current


def _load_public_key(key_id: str, pem: object) -> Ed25519PublicKey:
    if not isinstance(pem, str) or "-----BEGIN PUBLIC KEY-----" not in pem:
        raise ArtifactAdmissionError(f"trust policy key {key_id!r} public_key must be PEM SPKI text")
    try:
        key = load_pem_public_key(pem.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise ArtifactAdmissionError(f"trust policy key {key_id!r} public_key is not parsable PEM") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ArtifactAdmissionError(f"trust policy key {key_id!r} must be an Ed25519 public key")
    return key


def load_trust_policy(policy_path: Path) -> ArtifactTrustPolicy:
    """Parse and validate the operator-provided artifact trust policy."""

    if not policy_path.is_file():
        raise ArtifactAdmissionError(f"artifact trust policy not found: {policy_path}")
    data = read_artifact_json(policy_path, "artifact trust policy")
    allowed_top_level = {"artifact_trust_version", "publishers"}
    if data.keys() - allowed_top_level:
        raise ArtifactAdmissionError(
            f"artifact trust policy has unknown fields: {sorted(data.keys() - allowed_top_level)}"
        )
    if data.get("artifact_trust_version") != "1":
        raise ArtifactAdmissionError("artifact_trust_version must be '1'")
    publishers_raw = data.get("publishers")
    if not isinstance(publishers_raw, dict):
        raise ArtifactAdmissionError("artifact trust policy must carry a 'publishers' object")
    publishers: dict[str, PublisherTrust] = {}
    for publisher_ref, entry in publishers_raw.items():
        error = _identifier_error(publisher_ref, "trust policy publisher_ref")
        if error:
            raise ArtifactAdmissionError(error)
        if not isinstance(entry, dict):
            raise ArtifactAdmissionError(f"trust policy entry for {publisher_ref!r} must be an object")
        unknown = entry.keys() - {"keys", "revoked", "revoked_key_ids"}
        if unknown:
            raise ArtifactAdmissionError(
                f"trust policy entry for {publisher_ref!r} has unknown fields: {sorted(unknown)}"
            )
        keys_raw = entry.get("keys")
        if not isinstance(keys_raw, list) or not keys_raw or not all(isinstance(k, dict) for k in keys_raw):
            raise ArtifactAdmissionError(
                f"trust policy entry for {publisher_ref!r} must carry a non-empty 'keys' array"
            )
        keys: dict[str, Ed25519PublicKey] = {}
        for key_entry in keys_raw:
            unknown_key_fields = key_entry.keys() - {"key_id", "public_key"}
            if unknown_key_fields:
                raise ArtifactAdmissionError(
                    f"trust policy key entry for {publisher_ref!r} has unknown fields: {sorted(unknown_key_fields)}"
                )
            error = _identifier_error(key_entry.get("key_id"), f"trust policy key_id for {publisher_ref!r}")
            if error:
                raise ArtifactAdmissionError(error)
            key_id = key_entry["key_id"]
            if key_id in keys:
                raise ArtifactAdmissionError(f"trust policy lists duplicate key_id {key_id!r} for {publisher_ref!r}")
            keys[key_id] = _load_public_key(key_id, key_entry.get("public_key"))
        revoked = entry.get("revoked", False)
        if not isinstance(revoked, bool):
            raise ArtifactAdmissionError(f"trust policy 'revoked' for {publisher_ref!r} must be a boolean")
        revoked_raw = entry.get("revoked_key_ids", [])
        if not isinstance(revoked_raw, list) or not all(isinstance(k, str) and k for k in revoked_raw):
            raise ArtifactAdmissionError(
                f"trust policy 'revoked_key_ids' for {publisher_ref!r} must be an array of key ids"
            )
        unknown_revocations = set(revoked_raw) - set(keys)
        if unknown_revocations:
            raise ArtifactAdmissionError(
                f"trust policy revokes key ids not present for {publisher_ref!r}: {sorted(unknown_revocations)}"
            )
        publishers[publisher_ref] = PublisherTrust(keys=keys, revoked=revoked, revoked_key_ids=frozenset(revoked_raw))
    return ArtifactTrustPolicy(path=policy_path, publishers=publishers)


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate one Ed25519 keypair; returns (private PEM, public PEM)."""

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def sign_manifest(
    raw_manifest: dict,
    private_key_pem: bytes,
    *,
    publisher_ref: str,
    key_id: str,
) -> dict:
    """Produce the signature envelope dict for one manifest.

    The manifest must already declare the same ``publisher_ref`` — the
    publisher identity is part of the signed content, never an envelope-only
    claim. The private key bytes are consumed and never stored or logged.
    """

    error = _identifier_error(publisher_ref, "publisher_ref")
    if error:
        raise ArtifactAdmissionError(error)
    error = _identifier_error(key_id, "key_id")
    if error:
        raise ArtifactAdmissionError(error)
    try:
        manifest = validate_manifest_dict(raw_manifest)
    except ManifestError as exc:
        raise ArtifactAdmissionError(f"refusing to sign an invalid manifest: {exc}") from exc
    if manifest.publisher_ref != publisher_ref:
        raise ArtifactAdmissionError(
            f"manifest declares publisher_ref {manifest.publisher_ref!r}; refusing to sign as {publisher_ref!r}"
        )
    try:
        private_key = load_pem_private_key(private_key_pem, password=None)
    except (ValueError, TypeError) as exc:
        raise ArtifactAdmissionError("signing key is not parsable PEM") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ArtifactAdmissionError("signing key must be an Ed25519 private key")
    manifest_sha256 = sha256_hex(canonical_manifest_bytes(raw_manifest))
    payload = signature_payload(
        version=SIGNATURE_VERSION,
        algorithm=SIGNATURE_ALGORITHM,
        publisher_ref=publisher_ref,
        key_id=key_id,
        manifest_sha256=manifest_sha256,
    )
    signature = private_key.sign(payload)
    return {
        "signature_version": SIGNATURE_VERSION,
        "algorithm": SIGNATURE_ALGORITHM,
        "publisher_ref": publisher_ref,
        "key_id": key_id,
        "manifest_sha256": manifest_sha256,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_artifact_admission(
    raw_manifest: dict,
    profile_dir: Path,
    policy: ArtifactTrustPolicy,
) -> VerifiedArtifact:
    """Strict admission: verify publisher trust and signature over the manifest.

    Raises :class:`ArtifactAdmissionError` on every refusal path; the caller
    must treat any refusal as "do not start".
    """

    try:
        manifest = validate_manifest_dict(raw_manifest)
    except ManifestError as exc:
        raise ArtifactAdmissionError(f"invalid manifest: {exc}") from exc
    if not isinstance(manifest.publisher_ref, str) or not manifest.publisher_ref:
        raise ArtifactAdmissionError("manifest declares no publisher_ref; strict admission requires a signed publisher")
    if not isinstance(manifest.signature_ref, str) or not manifest.signature_ref:
        raise ArtifactAdmissionError("manifest declares no signature_ref; strict admission requires a signature")
    envelope_path = resolve_signature_path(profile_dir, manifest.signature_ref)
    envelope_data = read_artifact_json(envelope_path, "signature envelope")
    envelope = parse_signature_envelope(envelope_data)
    if envelope.version != SIGNATURE_VERSION:
        raise ArtifactAdmissionError(
            f"unsupported signature_version {envelope.version!r} (supported: {SIGNATURE_VERSION})"
        )
    if envelope.algorithm != SIGNATURE_ALGORITHM:
        raise ArtifactAdmissionError(
            f"unsupported signature algorithm {envelope.algorithm!r} (supported: {SIGNATURE_ALGORITHM})"
        )
    if envelope.publisher_ref != manifest.publisher_ref:
        raise ArtifactAdmissionError(
            f"signature envelope publisher {envelope.publisher_ref!r} does not match "
            f"manifest publisher_ref {manifest.publisher_ref!r}"
        )
    manifest_sha256 = sha256_hex(canonical_manifest_bytes(raw_manifest))
    if envelope.manifest_sha256 != manifest_sha256:
        raise ArtifactAdmissionError(
            "signed manifest digest does not match the profile manifest (manifest changed after signing)"
        )
    trust = policy.publishers.get(envelope.publisher_ref)
    if trust is None:
        raise ArtifactAdmissionError(f"publisher {envelope.publisher_ref!r} is not in the artifact trust policy")
    if trust.revoked:
        raise ArtifactAdmissionError(f"publisher {envelope.publisher_ref!r} is revoked in the artifact trust policy")
    if envelope.key_id not in trust.keys:
        raise ArtifactAdmissionError(
            f"signing key {envelope.key_id!r} is not trusted for publisher {envelope.publisher_ref!r}"
        )
    if envelope.key_id in trust.revoked_key_ids:
        raise ArtifactAdmissionError(
            f"signing key {envelope.key_id!r} is revoked for publisher {envelope.publisher_ref!r}"
        )
    payload = signature_payload(
        version=envelope.version,
        algorithm=envelope.algorithm,
        publisher_ref=envelope.publisher_ref,
        key_id=envelope.key_id,
        manifest_sha256=envelope.manifest_sha256,
    )
    try:
        trust.keys[envelope.key_id].verify(envelope.signature, payload)
    except InvalidSignature as exc:
        raise ArtifactAdmissionError("signature verification failed") from exc
    return VerifiedArtifact(
        publisher_ref=envelope.publisher_ref, key_id=envelope.key_id, manifest_sha256=manifest_sha256
    )
