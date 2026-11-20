"""Chat session management, document tracking, persistent database, and memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from termicode.chat.database import SessionDatabase
from termicode.chat.models import AttachedDocument, ChatMessageRecord, ChatSessionState
from termicode.chat.summarizer import MemorySummarizerStub
from termicode.config import Settings
from termicode.providers.redaction import redact_secrets
from termicode.workspace import WorkspaceBoundary


class ChatSessionError(Exception):
    """Base exception for chat session operations."""


class DocumentNotFoundError(ChatSessionError):
    """Raised when an attached document cannot be found on the filesystem."""


class ChatSession:
    """Manages active chat conversation turns, attached documents, persistent SQLite, and memory."""

    def __init__(
        self,
        workspace: WorkspaceBoundary,
        settings: Settings,
        verbose_steps: bool = True,
        db: SessionDatabase | None = None,
        session_id: str | None = None,
        max_memory_messages: int = 20,
    ) -> None:
        self.workspace = workspace
        self.settings = settings
        self.max_memory_messages = max_memory_messages

        if db is not None:
            self.db = db
        else:
            self.workspace.ensure_termicode_dir()
            db_path = self.workspace.termicode_dir / "chat_sessions.db"
            self.db = SessionDatabase(db_path)

        self.summarizer = MemorySummarizerStub()

        # Resolve or create persistent session record in database
        effective_sid: str
        if session_id:
            existing = self.db.get_session(session_id)
            if existing:
                effective_sid = session_id
                self.db.set_active_session(session_id)
            else:
                effective_sid = self.db.create_session(session_id=session_id, set_active=True)
        else:
            active = self.db.get_active_session()
            if active:
                effective_sid = active["id"]
            else:
                effective_sid = self.db.create_session(set_active=True)

        self.state = ChatSessionState(
            session_id=effective_sid,
            provider=settings.provider,
            model=settings.model,
            toolbridge_enabled=settings.toolbridge_enabled,
            toolbridge_url=settings.toolbridge_api_url,
            verbose_steps=verbose_steps,
        )

        self._load_messages_from_db()

    def _load_messages_from_db(self) -> None:
        """Populate in-memory messages list from persistent SQLite records."""
        self.state.messages.clear()
        db_messages = self.db.get_messages(self.state.session_id)
        for row in db_messages:
            self.state.messages.append(
                ChatMessageRecord(
                    role=row["role"],
                    content=row["content"],
                    timestamp=row.get("timestamp") or "",
                    step_logs=row.get("step_logs") or [],
                )
            )

    def new_chat(self, title: str = "New Session") -> str:
        """Clear conversation message history and start a fresh turn sequence."""
        new_sid = self.db.create_session(title=title, set_active=True)
        self.state.session_id = new_sid
        self.state.messages.clear()
        return new_sid

    def switch_session(self, session_id: str) -> bool:
        """Switch active session pointer and load message history."""
        if not self.db.set_active_session(session_id):
            return False
        self.state.session_id = session_id
        self._load_messages_from_db()
        return True

    def get_session_summary(self) -> str | None:
        """Retrieve stored condensation summary for active session."""
        sess = self.db.get_session(self.state.session_id)
        return sess.get("summary") if sess else None

    def _check_and_prune_memory(self) -> None:
        """Prune messages exceeding max_memory_messages limit and summarize."""
        if len(self.state.messages) <= self.max_memory_messages:
            return

        pruned = self.db.prune_older_messages(
            self.state.session_id, keep_last=self.max_memory_messages
        )
        if pruned:
            curr_summary = self.get_session_summary()
            new_summary = self.summarizer.summarize(pruned, existing_summary=curr_summary)
            self.db.update_summary(self.state.session_id, new_summary)
            self.state.messages = self.state.messages[-self.max_memory_messages :]

    def clear_all(self) -> None:
        """Reset conversation, attached documents, and session ID completely."""
        new_sid = self.db.create_session(title="New Session", set_active=True)
        self.state = ChatSessionState(
            session_id=new_sid,
            provider=self.settings.provider,
            model=self.settings.model,
            toolbridge_enabled=self.settings.toolbridge_enabled,
            toolbridge_url=self.settings.toolbridge_api_url,
            verbose_steps=self.state.verbose_steps,
        )

    def add_document(self, file_path: str | Path) -> AttachedDocument:
        """Attach a document (PDF, notes, markdown, code) to the active session.

        Args:
            file_path: Absolute or workspace-relative path of the target file.

        Returns:
            The created AttachedDocument record.

        Raises:
            DocumentNotFoundError: If the target file does not exist or is not a file.
        """
        raw_path = Path(file_path).expanduser()

        # If relative, attempt to resolve within workspace root
        if not raw_path.is_absolute():
            candidate = self.workspace.root / raw_path
            target = candidate if candidate.exists() else raw_path.resolve()
        else:
            target = raw_path.resolve()

        if not target.exists() or not target.is_file():
            raise DocumentNotFoundError(f"File not found or is not a regular file: '{file_path}'")

        doc_idx = len(self.state.documents) + 1
        doc_id = f"doc_{doc_idx}"

        # Prevent duplicate attachment of identical path
        for existing in self.state.documents:
            if existing.path == target.as_posix():
                return existing

        stat = target.stat()
        file_ext = target.suffix.lower() or ".txt"

        doc = AttachedDocument(
            doc_id=doc_id,
            path=target.as_posix(),
            filename=target.name,
            size_bytes=stat.st_size,
            file_type=file_ext,
        )
        self.state.documents.append(doc)
        return doc

    def list_documents(self) -> list[AttachedDocument]:
        """Return all currently attached documents in this session."""
        return list(self.state.documents)

    def remove_document(self, identifier: str) -> bool:
        """Remove an attached document by doc_id, filename, or path.

        Returns:
            True if document was found and removed; False otherwise.
        """
        target_id = identifier.strip().lower()
        original_count = len(self.state.documents)

        self.state.documents = [
            d
            for d in self.state.documents
            if d.doc_id.lower() != target_id
            and d.filename.lower() != target_id
            and d.path.lower() != target_id
        ]

        return len(self.state.documents) < original_count

    def add_user_message(self, content: str) -> ChatMessageRecord:
        """Append a user message to the conversation with secret redaction and persistence."""
        clean_content = redact_secrets(content)
        msg = ChatMessageRecord(role="user", content=clean_content)
        self.state.messages.append(msg)
        self.db.add_message(self.state.session_id, role="user", content=clean_content)
        self._check_and_prune_memory()
        return msg

    def add_assistant_message(
        self, content: str, step_logs: list[dict[str, Any]] | None = None
    ) -> ChatMessageRecord:
        """Append an assistant response turn with secret redaction and persistence."""
        clean_content = redact_secrets(content)
        msg = ChatMessageRecord(role="assistant", content=clean_content, step_logs=step_logs or [])
        self.state.messages.append(msg)
        self.db.add_message(
            self.state.session_id,
            role="assistant",
            content=clean_content,
            step_logs=step_logs,
        )
        self._check_and_prune_memory()
        return msg

    def get_history(self) -> list[ChatMessageRecord]:
        """Retrieve full conversation message turn history."""
        return list(self.state.messages)

    def toggle_steps(self, enabled: bool | None = None) -> bool:
        """Toggle or explicitly set display of intermediate reasoning and tool step logs."""
        if enabled is not None:
            self.state.verbose_steps = enabled
        else:
            self.state.verbose_steps = not self.state.verbose_steps
        return self.state.verbose_steps

    def update_setting(self, key: str, value: str) -> tuple[bool, str]:
        """Update a session runtime setting.

        Supported keys:
            - model: LLM model name (e.g. 'gpt-4o', 'qwen2.5-coder:7b')
            - provider: Provider name ('openai', 'ollama', 'fake')
            - toolbridge_enabled: 'true' or 'false'
            - toolbridge_url: ToolBridge base URL
        """
        norm_key = key.strip().lower()
        val = value.strip()

        if norm_key in ("model", "m"):
            self.state.model = val
            return True, f"Model updated to '{val}'"

        if norm_key in ("provider", "p"):
            norm_val = val.lower()
            if norm_val not in ("openai", "ollama", "fake", "local"):
                err_msg = f"Invalid provider '{val}'. Choose 'openai', 'ollama', or 'fake'."
                return False, err_msg
            self.state.provider = norm_val
            return True, f"Provider updated to '{norm_val}'"

        if norm_key in ("toolbridge", "toolbridge_enabled", "tb"):
            is_enabled = val.lower() in ("true", "1", "yes", "on", "enable")
            self.state.toolbridge_enabled = is_enabled
            status_str = "ENABLED" if is_enabled else "DISABLED"
            return True, f"ToolBridge integration {status_str}"

        if norm_key in ("toolbridge_url", "tb_url"):
            self.state.toolbridge_url = val
            return True, f"ToolBridge API URL updated to '{val}'"

        if norm_key in ("steps", "verbose_steps"):
            is_steps = val.lower() in ("true", "1", "yes", "on")
            self.state.verbose_steps = is_steps
            return True, f"Step logs set to {'ON' if is_steps else 'OFF'}"

        err_msg = f"Unknown setting key '{key}'. Supported: model, provider, toolbridge, steps."
        return False, err_msg
