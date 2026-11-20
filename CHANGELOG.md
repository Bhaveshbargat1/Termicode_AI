# TermiCode AI Changelog

All notable changes to TermiCode AI will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-11-16

### Added
- **Final Integration & Security Test Suites:**
  - `tests/test_final_integration.py` covering Chat with RAG, agent-run code tasks, ToolBridge connectivity, session persistence across restarts, and auth failure recovery.
  - `tests/test_final_security_and_perf.py` validating path traversal blockage, sensitive file shielding, credential redaction, and RAG retrieval latency benchmarks (< 100ms).
- **Packaging & Deployment:**
  - Production `Dockerfile` and `requirements.txt` / `requirements-dev.txt`.
  - Top-level `docker-compose.yml` linking TermiCode AI with ToolBridge MCP Gateway.
  - Unified `Makefile` and cross-platform start/stop scripts (`start.sh`, `stop.sh`, `start.ps1`, `stop.ps1`).
- **Documentation:**
  - `SECURITY.md` defining 7-layer defense-in-depth model and vulnerability reporting.
  - `LIMITATIONS.md` disclosing sandbox boundaries and execution scope.
  - Updated `README.md` with system status indicators, architecture diagrams, and user guidance.

### Changed
- Production version bump to `v1.0.0` in `pyproject.toml`, `src/termicode/__init__.py`, and runtime status console.

---

## [0.9.0] - Phase 10: UX Polish & Observability

### Added
- Rich interactive spinner overlays for generation, file uploads, and tool evaluations.
- Friendly error suggestion engine mapping technical exceptions to actionable remedies.
- Automatic technical traceback logging to `.termicode/logs/debug.log` while preserving clean terminal displays.
- Confirmation gates for destructive `/drop` and `/rollback` commands with `--yes` / `-y` bypass flags.
- Monokai syntax-highlighted code rendering in Chat REPL.

---

## [0.8.0] - Phase 9: Version Control Checkpoint & Rollback

### Added
- Git checkpoint engine creating automatic stashes before file mutations.
- `termicode checkpoint` for manual snapshot creation.
- `termicode rollback` to instantly revert uncommitted or broken agent edits.
- `termicode checkpoints` for inspecting version history.

---

## [0.7.0] - Phase 8: Unified Agent Execution Loop

### Added
- Multi-turn autonomous agent execution loop with dynamic tool dispatch.
- Integrated RAG document retrieval tool (`rag_retrieve`) with page-specific excerpting.
- Human approval engine with interactive diff previews.

---

## [0.6.0] - Phase 7: ToolBridge MCP Gateway Integration

### Added
- `termicode toolbridge add-server`, `list-servers`, and `remove-server` CLI management commands.
- `ToolBridgeClient` standard library HTTP adapter with zero external network dependencies.
- `MCPTool` dynamic wrapper translating remote MCP tools into typed `BaseTool` instances.

---

## [0.5.0] - Phase 6: Session Management & Persistent Memory

### Added
- SQLite-backed conversation history in `.termicode/chat_sessions.db`.
- Multi-session management commands: `termicode session new`, `list`, `switch`, `delete`.
- Sliding memory window with automatic older message pruning and condensation summarizer.

---

## [0.4.0] - Phase 5: Interactive Chat Interface

### Added
- Rich interactive REPL loop (`termicode chat`).
- Multi-line input mode for pasting complex prompts.
- Streaming chunk generation with togglable intermediate step logs.
- Document attachment support (`/upload <file>` and `/docs`).

---

## [0.3.0] - Phase 3 & 4: Subprocess Runner & LLM Providers

### Added
- Secure subprocess runner with strict command allowlists and `shell=False`.
- LLM provider adapters for OpenAI, Ollama, and offline `FakeProvider`.
- Secret redaction engine masking API keys and bearer tokens.

---

## [0.1.0] - Phase 1 & 2: Core Architecture & Workflow Profiles

### Added
- `WorkspaceBoundary` deterministic path resolution and traversal protection.
- `PolicyEngine` and `ToolRegistry` with risk tier classification.
- Single-agent workflow profiles (`generate`, `debug`, `understand`, `tests`, `review`, `refactor`).
