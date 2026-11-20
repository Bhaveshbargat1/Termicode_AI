"""RAG document search and page retrieval tool for TermiCode AI."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from termicode.tools.base import BaseTool, RiskLevel, ToolContext, ToolResult


class RAGRetrieveArgs(BaseModel):
    """Input parameters for RAG document retrieval."""

    query: str = Field(
        ...,
        description=(
            "Search query, question, or topic to retrieve from documents "
            "(e.g. 'text in page 2 of document.pdf')."
        ),
    )
    document_path: str | None = Field(
        default=None,
        description=(
            "Optional relative or absolute path to a specific document "
            "(e.g. 'document.pdf', 'notes.md')."
        ),
    )
    page: int | None = Field(
        default=None,
        description="Optional 1-indexed page number to extract specifically.",
    )
    top_k: int = Field(
        default=3,
        description="Maximum number of relevant excerpts to return.",
    )


class RAGRetrieveTool(BaseTool[RAGRetrieveArgs]):
    """Retrieves relevant text passages, sections, or specific pages from workspace documents."""

    name = "rag_retrieve"
    description = (
        "Retrieve relevant text passages, sections, or specific pages from documents. "
        "Use this tool when answering questions about attached files or documents in the workspace."
    )
    risk_level = RiskLevel.READ
    args_schema = RAGRetrieveArgs
    is_rag: bool = True

    def _extract_pages_from_text(self, text: str) -> dict[int, str]:
        """Split text into 1-indexed pages using page headers or line chunking."""
        # Check for explicit page markers: e.g. "--- Page 1 ---", "Page 2:", "[Page 3]"
        pattern = r"(?:^|\n)(?:[-=]{3,}\s*)?(?:Page|PAGE)\s*(\d+)\s*(?:[-=]{3,}|:|\n)"
        splits = list(re.finditer(pattern, text))
        if splits:
            pages: dict[int, str] = {}
            for i, match in enumerate(splits):
                page_num = int(match.group(1))
                start_pos = match.end()
                end_pos = splits[i + 1].start() if i + 1 < len(splits) else len(text)
                pages[page_num] = text[start_pos:end_pos].strip()
            return pages

        # Fallback: chunk into pages of 40 lines each
        lines = text.splitlines()
        chunk_size = 40
        pages = {}
        for idx in range(0, max(len(lines), 1), chunk_size):
            p_num = (idx // chunk_size) + 1
            pages[p_num] = "\n".join(lines[idx : idx + chunk_size]).strip()
        return pages

    def _extract_text_from_pdf_bytes(self, raw_bytes: bytes) -> str:
        """Extract readable text streams or embedded strings from PDF bytes."""
        try:
            # Check if it's UTF-8 or ASCII text mock
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            pass

        # Parse standard PDF text operators: (text) Tj or BT ... ET
        text_parts: list[str] = []
        tj_matches = re.findall(rb"\((.*?)\)\s*Tj", raw_bytes)
        if tj_matches:
            for item in tj_matches:
                try:
                    text_parts.append(item.decode("utf-8", errors="ignore"))
                except Exception:
                    pass

        if text_parts:
            return " ".join(text_parts)

        # Fallback string extraction for printable characters
        printable = re.findall(rb"[A-Za-z0-9 .,;:!?'\"()\-_/\n]{4,}", raw_bytes)
        return "\n".join(p.decode("ascii", errors="ignore") for p in printable)

    def _execute(
        self,
        args: RAGRetrieveArgs,
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute document retrieval."""
        workspace_root = context.workspace.root if context and context.workspace else Path.cwd()

        # Detect page number from query if not explicitly passed
        target_page = args.page
        if target_page is None:
            page_match = re.search(r"\bpage\s*(\d+)\b", args.query, re.IGNORECASE)
            if page_match:
                target_page = int(page_match.group(1))

        # 1. Target single document
        if args.document_path:
            raw_path = Path(args.document_path)
            if context and context.workspace:
                try:
                    target_file = context.workspace.resolve_safe(args.document_path)
                except Exception:
                    target_file = (workspace_root / raw_path).resolve()
            else:
                target_file = (workspace_root / raw_path).resolve()

            if not target_file.exists() or not target_file.is_file():
                return ToolResult(
                    ok=False,
                    output="",
                    error=f"Document '{args.document_path}' not found in workspace.",
                    metadata={"document_path": str(args.document_path), "is_rag": True},
                )

            # Read document content
            raw_bytes = target_file.read_bytes()
            if target_file.suffix.lower() == ".pdf":
                doc_text = self._extract_text_from_pdf_bytes(raw_bytes)
            else:
                doc_text = raw_bytes.decode("utf-8", errors="replace")

            # Page-specific extraction
            pages = self._extract_pages_from_text(doc_text)
            if target_page is not None:
                if target_page in pages:
                    content = pages[target_page]
                    output_text = (
                        f"--- Document: {target_file.name} (Page {target_page}) ---\n{content}"
                    )
                    return ToolResult(
                        ok=True,
                        output=output_text,
                        metadata={
                            "document": target_file.name,
                            "page": target_page,
                            "total_pages": len(pages),
                            "is_rag": True,
                        },
                    )
                else:
                    return ToolResult(
                        ok=False,
                        output="",
                        error=(
                            f"Page {target_page} not found in '{target_file.name}'. "
                            f"Document has {len(pages)} page(s)."
                        ),
                        metadata={"is_rag": True},
                    )

            # Keyword-based excerpt matching
            query_words = set(re.findall(r"\w+", args.query.lower()))
            paragraphs = [p.strip() for p in doc_text.split("\n\n") if p.strip()]
            scored: list[tuple[int, str]] = []
            for p in paragraphs:
                p_words = set(re.findall(r"\w+", p.lower()))
                score = len(query_words & p_words)
                if score > 0:
                    scored.append((score, p))

            scored.sort(key=lambda x: x[0], reverse=True)
            top_excerpts = [p for _, p in scored[: args.top_k]]
            if not top_excerpts:
                top_excerpts = paragraphs[: args.top_k]

            output_text = f"--- Document Excerpts: {target_file.name} ---\n\n" + "\n\n---\n\n".join(
                top_excerpts
            )
            return ToolResult(
                ok=True,
                output=output_text,
                metadata={
                    "document": target_file.name,
                    "excerpts_returned": len(top_excerpts),
                    "is_rag": True,
                },
            )

        # 2. Multi-document search across workspace
        doc_extensions = {".pdf", ".md", ".txt", ".rst"}
        candidate_files = [
            f
            for f in workspace_root.rglob("*")
            if f.is_file() and f.suffix.lower() in doc_extensions and not f.name.startswith(".")
        ]

        if not candidate_files:
            return ToolResult(
                ok=True,
                output="No documents found in the current workspace.",
                metadata={"documents_searched": 0, "is_rag": True},
            )

        query_words = set(re.findall(r"\w+", args.query.lower()))
        matching_results: list[str] = []

        for f in candidate_files[:15]:
            try:
                if f.suffix.lower() == ".pdf":
                    text = self._extract_text_from_pdf_bytes(f.read_bytes())
                else:
                    text = f.read_text(encoding="utf-8", errors="replace")

                f_words = set(re.findall(r"\w+", text.lower()))
                if query_words & f_words:
                    preview = text.strip()[:300].replace("\n", " ")
                    rel_name = f.relative_to(workspace_root).as_posix()
                    matching_results.append(f"[{rel_name}]\n{preview}...")
            except Exception:
                continue

        if matching_results:
            output_text = f"RAG Search Results for '{args.query}':\n\n" + "\n\n".join(
                matching_results[: args.top_k]
            )
        else:
            output_text = f"No documents matched query '{args.query}' in workspace."

        return ToolResult(
            ok=True,
            output=output_text,
            metadata={"matches_found": len(matching_results), "is_rag": True},
        )
