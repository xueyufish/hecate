"""Deployment composition manifest: bind the signed artifact to the environment.

Unit coverage for the composition mechanics — generation, manifest and
receipt digest verification, and every cold-start refusal (wrong active
release, admission disagreement, foreign or missing receipt, environment
mismatch, package-set drift). The installed-set comparison is
monkeypatched here; the real wheelhouse → venv → cold-start chain lives
in the scenario suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import sysconfig
from importlib.metadata import distributions
from pathlib import Path

import pytest
from hecate_runner import deployment
from hecate_runner.deployment import (
    DeploymentError,
    generate_deployment_manifest,
    load_deployment_manifest,
    read_install_receipt,
    verify_deployment,
)

PUBLISHER = "publisher:acme"
ARTIFACT_SHA = "a" * 64
ENVIRONMENT_SHA = "b" * 64


def _receipt_document(root: Path, **overrides: object) -> dict:
    receipt = {
        "format_version": 1,
        "environment_manifest_sha256": ENVIRONMENT_SHA,
        "python_target": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_implementation": sys.implementation.name,
        "python_abi": sysconfig.get_config_var("SOABI"),
        "venv_path_sha256": hashlib.sha256(os.fsencode(os.path.normcase(str(root.resolve())))).hexdigest(),
        "packages": {"hecate-runner": "0.1.0", "jsonschema": "4.23.0"},
        **overrides,
    }
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return receipt


def _write_receipt(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "hecate-install-receipt.json"
    path.write_text(
        json.dumps(_receipt_document(tmp_path, **overrides), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def _manifest_document(tmp_path: Path, **artifact_overrides: object) -> dict:
    manifest = generate_deployment_manifest(
        artifact_manifest_sha256=ARTIFACT_SHA,
        publisher_ref=PUBLISHER,
        environment_manifest_sha256=ENVIRONMENT_SHA,
        receipt_sha256=_receipt_document(tmp_path)["receipt_sha256"],
    )
    manifest["artifact"].update(artifact_overrides)
    manifest["deployment_sha256"] = deployment._digest_without(manifest, "deployment_sha256")
    return manifest


def test_generate_refuses_malformed_identities() -> None:
    with pytest.raises(DeploymentError, match="lowercase hex sha256"):
        generate_deployment_manifest(
            artifact_manifest_sha256="short",
            publisher_ref=PUBLISHER,
            environment_manifest_sha256=ENVIRONMENT_SHA,
            receipt_sha256="c" * 64,
        )
    with pytest.raises(DeploymentError, match="publisher_ref"):
        generate_deployment_manifest(
            artifact_manifest_sha256=ARTIFACT_SHA,
            publisher_ref="  ",
            environment_manifest_sha256=ENVIRONMENT_SHA,
            receipt_sha256="c" * 64,
        )


def test_round_trip_generate_write_and_load(tmp_path: Path) -> None:
    receipt_path = _write_receipt(tmp_path)
    document = generate_deployment_manifest(
        artifact_manifest_sha256=ARTIFACT_SHA,
        publisher_ref=PUBLISHER,
        environment_manifest_sha256=ENVIRONMENT_SHA,
        receipt_sha256=read_install_receipt(receipt_path).digest,
    )
    manifest_path = tmp_path / "deployment.json"
    manifest_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    loaded = load_deployment_manifest(manifest_path, document["deployment_sha256"])
    assert loaded.artifact_manifest_sha256 == ARTIFACT_SHA
    assert loaded.publisher_ref == PUBLISHER
    assert loaded.environment_manifest_sha256 == ENVIRONMENT_SHA

    # Any other expected digest refuses: the composition is correlated to
    # the operator's channel, not just internally consistent.
    with pytest.raises(DeploymentError, match="operator-provided digest"):
        load_deployment_manifest(manifest_path, "f" * 64)


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param({"format_version": 2}, id="format"),
        pytest.param({"extra_field": True}, id="unknown-field"),
    ],
)
def test_load_refuses_an_altered_manifest(tmp_path: Path, tamper: dict) -> None:
    document = _manifest_document(tmp_path)
    document.update(tamper)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DeploymentError, match="digest does not match|format_version"):
        load_deployment_manifest(path, document.get("deployment_sha256", ""))


def test_receipt_round_trip_and_refusals(tmp_path: Path) -> None:
    _write_receipt(tmp_path, packages={"hecate-runner": "0.1.0"})
    receipt = read_install_receipt(tmp_path / deployment.RECEIPT_FILENAME)
    assert receipt.environment_manifest_sha256 == ENVIRONMENT_SHA
    assert receipt.packages == {"hecate-runner": "0.1.0"}

    # Missing, altered digest, and foreign environments all refuse.
    with pytest.raises(DeploymentError, match="missing or unreadable"):
        read_install_receipt(tmp_path / "missing.json")

    altered = _receipt_document(tmp_path)
    altered["packages"]["hecate-runner"] = "0.2.0"
    (tmp_path / "hecate-install-receipt.json").write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(DeploymentError, match="digest does not match"):
        read_install_receipt(tmp_path / deployment.RECEIPT_FILENAME)


def test_verify_refuses_a_wrong_active_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_receipt(tmp_path)
    manifest = load_deployment_manifest(_write_manifest(tmp_path), _manifest_document(tmp_path)["deployment_sha256"])
    monkeypatch.setattr(deployment, "installed_distribution_set", lambda _root: _receipt_document(tmp_path)["packages"])
    with pytest.raises(DeploymentError, match="activate the composed release"):
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256="e" * 64,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256="e" * 64,
            venv_root=tmp_path,
        )
    # The refusal names the composed and the selected identity.
    try:
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256="e" * 64,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256="e" * 64,
            venv_root=tmp_path,
        )
    except DeploymentError as exc:
        assert ARTIFACT_SHA[:12] in str(exc) and ("e" * 12) in str(exc)


def _write_manifest(tmp_path: Path) -> Path:
    document = _manifest_document(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_verify_refuses_admission_disagreement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_receipt(tmp_path)
    document = _manifest_document(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = load_deployment_manifest(path, document["deployment_sha256"])
    monkeypatch.setattr(deployment, "installed_distribution_set", lambda _root: _receipt_document(tmp_path)["packages"])
    # The pointer selects the composed release, but admission verified a
    # different publisher/digest: the composition no longer matches policy.
    with pytest.raises(DeploymentError, match="policy-verified admission"):
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256=ARTIFACT_SHA,
            admitted_publisher_ref="publisher:other",
            admitted_artifact_manifest_sha256="d" * 64,
            venv_root=tmp_path,
        )


def test_verify_refuses_a_foreign_environment_and_receipt_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = _manifest_document(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = load_deployment_manifest(path, document["deployment_sha256"])
    monkeypatch.setattr(deployment, "installed_distribution_set", lambda _root: _receipt_document(tmp_path)["packages"])

    # The running venv's receipt certifies a different environment.
    foreign = tmp_path / "venv-root"
    foreign.mkdir()
    _write_receipt(foreign, environment_manifest_sha256="f" * 64)
    with pytest.raises(DeploymentError, match="different environment"):
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256=ARTIFACT_SHA,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256=ARTIFACT_SHA,
            venv_root=foreign,
        )

    # Right environment binding, but the receipt was re-issued (its digest
    # no longer matches the composed receipt digest).
    rebuilt = _receipt_document(tmp_path, packages={"hecate-runner": "0.1.0", "extra-pkg": "9.9.9"})
    (tmp_path / "hecate-install-receipt.json").write_text(json.dumps(rebuilt), encoding="utf-8")
    with pytest.raises(DeploymentError, match="receipt digest"):
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256=ARTIFACT_SHA,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256=ARTIFACT_SHA,
            venv_root=tmp_path,
        )


def test_verify_refuses_package_set_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_receipt(tmp_path)
    document = _manifest_document(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = load_deployment_manifest(path, document["deployment_sha256"])
    drifted = _receipt_document(tmp_path)["packages"] | {"sneaky-extra": "1.0"}
    monkeypatch.setattr(deployment, "installed_distribution_set", lambda _root: drifted)
    with pytest.raises(DeploymentError, match="drifted") as excinfo:
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256=ARTIFACT_SHA,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256=ARTIFACT_SHA,
            venv_root=tmp_path,
        )
    assert "sneaky-extra" in str(excinfo.value)


def test_verify_accepts_the_matching_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_receipt(tmp_path)
    document = _manifest_document(tmp_path)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = load_deployment_manifest(path, document["deployment_sha256"])
    monkeypatch.setattr(deployment, "installed_distribution_set", lambda _root: _receipt_document(tmp_path)["packages"])
    # No exception: the composition matches the pointer, the admission
    # and the running environment's receipt and package set.
    verify_deployment(
        manifest,
        active_artifact_manifest_sha256=ARTIFACT_SHA,
        admitted_publisher_ref=PUBLISHER,
        admitted_artifact_manifest_sha256=ARTIFACT_SHA,
        venv_root=tmp_path,
    )


def test_installed_set_normalizes_distribution_names() -> None:
    # packaging normalizes -_. separators; the comparison must too.
    assert deployment.canonical_name("Hecate_Runtime") == "hecate-runtime"
    assert deployment.canonical_name("hecate.durable") == "hecate-durable"


def test_main_host_flag_pairs_are_required_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from hecate_runner.__main__ import main

    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "x")
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    # --deployment-manifest without --host refuses.
    argv = [
        "--profile",
        str(profile_dir),
        "--deployment-manifest",
        str(tmp_path / "d.json"),
        "--expected-deployment-sha256",
        "0" * 64,
    ]
    assert main(argv) == 2
    assert "use --host" in capsys.readouterr().err

    # One of the pair alone refuses once the policy itself parses (a valid
    # deny-all policy reaches the host branch, where pairing is checked).
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"artifact_trust_version": "1", "publishers": {}}), encoding="utf-8")
    assert (
        main(
            [
                "--host",
                str(tmp_path / "host"),
                "--artifact-trust",
                str(policy),
                "--deployment-manifest",
                str(tmp_path / "d.json"),
            ]
        )
        == 2
    )
    assert "required together" in capsys.readouterr().err
    assert (
        main(
            [
                "--host",
                str(tmp_path / "host"),
                "--artifact-trust",
                str(policy),
                "--expected-deployment-sha256",
                "0" * 64,
            ]
        )
        == 2
    )
    assert "required together" in capsys.readouterr().err


def test_python_target_recorded_in_receipt(tmp_path: Path) -> None:
    _write_receipt(tmp_path, packages={"x": "1"})
    receipt = json.loads((tmp_path / "hecate-install-receipt.json").read_text(encoding="utf-8"))
    assert receipt["python_target"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert receipt["environment_manifest_sha256"] == ENVIRONMENT_SHA


def test_verified_manifest_rejects_malformed_fields(tmp_path: Path) -> None:
    document = _manifest_document(tmp_path, manifest_sha256=None)
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(DeploymentError, match="artifact manifest digest"):
        load_deployment_manifest(path, document["deployment_sha256"])


def test_receipt_copied_to_another_environment_refuses(tmp_path: Path) -> None:
    receipt_path = _write_receipt(tmp_path)
    document = _manifest_document(tmp_path)
    manifest = load_deployment_manifest(_write_manifest(tmp_path), document["deployment_sha256"])
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / receipt_path.name).write_bytes(receipt_path.read_bytes())
    with pytest.raises(DeploymentError, match="different environment path"):
        verify_deployment(
            manifest,
            active_artifact_manifest_sha256=ARTIFACT_SHA,
            admitted_publisher_ref=PUBLISHER,
            admitted_artifact_manifest_sha256=ARTIFACT_SHA,
            venv_root=foreign,
        )


def test_installed_set_refuses_foreign_and_duplicate_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local = tmp_path / "venv" / "site-packages"
    foreign = tmp_path / "other" / "site-packages"
    for site in (local, foreign):
        metadata = site / "hecate_runner-0.1.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: hecate_runner\nVersion: 0.1.0\n", encoding="utf-8")
    local_dist = next(iter(distributions(path=[str(local)])))
    foreign_dist = next(iter(distributions(path=[str(foreign)])))
    monkeypatch.setattr(deployment, "distributions", lambda: [local_dist, foreign_dist])
    with pytest.raises(DeploymentError, match="outside the verified environment"):
        deployment.installed_distribution_set(local.parent)
    monkeypatch.setattr(deployment, "distributions", lambda: [local_dist, local_dist])
    with pytest.raises(DeploymentError, match="duplicate installed distribution"):
        deployment.installed_distribution_set(local.parent)


def test_generate_refuses_unrelated_receipt_and_preserves_existing_output(tmp_path: Path, capsys) -> None:
    document = {"format_version": 1, "python_target": "3.12"}
    document["manifest_sha256"] = deployment._digest_without(document, "manifest_sha256")
    environment = tmp_path / "environment.json"
    environment.write_text(json.dumps(document), encoding="utf-8")
    receipt = _write_receipt(tmp_path).rename(tmp_path / "transferred-receipt.json")
    output = tmp_path / "deployment.json"
    args = [
        "generate",
        "--artifact-sha256",
        ARTIFACT_SHA,
        "--publisher-ref",
        PUBLISHER,
        "--environment-manifest",
        str(environment),
        "--receipt",
        str(receipt),
        "--out",
        str(output),
    ]
    assert deployment.generate_command(args) == 2
    assert "disagree" in capsys.readouterr().err
    assert not output.exists()

    _write_receipt(tmp_path, environment_manifest_sha256=document["manifest_sha256"]).replace(receipt)
    assert deployment.generate_command(args) == 0
    original = output.read_bytes()
    assert deployment.generate_command(args) == 2
    assert "already exists" in capsys.readouterr().err
    assert output.read_bytes() == original
