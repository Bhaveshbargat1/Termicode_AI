"""Comprehensive tests for safe filesystem tools in TermiCode AI."""

from pathlib import Path

import pytest

from termicode.tools.base import ToolContext
from termicode.tools.filesystem import (
    MAX_PATCH_BYTES,
    MAX_WRITE_BYTES,
    ApplyPatchTool,
    ListFilesTool,
    ReadFileTool,
    SearchTextTool,
    WriteFileTool,
)
from termicode.workspace import WorkspaceBoundary


def test_list_files_normal(tmp_path: Path) -> None:
    # Setup files and directories
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

    # Add ignored dir and sensitive file to ensure they are excluded
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=123", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ListFilesTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"directory": "."}, context=ctx)
    assert result.ok is True
    assert "src/main.py" in result.output
    assert "README.md" in result.output
    assert ".git" not in result.output
    assert ".env" not in result.output


def test_list_files_nonexistent_directory(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ListFilesTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"directory": "does_not_exist"}, context=ctx)
    assert result.ok is False
    assert "Directory does not exist" in (result.error or "")


def test_list_files_traversal_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ListFilesTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"directory": "../../"}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")


def test_read_file_normal(tmp_path: Path) -> None:
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": "sample.txt"}, context=ctx)
    assert result.ok is True
    assert result.output == "line 1\nline 2\nline 3\n"
    assert result.metadata["total_lines"] == 3


def test_read_file_absolute_path_inside_workspace(tmp_path: Path) -> None:
    sample_file = tmp_path / "sample.txt"
    sample_file.write_text("content", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": str(sample_file.resolve())}, context=ctx)
    assert result.ok is True
    assert result.output == "content"


def test_read_file_traversal_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": "../../outside.txt"}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")


def test_read_file_absolute_path_outside_workspace(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    outside = tmp_path.parent / "outside.txt"
    result = tool.run({"path": str(outside)}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")


def test_read_file_nonexistent(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": "nonexistent.txt"}, context=ctx)
    assert result.ok is False
    assert "File not found" in (result.error or "")


def test_read_file_binary(tmp_path: Path) -> None:
    bin_file = tmp_path / "binary.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": "binary.bin"}, context=ctx)
    assert result.ok is False
    assert "Cannot read binary file" in (result.error or "")


def test_read_file_oversized_truncation(tmp_path: Path) -> None:
    big_file = tmp_path / "big.txt"
    big_file.write_text("A" * 500, encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"path": "big.txt", "max_bytes": 100}, context=ctx)
    assert result.ok is True
    assert result.metadata["truncated"] is True
    assert "Warning: Content truncated at 100 bytes" in result.output
    assert len(result.output) > 100


def test_search_text_normal(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("def find_me():\n    return 42\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("def other():\n    pass\n", encoding="utf-8")
    # Binary file with target text should be skipped
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01find_me\x00")

    boundary = WorkspaceBoundary(tmp_path)
    tool = SearchTextTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"query": "find_me"}, context=ctx)
    assert result.ok is True
    assert "hello.py:1: def find_me():" in result.output
    assert "other.py" not in result.output
    assert "bin.dat" not in result.output
    assert result.metadata["matches"] == 1


def test_search_text_no_matches(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text("simple text", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = SearchTextTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"query": "missing_token"}, context=ctx)
    assert result.ok is True
    assert "No matches found" in result.output
    assert result.metadata["matches"] == 0


def test_search_text_traversal_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = SearchTextTool()
    ctx = ToolContext(workspace=boundary)

    result = tool.run({"directory": "../../", "query": "test"}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")


def test_write_file_pending_approval_unconfirmed(tmp_path: Path) -> None:
    # Unconfirmed write MUST NOT touch the disk
    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=False)

    dest = tmp_path / "new_dir" / "created.txt"
    result = tool.run(
        {"path": "new_dir/created.txt", "content": "hello world\n"},
        context=ctx,
    )

    assert result.ok is True
    assert "Pending user approval to write" in result.output
    assert "+hello world" in result.output
    assert result.changed_files == []
    assert result.metadata["pending_approval"] is True
    # Verify file was NOT created on disk
    assert not dest.exists()


