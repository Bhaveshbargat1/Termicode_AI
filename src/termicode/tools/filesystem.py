"""Safe filesystem tools bounded by workspace safety policies.

Provides:
- list_files: directory listing bounded by workspace root
- read_file: text file reader with binary detection and size caps
- search_text: text search bounded by workspace root and result caps
- write_file: preview-first file writer requiring explicit approval
- apply_patch: preview-first unified diff patcher requiring explicit approval
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field

from termicode.tools.base import (
    BaseTool,
    RiskLevel,
    ToolContext,
    ToolResult,
)
from termicode.workspace import WorkspaceBoundary

# Safety constants
DEFAULT_MAX_READ_BYTES: Final[int] = 65536  # 64 KB
HARD_CAP_READ_BYTES: Final[int] = 1048576  # 1 MB
MAX_WRITE_BYTES: Final[int] = 524288  # 512 KB
MAX_PATCH_BYTES: Final[int] = 262144  # 256 KB
DEFAULT_MAX_SEARCH_RESULTS: Final[int] = 100
DEFAULT_MAX_LIST_RESULTS: Final[int] = 500

IGNORED_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        ".termicode",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
)


def is_binary_file(path: Path, sample_size: int = 8192) -> bool:
    """Determine whether a file appears to be binary by inspecting initial bytes."""
    try:
        with path.open("rb") as f:
            chunk = f.read(sample_size)
            if b"\x00" in chunk:
                return True
            chunk.decode("utf-8")
            return False
    except UnicodeDecodeError:
        return True
    except OSError:
        return False


def _get_boundary(context: ToolContext | None) -> WorkspaceBoundary:
    """Extract WorkspaceBoundary from context or fallback to current directory."""
    if context and context.workspace is not None:
        return context.workspace
    return WorkspaceBoundary(Path.cwd())


# ---------------------------------------------------------------------------
# 1. list_files
# ---------------------------------------------------------------------------


class ListFilesArgs(BaseModel):
    directory: str = Field(
        default=".",
        description="Relative or workspace path of directory to inspect.",
    )
    recursive: bool = Field(
        default=True,
        description="Whether to list subdirectories recursively.",
    )
    max_results: int = Field(
        default=DEFAULT_MAX_LIST_RESULTS,
        ge=1,
        le=2000,
        description="Maximum entries to return.",
    )


class ListFilesTool(BaseTool[ListFilesArgs]):
    name = "list_files"
    description = "List files and directories within the workspace boundary."
    risk_level = RiskLevel.READ
    args_schema = ListFilesArgs

    def _execute(self, args: ListFilesArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)
        resolved_dir = boundary.resolve_safe(args.directory)

        if not resolved_dir.exists():
            return ToolResult(
                ok=False,
                error=f"Directory does not exist: '{args.directory}'",
            )

        if not resolved_dir.is_dir():
            return ToolResult(
                ok=False,
                error=f"Path is not a directory: '{args.directory}'",
            )

        entries: list[str] = []
        truncated = False

        if args.recursive:
            iterator = resolved_dir.rglob("*")
        else:
            iterator = resolved_dir.glob("*")

        for item in sorted(iterator):
            # Skip ignored directories and their contents
            rel_to_workspace = item.relative_to(boundary.root)
            if any(part in IGNORED_DIR_NAMES for part in rel_to_workspace.parts):
                continue

            # Skip sensitive files
            if boundary.is_sensitive(item):
                continue

            entry_type = "DIR " if item.is_dir() else "FILE"
            posix_rel = rel_to_workspace.as_posix()
            if item.is_file():
                try:
                    size = item.stat().st_size
                    entries.append(f"[{entry_type}] {posix_rel} ({size:,} bytes)")
                except OSError:
                    entries.append(f"[{entry_type}] {posix_rel}")
            else:
                entries.append(f"[{entry_type}] {posix_rel}")

            if len(entries) >= args.max_results:
                truncated = True
                break

        summary = f"Directory: '{rel_to_workspace.as_posix() or '.'}' ({len(entries)} items)"
        body = "\n".join(entries) if entries else "(Directory is empty)"
        if truncated:
            body += f"\n\n[Warning: Results truncated at {args.max_results} entries limit]"

        return ToolResult(
            ok=True,
            output=f"{summary}\n\n{body}",
            metadata={"count": len(entries), "truncated": truncated},
        )


# ---------------------------------------------------------------------------
# 2. read_file
# ---------------------------------------------------------------------------


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path to the file to read.")
    max_bytes: int = Field(
        default=DEFAULT_MAX_READ_BYTES,
        ge=1,
        le=HARD_CAP_READ_BYTES,
        description="Maximum bytes to read from the file.",
    )
    start_line: int = Field(
        default=1,
        ge=1,
        description="1-based line number to start reading from.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Optional 1-based line number to end reading at (inclusive).",
    )


class ReadFileTool(BaseTool[ReadFileArgs]):
    name = "read_file"
    description = "Read safe text file contents within workspace, bounded by size caps."
    risk_level = RiskLevel.READ
    args_schema = ReadFileArgs

    def _execute(self, args: ReadFileArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)
        resolved_file = boundary.resolve_safe(args.path)

        if not resolved_file.exists():
            return ToolResult(
                ok=False,
                error=f"File not found: '{args.path}'",
            )

        if not resolved_file.is_file():
            return ToolResult(
                ok=False,
                error=f"Path is not a regular file: '{args.path}'",
            )

        if is_binary_file(resolved_file):
            return ToolResult(
                ok=False,
                error=f"Cannot read binary file '{args.path}'. Only text files are permitted.",
            )

        try:
            full_content = resolved_file.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            return ToolResult(ok=False, error=f"Failed to read file: {err}")

        lines = full_content.splitlines(keepends=True)
        total_lines = len(lines)

        # Slice requested line range
        start_idx = args.start_line - 1
        end_idx = args.end_line if args.end_line is not None else total_lines
        sliced_content = "".join(lines[start_idx:end_idx])

        # Apply byte cap
        encoded = sliced_content.encode("utf-8")
        file_size = len(encoded)
        truncated = False

        if file_size > args.max_bytes:
            truncated = True
            output = encoded[: args.max_bytes].decode("utf-8", errors="ignore")
            output += (
                f"\n\n[Warning: Content truncated at {args.max_bytes:,} bytes. "
                f"Total slice size: {file_size:,} bytes]"
            )
        else:
            output = sliced_content

        return ToolResult(
            ok=True,
            output=output,
            metadata={
                "total_lines": total_lines,
                "read_bytes": min(file_size, args.max_bytes),
                "truncated": truncated,
            },
        )


# ---------------------------------------------------------------------------
# 3. search_text
# ---------------------------------------------------------------------------


class SearchTextArgs(BaseModel):
    query: str = Field(
        min_length=1,
        description="Text or pattern to search for across workspace files.",
    )
    directory: str = Field(
        default=".",
        description="Subdirectory inside workspace to search.",
    )
    case_sensitive: bool = Field(
        default=True,
        description="Whether match should be case sensitive.",
    )
    max_results: int = Field(
        default=DEFAULT_MAX_SEARCH_RESULTS,
        ge=1,
        le=500,
        description="Maximum matching occurrences to return.",
    )


class SearchTextTool(BaseTool[SearchTextArgs]):
    name = "search_text"
    description = "Search text across workspace files, ignoring binaries and hidden metadata."
    risk_level = RiskLevel.READ
    args_schema = SearchTextArgs

    def _execute(self, args: SearchTextArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)
        resolved_dir = boundary.resolve_safe(args.directory)

        if not resolved_dir.is_dir():
            return ToolResult(
                ok=False,
                error=f"Search path is not a directory: '{args.directory}'",
            )

        matches: list[str] = []
        truncated = False
        target_query = args.query if args.case_sensitive else args.query.lower()

        for item in sorted(resolved_dir.rglob("*")):
            if not item.is_file():
                continue

            rel = item.relative_to(boundary.root)
            if any(part in IGNORED_DIR_NAMES for part in rel.parts):
                continue

            if boundary.is_sensitive(item):
                continue

            if is_binary_file(item):
                continue

            try:
                content = item.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            for line_no, line in enumerate(content.splitlines(), start=1):
                haystack = line if args.case_sensitive else line.lower()
                if target_query in haystack:
                    posix_path = rel.as_posix()
                    matches.append(f"{posix_path}:{line_no}: {line.strip()}")
                    if len(matches) >= args.max_results:
                        truncated = True
                        break

            if truncated:
                break

        if not matches:
            return ToolResult(
                ok=True,
                output=f"No matches found for query '{args.query}'.",
                metadata={"matches": 0},
            )

        output = "\n".join(matches)
        if truncated:
            output += f"\n\n[Warning: Results truncated at {args.max_results} matches limit]"

        return ToolResult(
            ok=True,
            output=output,
            metadata={"matches": len(matches), "truncated": truncated},
        )


# ---------------------------------------------------------------------------
# 4. write_file
# ---------------------------------------------------------------------------


class WriteFileArgs(BaseModel):
    path: str = Field(description="Destination file path within workspace.")
    content: str = Field(description="Text content to write to the file.")
    create_parents: bool = Field(
        default=True,
        description="Whether to create parent directories if missing.",
    )


class WriteFileTool(BaseTool[WriteFileArgs]):
    name = "write_file"
    description = (
        "Write text to a file. Requires user approval before writing to disk; "
        "returns a unified diff preview if unconfirmed."
    )
    risk_level = RiskLevel.WRITE
    args_schema = WriteFileArgs

    def _execute(self, args: WriteFileArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)

        # Enforce oversized input checks
        content_bytes = len(args.content.encode("utf-8"))
        if content_bytes > MAX_WRITE_BYTES:
            return ToolResult(
                ok=False,
                error=(
                    f"Oversized input: Content size ({content_bytes:,} bytes) "
                    f"exceeds maximum allowed limit of {MAX_WRITE_BYTES:,} bytes."
                ),
            )

        resolved = boundary.resolve_safe(args.path)

        if resolved.exists() and resolved.is_dir():
            return ToolResult(
                ok=False,
                error=f"Destination path is a directory, not a regular file: '{args.path}'",
            )

        rel_path = resolved.relative_to(boundary.root).as_posix()

        # Check existing content for diff preview
        if resolved.exists():
            if is_binary_file(resolved):
                return ToolResult(
                    ok=False,
                    error=f"Cannot overwrite binary file '{args.path}' as text.",
                )
            try:
                old_content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError as err:
                return ToolResult(ok=False, error=f"Failed to read existing file: {err}")
        else:
            old_content = ""

        # Compute unified diff preview
        old_lines = old_content.splitlines(keepends=True)
        new_lines = args.content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{rel_path}" if old_content else "/dev/null",
                tofile=f"b/{rel_path}",
                lineterm="",
            )
        )
        diff_preview = "\n".join(diff_lines) if diff_lines else "(No content changes detected)"

        is_confirmed = bool(context and context.user_confirmed)

        if not is_confirmed:
            # Gated by approval: return preview without modifying disk
            return ToolResult(
                ok=True,
                output=(
                    f"Pending user approval to write '{rel_path}':\n"
                    f"--- DIFF PREVIEW ---\n"
                    f"{diff_preview}\n"
                    f"--- END PREVIEW ---\n"
                    "Confirmation required to apply change."
                ),
                changed_files=[],
                metadata={
                    "pending_approval": True,
                    "action": "write_file",
                    "target_file": rel_path,
                    "preview": diff_preview,
                },
            )

        # Approved: apply write
        try:
            if args.create_parents:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(args.content, encoding="utf-8")
        except OSError as err:
            return ToolResult(ok=False, error=f"Failed to write file: {err}")

        return ToolResult(
            ok=True,
            output=f"Successfully written {len(args.content):,} characters to '{rel_path}'.",
            changed_files=[rel_path],
            metadata={
                "applied": True,
                "action": "write_file",
                "target_file": rel_path,
                "preview": diff_preview,
            },
        )


# ---------------------------------------------------------------------------
# 5. apply_patch
# ---------------------------------------------------------------------------


class ApplyPatchArgs(BaseModel):
    path: str = Field(description="Target file to patch.")
    patch: str = Field(description="Unified diff text to apply.")


def apply_unified_diff(original: str, patch_text: str) -> str:
    """Apply a unified diff patch to original text, returning the updated string."""
    orig_lines = original.splitlines(keepends=False)
    patch_lines = patch_text.splitlines(keepends=False)

    hunk_regex = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    hunk_indices = [i for i, line in enumerate(patch_lines) if hunk_regex.match(line)]

    if not hunk_indices:
        raise ValueError(
            "Invalid patch format: no unified diff hunk headers ('@@ -L,N +L,N @@') found."
        )

    # We will build lines from original, applying hunks in order
    result: list[str] = []
    current_orig_idx = 0

    for idx, hunk_idx in enumerate(hunk_indices):
        header = patch_lines[hunk_idx]
        match = hunk_regex.match(header)
        assert match is not None
        orig_start = int(match.group(1)) - 1  # 1-based to 0-based

        # Determine bounds of this hunk in patch_lines
        next_hunk_idx = hunk_indices[idx + 1] if idx + 1 < len(hunk_indices) else len(patch_lines)
        hunk_body = patch_lines[hunk_idx + 1 : next_hunk_idx]

        # Copy unchanged lines from original up to orig_start
        while current_orig_idx < orig_start and current_orig_idx < len(orig_lines):
            result.append(orig_lines[current_orig_idx])
            current_orig_idx += 1

        # Process hunk body lines
        for line in hunk_body:
            if not line:
                # Blank context line
                if current_orig_idx < len(orig_lines):
                    result.append(orig_lines[current_orig_idx])
                    current_orig_idx += 1
                continue

            indicator = line[0]
            content = line[1:]

            if indicator == " ":
                # Context line: must match original
                if current_orig_idx >= len(orig_lines):
                    raise ValueError(f"Hunk context beyond end of file: '{content}'")
                if orig_lines[current_orig_idx] != content:
                    raise ValueError(
                        f"Hunk context mismatch at line {current_orig_idx + 1}: "
                        f"expected '{content}', found '{orig_lines[current_orig_idx]}'"
                    )
                result.append(orig_lines[current_orig_idx])
                current_orig_idx += 1

            elif indicator == "-":
                # Line to remove: must match original
                if current_orig_idx >= len(orig_lines):
                    raise ValueError(f"Hunk removal beyond end of file: '{content}'")
                if orig_lines[current_orig_idx] != content:
                    raise ValueError(
                        f"Hunk removal mismatch at line {current_orig_idx + 1}: "
                        f"expected '{content}', found '{orig_lines[current_orig_idx]}'"
                    )
                current_orig_idx += 1

            elif indicator == "+":
                # Line to add
                result.append(content)

            elif indicator == "\\":
                # e.g., "\ No newline at end of file" - skip
                continue

    # Append any remaining lines from original
    while current_orig_idx < len(orig_lines):
        result.append(orig_lines[current_orig_idx])
        current_orig_idx += 1

    patched = "\n".join(result)
    # Preserve original trailing newline if original had one
    if original.endswith("\n"):
        patched += "\n"
    return patched


class ApplyPatchTool(BaseTool[ApplyPatchArgs]):
    name = "apply_patch"
    description = (
        "Apply a unified diff patch to a target file. Requires user approval "
        "before modifying disk; returns diff preview if unconfirmed."
    )
    risk_level = RiskLevel.WRITE
    args_schema = ApplyPatchArgs

    def _execute(self, args: ApplyPatchArgs, context: ToolContext | None = None) -> ToolResult:
        boundary = _get_boundary(context)

        # Enforce patch size cap
        patch_bytes = len(args.patch.encode("utf-8"))
        if patch_bytes > MAX_PATCH_BYTES:
            return ToolResult(
                ok=False,
                error=(
                    f"Oversized input: Patch size ({patch_bytes:,} bytes) "
                    f"exceeds maximum allowed limit of {MAX_PATCH_BYTES:,} bytes."
                ),
            )

        resolved = boundary.resolve_safe(args.path)

        if not resolved.exists():
            return ToolResult(
                ok=False,
                error=f"Target file not found: '{args.path}'",
            )

        if not resolved.is_file():
            return ToolResult(
                ok=False,
                error=f"Target path is not a regular file: '{args.path}'",
            )

        if is_binary_file(resolved):
            return ToolResult(
                ok=False,
                error=f"Cannot apply patch to binary file '{args.path}'.",
            )

        try:
            original_content = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            return ToolResult(ok=False, error=f"Failed to read target file: {err}")

        # Compute patched content
        try:
            patched_content = apply_unified_diff(original_content, args.patch)
        except ValueError as err:
            return ToolResult(
                ok=False,
                error=f"Patch application failed: {err}",
            )

        rel_path = resolved.relative_to(boundary.root).as_posix()
        is_confirmed = bool(context and context.user_confirmed)

        if not is_confirmed:
            # Gated by approval: return preview without modifying disk
            return ToolResult(
                ok=True,
                output=(
                    f"Pending user approval to apply patch to '{rel_path}':\n"
                    f"--- DIFF PREVIEW ---\n"
                    f"{args.patch.strip()}\n"
                    f"--- END PREVIEW ---\n"
                    "Confirmation required to apply change."
                ),
                changed_files=[],
                metadata={
                    "pending_approval": True,
                    "action": "apply_patch",
                    "target_file": rel_path,
                    "preview": args.patch.strip(),
                },
            )

        # Approved: apply patch to disk
        try:
            resolved.write_text(patched_content, encoding="utf-8")
        except OSError as err:
            return ToolResult(ok=False, error=f"Failed to write patched file: {err}")

        return ToolResult(
            ok=True,
            output=f"Successfully applied patch to '{rel_path}'.",
            changed_files=[rel_path],
            metadata={
                "applied": True,
                "action": "apply_patch",
                "target_file": rel_path,
                "preview": args.patch.strip(),
            },
        )
