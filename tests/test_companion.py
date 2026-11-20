"""Acceptance and security tests for TermiCode Local Companion Service (Phase 5).

Verifies:
1. Disallowed origins return 403 Forbidden.
2. Missing or invalid companion tokens return 401 Unauthorized.
3. Valid tokens grant access to authorized workspace endpoints.
4. Path traversal escaping workspace boundary is strictly blocked.
5. Sensitive files (.env, keys, credentials) are blocked from inspection.
6. Mutating tool runs trigger the approval gate and return diff previews.
7. Denied actions cause no file mutation on disk.
8. Approved actions apply mutations safely.
9. Health probe returns operational metrics without exposing secrets.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from termicode.companion.server import CompanionServer
from termicode.config import Settings
from termicode.providers.base import CompletionResponse, ToolCall
from termicode.providers.fake import FakeProvider


@pytest.fixture
def companion_service(tmp_path: Path):
    """Fixture to start an isolated CompanionServer on an ephemeral port."""
    # Create sample files in workspace
    (tmp_path / "hello.py").write_text("print('hello world')\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET_KEY=super_secret_123\n", encoding="utf-8")
    (tmp_path / "secrets.pem").write_text("PRIVATE KEY\n", encoding="utf-8")

    settings = Settings(
        provider="fake",
        model="fake-model",
    )
    server = CompanionServer(
        workspace_path=tmp_path,
        host="127.0.0.1",
        port=0,  # OS assigned ephemeral port
        token="test_companion_secret_token_12345",
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        settings=settings,
        provider=FakeProvider(),
    )
    server.start(block=False)
    time.sleep(0.1)

    yield server, tmp_path

    server.stop()


def _make_req(
    server: CompanionServer,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    origin: str | None = None,
) -> tuple[int, dict]:
    """Helper to perform HTTP request against the companion server."""
    url = f"http://127.0.0.1:{server.port}{path}"
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["X-Companion-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"

    req = Request(url=url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=5) as resp:
            status = resp.status
            content = json.loads(resp.read().decode("utf-8"))
            return status, content
    except HTTPError as e:
        status = e.code
        try:
            content = json.loads(e.read().decode("utf-8"))
        except Exception:
            content = {"detail": str(e)}
        return status, content


# ==============================================================================
# 1. Health Probe
# ==============================================================================


def test_companion_health_public(companion_service):
    """Verify /health returns 200 without token and does not leak secrets."""
    server, tmp_path = companion_service
    status, data = _make_req(server, "/health")
    assert status == 200
    assert data["status"] == "healthy"
    assert data["service"] == "TermiCode Companion Service"
    assert data["workspace_root"] == str(tmp_path)
    # Ensure token is never in public health payload
    assert "token" not in data


# ==============================================================================
# 2. Authentication Gate (401 on missing/invalid token)
# ==============================================================================


def test_companion_unauthenticated_request_rejected(companion_service):
    """Verify /api/status returns 401 when token is missing."""
    server, _ = companion_service
    status, data = _make_req(server, "/api/status", token=None)
    assert status == 401
    assert "Authentication required" in data.get("error", "")


def test_companion_wrong_token_rejected(companion_service):
    """Verify /api/status returns 401 when token is invalid."""
    server, _ = companion_service
    status, data = _make_req(server, "/api/status", token="wrong_invalid_token")
    assert status == 401


def test_companion_valid_token_accepted(companion_service):
    """Verify /api/status returns 200 when token is valid."""
    server, tmp_path = companion_service
    status, data = _make_req(server, "/api/status", token=server.token)
    assert status == 200
    assert data["status"] == "active"
    assert data["workspace"] == str(tmp_path)


# ==============================================================================
# 3. Origin & CSRF Protection (403 on disallowed origin)
# ==============================================================================


def test_companion_disallowed_origin_rejected(companion_service):
    """Verify requests with untrusted Origin header are rejected with 403."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/status",
        token=server.token,
        origin="http://malicious-attacker-website.com",
    )
    assert status == 403
    assert "Origin" in data.get("error", "")


def test_companion_allowed_origin_accepted(companion_service):
    """Verify requests with trusted localhost origin are permitted."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/status",
        token=server.token,
        origin="http://127.0.0.1:8765",
    )
    assert status == 200


# ==============================================================================
# 4. Path Traversal & Sensitive File Protection
# ==============================================================================


def test_companion_blocks_path_traversal(companion_service):
    """Verify directory traversal attempts returning 403."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/workspace/file?path=../../outside.txt",
        token=server.token,
    )
    assert status == 403
    assert "Security Boundary" in data.get("error", "")


def test_companion_blocks_sensitive_env_file(companion_service):
    """Verify accessing .env file is blocked by WorkspaceBoundary."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/workspace/file?path=.env",
        token=server.token,
    )
    assert status == 403
    assert "Security Boundary" in data.get("error", "")


def test_companion_blocks_sensitive_pem_file(companion_service):
    """Verify accessing private keys is blocked by WorkspaceBoundary."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/workspace/file?path=secrets.pem",
        token=server.token,
    )
    assert status == 403