def test_write_file_confirmed_writes_to_disk(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    dest = tmp_path / "new_dir" / "created.txt"
    result = tool.run(
        {"path": "new_dir/created.txt", "content": "hello world\n"},
        context=ctx,
    )

    assert result.ok is True
    assert "Successfully written" in result.output
    assert result.changed_files == ["new_dir/created.txt"]
    assert result.metadata["applied"] is True
    # Verify file was actually created on disk
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "hello world\n"


def test_write_file_diff_preview_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "existing.py"
    target.write_text("def old():\n    return 1\n", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=False)

    result = tool.run(
        {"path": "existing.py", "content": "def new():\n    return 2\n"},
        context=ctx,
    )

    assert result.ok is True
    assert "-def old():" in result.output
    assert "+def new():" in result.output
    assert result.changed_files == []
    # File content on disk remains unchanged
    assert target.read_text(encoding="utf-8") == "def old():\n    return 1\n"


def test_write_file_oversized_input_rejected(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    giant_content = "X" * (MAX_WRITE_BYTES + 100)
    result = tool.run({"path": "giant.txt", "content": giant_content}, context=ctx)
    assert result.ok is False
    assert "Oversized input" in (result.error or "")
    assert not (tmp_path / "giant.txt").exists()


def test_write_file_blocks_workspace_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": "../../escaped.txt", "content": "bad"}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_write_file_blocks_sensitive_path(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = WriteFileTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": ".env", "content": "EVIL=true"}, context=ctx)
    assert result.ok is False
    assert "Access denied" in (result.error or "")


def test_apply_patch_pending_approval_unconfirmed(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    patch_text = "@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 20\n z = 3\n"

    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=False)

    result = tool.run({"path": "code.py", "patch": patch_text}, context=ctx)
    assert result.ok is True
    assert "Pending user approval to apply patch" in result.output
    assert "-y = 2" in result.output
    assert "+y = 20" in result.output
    assert result.changed_files == []
    assert result.metadata["pending_approval"] is True
    # Disk unchanged
    assert target.read_text(encoding="utf-8") == "x = 1\ny = 2\nz = 3\n"


def test_apply_patch_confirmed_modifies_disk(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    patch_text = "@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 20\n z = 3\n"

    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": "code.py", "patch": patch_text}, context=ctx)
    assert result.ok is True
    assert "Successfully applied patch" in result.output
    assert result.changed_files == ["code.py"]
    assert result.metadata["applied"] is True
    # Verify disk updated
    assert target.read_text(encoding="utf-8") == "x = 1\ny = 20\nz = 3\n"


def test_apply_patch_nonexistent_file(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": "ghost.py", "patch": "@@ -1,1 +1,1 @@\n-a\n+b\n"}, context=ctx)
    assert result.ok is False
    assert "Target file not found" in (result.error or "")


def test_apply_patch_hunk_context_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    # Mismatched context line (expects "omega", actual is "alpha")
    bad_patch = "@@ -1,3 +1,3 @@\n omega\n-beta\n+delta\n gamma\n"

    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": "file.txt", "patch": bad_patch}, context=ctx)
    assert result.ok is False
    assert "Patch application failed" in (result.error or "")
    assert "Hunk context mismatch" in (result.error or "")
    # Disk unchanged
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\ngamma\n"


def test_apply_patch_binary_file(tmp_path: Path) -> None:
    bin_file = tmp_path / "binary.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\xff")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run({"path": "binary.bin", "patch": "@@ -1,1 +1,1 @@\n-a\n+b\n"}, context=ctx)
    assert result.ok is False
    assert "Cannot apply patch to binary file" in (result.error or "")


def test_apply_patch_oversized_input_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    giant_patch = "X" * (MAX_PATCH_BYTES + 100)
    result = tool.run({"path": "target.txt", "patch": giant_patch}, context=ctx)
    assert result.ok is False
    assert "Oversized input" in (result.error or "")


def test_apply_patch_blocks_workspace_escape(tmp_path: Path) -> None:
    boundary = WorkspaceBoundary(tmp_path)
    tool = ApplyPatchTool()
    ctx = ToolContext(workspace=boundary, user_confirmed=True)

    result = tool.run(
        {"path": "../../outside.txt", "patch": "@@ -1,1 +1,1 @@\n-a\n+b\n"},
        context=ctx,
    )
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")


def test_symlink_escape_rejection(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret_outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "symlink_outside.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation requires elevated privileges on Windows")

    boundary = WorkspaceBoundary(tmp_path)
    tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)
    result = tool.run({"path": "symlink_outside.txt"}, context=ctx)
    assert result.ok is False
    assert "outside approved workspace root" in (result.error or "")
