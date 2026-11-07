"""Tests for workspace boundary validation and safe path resolution."""

from pathlib import Path

import pytest

from termicode.workspace import (
    SensitivePathError,
    WorkspaceBoundary,
    WorkspaceEscapeError,
    WorkspaceNotADirectoryError,
    WorkspaceNotFoundError,
)


def test_workspace_boundary_initialization(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    assert boundary.root == tmp_path.resolve()
    assert boundary.termicode_dir == tmp_path.resolve() / ".termicode"


def test_workspace_boundary_not_found(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    with pytest.raises(WorkspaceNotFoundError):
        WorkspaceBoundary(non_existent)


def test_workspace_boundary_not_a_directory(tmp_path: Path) -> None:
    test_file = tmp_path / "regular_file.txt"
    test_file.write_text("hello", encoding="utf-8")
    with pytest.raises(WorkspaceNotADirectoryError):
        WorkspaceBoundary(test_file)


def test_resolve_safe_relative_path(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    resolved = boundary.resolve_safe("src/module.py")
    assert resolved == (tmp_path / "src" / "module.py").resolve()
    assert boundary.is_safe("src/module.py") is True


def test_resolve_safe_absolute_path_inside_workspace(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    target = tmp_path / "data" / "file.txt"
    resolved = boundary.resolve_safe(target)
    assert resolved == target.resolve()
    assert boundary.is_safe(target) is True


def test_resolve_safe_internal_traversal(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    # Stays within workspace
    resolved = boundary.resolve_safe("subdir/../file.txt")
    assert resolved == (tmp_path / "file.txt").resolve()
    assert boundary.is_safe("subdir/../file.txt") is True


def test_resolve_safe_blocks_escape_traversal(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    with pytest.raises(WorkspaceEscapeError):
        boundary.resolve_safe("../../escaped.txt")
    assert boundary.is_safe("../../escaped.txt") is False


def test_resolve_safe_blocks_external_absolute_path(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    outside_path = tmp_path.parent / "outside.txt"
    with pytest.raises(WorkspaceEscapeError):
        boundary.resolve_safe(outside_path)
    assert boundary.is_safe(outside_path) is False


@pytest.mark.parametrize(
    "sensitive_path",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".git/config",
        ".git/HEAD",
        ".ssh/id_rsa",
        ".aws/credentials",
        "secrets.pem",
        "private.key",
        "id_rsa",
        "id_ed25519",
        "credentials.json",
    ],
)
def test_resolve_safe_blocks_sensitive_paths(tmp_path: Path, sensitive_path: str) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    with pytest.raises(SensitivePathError):
        boundary.resolve_safe(sensitive_path)
    assert boundary.is_safe(sensitive_path) is False


def test_resolve_safe_allows_env_example(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    resolved = boundary.resolve_safe(".env.example")
    assert resolved == (tmp_path / ".env.example").resolve()
    assert boundary.is_safe(".env.example") is True


def test_resolve_safe_allow_sensitive_override(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    resolved = boundary.resolve_safe(".env", allow_sensitive=True)
    assert resolved == (tmp_path / ".env").resolve()


def test_ensure_termicode_dir(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    assert not boundary.termicode_dir.exists()
    termicode_dir = boundary.ensure_termicode_dir()
    assert termicode_dir.exists()
    assert termicode_dir.is_dir()
    assert termicode_dir == tmp_path / ".termicode"
