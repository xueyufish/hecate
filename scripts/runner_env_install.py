"""Install a verified offline Runner delivery using only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

MANIFEST_FORMAT_VERSION = 1
WORKSPACE_PACKAGES = frozenset({"hecate-runtime", "hecate-governance", "hecate-durable", "hecate-runner"})
WHEEL_FILENAME = re.compile(
    r"(?P<name>[A-Za-z0-9_.]+)-(?P<version>[A-Za-z0-9_.!+]+)(?:-[0-9][A-Za-z0-9_]*)?"
    r"-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PIN = re.compile(
    r"(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9_.!+]+)"
    r"(?:\s*;\s*(?P<marker>[\w .!<>=~*'\"()+-]+))?"
)
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


class InstallError(RuntimeError):
    """Offline installation refused or failed."""


def canonical_name(name: str) -> str:
    """Normalize a distribution name for comparison."""
    return re.sub(r"[-_.]+", "-", name).lower()


def manifest_digest(manifest: dict) -> str:
    """Compute the digest without its embedded copy."""
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file without loading it into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_verified_manifest(delivery: Path, expected_digest: str) -> dict:
    """Check the manifest against the digest from the operator's trusted channel."""
    try:
        manifest = json.loads((delivery / "environment.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise InstallError(f"unreadable delivery manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise InstallError("delivery manifest must be a JSON object")
    recorded = manifest.get("manifest_sha256")
    if not isinstance(recorded, str) or manifest_digest(manifest) != recorded:
        raise InstallError("delivery manifest digest does not match its own content")
    expected_digest = expected_digest.strip().lower()
    if not _SHA256.fullmatch(expected_digest) or not hmac.compare_digest(recorded, expected_digest):
        raise InstallError("manifest digest does not match the operator-provided digest")
    if manifest.get("format_version") != MANIFEST_FORMAT_VERSION:
        raise InstallError(f"unsupported manifest format_version: {manifest.get('format_version')!r}")
    return manifest


def _check_target_compatibility(manifest: dict) -> None:
    python_target = f"{sys.version_info.major}.{sys.version_info.minor}"
    if python_target != manifest.get("python_target"):
        raise InstallError(
            f"delivery targets Python {manifest.get('python_target')!r}; this interpreter is {python_target}"
        )
    current_platform = f"{sys.platform}:{platform.machine()}"
    if current_platform != manifest.get("platform"):
        raise InstallError(
            f"delivery targets platform {manifest.get('platform')!r}; this machine is {current_platform!r}"
        )

    if sys.implementation.name != manifest.get("python_implementation") or sysconfig.get_config_var(
        "SOABI"
    ) != manifest.get("python_abi"):
        raise InstallError("delivery targets a different Python implementation or ABI")


def _audit_requirements_lock(lock_path: Path, manifest: dict) -> set[tuple[str, str, str]]:
    data = lock_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest.get("requirements_lock_sha256"):
        raise InstallError("requirements.lock does not match the manifest digest")

    # Only accept the pin + hash format emitted by uv. Never pass the delivered
    # requirements file (including its marker expressions) to the installer.
    pins: set[tuple[str, str, str]] = set()
    block: list[str] = []
    for raw_line in data.decode("utf-8").splitlines():
        line = raw_line.strip()
        if not block and (not line or line.startswith("#")):
            continue
        block.append(line.removesuffix("\\").rstrip())
        if line.endswith("\\"):
            continue
        pin = _PIN.fullmatch(block[0])
        if pin is None or re.search(r"\s--", pin["marker"] or ""):
            raise InstallError("requirements.lock contains a URL, source option, or invalid pin")
        if len(block) == 1:
            raise InstallError("requirements.lock requirement carries no sha256 hash")
        for value in block[1:]:
            digest = _HASH.fullmatch(value)
            if digest is None:
                raise InstallError("requirements.lock contains an invalid sha256 continuation")
            pins.add((canonical_name(pin["name"]), pin["version"], digest[1]))
        block.clear()
    if block:
        raise InstallError("requirements.lock ends with an unfinished continuation")
    return pins


def _audit_wheelhouse(
    wheelhouse: Path, snapshot: Path, manifest: dict, pins: set[tuple[str, str, str]]
) -> tuple[list[str], dict[str, str]]:
    wheels = manifest.get("wheels")
    if not isinstance(wheels, list) or not wheels:
        raise InstallError("manifest lists no wheels")
    present = {path.name: path for path in wheelhouse.iterdir()}
    requirements: list[str] = []
    versions: dict[str, str] = {}
    for entry in wheels:
        if not isinstance(entry, dict):
            raise InstallError("invalid manifest wheel entry")
        filename, digest = entry.get("filename"), entry.get("sha256")
        match = WHEEL_FILENAME.fullmatch(filename) if isinstance(filename, str) else None
        if match is None or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise InstallError("invalid manifest wheel filename or digest")
        name, version = canonical_name(match["name"]), match["version"]
        if entry.get("name") != match["name"].lower() or entry.get("version") != version:
            raise InstallError(f"manifest name or version disagrees with wheel filename: {filename!r}")
        if name == "hecate" or name in versions:
            raise InstallError(f"unexpected or duplicate distribution in wheelhouse: {name!r}")
        path = present.pop(filename, None)
        if path is None or path.is_symlink() or not path.is_file():
            raise InstallError(f"wheelhouse is missing manifest-listed wheels or has a non-regular entry: {filename!r}")
        local_wheel = snapshot / path.name
        shutil.copyfile(path, local_wheel)
        if sha256_file(local_wheel) != digest:
            raise InstallError(f"wheel content does not match the manifest digest: {filename!r}")
        if name not in WORKSPACE_PACKAGES and (name, version, digest) not in pins:
            raise InstallError(f"third-party wheel is absent from the pinned lock: {filename!r}")
        versions[name] = version
        requirements.append(f"{local_wheel.as_uri()} --hash=sha256:{digest}")
    if present:
        raise InstallError(f"wheelhouse carries files the manifest does not list: {sorted(present)!r}")
    if missing := WORKSPACE_PACKAGES.difference(versions):
        raise InstallError(f"delivery is missing workspace wheels: {sorted(missing)!r}")
    return requirements, versions


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(  # noqa: S603 - fixed operator-invoked tooling
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"{Path(command[0]).name} failed: {(result.stderr or result.stdout)[-3000:]}")
    return result.stdout


def install_environment(delivery: Path, expected_digest: str, target: Path) -> Path:
    """Verify the delivery and install into a new, exclusively reserved venv."""
    delivery, target = delivery.absolute(), target.absolute()
    if target.exists() or target.is_symlink():
        raise InstallError(f"target environment already exists: {target}")
    manifest = load_verified_manifest(delivery, expected_digest)
    _check_target_compatibility(manifest)
    pins = _audit_requirements_lock(delivery / "requirements.lock", manifest)
    # pip must consume the verified bytes even if the delivery is later changed.
    with tempfile.TemporaryDirectory(prefix="runner-env-wheels-") as snapshot:
        requirements, versions = _audit_wheelhouse(delivery / "wheelhouse", Path(snapshot), manifest, pins)
        _install_verified_wheels(target, requirements, versions)
    return target


def _install_verified_wheels(target: Path, requirements: list[str], versions: dict[str, str]) -> None:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith(("PIP_", "PYTHON"))}
    env["PIP_CONFIG_FILE"] = os.devnull

    # Venv activation scripts and console launchers embed the final path.
    # Reserve it exclusively and remove it on failure rather than relocate it.
    try:
        target.mkdir(parents=True)
    except FileExistsError as exc:
        raise InstallError(f"target environment already exists: {target}") from exc
    try:
        _run([sys.executable, "-I", "-m", "venv", str(target)], cwd=target, env=env)
        venv_python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        pip = [str(venv_python), "-I", "-m", "pip", "--disable-pip-version-check", "--no-cache-dir"]
        install_lock = target / "install.lock"
        install_lock.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        # Resolve only the exact verified local files. Dependency metadata can
        # carry direct URLs, so check dependencies after installation instead.
        _run(
            [
                *pip,
                "install",
                "--no-index",
                "--no-deps",
                "--only-binary=:all:",
                "--require-hashes",
                "-r",
                str(install_lock),
            ],
            cwd=target,
            env=env,
        )
        listing = json.loads(_run([*pip, "list", "--format", "json"], cwd=target, env=env))
        installed = {canonical_name(entry["name"]): entry["version"] for entry in listing}
        # venv seeds pip independently of the Runner dependency lock.
        if "pip" not in versions:
            installed.pop("pip", None)
        if installed != versions:
            raise InstallError("installed distribution set does not match the verified wheelhouse")
        _run([*pip, "check"], cwd=target, env=env)
        install_lock.unlink()
    except (Exception, KeyboardInterrupt):
        shutil.rmtree(target)
        raise


def main(argv: list[str] | None = None) -> int:
    """Run the standalone installer CLI."""
    parser = argparse.ArgumentParser(
        prog="runner-env-install", description="Verify a Runner delivery and install it into a new offline environment"
    )
    parser.add_argument("--delivery", required=True, type=Path, help="prepared delivery directory")
    parser.add_argument(
        "--expected-manifest-sha256", required=True, help="manifest digest received over an independent trusted channel"
    )
    parser.add_argument("--target", required=True, type=Path, help="new environment path (must not exist)")
    args = parser.parse_args(argv)
    try:
        target = install_environment(args.delivery, args.expected_manifest_sha256, args.target)
    except (InstallError, OSError, ValueError) as exc:
        print(f"runner-env-install: {exc}", file=sys.stderr)
        return 2
    print(f"runner-env-install: environment installed at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
