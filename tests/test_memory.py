"""Tests for local memory, compact summaries, transcripts, serialization, and redaction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from termicode.agent.memory import (
    ApprovedActionRecord,
    CommandExecutionRecord,
    CorruptedSummaryError,
    CorruptedTranscriptError,
    LocalMemory,
    RunNotFoundError,
    RunSummary,
)
from termicode.agent.models import AgentRequest, StopReason
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import WorkflowType
from termicode.cli import app
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider
from termicode.tools.registry import create_default_registry
from termicode.workspace import WorkspaceBoundary

runner = CliRunner()


def test_run_summary_serialization_round_trip(tmp_path: Path) -> None:
    """Verify that RunSummary serializes to JSON and deserializes without data loss."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)

    summary = RunSummary(
        run_id="abc123def456",
        timestamp="2026-11-05T12:00:00Z",
        task_objective="Implement cache invalidation logic",
        workflow="generate",
        final_stop_reason=StopReason.COMPLETED,
        approved_actions=[
            ApprovedActionRecord(
                tool_name="write_file",
                arguments={"path": "src/cache.py", "content": "def invalidate(): pass\n"},
                preview="- old\n+ def invalidate(): pass",
                step=1,
            )
        ],
        changed_files=["src/cache.py"],
        commands_and_tests=[
            CommandExecutionRecord(
                command="pytest tests/test_cache.py",
                ok=True,
                exit_code=0,
                output="1 passed in 0.05s",
                duration_seconds=0.05,
                is_test=True,
            )
        ],
        unresolved_issues=[],
        summary="Cache invalidation implemented cleanly.",
        steps_used=2,
        repairs_used=0,
        transcript_path=str(tmp_path / ".termicode" / "transcripts" / "run_abc123def456.jsonl"),
    )

    saved_path = memory.save_summary(summary)
    assert saved_path.exists()
    assert saved_path.is_file()

    # Load back and verify fields
    loaded = memory.get_summary("abc123def456")
    assert loaded.run_id == "abc123def456"
    assert loaded.task_objective == "Implement cache invalidation logic"
    assert loaded.workflow == "generate"
    assert loaded.final_stop_reason == StopReason.COMPLETED
    assert loaded.stop_reason == StopReason.COMPLETED
    assert len(loaded.approved_actions) == 1
    assert loaded.approved_actions[0].tool_name == "write_file"
    assert loaded.approved_actions[0].step == 1
    assert loaded.changed_files == ["src/cache.py"]
    assert len(loaded.commands_and_tests) == 1
    assert loaded.commands_and_tests[0].command == "pytest tests/test_cache.py"
    assert loaded.commands_and_tests[0].ok is True
    assert loaded.commands_and_tests[0].is_test is True
    assert loaded.unresolved_issues == []
    assert loaded.steps_used == 2


def test_secret_redaction_in_saved_summary(tmp_path: Path) -> None:
    """Verify that credentials and API keys are redacted before writing summary to disk."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)

    secret_key = "sk-proj-supersecretkey1234567890abcdef"
    secret_token = "Bearer mysecrettoken9876543210"

    summary = RunSummary(
        run_id="sec999",
        timestamp="2026-11-05T12:00:00Z",
        task_objective=f"Connect using API key {secret_key}",
        workflow="generate",
        final_stop_reason=StopReason.COMPLETED,
        approved_actions=[
            ApprovedActionRecord(
                tool_name="write_file",
                arguments={"path": "auth.py", "auth_header": secret_token},
                preview=f"Authorization: {secret_token}",
                step=1,
            )
        ],
        changed_files=["auth.py"],
        commands_and_tests=[
            CommandExecutionRecord(
                command=f"python client.py --key {secret_key}",
                ok=False,
                exit_code=1,
                output=f"Error connecting with {secret_key}",
            )
        ],
        unresolved_issues=[f"Connection failed with token: {secret_token}"],
        summary=f"Finished with key {secret_key}",
    )

    saved_path = memory.save_summary(summary)
    raw_content = saved_path.read_text(encoding="utf-8")

    # Assert raw secrets are NOT present anywhere in file
    assert secret_key not in raw_content
    assert secret_token not in raw_content
    assert "[REDACTED]" in raw_content

    # Load back and verify parsed model contains redacted values
    loaded = memory.get_summary("sec999")
    assert secret_key not in loaded.task_objective
    assert loaded.approved_actions[0].preview is not None
    assert secret_token not in loaded.approved_actions[0].preview
    assert secret_key not in loaded.commands_and_tests[0].command
    assert secret_token not in loaded.unresolved_issues[0]
    assert secret_key not in loaded.summary


def test_missing_files_error_handling(tmp_path: Path) -> None:
    """Verify that queries for missing run summaries or transcripts raise RunNotFoundError."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)

    # Empty memory
    assert memory.list_run_ids() == []
    assert memory.list_summaries() == []
    assert memory.get_latest_run_id() is None

    # Missing specific run summary
    with pytest.raises(RunNotFoundError, match="Run summary not found"):
        memory.get_summary("ghost_run_001")

    # Missing 'latest' run summary
    with pytest.raises(RunNotFoundError, match="No recorded runs found"):
        memory.get_summary("latest")

    # Missing transcript
    with pytest.raises(RunNotFoundError, match="Transcript not found"):
        memory.read_transcript("ghost_run_001")


