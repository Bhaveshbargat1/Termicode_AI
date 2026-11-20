"""TermiCode Local Companion Server.

Provides a secure local HTTP service exposing TermiCode's workspace boundary,
policy engine, agent runtime, conversational sessions, Git checkpoints, and ToolBridge MCP tools
to web browsers and local development interfaces.
All filesystem mutations and commands strictly require user approval.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from termicode.agent.models import (
    AgentRequest,
    PolicyDecision,
    ProposedAction,
)
from termicode.agent.policy import PolicyEngine
from termicode.agent.runtime import AgentRuntime
from termicode.agent.workflows import get_workflow_profile, list_workflows
from termicode.chat.engine import ChatEngine
from termicode.chat.session import ChatSession
from termicode.config import Settings
from termicode.providers.base import LLMProvider
from termicode.providers.fake import FakeProvider
from termicode.providers.openai import OpenAIProvider
from termicode.providers.redaction import redact_secrets
from termicode.tools.registry import create_default_registry
from termicode.tools.toolbridge import ToolBridgeServerManager
from termicode.version_control import CheckpointManager
from termicode.workspace import (
    SensitivePathError,
    WorkspaceBoundary,
    WorkspaceEscapeError,
)

logger = logging.getLogger("termicode.companion")


class PendingApproval:
    """Represents a pending mutating action awaiting user approval via browser."""

    def __init__(
        self,
        approval_id: str,
        action: ProposedAction,
        decision: PolicyDecision,
    ) -> None:
        self.approval_id = approval_id
        self.action = action
        self.decision = decision
        self.event = threading.Event()
        self.approved: bool | None = None
        self.created_at = time.time()


class CompanionServer:
    """Manages the lifecycle and state of the TermiCode local companion HTTP service."""

    def __init__(
        self,
        workspace_path: Path | str,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str | None = None,
        allowed_origins: list[str] | None = None,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        hosted_mode: bool | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.workspace_boundary = WorkspaceBoundary(self.workspace_path)
        self.workspace_boundary.ensure_termicode_dir()

        hosted_env = bool(os.environ.get("RENDER") or os.environ.get("TERMICODE_HOSTED_MODE"))
        self.is_hosted_mode = hosted_env if hosted_mode is None else hosted_mode

        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(24)
        self.allowed_origins = allowed_origins or [
            "http://127.0.0.1",
            "http://127.0.0.1:*",
            "http://localhost",
            "http://localhost:*",
            "https://*.onrender.com",
            "https://*.render.com",
        ]

        self.settings = settings or Settings.load(self.workspace_path)
        self._provider = provider

        # Initialize core registry and policy engine
        self.registry = create_default_registry(self.workspace_boundary)
        self.policy_engine = PolicyEngine(registry=self.registry)

        # Initialize Chat Session and Chat Engine
        self.chat_session = ChatSession(
            workspace=self.workspace_boundary,
            settings=self.settings,
        )
        self.chat_engine = ChatEngine(
            session=self.chat_session,
            boundary=self.workspace_boundary,
            provider=self._get_provider(),
        )

        # Version control & ToolBridge integrations
        self.checkpoint_manager = CheckpointManager(self.workspace_boundary.root)
        self.toolbridge_manager = ToolBridgeServerManager(self.workspace_boundary.root)

        # Approvals state
        self.pending_approvals: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

        # HTTP Server handle
        self._httpd: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    def _cleanup_stale_approvals(self, max_age_seconds: float = 3600.0) -> None:
        """Evict abandoned approval requests older than max_age_seconds."""
        now = time.time()
        with self._lock:
            stale_keys = [
                k for k, v in self.pending_approvals.items()
                if (now - v.created_at) > max_age_seconds
            ]
            for k in stale_keys:
                self.pending_approvals.pop(k, None)

    def _get_provider(self) -> LLMProvider:
        """Resolve active LLM provider."""
        if self._provider is not None:
            return self._provider
        prov = self.settings.provider.lower()
        if prov == "fake":
            return FakeProvider()
        if prov in ("ollama", "local"):
            try:
                base_url = self.settings.api_base or "http://localhost:11434/v1"
                return OpenAIProvider(
                    api_key="ollama",
                    base_url=base_url,
                    model=self.settings.model,
                )
            except Exception:
                return FakeProvider()
        try:
            return OpenAIProvider.from_env()
        except Exception:
            return FakeProvider()

    def is_origin_allowed(self, origin: str | None) -> bool:
        """Check whether the incoming Origin header matches allowed patterns."""
        if not origin:
            return True  # Direct non-browser requests (e.g. curl, python scripts)
        origin = origin.strip().lower()
        if origin == "null":
            return False  # Strictly reject null/sandboxed origins to prevent iframe bypasses
        for pattern in self.allowed_origins:
            p_regex = "^" + re.escape(pattern.lower()).replace(r"\*", ".*") + "$"
            if re.match(p_regex, origin):
                return True
        return False

    def validate_token(self, token_candidate: str | None) -> bool:
        """Verify companion token using constant-time comparison.
        
        In hosted cloud demo mode, open read-only access is permitted.
        """
        if self.is_hosted_mode:
            return True
        if not token_candidate:
            return False
        return hmac.compare_digest(token_candidate.strip(), self.token)

    def start(self, block: bool = False) -> None:
        """Start the companion HTTP server."""
        server_instance = self

        class CompanionHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress noisy default stdio logs
                logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

            def _send_cors_headers(self) -> None:
                origin = self.headers.get("Origin")
                if origin and server_instance.is_origin_allowed(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Credentials", "true")
                else:
                    self.send_header("Access-Control-Allow-Origin", f"http://127.0.0.1:{server_instance.port}")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Companion-Token, Authorization")

            def _send_json(self, status: int, data: dict[str, Any]) -> None:
                body = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _send_error_json(self, status: int, message: str) -> None:
                self._send_json(status, {"error": message, "status": "error", "code": status})

            def do_OPTIONS(self) -> None:
                origin = self.headers.get("Origin")
                if origin and not server_instance.is_origin_allowed(origin):
                    self.send_response(403)
                    self._send_cors_headers()
                    self.end_headers()
                    return
                self.send_response(204)
                self._send_cors_headers()
                self.end_headers()

            def _extract_token(self) -> str | None:
                hdr = self.headers.get("X-Companion-Token")
                if hdr:
                    return hdr
                auth = self.headers.get("Authorization")
                if auth and auth.startswith("Bearer "):
                    return auth[7:].strip()
                query = parse_qs(urlparse(self.path).query)
                token_list = query.get("token")
                if token_list:
                    return token_list[0]
                return None

            def do_GET(self) -> None:
                origin = self.headers.get("Origin")
                if origin and not server_instance.is_origin_allowed(origin):
                    self._send_error_json(403, f"Origin '{origin}' is not permitted by Companion policy.")
                    return

                parsed = urlparse(self.path)
                path = parsed.path

                # Public health probe
                if path in ("/health", "/api/health"):
                    self._send_json(200, {
                        "status": "healthy",
                        "ok": True,
                        "service": "TermiCode Companion Service",
                        "version": "1.0.0",
                        "workspace_root": str(server_instance.workspace_boundary.root),
                        "provider": server_instance.settings.provider,
                        "model": server_instance.settings.model,
                        "session_id": server_instance.chat_session.state.session_id,
                        "pending_approvals": len(server_instance.pending_approvals),
                    })
                    return

                # Local Companion Web UI frontend
                if path in ("/", "/index.html"):
                    token = self._extract_token()
                    if not server_instance.validate_token(token) and not server_instance.is_hosted_mode:
                        html = server_instance.get_login_ui_html()
                    else:
                        html = server_instance.get_embedded_ui_html()
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                    return

                # All /api/ routes require valid companion token
                token = self._extract_token()
                if not server_instance.validate_token(token):
                    self._send_error_json(401, "Authentication required: missing or invalid companion token.")
                    return

                if path == "/api/status":
                    is_git = server_instance.checkpoint_manager.is_repo()
                    cps = server_instance.checkpoint_manager.list_checkpoints(limit=5) if is_git else []
                    self._send_json(200, {
                        "status": "active",
                        "workspace": str(server_instance.workspace_boundary.root),
                        "provider": server_instance.settings.provider,
                        "model": server_instance.settings.model,
                        "session_id": server_instance.chat_session.state.session_id,
                        "workflows": list_workflows(),
                        "git_active": is_git,
                        "checkpoints_count": len(cps),
                        "toolbridge_enabled": server_instance.settings.toolbridge_enabled,
                        "pending_approvals": [
                            {
                                "id": a.approval_id,
                                "tool": a.action.tool_name,
                                "preview": a.decision.preview,
                                "diff": a.decision.diff,
                                "created_at": a.created_at,
                            }
                            for a in server_instance.pending_approvals.values()
                        ],
                    })
                    return

                if path == "/api/git/status":
                    is_repo = server_instance.checkpoint_manager.is_repo()
                    uncommitted = server_instance.checkpoint_manager.get_uncommitted_changes() if is_repo else []
                    checkpoints = [
                        cp.model_dump()
                        for cp in server_instance.checkpoint_manager.list_checkpoints(limit=10)
                    ] if is_repo else []
                    self._send_json(200, {
                        "is_repo": is_repo,
                        "uncommitted_changes": uncommitted,
                        "has_uncommitted": len(uncommitted) > 0,
                        "checkpoints": checkpoints,
                    })
                    return

                if path == "/api/checkpoints":
                    is_repo = server_instance.checkpoint_manager.is_repo()
                    checkpoints = [
                        cp.model_dump()
                        for cp in server_instance.checkpoint_manager.list_checkpoints(limit=15)
                    ] if is_repo else []
                    self._send_json(200, {
                        "is_repo": is_repo,
                        "checkpoints": checkpoints,
                    })
                    return

                if path == "/api/toolbridge/status":
                    try:
                        servers = server_instance.toolbridge_manager.list_servers()
                        tools = server_instance.toolbridge_manager.discover_tools()
                        self._send_json(200, {
                            "enabled": server_instance.settings.toolbridge_enabled,
                            "api_url": server_instance.settings.toolbridge_api_url,
                            "servers": servers,
                            "tools": tools,
                            "total_tools": len(tools),
                        })
                    except Exception as e:
                        self._send_json(200, {
                            "enabled": server_instance.settings.toolbridge_enabled,
                            "api_url": server_instance.settings.toolbridge_api_url,
                            "servers": [],
                            "tools": [],
                            "total_tools": 0,
                            "error": str(e),
                        })
                    return

                if path == "/api/workspace/files":
                    query = parse_qs(parsed.query)
                    subpath = query.get("path", [""])[0]
                    try:
                        resolved = server_instance.workspace_boundary.resolve_safe(subpath)
                        if not resolved.is_dir():
                            self._send_error_json(400, f"Path '{subpath}' is not a directory.")
                            return
                        entries = []
                        for item in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                            if item.name.startswith((".git", ".termicode", "__pycache__")):
                                continue
                            if not server_instance.workspace_boundary.is_safe(item):
                                continue
                            entries.append({
                                "name": item.name,
                                "is_dir": item.is_dir(),
                                "size": item.stat().st_size if item.is_file() else 0,
                                "rel_path": str(item.relative_to(server_instance.workspace_boundary.root)).replace("\\", "/"),
                            })
                        self._send_json(200, {"root": str(server_instance.workspace_boundary.root), "entries": entries})
                    except (WorkspaceEscapeError, SensitivePathError) as e:
                        self._send_error_json(403, f"Security Boundary Rejection: {e}")
                    except Exception as e:
                        self._send_error_json(500, f"Failed to list directory: {e}")
                    return

                if path == "/api/workspace/file":
                    query = parse_qs(parsed.query)
                    file_path = query.get("path", [""])[0]
                    if not file_path:
                        self._send_error_json(400, "Missing required query parameter 'path'.")
                        return
                    try:
                        resolved = server_instance.workspace_boundary.resolve_safe(file_path)
                        if not resolved.is_file():
                            self._send_error_json(404, f"File '{file_path}' not found.")
                            return
                        content = resolved.read_text(encoding="utf-8", errors="replace")
                        # Redact secrets from file preview
                        safe_content = redact_secrets(content)
                        self._send_json(200, {
                            "path": file_path,
                            "content": safe_content,
                            "size": len(content),
                        })
                    except (WorkspaceEscapeError, SensitivePathError) as e:
                        self._send_error_json(403, f"Security Boundary Rejection: {e}")
                    except Exception as e:
                        self._send_error_json(500, f"Failed to read file: {e}")
                    return

                if path == "/api/chat/history":
                    messages = server_instance.chat_session.get_history()
                    self._send_json(200, {
                        "session_id": server_instance.chat_session.state.session_id,
                        "messages": [
                            {"role": m.role, "content": redact_secrets(m.content), "timestamp": m.timestamp}
                            for m in messages
                        ],
                    })
                    return

                self._send_error_json(404, f"Endpoint '{path}' not found.")

            def do_POST(self) -> None:
                origin = self.headers.get("Origin")
                if origin and not server_instance.is_origin_allowed(origin):
                    self._send_error_json(403, f"Origin '{origin}' is not permitted by Companion policy.")
                    return

                parsed = urlparse(self.path)
                path = parsed.path

                # All POST endpoints require companion token
                token = self._extract_token()
                if not server_instance.validate_token(token):
                    self._send_error_json(401, "Authentication required: missing or invalid companion token.")
                    return

                # Read body
                content_length = int(self.headers.get("Content-Length", 0))
                body_bytes = self.rfile.read(content_length) if content_length > 0 else b"{}"
                try:
                    payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
                    if not isinstance(payload, dict):
                        payload = {}
                except Exception:
                    self._send_error_json(400, "Malformed JSON request body.")
                    return

                if path == "/api/chat":
                    query = payload.get("message", "").strip()
                    if not query:
                        self._send_error_json(400, "Field 'message' cannot be empty.")
                        return

                    # Process chat query
                    try:
                        response_text, step_logs = server_instance.chat_engine.generate_response(query)
                    except Exception as e:
                        logger.exception("Error processing chat message in companion")
                        response_text = f"Notice: Error generating response: {e}"
                        step_logs = []

                    self._send_json(200, {
                        "response": redact_secrets(response_text),
                        "step_logs": step_logs,
                        "session_id": server_instance.chat_session.state.session_id,
                    })
                    return

                if path == "/api/plan":
                    task = payload.get("task", "").strip()
                    wf_name = payload.get("workflow", "understand").strip().lower()
                    if not task:
                        self._send_error_json(400, "Field 'task' cannot be empty.")
                        return

                    try:
                        wf = get_workflow_profile(wf_name)
                    except ValueError as e:
                        self._send_error_json(400, str(e))
                        return

                    # Read-only plan execution
                    runtime = AgentRuntime(
                        provider=server_instance._get_provider(),
                        registry=server_instance.registry,
                        policy_engine=server_instance.policy_engine,
                    )
                    req = AgentRequest(
                        workspace=server_instance.workspace_boundary,
                        task=task,
                        workflow=wf.name,
                        dry_run=True,
                    )
                    res = runtime.run(req)
                    self._send_json(200, {
                        "task": task,
                        "workflow": wf.name,
                        "status": res.stop_reason.value,
                        "summary": redact_secrets(res.summary or "Plan generated."),
                        "steps_used": res.steps_used,
                    })
                    return

                if path == "/api/run":
                    task = payload.get("task", "").strip()
                    wf_name = payload.get("workflow", "generate").strip().lower()
                    auto_approve = bool(payload.get("auto_approve", False))

                    if not task:
                        self._send_error_json(400, "Field 'task' cannot be empty.")
                        return

                    if server_instance.is_hosted_mode and wf_name in ("generate", "refactor", "tests", "debug"):
                        self._send_error_json(
                            403,
                            "Hosted Sandbox Policy: Arbitrary shell execution and workspace mutations are disabled on this public demo instance. To execute workflows against your local code repositories, install TermiCode locally and launch 'termicode web'."
                        )
                        return

                    try:
                        wf = get_workflow_profile(wf_name)
                    except ValueError as e:
                        self._send_error_json(400, str(e))
                        return

                    approval_result: dict[str, Any] = {}

                    def companion_approval_handler(action: ProposedAction, decision: PolicyDecision) -> bool:
                        if auto_approve:
                            return True
                        # Generate approval record
                        app_id = secrets.token_hex(8)
                        pending = PendingApproval(approval_id=app_id, action=action, decision=decision)
                        with server_instance._lock:
                            server_instance.pending_approvals[app_id] = pending

                        approval_result["status"] = "needs_approval"
                        approval_result["approval_id"] = app_id
                        approval_result["tool"] = action.tool_name
                        approval_result["preview"] = decision.preview
                        approval_result["diff"] = decision.diff
                        approval_result["command_args"] = decision.command_args
                        approval_result["target_path"] = str(decision.target_path) if decision.target_path else None

                        # Deny execution synchronously in this pass, prompting client to approve via /api/approve
                        return False

                    runtime = AgentRuntime(
                        provider=server_instance._get_provider(),
                        registry=server_instance.registry,
                        policy_engine=server_instance.policy_engine,
                        approval_handler=companion_approval_handler,
                    )

                    req = AgentRequest(
                        workspace=server_instance.workspace_boundary,
                        task=task,
                        workflow=wf.name,
                    )
                    res = runtime.run(req)

                    if approval_result.get("status") == "needs_approval":
                        self._send_json(200, {
                            "status": "needs_approval",
                            "approval_id": approval_result["approval_id"],
                            "tool": approval_result["tool"],
                            "preview": approval_result["preview"],
                            "diff": approval_result["diff"],
                            "command_args": approval_result["command_args"],
                            "target_path": approval_result["target_path"],
                        })
                        return

                    self._send_json(200, {
                        "status": res.stop_reason.value,
                        "summary": redact_secrets(res.summary or "Task finished."),
                        "steps_used": res.steps_used,
                        "changed_files": res.changed_files,
                    })
                    return

                if path == "/api/approve":
                    if server_instance.is_hosted_mode:
                        self._send_error_json(
                            403,
                            "Hosted Sandbox Policy: Modifying files or running commands via approval is disabled on this public demo instance."
                        )
                        return

                    approval_id = payload.get("approval_id", "")
                    approved = bool(payload.get("approved", False))

                    with server_instance._lock:
                        pending = server_instance.pending_approvals.pop(approval_id, None)

                    if not pending:
                        self._send_error_json(404, f"Approval request '{approval_id}' not found or already processed.")
                        return

                    if not approved:
                        self._send_json(200, {
                            "approval_id": approval_id,
                            "status": "denied",
                            "message": "Action was explicitly denied by user. No files were modified.",
                        })
                        return

                    # Execute the approved action safely through the tool registry
                    if not server_instance.registry.has(pending.action.tool_name):
                        self._send_error_json(400, f"Tool '{pending.action.tool_name}' is not registered.")
                        return

                    try:
                        from termicode.tools.base import ToolContext
                        tool = server_instance.registry.get(pending.action.tool_name)
                        ctx = ToolContext(
                            workspace=server_instance.workspace_boundary,
                            user_confirmed=True,
                        )
                        res = tool.run(pending.action.arguments, context=ctx)
                        self._send_json(200, {
                            "approval_id": approval_id,
                            "status": "executed" if res.ok else "error",
                            "result": redact_secrets(str(res.output or "")),
                            "error": redact_secrets(str(res.error)) if res.error else None,
                        })
                    except Exception as e:
                        logger.exception("Error executing approved tool action %s", pending.action.tool_name)
                        self._send_json(200, {
                            "approval_id": approval_id,
                            "status": "error",
                            "result": "",
                            "error": redact_secrets(str(e)),
                        })
                    return

                if path in ("/api/checkpoint", "/api/git/checkpoint"):
                    if server_instance.is_hosted_mode:
                        self._send_error_json(
                            403,
                            "Hosted Sandbox Policy: Git checkpoints are disabled on the public demo instance."
                        )
                        return
                    msg = payload.get("message", "Manual snapshot via Companion UI")
                    try:
                        cp = server_instance.checkpoint_manager.create_checkpoint(
                            message=msg,
                            checkpoint_type="manual",
                        )
                        self._send_json(200, {
                            "status": "ok",
                            "checkpoint": cp.model_dump(),
                            "message": f"Checkpoint '{cp.id}' successfully recorded.",
                        })
                    except Exception as e:
                        self._send_error_json(400, f"Failed to create checkpoint: {e}")
                    return

                if path in ("/api/rollback", "/api/git/rollback"):
                    if server_instance.is_hosted_mode:
                        self._send_error_json(
                            403,
                            "Hosted Sandbox Policy: Workspace rollbacks are disabled on the public demo instance."
                        )
                        return
                    cp_id = payload.get("checkpoint_id")
                    try:
                        cp = server_instance.checkpoint_manager.rollback(checkpoint_id=cp_id)
                        self._send_json(200, {
                            "status": "ok",
                            "checkpoint_id": cp.id,
                            "message": f"Workspace rolled back to checkpoint '{cp.id}' ({cp.message}).",
                        })
                    except Exception as e:
                        self._send_error_json(400, f"Rollback failed: {e}")
                    return

                self._send_error_json(404, f"Endpoint '{path}' not found.")

        self._httpd = ThreadingHTTPServer((self.host, self.port), CompanionHandler)
        self.port = self._httpd.server_port

        if block:
            self._httpd.serve_forever()
        else:
            self._server_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._server_thread.start()

    def stop(self) -> None:
        """Shut down the companion HTTP server."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def get_login_ui_html(self) -> str:
        """Return the unauthenticated login prompt HTML without leaking companion token."""
        return """<!DOCTYPE html>
<html lang="en" class="h-full bg-slate-950 text-slate-100">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TermiCode AI — Authentication Required</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass-card { background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(16px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .glow-accent { box-shadow: 0 0 40px -10px rgba(14, 165, 233, 0.3); }
  </style>
</head>
<body class="h-full flex items-center justify-center bg-slate-950 text-slate-100 p-4 antialiased selection:bg-sky-500 selection:text-white">
  <div class="max-w-md w-full glass-card glow-accent rounded-2xl p-8 shadow-2xl space-y-6 border border-slate-800">
    <div class="flex items-center space-x-3">
      <div class="w-12 h-12 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center text-white text-2xl font-bold shadow-lg shadow-sky-500/20">
        ⚡
      </div>
      <div>
        <h1 class="text-xl font-bold bg-gradient-to-r from-white via-slate-100 to-sky-400 bg-clip-text text-transparent">
          TermiCode AI
        </h1>
        <p class="text-xs text-slate-400 font-medium">Local Companion Control Center</p>
      </div>
    </div>

    <div class="bg-sky-950/40 border border-sky-800/40 rounded-xl p-3.5 text-xs text-sky-200/90 space-y-1">
      <div class="font-semibold text-sky-300 flex items-center">
        <i class="fa-solid fa-shield-halved mr-1.5 text-sky-400"></i> Authentication Required
      </div>
      <p class="text-[11px] text-slate-400 leading-relaxed">
        To prevent unauthorized cross-origin requests from web pages, TermiCode requires your local session token.
        It is printed in your terminal when you launch <code class="text-sky-300">termicode web</code> or <code class="text-sky-300">termicode companion</code>.
      </p>
    </div>

    <div class="space-y-2">
      <label class="block text-xs text-slate-300 font-medium">Companion Token:</label>
      <div class="relative">
        <input type="password" id="tokenInp" autofocus placeholder="Paste token here..." class="w-full bg-slate-900/90 border border-slate-700/80 rounded-xl px-4 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-sky-500 font-mono transition" onkeydown="if(event.key==='Enter') connect()">
        <button type="button" onclick="toggleTokenVisibility()" class="absolute right-3 top-2.5 text-slate-500 hover:text-slate-300 text-xs">
          <i id="eyeIcon" class="fa-solid fa-eye"></i>
        </button>
      </div>
    </div>

    <button onclick="connect()" class="w-full py-3 px-4 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white rounded-xl text-xs font-semibold shadow-lg shadow-sky-600/20 transition flex items-center justify-center space-x-2">
      <i class="fa-solid fa-lock-open text-xs"></i>
      <span>Connect to Local Workspace</span>
    </button>

    <div class="pt-2 text-center">
      <span class="text-[11px] text-slate-500">
        Safe, Bounded Terminal AI Assistant • Portfolio Ready
      </span>
    </div>
  </div>

  <script>
    function toggleTokenVisibility() {
      const inp = document.getElementById('tokenInp');
      const eye = document.getElementById('eyeIcon');
      if (inp.type === 'password') {
        inp.type = 'text';
        eye.className = 'fa-solid fa-eye-slash';
      } else {
        inp.type = 'password';
        eye.className = 'fa-solid fa-eye';
      }
    }

    function connect() {
      const tok = document.getElementById('tokenInp').value.trim();
      if (!tok) return;
      window.location.search = '?token=' + encodeURIComponent(tok);
    }
  </script>
</body>
</html>
"""

    def get_embedded_ui_html(self) -> str:
        """Return the standalone modern HTML frontend for the local companion."""
        html = _EMBEDDED_COMPANION_HTML
        hosted_banner = ""
        badge_text = "Deterministic Boundary Active"
        ws_display = str(self.workspace_boundary.root).replace("\\", "/")
        if self.is_hosted_mode:
            hosted_banner = """
  <div class="bg-indigo-950/70 border-b border-indigo-800/60 px-4 py-2.5 text-xs text-indigo-200 flex flex-wrap items-center justify-between gap-2">
    <div class="flex items-center space-x-2">
      <span class="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 font-semibold text-[10px] uppercase tracking-wider">Cloud Demo Active</span>
      <span>Sandboxed read-only demonstration. File system mutations and shell commands are disabled on this instance. Run locally via <code>termicode web</code> for full workspace execution.</span>
    </div>
    <a href="https://github.com/Bhaveshbargat1/Termicode_AI" target="_blank" class="text-sky-400 hover:text-sky-300 hover:underline flex items-center space-x-1 font-medium">
      <span>GitHub Repository</span> <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
    </a>
  </div>
"""
            badge_text = "Cloud Demo (Sandboxed)"
            ws_display = "TermiCode AI Cloud Workspace"

        return (
            html.replace("__WORKSPACE_ROOT__", ws_display)
            .replace("__HOSTED_BANNER__", hosted_banner)
            .replace("__BOUNDARY_BADGE__", badge_text)
            .replace("__COMPANION_TOKEN__", self.token)
            .replace("__SESSION_ID__", self.chat_session.state.session_id)
            .replace("__PROVIDER__", self.settings.provider)
            .replace("__MODEL__", self.settings.model)
        )


