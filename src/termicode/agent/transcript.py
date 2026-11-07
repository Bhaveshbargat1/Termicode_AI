"""Auditable transcript writer persisting redacted events locally under .termicode/."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from termicode.providers.redaction import redact_secrets
from termicode.workspace import WorkspaceBoundary


class TranscriptWriter:
    """Writes chronological JSONL events for an agent run into the workspace metadata directory."""

    def __init__(self, workspace: WorkspaceBoundary, run_id: str) -> None:
        self._workspace = workspace
        self._run_id = run_id
        transcripts_dir = workspace.termicode_dir / "transcripts"
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        self._path = transcripts_dir / f"run_{run_id}.jsonl"

    @property
    def path(self) -> Path:
        """Absolute file path to the transcript."""
        return self._path

    def log_event(self, event_type: str, step: int, data: dict[str, Any]) -> None:
        """Append a sanitized event to the transcript file."""
        timestamp = datetime.now(UTC).isoformat()
        raw_json = json.dumps(data, default=str)
        sanitized_json = redact_secrets(raw_json)

        try:
            sanitized_data = json.loads(sanitized_json)
        except Exception:
            sanitized_data = {"raw": sanitized_json}

        entry = {
            "run_id": self._run_id,
            "timestamp": timestamp,
            "step": step,
            "event": event_type,
            "data": sanitized_data,
        }

        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
