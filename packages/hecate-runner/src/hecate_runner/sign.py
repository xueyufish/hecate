"""Offline signing command: ``hecate-runner-sign`` / ``python -m hecate_runner.sign``.

Operator tooling for the artifact trust admission: generate an Ed25519
keypair, sign an execution manifest (which must already declare the matching
``publisher_ref`` and a ``signature_ref`` pointing at the output path), and
preflight-verify a profile directory against a trust policy without starting
the runner. Private keys are read from operator-provided files and are never
written back, logged, or embedded in any output artifact.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

from .artifact_signing import (
    ArtifactAdmissionError,
    generate_keypair,
    load_trust_policy,
    read_artifact_json,
    resolve_signature_path,
    sign_manifest,
)
from .profile import ProfileError, verify_profile_artifact


def _read_manifest(manifest_path: Path) -> dict:
    return read_artifact_json(manifest_path, "agent manifest")


def _write_text(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ArtifactAdmissionError(f"cannot write {path}: {exc}") from exc


def cmd_generate(args: argparse.Namespace) -> int:
    """Create a fresh keypair without replacing either existing output."""

    private_path, public_path = args.out_private_key, args.out_public_key
    if private_path.resolve() == public_path.resolve():
        raise ArtifactAdmissionError("private and public key outputs must be different paths")
    if any(path.exists() or path.is_symlink() for path in (private_path, public_path)):
        raise ArtifactAdmissionError("key output already exists; use fresh paths to generate a new keypair")
    private_pem, public_pem = generate_keypair()
    created: list[Path] = []
    try:
        for path, content, mode in ((private_path, private_pem, 0o600), (public_path, public_pem, 0o644)):
            # Exclusive creation also protects against an output appearing
            # after the preflight; the private key is never written world-readable.
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
            created.append(path)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
    except OSError as exc:
        for path in created:
            with contextlib.suppress(OSError):
                path.unlink()
        raise ArtifactAdmissionError("cannot create keypair; existing key files are never overwritten") from exc
    print(f"hecate-runner-sign: wrote private key {args.out_private_key} and public key {args.out_public_key}")
    print(
        "Keep the private key offline and out of the artifact; publish the public key "
        "inside the host's artifact trust policy."
    )
    return 0


def _read_private_key(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ArtifactAdmissionError(f"cannot read signing key {path}: {exc}") from exc


def cmd_sign(args: argparse.Namespace) -> None:
    """Sign into the declared local envelope path without replacing source files."""

    raw = _read_manifest(args.manifest)
    declared = raw.get("publisher_ref")
    if not isinstance(declared, str) or not declared:
        raise ArtifactAdmissionError(
            "manifest declares no publisher_ref; set publisher_ref and signature_ref before signing"
        )
    if not isinstance(raw.get("signature_ref"), str) or not raw["signature_ref"]:
        raise ArtifactAdmissionError(
            "manifest declares no signature_ref; point it at the signature output path before signing"
        )
    private_pem = _read_private_key(args.key)
    envelope = sign_manifest(raw, private_pem, publisher_ref=args.publisher_ref, key_id=args.key_id)
    out_path = resolve_signature_path(args.manifest.parent, raw["signature_ref"], require_file=False)
    if args.out is not None and args.out.resolve() != out_path.resolve():
        raise ArtifactAdmissionError("--out must match the manifest's profile-relative signature_ref")
    protected = [args.manifest, args.key, args.manifest.parent / "runner.json", args.manifest.parent / "identity.json"]
    protected.extend(args.manifest.parent / "files" / entry["path"] for entry in raw["files"])
    if any(
        out_path.resolve() == path.resolve() or (out_path.exists() and path.exists() and out_path.samefile(path))
        for path in protected
    ):
        raise ArtifactAdmissionError(
            "signature output must not replace the manifest, signing key, host config or files"
        )
    _write_text(out_path, json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    print(f"hecate-runner-sign: wrote signature envelope {out_path}")
    print(f"hecate-runner-sign: manifest digest {envelope['manifest_sha256']}")


def cmd_verify(args: argparse.Namespace) -> int:
    """Check the publisher and all listed file contents without loading host credentials."""

    profile_dir = args.profile_dir.resolve()
    policy = load_trust_policy(args.trust_policy)
    verified = verify_profile_artifact(profile_dir, policy)
    print(
        f"hecate-runner-sign: admission verified publisher={verified.publisher_ref} "
        f"key={verified.key_id} manifest_sha256={verified.manifest_sha256}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run offline signing commands with stable refusal exit codes."""

    parser = argparse.ArgumentParser(
        prog="hecate-runner-sign",
        description="Offline Ed25519 signing and admission preflight for Hecate runner artifacts",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="generate one Ed25519 keypair (private PEM, public PEM)")
    generate.add_argument("--out-private-key", required=True, type=Path)
    generate.add_argument("--out-public-key", required=True, type=Path)
    generate.set_defaults(func=cmd_generate)

    sign = sub.add_parser("sign", help="sign agent-manifest.json into a signature envelope")
    sign.add_argument("--manifest", required=True, type=Path, help="path to agent-manifest.json")
    sign.add_argument("--key", required=True, type=Path, help="PEM file with the Ed25519 private key")
    sign.add_argument("--publisher-ref", required=True, help="publisher identity; must match the manifest")
    sign.add_argument("--key-id", required=True, help="key identifier recorded in the envelope")
    sign.add_argument(
        "--out",
        type=Path,
        default=None,
        help="signature output path; must match signature_ref (default: the manifest's declared signature_ref)",
    )
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify", help="preflight strict admission for a profile directory")
    verify.add_argument("--profile-dir", required=True, type=Path)
    verify.add_argument("--trust-policy", required=True, type=Path)
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    try:
        result = args.func(args) or 0
    except (ArtifactAdmissionError, ProfileError) as exc:
        print(f"hecate-runner-sign: {exc}", file=sys.stderr)
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