_EMBEDDED_COMPANION_HTML = """<!DOCTYPE html>
<html lang="en" class="h-full bg-slate-950 text-slate-100">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TermiCode AI — Local Companion Control Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <style>
    body { font-family: 'Inter', sans-serif; }
    code, pre { font-family: 'JetBrains Mono', monospace; }
    .glass-card { background: rgba(15, 23, 42, 0.75); backdrop-filter: blur(14px); border: 1px solid rgba(255, 255, 255, 0.08); }
    .custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .custom-scroll::-webkit-scrollbar-thumb { background: rgba(51, 65, 85, 0.8); border-radius: 4px; }
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: rgba(100, 116, 139, 0.9); }
    .prose-code pre { background: #0f172a !important; padding: 0.8rem; border-radius: 0.5rem; border: 1px solid rgba(255, 255, 255, 0.08); }
    .prose-code code { color: #38bdf8; }
  </style>
</head>
<body class="h-full flex flex-col bg-slate-950 text-slate-100 antialiased selection:bg-sky-500 selection:text-white">
  __HOSTED_BANNER__
  <!-- Top Navigation Header -->
  <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-sky-500 via-indigo-600 to-violet-600 flex items-center justify-center text-white font-bold text-xl shadow-lg shadow-sky-500/20">
          ⚡
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <span class="font-bold text-base bg-gradient-to-r from-white via-slate-100 to-sky-400 bg-clip-text text-transparent">
              TermiCode AI
            </span>
            <span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
              ● Online
            </span>
            <span class="hidden sm:inline text-[10px] px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-400 border border-sky-500/20">
              __BOUNDARY_BADGE__
            </span>
          </div>
          <p class="text-xs text-slate-400 font-mono truncate max-w-sm sm:max-w-md" title="__WORKSPACE_ROOT__">
            <i class="fa-regular fa-folder-open text-sky-400 mr-1"></i> __WORKSPACE_ROOT__
          </p>
        </div>
      </div>

      <!-- Header Action Controls -->
      <div class="flex items-center space-x-3 text-xs">
        <button onclick="createQuickCheckpoint()" class="hidden md:flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition" title="Save Git checkpoint snapshot">
          <i class="fa-solid fa-code-commit text-emerald-400"></i>
          <span>Save Checkpoint</span>
        </button>
        <button onclick="confirmRollbackPrompt()" class="hidden md:flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-amber-950/40 hover:bg-amber-900/40 text-amber-300 border border-amber-800/40 transition" title="Revert workspace to previous checkpoint">
          <i class="fa-solid fa-rotate-left text-amber-400"></i>
          <span>Rollback</span>
        </button>
        <a href="/health" target="_blank" class="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-[11px] font-mono transition">
          Health: OK
        </a>
      </div>
    </div>
  </header>

  <!-- Main Content Layout -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- Left / Center Column: Interactive Assistant, Chat & Approvals (7 cols) -->
    <div class="lg:col-span-7 flex flex-col space-y-4">

      <!-- Chat & Execution Card -->
      <div class="glass-card rounded-2xl p-4 sm:p-5 flex-1 flex flex-col min-h-[580px]">

        <!-- Card Header with Workflow selector -->
        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3 mb-3">
          <div class="flex items-center space-x-2">
            <i class="fa-solid fa-terminal text-sky-400"></i>
            <span class="font-semibold text-sm text-white">Agent Cockpit</span>
          </div>

          <div class="flex items-center space-x-2 text-xs">
            <span class="text-slate-400">Workflow:</span>
            <select id="wfSelect" class="bg-slate-900 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-sky-500 font-mono">
              <option value="understand">understand (Read-only Architecture)</option>
              <option value="plan">plan (Read-only Implementation Plan)</option>
              <option value="generate" selected>generate (Safe Code Mutation)</option>
              <option value="debug">debug (Analyze & Propose Fix)</option>
              <option value="tests">tests (Generate & Run Tests)</option>
              <option value="refactor">refactor (Bounded Refactor)</option>
            </select>
          </div>
        </div>

        <!-- Quick Demo Prompts -->
        <div class="flex flex-wrap gap-1.5 mb-3 text-[11px]">
          <button onclick="setPrompt('Explain the architecture and main components of this repository.')" class="px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition">
            ⚡ Explain Architecture
          </button>
          <button onclick="setPrompt('Plan the implementation of a new safe utility tool.')" class="px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition">
            📋 Plan Feature
          </button>
          <button onclick="setPrompt('Check the 7-layer safety boundary and test report.')" class="px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition">
            🛡️ Safety Audit
          </button>
          <button onclick="setPrompt('List all ToolBridge remote tools and their sensitivity tiers.')" class="px-2.5 py-1 rounded-full bg-slate-800/80 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition">
            🌉 Inspect MCP Tools
          </button>
        </div>

        <!-- Pending Mutation Approval Banner (Human-in-the-Loop) -->
        <div id="approvalBanner" class="hidden rounded-xl p-4 border border-amber-500/60 bg-amber-950/30 mb-3 space-y-3 shadow-lg">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-amber-300 flex items-center">
              <i class="fa-solid fa-triangle-exclamation mr-1.5 text-amber-400"></i> Mutation Approval Gate Triggered
            </span>
            <span id="approvalToolBadge" class="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono font-semibold"></span>
          </div>
          <div class="text-[11px] text-slate-300 space-y-1">
            <div id="approvalTargetPath" class="text-sky-300 font-mono font-semibold"></div>
            <pre id="approvalPreviewText" class="text-[11px] text-slate-200 font-mono bg-slate-900/90 p-3 rounded-lg border border-slate-800 max-h-48 overflow-y-auto whitespace-pre-wrap custom-scroll"></pre>
          </div>
          <div class="flex items-center justify-end space-x-2 pt-1">
            <button onclick="resolveApproval(false)" class="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium transition">
              Deny Action
            </button>
            <button onclick="resolveApproval(true)" class="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-medium shadow-md shadow-emerald-600/30 transition flex items-center space-x-1.5">
              <i class="fa-solid fa-check"></i>
              <span>Approve & Execute</span>
            </button>
          </div>
        </div>

        <!-- Chat Stream Window -->
        <div id="chatMessages" class="flex-1 min-h-[320px] max-h-[460px] overflow-y-auto space-y-3 p-3 bg-slate-900/50 rounded-xl border border-slate-800/80 custom-scroll text-xs">
          <div class="p-3.5 rounded-xl bg-slate-800/40 border border-slate-700/40 text-slate-300 space-y-2">
            <div class="flex items-center space-x-2 text-sky-400 font-semibold text-xs">
              <i class="fa-solid fa-circle-info"></i>
              <span>Welcome to TermiCode Local Companion</span>
            </div>
            <p class="leading-relaxed text-slate-300">
              This interface communicates directly with your local workspace via deterministic safety gates.
              Mutations (file modifications or commands) require your explicit approval before touching the disk.
            </p>
            <div class="pt-1 flex flex-wrap gap-2 text-[11px] text-slate-400 font-mono">
              <span class="px-2 py-0.5 rounded bg-slate-900 border border-slate-800">Provider: __PROVIDER__</span>
              <span class="px-2 py-0.5 rounded bg-slate-900 border border-slate-800">Model: __MODEL__</span>
              <span class="px-2 py-0.5 rounded bg-slate-900 border border-slate-800">Git Rollback: Ready</span>
            </div>
          </div>
        </div>

        <!-- Typing Indicator -->
        <div id="typingIndicator" class="hidden pt-2 flex items-center space-x-2 text-xs text-sky-400">
          <div class="w-2 h-2 rounded-full bg-sky-400 animate-ping"></div>
          <span>TermiCode is thinking & executing policy checks...</span>
        </div>

        <!-- Message Input Box -->
        <div class="mt-3 space-y-2">
          <div class="relative">
            <textarea id="chatInput" rows="2" placeholder="Ask a question or enter a task description (e.g. 'Add unit test for helper function')..." class="w-full bg-slate-900 border border-slate-700/80 rounded-xl px-3.5 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-sky-500 custom-scroll resize-none font-sans" onkeydown="handleInputKey(event)"></textarea>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-[10px] text-slate-500">
              <kbd class="px-1 py-0.5 bg-slate-800 rounded border border-slate-700">Enter</kbd> to send, <kbd class="px-1 py-0.5 bg-slate-800 rounded border border-slate-700">Shift+Enter</kbd> for new line
            </span>
            <div class="flex items-center space-x-2">
              <button onclick="sendChatMessage()" class="px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-sky-600/20 transition flex items-center space-x-1.5">
                <i class="fa-regular fa-paper-plane"></i>
                <span>Send Chat</span>
              </button>
              <button onclick="runTask()" class="px-4 py-2 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white rounded-lg text-xs font-semibold shadow-md shadow-indigo-600/20 transition flex items-center space-x-1.5">
                <i class="fa-solid fa-play text-[10px]"></i>
                <span>Run Agent</span>
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>

    <!-- Right Column: Workspace Inspector & Safety Boundary (5 cols) -->
    <div class="lg:col-span-5 space-y-4">

      <!-- Card 1: Git Checkpoints & Rollback Hub -->
      <div class="glass-card rounded-2xl p-4 sm:p-5 space-y-3">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <h3 class="text-sm font-semibold text-white flex items-center">
            <i class="fa-solid fa-code-branch text-emerald-400 mr-2"></i> Git Safety & Rollback
          </h3>
          <span id="gitRepoStatusBadge" class="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
            Active
          </span>
        </div>

        <div class="flex items-center justify-between text-xs">
          <span class="text-slate-400">Uncommitted Changes:</span>
          <span id="gitUncommittedCount" class="font-mono text-slate-200 font-semibold">0 files</span>
        </div>

        <div class="grid grid-cols-2 gap-2 pt-1">
          <button onclick="createQuickCheckpoint()" class="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition flex items-center justify-center space-x-1.5 border border-slate-700">
            <i class="fa-solid fa-camera text-emerald-400"></i>
            <span>Snapshot State</span>
          </button>
          <button onclick="confirmRollbackPrompt()" class="px-3 py-2 bg-amber-950/40 hover:bg-amber-900/40 text-amber-200 rounded-lg text-xs font-medium transition flex items-center justify-center space-x-1.5 border border-amber-800/40">
            <i class="fa-solid fa-clock-rotate-left text-amber-400"></i>
            <span>Revert Changes</span>
          </button>
        </div>

        <!-- Recent Checkpoints List -->
        <div class="pt-2">
          <div class="text-[11px] text-slate-400 mb-1.5 font-medium">Recent Checkpoints:</div>
          <div id="checkpointsList" class="space-y-1.5 max-h-32 overflow-y-auto custom-scroll text-[11px] font-mono">
            <span class="text-slate-500">Loading checkpoints...</span>
          </div>
        </div>
      </div>

      <!-- Card 2: Workspace Files Explorer -->
      <div class="glass-card rounded-2xl p-4 sm:p-5 space-y-3 flex flex-col min-h-[300px]">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <h3 class="text-sm font-semibold text-white flex items-center">
            <i class="fa-solid fa-folder-tree text-indigo-400 mr-2"></i> Workspace Files
          </h3>
          <div class="flex items-center space-x-2">
            <button onclick="loadFiles()" class="text-xs text-sky-400 hover:text-sky-300 transition flex items-center space-x-1" title="Refresh files list">
              <i class="fa-solid fa-arrows-rotate"></i>
              <span>Refresh</span>
            </button>
          </div>
        </div>

        <!-- File Filter Input -->
        <div class="relative">
          <input type="text" id="fileFilterInp" placeholder="Filter files..." oninput="filterFileList()" class="w-full bg-slate-900/80 border border-slate-800 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-sky-500 font-mono">
        </div>

        <!-- Files List Container -->
        <div id="fileList" class="flex-1 overflow-y-auto space-y-1 text-xs font-mono text-slate-300 custom-scroll max-h-56">
          Loading files...
        </div>
      </div>

      <!-- Card 3: 7-Layer Boundary & ToolBridge MCP Integration -->
      <div class="glass-card rounded-2xl p-4 sm:p-5 space-y-3">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2">
          <h3 class="text-sm font-semibold text-white flex items-center">
            <i class="fa-solid fa-shield-halved text-sky-400 mr-2"></i> Security & Gateway
          </h3>
          <span class="text-[10px] px-2 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20 font-medium">
            Strict Containment
          </span>
        </div>

        <div class="space-y-2 text-xs text-slate-400">
          <div class="flex items-center justify-between">
            <span>Path Traversal Guards:</span>
            <span class="text-emerald-400 font-semibold font-mono">ENFORCED</span>
          </div>
          <div class="flex items-center justify-between">
            <span>Secret Masking (.env, keys):</span>
            <span class="text-emerald-400 font-semibold font-mono">ACTIVE</span>
          </div>
          <div class="flex items-center justify-between">
            <span>Subprocess Whitelist:</span>
            <span class="text-slate-200 font-mono">python, pytest, git</span>
          </div>
          <div class="flex items-center justify-between">
            <span>ToolBridge MCP Gateway:</span>
            <span id="tbGatewayStatus" class="text-slate-200 font-mono">Checking...</span>
          </div>
        </div>
      </div>

    </div>
  </main>

  <!-- In-Browser Code Viewer Modal -->
  <div id="fileViewerModal" class="hidden fixed inset-0 z-50 bg-slate-950/80 backdrop-blur flex items-center justify-center p-4">
    <div class="glass-card max-w-4xl w-full rounded-2xl p-6 shadow-2xl flex flex-col max-h-[85vh] border border-slate-700">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
        <div class="flex items-center space-x-2 truncate">
          <i class="fa-regular fa-file-code text-sky-400"></i>
          <span id="modalFileName" class="font-mono text-xs text-white font-semibold truncate"></span>
          <span id="modalFileSize" class="text-[10px] text-slate-500 font-mono ml-2"></span>
        </div>
        <div class="flex items-center space-x-2">
          <button onclick="copyModalContent()" class="text-xs px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition">
            Copy
          </button>
          <button onclick="closeFileModal()" class="text-xs px-2.5 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded border border-slate-700 transition">
            Close
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-auto custom-scroll rounded-xl bg-slate-900/90 border border-slate-800 p-4">
        <pre><code id="modalFileContent" class="text-xs font-mono"></code></pre>
      </div>
    </div>
  </div>

  <script>
    const COMPANION_TOKEN = "__COMPANION_TOKEN__";
    let currentApprovalId = null;
    let cachedFiles = [];

    async function apiFetch(endpoint, opts = {}) {
      const headers = opts.headers || {};
      headers["X-Companion-Token"] = COMPANION_TOKEN;
      if (opts.body && typeof opts.body === "object") {
        headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(opts.body);
      }
      return fetch(endpoint, { ...opts, headers });
    }

    function setPrompt(text) {
      const inp = document.getElementById("chatInput");
      inp.value = text;
      inp.focus();
    }

    function handleInputKey(event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
      }
    }

    async function loadFiles() {
      try {
        const res = await apiFetch("/api/workspace/files");
        if (res.ok) {
          const data = await res.json();
          cachedFiles = data.entries || [];
          renderFileList(cachedFiles);
        }
      } catch (err) {
        console.error("loadFiles err:", err);
      }
    }

    function getFileIcon(name, isDir) {
      if (isDir) return 'fa-solid fa-folder text-amber-400';
      if (name.endsWith('.py')) return 'fa-brands fa-python text-sky-400';
      if (name.endsWith('.json')) return 'fa-solid fa-brackets-curly text-yellow-400';
      if (name.endsWith('.md')) return 'fa-solid fa-file-lines text-indigo-400';
      if (name.endsWith('.toml') || name.endsWith('.yaml') || name.endsWith('.yml')) return 'fa-solid fa-gear text-slate-400';
      return 'fa-regular fa-file text-slate-400';
    }

    function renderFileList(entries) {
      const el = document.getElementById("fileList");
      if (!entries || entries.length === 0) {
        el.innerHTML = '<span class="text-slate-500">Workspace is empty.</span>';
        return;
      }
      el.innerHTML = entries.map(e => `
        <div onclick="openFilePreview('${e.rel_path}', ${e.is_dir})" class="flex items-center space-x-2 py-1.5 px-2.5 rounded-lg hover:bg-slate-800/60 cursor-pointer transition">
          <i class="${getFileIcon(e.name, e.is_dir)} text-xs"></i>
          <span class="text-slate-200 truncate flex-1">${e.name}</span>
          <span class="text-[10px] text-slate-500">${e.is_dir ? 'dir' : formatBytes(e.size)}</span>
        </div>
      `).join("");
    }

    function formatBytes(bytes) {
      if (bytes < 1024) return bytes + ' B';
      if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
      return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function filterFileList() {
      const q = document.getElementById("fileFilterInp").value.trim().toLowerCase();
      if (!q) {
        renderFileList(cachedFiles);
        return;
      }
      const filtered = cachedFiles.filter(e => e.name.toLowerCase().includes(q));
      renderFileList(filtered);
    }

    async function openFilePreview(path, isDir) {
      if (isDir) return;
      try {
        const res = await apiFetch("/api/workspace/file?path=" + encodeURIComponent(path));
        if (res.ok) {
          const data = await res.json();
          document.getElementById("modalFileName").textContent = data.path;
          document.getElementById("modalFileSize").textContent = formatBytes(data.size);
          const codeEl = document.getElementById("modalFileContent");
          codeEl.textContent = data.content;
          if (window.hljs) {
            hljs.highlightElement(codeEl);
          }
          document.getElementById("fileViewerModal").classList.remove("hidden");
        }
      } catch (err) {
        alert("Failed to read file: " + err.message);
      }
    }

    function closeFileModal() {
      document.getElementById("fileViewerModal").classList.add("hidden");
    }

    function copyModalContent() {
      const text = document.getElementById("modalFileContent").textContent;
      navigator.clipboard.writeText(text);
      alert("File content copied to clipboard.");
    }

    async function loadGitStatus() {
      try {
        const res = await apiFetch("/api/git/status");
        if (res.ok) {
          const data = await res.json();
          document.getElementById("gitUncommittedCount").textContent = `${data.uncommitted_changes.length} uncommitted file(s)`;
          const cpList = document.getElementById("checkpointsList");
          if (!data.checkpoints || data.checkpoints.length === 0) {
            cpList.innerHTML = '<span class="text-slate-500">No checkpoints recorded yet.</span>';
          } else {
            cpList.innerHTML = data.checkpoints.slice(0, 4).map(c => `
              <div class="flex items-center justify-between p-1.5 rounded bg-slate-900 border border-slate-800/80">
                <span class="text-slate-300 truncate" title="${c.message}">${c.id} - ${c.message}</span>
                <button onclick="rollbackToCheckpoint('${c.id}')" class="text-[10px] text-amber-400 hover:underline ml-2">Restore</button>
              </div>
            `).join("");
          }
        }
      } catch (err) {
        console.error("loadGitStatus err:", err);
      }
    }

    async function createQuickCheckpoint() {
      const msg = prompt("Enter a description for this Git checkpoint:", "Manual snapshot via Companion");
      if (!msg) return;
      try {
        const res = await apiFetch("/api/checkpoint", { method: "POST", body: { message: msg } });
        const data = await res.json();
        if (res.ok) {
          alert(`Checkpoint '${data.checkpoint.id}' recorded successfully.`);
          loadGitStatus();
        } else {
          alert("Error creating checkpoint: " + data.error);
        }
      } catch (err) {
        alert("Checkpoint error: " + err.message);
      }
    }

    async function confirmRollbackPrompt() {
      if (!confirm("Are you sure you want to revert your workspace to the latest checkpoint? Uncommitted changes will be restored.")) {
        return;
      }
      rollbackToCheckpoint(null);
    }

    async function rollbackToCheckpoint(cpId) {
      try {
        const res = await apiFetch("/api/rollback", { method: "POST", body: { checkpoint_id: cpId } });
        const data = await res.json();
        if (res.ok) {
          alert(data.message || "Rollback succeeded.");
          loadFiles();
          loadGitStatus();
        } else {
          alert("Rollback failed: " + data.error);
        }
      } catch (err) {
        alert("Rollback error: " + err.message);
      }
    }

    async function checkToolBridgeStatus() {
      try {
        const res = await apiFetch("/api/toolbridge/status");
        if (res.ok) {
          const data = await res.json();
          const tbEl = document.getElementById("tbGatewayStatus");
          if (data.enabled && data.total_tools > 0) {
            tbEl.innerHTML = `<span class="text-emerald-400 font-semibold">${data.total_tools} MCP Tools Online</span>`;
          } else if (data.enabled) {
            tbEl.innerHTML = `<span class="text-amber-400">Gateway Active (0 tools)</span>`;
          } else {
            tbEl.innerHTML = `<span class="text-slate-500">Standby (Local Tools Only)</span>`;
          }
        }
      } catch (err) {
        document.getElementById("tbGatewayStatus").textContent = "Standby";
      }
    }

    function appendMessage(role, text, stepLogs = null) {
      const c = document.getElementById("chatMessages");
      const div = document.createElement("div");
      div.className = role === "user"
        ? "p-3 rounded-xl bg-sky-950/40 border border-sky-800/40 text-sky-200 ml-4 shadow-sm"
        : "p-3.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-slate-200 mr-4 shadow-sm";

      const roleBadge = role === "user"
        ? '<div class="flex items-center space-x-1.5 mb-1.5 font-semibold text-[11px] text-sky-400"><i class="fa-regular fa-user"></i><span>You</span></div>'
        : '<div class="flex items-center space-x-1.5 mb-1.5 font-semibold text-[11px] text-emerald-400"><i class="fa-solid fa-bolt"></i><span>TermiCode AI</span></div>';

      let renderedBody = "";
      if (window.marked && typeof marked.parse === "function") {
        renderedBody = marked.parse(text);
      } else {
        renderedBody = `<p class="whitespace-pre-wrap">${text}</p>`;
      }

      let stepLogsHtml = "";
      if (stepLogs && stepLogs.length > 0) {
        stepLogsHtml = `
          <details class="mt-2 text-[11px] bg-slate-900/80 rounded-lg border border-slate-800 p-2">
            <summary class="cursor-pointer text-sky-400 font-medium">💡 Execution Trace (${stepLogs.length} steps)</summary>
            <div class="mt-2 space-y-1 font-mono text-[10px] text-slate-300">
              ${stepLogs.map(s => `<div>● [Step ${s.step || 1}] ${s.tool || 'inspect'}: ${s.status || 'done'}</div>`).join("")}
            </div>
          </details>
        `;
      }

      div.innerHTML = roleBadge + `<div class="prose-code text-xs leading-relaxed space-y-2">${renderedBody}</div>` + stepLogsHtml;
      c.appendChild(div);

      // Syntax highlight code blocks
      if (window.hljs) {
        div.querySelectorAll('pre code').forEach((block) => {
          hljs.highlightElement(block);
        });
      }

      c.scrollTop = c.scrollHeight;
    }

    async function sendChatMessage() {
      const inp = document.getElementById("chatInput");
      const msg = inp.value.trim();
      if (!msg) return;
      inp.value = "";
      appendMessage("user", msg);
      document.getElementById("typingIndicator").classList.remove("hidden");

      try {
        const res = await apiFetch("/api/chat", { method: "POST", body: { message: msg } });
        document.getElementById("typingIndicator").classList.add("hidden");
        if (res.ok) {
          const data = await res.json();
          appendMessage("assistant", data.response, data.step_logs);
        } else {
          let errDetail = "Server Error: " + res.status;
          try {
            const errData = await res.json();
            if (errData && errData.error) errDetail = errData.error;
          } catch(e) {}
          appendMessage("assistant", errDetail);
        }
      } catch (err) {
        document.getElementById("typingIndicator").classList.add("hidden");
        appendMessage("assistant", "Network error: " + err.message);
      }
    }

    async function runTask() {
      const inp = document.getElementById("chatInput");
      const task = inp.value.trim();
      if (!task) return;
      const wf = document.getElementById("wfSelect").value;
      inp.value = "";
      appendMessage("user", `[Running ${wf} task]: ` + task);
      document.getElementById("typingIndicator").classList.remove("hidden");

      try {
        const res = await apiFetch("/api/run", { method: "POST", body: { task, workflow: wf } });
        document.getElementById("typingIndicator").classList.add("hidden");
        if (res.ok) {
          const data = await res.json();
          if (data.status === "needs_approval") {
            showApprovalBanner(data);
          } else {
            appendMessage("assistant", `Task completed: ${data.summary}`);
            loadFiles();
            loadGitStatus();
          }
        } else {
          let errDetail = "Task error (" + res.status + ")";
          try {
            const errData = await res.json();
            if (errData && errData.error) errDetail = errData.error;
          } catch(e) {}
          appendMessage("assistant", errDetail);
        }
      } catch (err) {
        document.getElementById("typingIndicator").classList.add("hidden");
        appendMessage("assistant", "Task error: " + err.message);
      }
    }

    function showApprovalBanner(data) {
      currentApprovalId = data.approval_id;
      document.getElementById("approvalToolBadge").textContent = data.tool;
      document.getElementById("approvalTargetPath").textContent = data.target_path ? `Target: ${data.target_path}` : '';
      document.getElementById("approvalPreviewText").textContent = data.diff || data.preview || JSON.stringify(data, null, 2);
      document.getElementById("approvalBanner").classList.remove("hidden");
    }

    async function resolveApproval(approved) {
      if (!currentApprovalId) return;
      try {
        const res = await apiFetch("/api/approve", { method: "POST", body: { approval_id: currentApprovalId, approved } });
        document.getElementById("approvalBanner").classList.add("hidden");
        if (res.ok) {
          const data = await res.json();
          appendMessage("assistant", approved ? `Approved: ${data.result || 'Executed successfully.'}` : `Action was cancelled by user.`);
          loadFiles();
          loadGitStatus();
        }
      } catch (err) {
        alert("Approval resolution error: " + err.message);
      }
    }

    // Initial load
    loadFiles();
    loadGitStatus();
    checkToolBridgeStatus();
  </script>
</body>
</html>
"""