def test_corrupted_transcript_handling(tmp_path: Path) -> None:
    """Verify corrupted lines in JSONL transcripts are handled safely."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)
    memory.ensure_directories()

    run_id = "corrupt01"
    transcript_file = memory.transcripts_dir / f"run_{run_id}.jsonl"

    valid_event_1 = json.dumps({"step": 1, "event": "start", "data": {"task": "test"}})
    valid_event_2 = json.dumps({"step": 2, "event": "done", "data": {"result": "ok"}})

    # Write transcript with broken JSON and non-object lines
    transcript_file.write_text(
        f"{valid_event_1}\n"
        "{{this is definitely not valid json\n"
        "\n"  # blank line should be skipped
        '"scalar string instead of object"\n'
        f"{valid_event_2}\n",
        encoding="utf-8",
    )

    # 1. With allow_corrupted=True (default), does not crash
    events = memory.read_transcript(run_id, allow_corrupted=True)
    assert len(events) == 4

    # Event 0: valid
    assert events[0]["step"] == 1
    assert events[0]["event"] == "start"

    # Event 1: corrupted line
    assert events[1].get("corrupted") is True
    assert events[1]["line_number"] == 2
    assert "error" in events[1]

    # Event 2: scalar string corrupted line
    assert events[2].get("corrupted") is True
    assert events[2]["line_number"] == 4

    # Event 3: valid
    assert events[3]["step"] == 2
    assert events[3]["event"] == "done"

    # 2. With allow_corrupted=False, raises CorruptedTranscriptError
    with pytest.raises(CorruptedTranscriptError, match="is corrupted"):
        memory.read_transcript(run_id, allow_corrupted=False)


def test_corrupted_summary_handling(tmp_path: Path) -> None:
    """Verify invalid JSON in summary files raises CorruptedSummaryError and skips in list."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)
    memory.ensure_directories()

    # 1. Save one valid summary
    valid_summary = RunSummary(
        run_id="valid01",
        timestamp="2026-11-05T12:00:00Z",
        task_objective="Valid task",
        workflow="understand",
        final_stop_reason=StopReason.COMPLETED,
    )
    memory.save_summary(valid_summary)

    # 2. Save one corrupted summary
    corrupted_path = memory.summaries_dir / "run_bad01.json"
    corrupted_path.write_text("{broken json file without closing", encoding="utf-8")

    # Direct query raises CorruptedSummaryError
    with pytest.raises(CorruptedSummaryError, match="contains invalid JSON"):
        memory.get_summary("bad01")

    # list_summaries() skips the bad file and returns the valid one
    summaries = memory.list_summaries()
    assert len(summaries) == 1
    assert summaries[0].run_id == "valid01"


