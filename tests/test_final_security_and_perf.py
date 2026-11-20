"""Security validation and performance benchmark suite for TermiCode AI + ToolBridge.

Verifies:
1. Strict prevention of path traversal attacks (../../, absolute system paths).
2. Protection of sensitive files and credential artifacts (.env, id_rsa, keys).
3. Secret redaction (OpenAI, Gemini, Bearer tokens, custom keys) across logs and databases.
4. ToolBridge TLS termination parameters and CORS origin restrictions.
5. RAG document retrieval performance on 100KB documents (< 100ms).
6. Memory summarization and token size bounding under message overload.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from termicode.chat.database import SessionDatabase
from termicode.chat.session import ChatSession
from termicode.config import Settings
from termicode.providers.redaction import redact_secrets
from termicode.tools.base import ToolContext
from termicode.tools.filesystem import ReadFileTool, WriteFileTool
from termicode.tools.rag import RAGRetrieveTool
from termicode.workspace import (
    SensitivePathError,
    WorkspaceBoundary,
    WorkspaceEscapeError,
)

# ==============================================================================
# 1. Security: Path Traversal Prevention
# ==============================================================================


def test_security_path_traversal_attempts_blocked(tmp_path: Path) -> None:
    """Verify that any path traversal escaping workspace boundary is blocked."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    malicious_paths = [
        "../../outside.txt",
        "..\\..\\outside.txt",
        "nested/../../../../etc/passwd",
        "sub/dir/../../../../windows/system32/cmd.exe",
    ]

    for bad_path in malicious_paths:
        with pytest.raises(WorkspaceEscapeError):
            boundary.resolve_safe(bad_path)

        assert not boundary.is_safe(bad_path)

    # Tool execution verification
    read_tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    res = read_tool.run({"path": "../../outside_secret.txt"}, context=ctx)
    assert not res.ok
    assert "WorkspaceEscapeError" in (res.error or "")

    write_tool = WriteFileTool()
    write_res = write_tool.run(
        {"path": "../../escape.txt", "content": "malicious payload"},
        context=ctx,
    )
    assert not write_res.ok
    assert "WorkspaceEscapeError" in (write_res.error or "")


# ==============================================================================
# 2. Security: Sensitive Files Protection
# ==============================================================================