def test_companion_allows_safe_file(companion_service):
    """Verify accessing regular safe files inside workspace works."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/workspace/file?path=hello.py",
        token=server.token,
    )
    assert status == 200
    assert "hello world" in data["content"]


# ==============================================================================
# 5. Mutation Approval Gate & Rejection
# ==============================================================================


def test_companion_task_run_triggers_approval_gate(companion_service):
    """Verify mutating task triggers needs_approval with diff preview."""
    server, tmp_path = companion_service
    server._provider.enqueue(
        CompletionResponse(
            tool_calls=[
                ToolCall(
                    id="call_write_1",
                    name="write_file",
                    arguments={"path": "created_by_test.txt", "content": "hello world from companion"},
                )
            ],
            finish_reason="tool_calls",
        )
    )
    status, data = _make_req(
        server,
        "/api/run",
        method="POST",
        body={"task": "create a new file named created_by_test.txt with content hello", "workflow": "generate"},
        token=server.token,
    )
    assert status == 200
    assert data["status"] == "needs_approval"
    assert "approval_id" in data
    assert data["tool"] in ("write_file", "apply_patch")
    assert "created_by_test.txt" in (data.get("preview") or "") or "created_by_test.txt" in str(data.get("diff") or "")


def test_companion_denied_approval_causes_zero_disk_mutation(companion_service):
    """Verify that denying approval leaves the filesystem completely untouched."""
    server, tmp_path = companion_service
    target_file = tmp_path / "should_not_exist.txt"
    assert not target_file.exists()

    server._provider.enqueue(
        CompletionResponse(
            tool_calls=[
                ToolCall(
                    id="call_write_deny",
                    name="write_file",
                    arguments={"path": "should_not_exist.txt", "content": "denied payload"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    # Propose mutation
    status, data = _make_req(
        server,
        "/api/run",
        method="POST",
        body={"task": "create file should_not_exist.txt", "workflow": "generate"},
        token=server.token,
    )
    assert status == 200
    assert data["status"] == "needs_approval"
    app_id = data["approval_id"]

    # Explicitly deny the action
    app_status, app_data = _make_req(
        server,
        "/api/approve",
        method="POST",
        body={"approval_id": app_id, "approved": False},
        token=server.token,
    )
    assert app_status == 200
    assert app_data["status"] == "denied"

    # Disk must NOT have the file
    assert not target_file.exists(), "Target file must not be created when approval is denied!"


def test_companion_approved_action_modifies_disk(companion_service):
    """Verify that explicitly approving an action executes the mutation."""
    server, tmp_path = companion_service
    target_file = tmp_path / "approved_file.txt"
    assert not target_file.exists()

    server._provider.enqueue(
        CompletionResponse(
            tool_calls=[
                ToolCall(
                    id="call_write_approve",
                    name="write_file",
                    arguments={"path": "approved_file.txt", "content": "test-approved payload"},
                )
            ],
            finish_reason="tool_calls",
        )
    )

    status, data = _make_req(
        server,
        "/api/run",
        method="POST",
        body={"task": "create file approved_file.txt with content test-approved", "workflow": "generate"},
        token=server.token,
    )
    assert status == 200
    assert data["status"] == "needs_approval"
    app_id = data["approval_id"]

    # Explicitly approve
    app_status, app_data = _make_req(
        server,
        "/api/approve",
        method="POST",
        body={"approval_id": app_id, "approved": True},
        token=server.token,
    )
    assert app_status == 200
    assert app_data["status"] == "executed"

    # Disk MUST have the file
    assert target_file.exists()
    assert "test-approved payload" in target_file.read_text(encoding="utf-8")
    assert "test-approved" in target_file.read_text(encoding="utf-8")


# ==============================================================================
# 6. Advanced Edge Cases, CSRF & Defense Hardening
# ==============================================================================


def _make_text_req(
    server: CompanionServer,
    path: str,
    method: str = "GET",
    token: str | None = None,
    origin: str | None = None,
) -> tuple[int, str]:
    """Helper to perform HTTP request and return raw string response."""
    url = f"http://127.0.0.1:{server.port}{path}"
    headers = {}
    if token is not None:
        headers["X-Companion-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    req = Request(url=url, headers=headers, method=method)
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_companion_null_origin_rejected(companion_service):
    """Verify that Origin: null (from sandboxed iframes) is rejected with 403."""
    server, _ = companion_service
    status, data = _make_req(
        server,
        "/api/status",
        token=server.token,
        origin="null",
    )
    assert status == 403
    assert "Origin 'null' is not permitted" in data.get("error", "")


def test_companion_cors_options_preflight_checks_origin(companion_service):
    """Verify OPTIONS pre-flight checks origin and returns 403 on untrusted origin."""
    server, _ = companion_service
    status, body = _make_text_req(
        server,
        "/api/status",
        method="OPTIONS",
        origin="http://untrusted-cross-site-attacker.com",
    )
    assert status == 403


def test_companion_unauthenticated_root_does_not_leak_token(companion_service):
    """Verify GET / without token returns login page without exposing secret token."""
    server, _ = companion_service
    status, html = _make_text_req(server, "/")
    assert status == 200
    assert "Authentication Required" in html
    assert server.token not in html


def test_companion_authenticated_root_with_query_token(companion_service):
    """Verify GET /?token=... returns the full companion dashboard."""
    server, _ = companion_service
    status, html = _make_text_req(server, f"/?token={server.token}")
    assert status == 200
    assert "Local Companion Control Center" in html
    assert f'const COMPANION_TOKEN = "{server.token}"' in html


def test_companion_malformed_json_returns_400(companion_service):
    """Verify invalid or non-dict JSON body returns 400 error cleanly."""
    server, _ = companion_service
    # Test array payload
    status, data = _make_req(
        server,
        "/api/run",
        method="POST",
        body=[1, 2, 3],  # type: ignore
        token=server.token,
    )
    assert status == 400
    assert "cannot be empty" in data.get("error", "")

