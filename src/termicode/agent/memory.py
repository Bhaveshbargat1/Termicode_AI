"""Local memory system persisting JSONL transcripts and compact run summaries under .termicode/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from termicode.agent.models import (
    ApprovedActionRecord,
    CommandExecutionRecord,
    RunSummary,
)
from termicode.agent.transcript import TranscriptWriter
from termicode.providers.redaction import redact_secrets
from termicode.workspace import WorkspaceBoundary


class MemoryError(Exception):
    """Base exception for local memory and transcript storage operations."""


class RunNotFoundError(MemoryError):
    """Raised when the requested run summary or transcript does not exist."""


class CorruptedTranscriptError(MemoryError):
    """Raised when a transcript file is malformed or cannot be parsed."""


class CorruptedSummaryError(MemoryError):
    """Raised when a run summary file contains malformed JSON or fails validation."""


class LocalMemory:
    """Manages storage and retrieval of transcripts and run summaries under .termicode/."""

    def __init__(self, workspace: WorkspaceBoundary) -> None:
        self._workspace = workspace
        self._summaries_dir = workspace.termicode_dir / "summaries"
        self._transcripts_dir = workspace.termicode_dir / "transcripts"

    @property
    def summaries_dir(self) -> Path:
        """Directory where JSON summaries are stored."""
        return self._summaries_dir

    @property
    def transcripts_dir(self) -> Path:
        """Directory where JSONL transcripts are stored."""
        return self._transcripts_dir

    def ensure_directories(self) -> None:
        """Ensure memory directories exist under .termicode/."""
        self._summaries_dir.mkdir(parents=True, exist_ok=True)
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)

    def save_summary(self, summary: RunSummary) -> Path:
        """Persist a run summary to .termicode/summaries/ with secret redaction."""
        self.ensure_directories()
        path = self._summaries_dir / f"run_{summary.run_id}.json"

        # Serialize to JSON and strictly redact any secret credentials
        raw_json = summary.model_dump_json(indent=2)
        sanitized_json = redact_secrets(raw_json)

        path.write_text(sanitized_json, encoding="utf-8")
        return path

    def list_run_ids(self) -> list[str]:
        """Return all recorded run IDs sorted by creation time (most recent first)."""
        if not self._summaries_dir.exists():
            return []

        files = list(self._summaries_dir.glob("run_*.json"))
        # Sort by filesystem modification time descending
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        run_ids: list[str] = []
        for f in files:
            stem = f.stem
            if stem.startswith("run_"):
                run_ids.append(stem[4:])
        return run_ids

    def get_latest_run_id(self) -> str | None:
        """Return the ID of the most recent run, or None if no runs exist."""
        run_ids = self.list_run_ids()
        return run_ids[0] if run_ids else None

    def get_summary(self, run_id: str = "latest") -> RunSummary:
        """Retrieve and parse a RunSummary by ID (or 'latest').

        Raises:
            RunNotFoundError: If the summary file does not exist.
            CorruptedSummaryError: If the summary file cannot be parsed or validated.
        """
        target_id = run_id.strip()
        if target_id == "latest":
            latest = self.get_latest_run_id()
            if not latest:
                raise RunNotFoundError("No recorded runs found in workspace.")
            target_id = latest

        path = self._summaries_dir / f"run_{target_id}.json"
        if not path.is_file():
            raise RunNotFoundError(f"Run summary not found for run ID '{target_id}' at {path}")

        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            return RunSummary.model_validate(data)
        except json.JSONDecodeError as err:
            raise CorruptedSummaryError(
                f"Summary file for run '{target_id}' contains invalid JSON: {err}"
            ) from err
        except Exception as err:
            raise CorruptedSummaryError(
                f"Summary file for run '{target_id}' failed schema validation: {err}"
            ) from err

    def list_summaries(self) -> list[RunSummary]:
        """Load all valid run summaries in the workspace (most recent first)."""
        run_ids = self.list_run_ids()
        summaries: list[RunSummary] = []
        for rid in run_ids:
            try:
                summaries.append(self.get_summary(rid))
            except CorruptedSummaryError:
                continue
        return summaries

    def read_transcript(
        self,
        run_id: str = "latest",
        allow_corrupted: bool = True,
    ) -> list[dict[str, Any]]:
        """Read and parse chronological JSONL events from a run transcript.

        Args:
            run_id: Unique run ID or 'latest'.
            allow_corrupted: When True, corrupted lines are annotated rather than crashing.

        Returns:
            List of parsed event dictionaries.

        Raises:
            RunNotFoundError: If the transcript file does not exist.
            CorruptedTranscriptError: If allow_corrupted is False and a line is invalid JSON.
        """
        target_id = run_id.strip()
        if target_id == "latest":
            latest = self.get_latest_run_id()
            if not latest:
                raise RunNotFoundError("No recorded runs found in workspace.")
            target_id = latest

        path = self._transcripts_dir / f"run_{target_id}.jsonl"
        if not path.is_file():
            raise RunNotFoundError(f"Transcript not found for run ID '{target_id}' at {path}")

        content = path.read_text(encoding="utf-8")
        events: list[dict[str, Any]] = []

        for line_idx, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
                if not isinstance(event, dict):
                    raise ValueError(f"Line {line_idx} is not a JSON object.")
                events.append(event)
            except Exception as err:
                if not allow_corrupted:
                    raise CorruptedTranscriptError(
                        f"Transcript '{path.name}' line {line_idx} is corrupted: {err}"
                    ) from err
                events.append(
                    {
                        "corrupted": True,
                        "line_number": line_idx,
                        "error": str(err),
                        "raw": stripped,
                    }
                )

        return events


__all__ = [
    "ApprovedActionRecord",
    "CommandExecutionRecord",
    "CorruptedSummaryError",
    "CorruptedTranscriptError",
    "LocalMemory",
    "MemoryError",
    "RunNotFoundError",
    "RunSummary",
    "TranscriptWriter",
]