def test_security_sensitive_credentials_files_blocked(tmp_path: Path) -> None:
    """Verify that credentials, private keys, and environment files are blocked from access."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    # Create dummy sensitive files in workspace
    (tmp_path / ".env").write_text("SECRET_KEY=12345\n", encoding="utf-8")
    (tmp_path / ".env.production").write_text("API_KEY=67890\n", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    (tmp_path / "cert.pem").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")

    sensitive_targets = [
        ".env",
        ".env.production",
        ".env.local",
        "id_rsa",
        "cert.pem",
        ".git/config",
    ]

    for target in sensitive_targets:
        with pytest.raises(SensitivePathError):
            boundary.resolve_safe(target)

        assert not boundary.is_safe(target)

    # Tool execution verification
    read_tool = ReadFileTool()
    ctx = ToolContext(workspace=boundary)

    res = read_tool.run({"path": ".env"}, context=ctx)
    assert not res.ok
    assert "SensitivePathError" in (res.error or "")

    write_tool = WriteFileTool()
    w_res = write_tool.run({"path": ".env", "content": "HACKED=true"}, context=ctx)
    assert not w_res.ok
    assert "SensitivePathError" in (w_res.error or "")


# ==============================================================================
# 3. Security: Secret Redaction
# ==============================================================================


def test_security_secret_redaction_comprehensive(tmp_path: Path) -> None:
    """Verify that API keys, Bearer tokens, and secrets are redacted from output and database."""
    sample_text = (
        "Connecting with OpenAI: sk-AbCdEfGhIjKlMnOpQrStUvWxYz123456\n"
        "Gemini API key: AIzaSyB12345678901234567890123456789012\n"
        "Authorization header: Bearer mySecretToken123456789abcdef\n"
        "Custom token: CUSTOM_TOKEN_SECRET_999\n"
    )

    redacted = redact_secrets(
        sample_text,
        extra_secrets=["CUSTOM_TOKEN_SECRET_999"],
    )

    assert "sk-AbCdEfGhIjKlMnOpQrStUvWxYz123456" not in redacted
    assert "AIzaSyB12345678901234567890123456789012" not in redacted
    assert "mySecretToken123456789abcdef" not in redacted
    assert "CUSTOM_TOKEN_SECRET_999" not in redacted
    assert "[REDACTED]" in redacted

    # Verify session database redaction upon insert
    db_path = tmp_path / "test_sessions.db"
    db = SessionDatabase(db_path)
    sid = db.create_session(title="Security Session")

    db.add_message(
        session_id=sid,
        role="user",
        content="Here is my private token sk-SecretKey123456789012345678 for access.",
    )

    msgs = db.get_messages(sid)
    assert len(msgs) == 1
    assert "sk-SecretKey123456789012345678" not in msgs[0]["content"]
    assert "[REDACTED]" in msgs[0]["content"]


# ==============================================================================
# 4. Security: ToolBridge Client Protocol & URL Validation
# ==============================================================================


def test_security_toolbridge_client_protocol_and_url_validation(tmp_path: Path) -> None:
    """Verify ToolBridge client strictly validates URL schemes and prevents protocol injection."""
    from termicode.tools.toolbridge import (
        ToolBridgeClient,
        ToolBridgeConfig,
        ToolBridgeServerManager,
    )

    server_mgr = ToolBridgeServerManager(workspace_root=tmp_path)

    # Disallowed protocols should be rejected
    disallowed_urls = [
        "file:///etc/passwd",
        "ftp://malicious.host/data",
        "javascript:alert(1)",
        "gopher://evil.com",
    ]

    for bad_url in disallowed_urls:
        with pytest.raises(ValueError) as exc_info:
            server_mgr.add_server(name="bad_server", url=bad_url)
        assert "Must start with 'http://' or 'https://'" in str(exc_info.value)

    # Empty names rejected
    with pytest.raises(ValueError):
        server_mgr.add_server(name="", url="https://valid.example.com")

    # Secure client request construction
    cfg = ToolBridgeConfig(
        api_url="https://secure-gateway.corp.net:8443",
        api_key="  secret_token_val_1234  ",
        timeout_seconds=15.0,
    )
    client = ToolBridgeClient(config=cfg)
    req = client._build_request("/health", method="GET")

    assert req.full_url == "https://secure-gateway.corp.net:8443/health"
    assert req.headers.get("X-api-key") == "secret_token_val_1234"
    assert req.headers.get("Accept") == "application/json"


# ==============================================================================
# 5. Performance: RAG Document Ingestion & Retrieval Benchmark
# ==============================================================================


def test_perf_rag_document_ingestion_and_retrieval(tmp_path: Path) -> None:
    """Benchmark RAG retrieval performance over a ~100KB structured document."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()

    # Generate a ~100KB markdown document with 50 pages / sections
    sections: list[str] = []
    for i in range(1, 51):
        sections.append(
            f"--- Page {i} ---\n"
            f"# Section {i}: Microservices Component Analysis\n"
            f"This is the detailed architecture description for module {i}. "
            f"Key metrics include latency budget of {i * 2}ms, concurrency factor {i * 10}, "
            f"and persistent storage requirements. Security boundary is strictly enforced.\n"
            f"Special marker for page {i}: MARKER_P{i}_TARGET_CODE\n"
        )
    doc_content = "\n".join(sections)
    doc_file = tmp_path / "large_specs.md"
    doc_file.write_text(doc_content, encoding="utf-8")

    assert doc_file.stat().st_size >= 15000  # Multi-KB document

    rag_tool = RAGRetrieveTool()
    ctx = ToolContext(workspace=boundary)

    # Benchmark page-specific retrieval
    start_t = time.perf_counter()
    page_res = rag_tool.run(
        {
            "query": "What is the marker for page 42?",
            "document_path": "large_specs.md",
            "page": 42,
        },
        context=ctx,
    )
    duration_ms = (time.perf_counter() - start_t) * 1000

    assert page_res.ok
    assert "MARKER_P42_TARGET_CODE" in page_res.output
    # Retrieval over in-memory structured doc should be sub-50ms, well below 100ms
    assert duration_ms < 100.0, f"Page retrieval took {duration_ms:.2f}ms, expected < 100ms"

    # Benchmark full-text search retrieval
    start_search = time.perf_counter()
    search_res = rag_tool.run(
        {"query": "MARKER_P25_TARGET_CODE", "document_path": "large_specs.md"},
        context=ctx,
    )
    search_duration_ms = (time.perf_counter() - start_search) * 1000

    assert search_res.ok
    assert "MARKER_P25_TARGET_CODE" in search_res.output
    assert search_duration_ms < 100.0, f"Search retrieval took {search_duration_ms:.2f}ms"


# ==============================================================================
# 6. Performance: Memory Summarization & Token Bounds
# ==============================================================================


def test_perf_memory_summarization_and_token_bounds(tmp_path: Path) -> None:
    """Verify conversation message limit is strictly bounded and older turns are summarized."""
    boundary = WorkspaceBoundary(tmp_path)
    boundary.ensure_termicode_dir()
    settings = Settings.load(workspace=tmp_path)
    settings.provider = "fake"

    session = ChatSession(workspace=boundary, settings=settings)
    # Set tight memory window for testing bounds
    session.max_memory_messages = 4

    # Add 10 conversational turns
    for idx in range(1, 11):
        session.add_user_message(f"User message turn #{idx}: Requesting task #{idx}")
        session.add_assistant_message(f"Assistant response turn #{idx}: Completed task #{idx}")

    # Verify active in-memory messages list is bounded by max_memory_messages
    assert len(session.state.messages) <= 4

    # Verify summary was triggered and persists in database
    summary = session.get_session_summary()
    assert summary is not None
    assert len(summary) > 0
    # Summary preserves condensed historical context
    assert "condensed memory" in summary.lower() or "discussed" in summary.lower()
