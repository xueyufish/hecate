"""Tripwire: no two test files may resolve to the same pytest module name.

Under pytest's default import mode, a test file's effective module name is
derived from its basename extended by the ``__init__.py`` chain above it. Two
files whose effective names collide (classic case: same basename in two
directories that are both *not* packages) abort full-suite collection with
``import file mismatch`` — an error only visible when both files are collected
together, which is exactly what the CI full-suite run does and scoped local
runs do not.

Regression: ``tests/test_execution/test_manifest.py`` collided with
``tests/test_plugin/test_manifest.py`` (both directories were bare) and broke
CI after landing, since scoped local runs never imported the pair together.
"""

from __future__ import annotations

from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent


def _effective_module(path: Path) -> str:
    """Derive the module name pytest will use for ``path``.

    Walk up the directory chain while ``__init__.py`` exists, building the
    dotted package path; a bare directory contributes only the basename.
    """

    parts = [path.stem]
    directory = path.parent
    while (directory / "__init__.py").exists() and directory != TESTS_ROOT.parent:
        parts.append(directory.name)
        directory = directory.parent
    return ".".join(reversed(parts))


def test_no_test_file_module_name_collisions() -> None:
    owners: dict[str, Path] = {}
    collisions: list[str] = []

    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _effective_module(path)
        if module in owners and owners[module] != path:
            collisions.append(
                f"{module}: {owners[module].relative_to(TESTS_ROOT.parent)} vs {path.relative_to(TESTS_ROOT.parent)}"
            )
        else:
            owners[module] = path

    assert not collisions, (
        "test files share an effective pytest module name; full-suite collection "
        "will fail with 'import file mismatch'. Rename one file or add the "
        "missing __init__.py:\n" + "\n".join(collisions)
    )


def test_guard_self_check_finds_planted_collision(tmp_path, monkeypatch) -> None:
    """Negative control: the detector fires on a planted bare-dir collision."""

    dir_a = tmp_path / "pkg_a"
    dir_b = tmp_path / "pkg_b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "test_dup.py").write_text("", encoding="utf-8")
    (dir_b / "test_dup.py").write_text("", encoding="utf-8")

    owners: dict[str, Path] = {}
    collisions: list[str] = []
    for path in sorted(tmp_path.rglob("test_*.py")):
        parts = [path.stem]
        directory = path.parent
        while (directory / "__init__.py").exists() and directory != tmp_path.parent:
            parts.append(directory.name)
            directory = directory.parent
        module = ".".join(reversed(parts))
        if module in owners and owners[module] != path:
            collisions.append(module)
        else:
            owners[module] = path

    assert collisions == ["test_dup"]