def test_agent_runtime_persists_summary_and_populates_fields(tmp_path: Path) -> None:
    """Verify that AgentRuntime automatically generates and saves RunSummary."""
    boundary = WorkspaceBoundary(tmp_path)
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "result.txt", "content": "Done!\n"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            CompletionResponse(
                content="File result.txt written and task completed.",
                finish_reason="stop",
            ),
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,
    )
    req = AgentRequest(
        task="Create result.txt",
        workspace=boundary,
        workflow=WorkflowType.GENERATE,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.COMPLETED
    assert result.run_summary is not None
    assert result.run_summary.task_objective == "Create result.txt"
    assert result.run_summary.workflow == "generate"
    assert result.run_summary.final_stop_reason == StopReason.COMPLETED
    assert len(result.run_summary.approved_actions) == 1
    assert result.run_summary.approved_actions[0].tool_name == "write_file"
    assert "result.txt" in result.run_summary.changed_files
    assert result.run_summary.unresolved_issues == []

    # Check file on disk
    memory = LocalMemory(boundary)
    saved_summary = memory.get_summary(result.run_summary.run_id)
    assert saved_summary.run_id == result.run_summary.run_id
    assert saved_summary.task_objective == "Create result.txt"
    assert saved_summary.changed_files == ["result.txt"]


def test_agent_runtime_records_unresolved_issues_on_policy_rejection(tmp_path: Path) -> None:
    """Verify that policy rejections are noted in unresolved_issues of the summary."""
    boundary = WorkspaceBoundary(tmp_path)
    fake_provider = FakeProvider(
        responses=[
            CompletionResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "unauthorized.txt", "content": "bad"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )

    runtime = AgentRuntime(
        provider=fake_provider,
        registry=create_default_registry(),
        approval_handler=lambda _act, _dec: True,
    )
    req = AgentRequest(
        task="Try unauthorized write in understand",
        workspace=boundary,
        workflow=WorkflowType.UNDERSTAND,
    )

    result = runtime.run(req)

    assert result.stop_reason == StopReason.POLICY_REJECTED
    assert result.run_summary is not None
    assert result.run_summary.final_stop_reason == StopReason.POLICY_REJECTED
    assert len(result.run_summary.unresolved_issues) > 0
    assert any("Policy denied" in issue for issue in result.run_summary.unresolved_issues)


def test_cli_runs_and_summary_commands(tmp_path: Path) -> None:
    """Verify CLI `termicode runs` and `termicode summary` render expected output."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)

    # 1. When empty:
    res_empty = runner.invoke(app, ["--workspace", str(tmp_path), "runs"])
    assert res_empty.exit_code == 0
    assert "No recorded runs found" in res_empty.output

    # 2. Create two summaries
    s1 = RunSummary(
        run_id="run001",
        timestamp="2026-11-05T10:00:00Z",
        task_objective="First run objective",
        workflow="understand",
        final_stop_reason=StopReason.COMPLETED,
        steps_used=1,
    )
    s2 = RunSummary(
        run_id="run002",
        timestamp="2026-11-05T11:00:00Z",
        task_objective="Second run objective",
        workflow="generate",
        final_stop_reason=StopReason.USER_CANCELLED,
        steps_used=3,
        unresolved_issues=["User denied approval for tool 'write_file'."],
    )
    memory.save_summary(s1)
    memory.save_summary(s2)

    # 3. Test `termicode runs`
    res_list = runner.invoke(app, ["--workspace", str(tmp_path), "runs"])
    assert res_list.exit_code == 0
    assert "Recorded TermiCode Runs" in res_list.output
    assert "run001" in res_list.output
    assert "run002" in res_list.output
    assert "understand" in res_list.output
    assert "generate" in res_list.output

    # 4. Test `termicode summary <run_id>`
    res_sum = runner.invoke(app, ["--workspace", str(tmp_path), "summary", "run001"])
    assert res_sum.exit_code == 0
    assert "Run Summary: run001" in res_sum.output
    assert "First run objective" in res_sum.output
    assert "completed" in res_sum.output

    # 5. Test `termicode summary` with unresolved issues
    res_sum2 = runner.invoke(app, ["--workspace", str(tmp_path), "summary", "run002"])
    assert res_sum2.exit_code == 0
    assert "Run Summary: run002" in res_sum2.output
    assert "Unresolved Issues" in res_sum2.output
    assert "User denied approval" in res_sum2.output

    # 6. Test missing summary
    res_missing = runner.invoke(app, ["--workspace", str(tmp_path), "summary", "ghost"])
    assert res_missing.exit_code == 1
    assert "Run Not Found" in res_missing.output


def test_cli_transcript_command(tmp_path: Path) -> None:
    """Verify CLI `termicode transcript` renders chronological events."""
    boundary = WorkspaceBoundary(tmp_path)
    memory = LocalMemory(boundary)
    memory.ensure_directories()

    run_id = "trans01"
    transcript_path = memory.transcripts_dir / f"run_{run_id}.jsonl"
    event_1 = json.dumps({"step": 1, "event": "run_start", "data": {"task": "audit"}})
    event_2 = json.dumps({"step": 2, "event": "run_stop", "data": {"status": "completed"}})
    transcript_path.write_text(f"{event_1}\n{event_2}\n", encoding="utf-8")

    res = runner.invoke(app, ["--workspace", str(tmp_path), "transcript", run_id])
    assert res.exit_code == 0
    assert f"Transcript Events: {run_id}" in res.output
    assert "run_start" in res.output
    assert "run_stop" in res.output
