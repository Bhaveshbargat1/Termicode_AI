"""Script to generate a comprehensive, publication-quality technical portfolio PDF for TermiCode AI.

Author: Bhavesh Bargat
Target Roles: AI Engineer, AI/ML Engineer, Software Engineer
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen import canvas

# Professional Color Palette
PRIMARY = HexColor("#0F172A")        # Deep Slate / Navy
ACCENT_BLUE = HexColor("#2563EB")    # Vibrant Royal Blue
ACCENT_GREEN = HexColor("#059669")   # Emerald Green
ACCENT_AMBER = HexColor("#D97706")   # Amber / Highlight
TEXT_DARK = HexColor("#1E293B")      # Main Body Slate
TEXT_MUTED = HexColor("#64748B")     # Secondary Muted Slate
BG_LIGHT = HexColor("#F8FAFC")       # Card Background
BG_ACCENT = HexColor("#EFF6FF")      # Soft Blue Card Accent
BORDER_COLOR = HexColor("#E2E8F0")   # Border Slate


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and print 'Page X of Y' footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Helvetica", 8.5)
        self.setFillColor(HexColor("#94A3B8"))

        # Running Header (Pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 750, "TermiCode AI — Comprehensive Technical Portfolio Documentation")
            self.drawRightString(558, 750, "Bhavesh Bargat")
            self.setStrokeColor(HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)

        # Running Footer
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(558, 36, page_str)
        self.drawString(54, 36, "TermiCode AI · Bounded Terminal Coding Assistant · GitHub: Bhaveshbargat1/Termicode_AI")
        self.setStrokeColor(HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 48, 558, 48)
        self.restoreState()


def create_card(title: str, content_elements: list, title_style: ParagraphStyle, bg_color: HexColor = BG_LIGHT, border_color: HexColor = BORDER_COLOR) -> Table:
    """Wrap content elements inside a stylized card panel."""
    card_flowables = [
        Paragraph(title, title_style),
        Spacer(1, 4),
    ] + content_elements

    t = Table([[card_flowables]], colWidths=[504])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg_color),
                ("BOX", (0, 0), (-1, -1), 1, border_color),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    return t


def build_portfolio_pdf(output_filename: str) -> str:
    pdf_path = Path(output_filename).resolve()
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Typography Hierarchy
    doc_title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=PRIMARY,
        spaceAfter=3,
    )
    doc_subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=ACCENT_BLUE,
        spaceAfter=10,
    )
    sec_h1 = ParagraphStyle(
        "SecH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=PRIMARY,
        spaceBefore=12,
        spaceAfter=6,
        keepWithNext=True,
    )
    sec_h2 = ParagraphStyle(
        "SecH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=ACCENT_BLUE,
        spaceBefore=8,
        spaceAfter=3,
        keepWithNext=True,
    )
    body = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_DARK,
        spaceAfter=4,
    )
    bullet = ParagraphStyle(
        "BulletText",
        parent=body,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=3,
    )
    code_text = ParagraphStyle(
        "CodeText",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=7.5,
        leading=10,
        textColor=HexColor("#0F172A"),
    )
    table_header = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10.5,
        textColor=PRIMARY,
    )
    table_cell = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=TEXT_DARK,
    )
    qa_q = ParagraphStyle(
        "QAQuestion",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=PRIMARY,
        spaceBefore=6,
        spaceAfter=2,
        keepWithNext=True,
    )
    qa_a = ParagraphStyle(
        "QAAnswer",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11.5,
        textColor=TEXT_DARK,
        leftIndent=10,
        spaceAfter=6,
    )

    story = []

    # =========================================================================
    # COVER / HEADER BANNER
    # =========================================================================
    header_content = [
        Paragraph("TermiCode AI — Technical Portfolio & System Architecture", doc_title_style),
        Paragraph("<b>Author / Candidate:</b> Bhavesh Bargat &nbsp;|&nbsp; <b>Target Roles:</b> AI Engineer · AI/ML Engineer · Software Engineer", doc_subtitle_style),
        Paragraph("<b>GitHub:</b> github.com/Bhaveshbargat1/Termicode_AI &nbsp;|&nbsp; <b>Package:</b> termicode v0.1.0 &nbsp;|&nbsp; <b>Verification:</b> 288 Automated Tests (100% Pass)", body),
    ]
    story.append(create_card("", header_content, sec_h2, bg_color=BG_ACCENT, border_color=ACCENT_BLUE))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 1: PROJECT OVERVIEW
    # =========================================================================
    story.append(Paragraph("1. Project Overview & Problem Statement", sec_h1))
    story.append(Paragraph(
        "<b>TermiCode AI</b> is an open-source, bounded terminal coding assistant designed around the core principle of "
        "<b>deterministic authority separation</b>. It bridges generative Large Language Models (LLMs) with local software "
        "development workflows while eliminating the severe security risks associated with autonomous code execution.",
        body
    ))
    story.append(Paragraph(
        "<b>The Problem:</b> Traditional autonomous agents grant untrusted LLMs direct shell access (<code>shell=True</code>) "
        "and unbounded filesystem privileges. This exposes developers to accidental catastrophic deletion (<code>rm -rf /</code>), "
        "directory traversal attacks accessing sensitive credentials (<code>~/.ssh/id_rsa</code>, <code>.env</code>), "
        "infinite token consumption loops, and silent code overwrites without human verification.",
        body
    ))
    story.append(Paragraph(
        "<b>The Solution:</b> TermiCode AI strictly treats the LLM as an untrusted proposal engine. Python deterministic "
        "software boundaries validate safety policies, confine all file operations to an approved workspace boundary, "
        "enforce human-in-the-loop visual unified diff approvals, and execute allowlisted subprocesses with <code>shell=False</code>.",
        body
    ))
    story.append(Spacer(1, 8))

    # =========================================================================
    # SECTION 2: TECHNOLOGY STACK
    # =========================================================================
    story.append(Paragraph("2. Complete Technology Stack & Rationale", sec_h1))
    
    stack_data = [
        [Paragraph("Category", table_header), Paragraph("Technology", table_header), Paragraph("Version / Source", table_header), Paragraph("Architectural Rationale", table_header)],
        [Paragraph("Core Language", table_cell), Paragraph("Python", table_cell), Paragraph(">=3.11", table_cell), Paragraph("Modern typed unions, match syntax, asyncio, and StrEnum.", table_cell)],
        [Paragraph("CLI Framework", table_cell), Paragraph("Typer", table_cell), Paragraph(">=0.12.0", table_cell), Paragraph("Type-annotated CLI parsing, flags, autocompletion, and subcommands.", table_cell)],
        [Paragraph("Terminal UI", table_cell), Paragraph("Rich", table_cell), Paragraph(">=13.0.0", table_cell), Paragraph("Status panels, execution tables, unified diff syntax highlighting.", table_cell)],
        [Paragraph("Validation", table_cell), Paragraph("Pydantic", table_cell), Paragraph(">=2.7.0", table_cell), Paragraph("Runtime data validation, tool schemas, and JSON serialization.", table_cell)],
        [Paragraph("Settings", table_cell), Paragraph("Pydantic-Settings", table_cell), Paragraph(">=2.2.0", table_cell), Paragraph("Automated loading of TERMICODE_* env vars and validation bounds.", table_cell)],
        [Paragraph("HTTP Client", table_cell), Paragraph("urllib.request", table_cell), Paragraph("Standard Library", table_cell), Paragraph("Zero external dependencies; raw HTTP to OpenAI and Ollama.", table_cell)],
        [Paragraph("Safety Bounds", table_cell), Paragraph("pathlib & subprocess", table_cell), Paragraph("Standard Library", table_cell), Paragraph("Canonical path resolution (resolve()) and process isolation (shell=False).", table_cell)],
        [Paragraph("Testing Suite", table_cell), Paragraph("Pytest", table_cell), Paragraph(">=8.0.0", table_cell), Paragraph("288 automated regression tests covering agents, policy, and tools.", table_cell)],
        [Paragraph("Quality & Type", table_cell), Paragraph("Ruff & Mypy", table_cell), Paragraph("Strict Mode", table_cell), Paragraph("100% type coverage across 38 source files; clean 100-char style.", table_cell)],
    ]
    t_stack = Table(stack_data, colWidths=[80, 85, 85, 254])
    t_stack.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG_ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_stack)
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 3: SYSTEM ARCHITECTURE
    # =========================================================================
    story.append(Paragraph("3. System Architecture & Authority Separation", sec_h1))
    story.append(Paragraph(
        "TermiCode AI decouples cognitive decision-making from execution authority across 5 distinct operational layers:",
        body
    ))
    
    arch_layers = [
        Paragraph("• <b>Layer 1 — CLI & Terminal UI (cli.py, console.py):</b> Ingests user tasks, manages subcommands (<code>run</code>, <code>understand</code>, <code>generate</code>, <code>debug</code>, <code>tests</code>, <code>refactor</code>), and renders Rich formatted tables.", bullet),
        Paragraph("• <b>Layer 2 — Safety Fence (workspace.py, config.py):</b> Canonical path resolution anchors all actions strictly to the project root and protects sensitive credentials (<code>.env</code>, <code>.git</code>, SSH keys).", bullet),
        Paragraph("• <b>Layer 3 — Bounded Agent Loop (runtime.py, workflows.py):</b> Event-driven state machine managing step budgets (8–12 steps), repair attempts (max 3), and context history.", bullet),
        Paragraph("• <b>Layer 4 — Policy & Approval Gate (policy.py, cli.py):</b> Categorizes actions into READ, WRITE, EXECUTE. Displays unified diff previews and strictly defaults to DENY on invalid input.", bullet),
        Paragraph("• <b>Layer 5 — Tool Registry & Sandboxed Runner (tools/, runner/):</b> Executes allowlisted binaries (<code>python</code>, <code>pytest</code>, <code>ruff</code>, <code>mypy</code>, <code>git</code>) with <code>shell=False</code>, 30s timeout, and 64KB buffer caps.", bullet),
    ]
    story.append(create_card("Core Architectural Layers", arch_layers, sec_h2))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 4: END-TO-END WORKFLOW
    # =========================================================================
    story.append(Paragraph("4. End-to-End Execution Workflow", sec_h1))
    story.append(Paragraph(
        "When a user runs <code>termicode generate \"create src/utils/math_helper.py\"</code>, the application executes the following deterministic sequence:",
        body
    ))
    
    steps_data = [
        Paragraph("<b>1. Ingestion:</b> <code>cli.py</code> receives the task and options, initializing <code>WorkspaceBoundary(Path.cwd())</code>.", bullet),
        Paragraph("<b>2. Profile Loading:</b> <code>workflows.py</code> loads the <code>generate</code> profile (12 steps, risk ceiling: WRITE, system prompt).", bullet),
        Paragraph("<b>3. Provider Selection:</b> Probes local Ollama endpoint (<code>http://localhost:11434/v1</code>) or validates <code>OPENAI_API_KEY</code> with interactive offline fallback.", bullet),
        Paragraph("<b>4. AI Proposal:</b> <code>AgentRuntime</code> dispatches context and Pydantic tool schemas. Model returns <code>ToolCall(name=\"write_file\", arguments={...})</code>.", bullet),
        Paragraph("<b>5. Policy Evaluation:</b> <code>PolicyEngine</code> verifies the path is inside workspace and marks status as <code>needs_approval</code>.", bullet),
        Paragraph("<b>6. Human Gate:</b> <code>cli.py</code> renders a visual unified diff (red deletions, green additions) and prompts: <code>Approve action? [y/N]</code>.", bullet),
        Paragraph("<b>7. Tool Execution:</b> Upon typing 'y', <code>WriteFileTool</code> creates the parent directory and writes the file to disk.", bullet),
        Paragraph("<b>8. Context & Audit:</b> <code>TranscriptWriter</code> logs the event to <code>.termicode/transcripts/run_*.jsonl</code> with secrets scrubbed. Tool output is appended to message history.", bullet),
        Paragraph("<b>9. Completion:</b> Model sees the successful result, completes without tool calls, and <code>cli.py</code> prints the Rich Execution Summary.", bullet),
    ]
    story.append(create_card("Execution Lifecycle Trace", steps_data, sec_h2, bg_color=BG_ACCENT))
    story.append(Spacer(1, 10))

    # Page Break for clean reading
    story.append(PageBreak())

    # =========================================================================
    # SECTION 5: FEATURES & WORKFLOW PROFILES
    # =========================================================================
    story.append(Paragraph("5. Feature Catalog & Workflow Profiles", sec_h1))
    
    wf_data = [
        [Paragraph("Workflow Profile", table_header), Paragraph("Allowed Risk", table_header), Paragraph("Step Budget", table_header), Paragraph("Primary Objective & Permission Matrix", table_header)],
        [Paragraph("understand", table_cell), Paragraph("READ only", table_cell), Paragraph("8 steps", table_cell), Paragraph("Codebase comprehension, AST exploration, file structure analysis. Mutating tools strictly excluded.", table_cell)],
        [Paragraph("review", table_cell), Paragraph("READ only", table_cell), Paragraph("8 steps", table_cell), Paragraph("Static code review, security audits, linting inspection. Zero disk writes allowed.", table_cell)],
        [Paragraph("generate", table_cell), Paragraph("READ, WRITE", table_cell), Paragraph("12 steps", table_cell), Paragraph("Feature implementation, module creation, and test authoring with unified diff preview.", table_cell)],
        [Paragraph("debug", table_cell), Paragraph("READ, WRITE, EXECUTE", table_cell), Paragraph("10 steps", table_cell), Paragraph("Bug diagnosis, targeted patch application, and verification via allowlisted test runner.", table_cell)],
        [Paragraph("tests", table_cell), Paragraph("READ, WRITE, EXECUTE", table_cell), Paragraph("10 steps", table_cell), Paragraph("Test suite discovery, test generation, and running pytest under subprocess supervision.", table_cell)],
        [Paragraph("refactor", table_cell), Paragraph("READ, WRITE, EXECUTE", table_cell), Paragraph("10 steps", table_cell), Paragraph("Structural code modernization, deduplication, and dependency decoupling with regression checks.", table_cell)],
    ]
    t_wf = Table(wf_data, colWidths=[70, 70, 60, 304])
    t_wf.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG_ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t_wf)
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 6: AI INTEGRATION & LOCAL GPU DEPLOYMENT
    # =========================================================================
    story.append(Paragraph("6. AI Integration & Local GPU Deployment", sec_h1))
    story.append(Paragraph(
        "TermiCode AI implements a hybrid inference topology supporting both cloud APIs and 100% private local GPU models:",
        body
    ))
    
    ai_points = [
        Paragraph("• <b>Cloud Inference:</b> Connects to OpenAI (<code>gpt-4o-mini</code>, <code>gpt-4o</code>) via HTTP POST using Python's standard <code>urllib.request</code>.", bullet),
        Paragraph("• <b>Local GPU Inference (Ollama):</b> Auto-detects local Ollama instances on <code>http://localhost:11434/v1</code>. Highly optimized for consumer GPUs like the <b>NVIDIA GeForce RTX 3050 6GB</b> using <code>qwen2.5-coder:7b</code> (~4.7 GB VRAM at 40-60 tokens/sec).", bullet),
        Paragraph("• <b>Resilient Fallback Parser:</b> Smaller open-weights models occasionally emit tool calls formatted in markdown (<code>```json</code> or <code>&lt;tool_call&gt;</code>). <code>OpenAIProvider._extract_fallback_tool_call()</code> uses regex to extract and parse them into valid <code>ToolCall</code> objects.", bullet),
        Paragraph("• <b>Smart Offline Engine:</b> Passing <code>--offline</code> routes execution to a context-aware simulation engine that inspects real workspace files and generates typed, production-ready Python modules with zero external calls.", bullet),
    ]
    story.append(create_card("AI Integration & Inference Strategy", ai_points, sec_h2))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 7: SECURITY ARCHITECTURE & DEFENSE MATRIX
    # =========================================================================
    story.append(Paragraph("7. Security Architecture & Threat Mitigation Matrix", sec_h1))
    
    sec_data = [
        [Paragraph("Security Threat", table_header), Paragraph("Mitigation Mechanism", table_header), Paragraph("Implementation File", table_header), Paragraph("Defense Reality & Guarantees", table_header)],
        [Paragraph("Directory Traversal (../../)", table_cell), Paragraph("Canonical Path Resolution", table_cell), Paragraph("workspace.py: resolve_safe()", table_cell), Paragraph("Resolves absolute symlinks via Path.resolve() and checks is_relative_to(). Escapes blocked with WorkspaceEscapeError.", table_cell)],
        [Paragraph("Credential Theft (.env, SSH)", table_cell), Paragraph("Sensitive File Blocklist", table_cell), Paragraph("workspace.py: is_sensitive()", table_cell), Paragraph("Blocks access to .git, .ssh, .aws, .env, id_rsa, and *.key files even inside the approved workspace root.", table_cell)],
        [Paragraph("Shell Injection Attacks", table_cell), Paragraph("Subprocess Disabling Shell", table_cell), Paragraph("runner.py: CommandRunner", table_cell), Paragraph("Runs commands with shell=False. Metacharacters (|, ;, &&, $()) are treated as literal strings and blocked by validator.", table_cell)],
        [Paragraph("Arbitrary Process Spawning", table_cell), Paragraph("Strict Binary Allowlist", table_cell), Paragraph("runner/validator.py", table_cell), Paragraph("Only 5 binaries permitted: python, pytest, ruff, mypy, git. Destructive git subcommands (push, reset, rm) blocked.", table_cell)],
        [Paragraph("Accidental File Overwrite", table_cell), Paragraph("Interactive Diff Approval", table_cell), Paragraph("cli.py & policy.py", table_cell), Paragraph("Displays colored unified diffs before writes. Defaults strictly to DENY on invalid input or non-interactive sessions.", table_cell)],
        [Paragraph("Credential Leakage in Logs", table_cell), Paragraph("Automated Secret Redaction", table_cell), Paragraph("providers/redaction.py", table_cell), Paragraph("Deterministic regex scrubber masks sk-*, AIza*, and bearer tokens before writing to transcripts or summaries.", table_cell)],
    ]
    t_sec = Table(sec_data, colWidths=[90, 85, 95, 234])
    t_sec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG_ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_sec)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>Honest MVP Limitations:</b> TermiCode AI enforces application-level policy boundaries and human approval gates. "
        "It does <i>not</i> provide OS kernel-level sandboxing (no Docker/seccomp containers). Permitted tools run under the user's host account.",
        body
    ))
    story.append(Spacer(1, 10))

    # Page Break for clean reading
    story.append(PageBreak())

    # =========================================================================
    # SECTION 8: TESTING & CODE QUALITY VERIFICATION
    # =========================================================================
    story.append(Paragraph("8. Testing, Quality & Performance Verification", sec_h1))
    
    test_content = [
        Paragraph("• <b>Automated Pytest Suite:</b> <b>288 collected test items</b> across 12 test modules. <b>287 passed, 1 skipped</b> (live Ollama test requiring daemon) in <b>9.81s</b>.", bullet),
        Paragraph("• <b>Static Type Analysis (Mypy):</b> Enforced under <code>strict = true</code>, <code>disallow_untyped_defs = true</code>. Result: <b>Success: no issues found in 38 source files</b>.", bullet),
        Paragraph("• <b>Linter & Formatter (Ruff):</b> Enforced under 100-character line lengths, pyflakes, pycodestyle, isort, and bugbear rules. Result: <b>All checks passed cleanly</b>.", bullet),
        Paragraph("• <b>Regression Coverage:</b> Comprehensive tests for agent loops, step budgets, repair counters, tool validation, workspace path escapes, and secret redaction.", bullet),
    ]
    story.append(create_card("Verified Code Quality Metrics", test_content, sec_h2, bg_color=BG_LIGHT))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 9: RESUME BULLETS & ATS KEYWORDS
    # =========================================================================
    story.append(Paragraph("9. Professional Resume Descriptions & Technical Keywords", sec_h1))
    
    resume_content = [
        Paragraph("<b>Professional Project Description:</b><br/>"
                  "<i>TermiCode AI is an open-source, bounded terminal coding assistant built with Python and Typer that enforces "
                  "deterministic authority separation between Large Language Model planners and local system execution. It enables "
                  "safe local code generation, debugging, and testing through canonical workspace confinement, unified diff approval gates, "
                  "and allowlisted subprocess sandboxing supporting cloud and local (Ollama) inference.</i>", body),
        Spacer(1, 4),
        Paragraph("<b>High-Impact Technical Resume Bullet Points:</b>", sec_h2),
        Paragraph("• <b>Architected a secure, bounded AI agent runtime</b> in Python 3.11 with an event-driven propose-validate-approve-execute state machine, enforcing strict step budgets and automatic repair cycles across 6 distinct workflow profiles.", bullet),
        Paragraph("• <b>Engineered deterministic security guardrails</b> utilizing <code>pathlib</code> canonicalization to prevent path traversal attacks and subprocess execution with <code>shell=False</code> to eliminate shell injection vulnerabilities.", bullet),
        Paragraph("• <b>Built a zero-dependency OpenAI-compatible provider adapter</b> using standard library <code>urllib.request</code>, enabling seamless switching between OpenAI cloud APIs, local GPU-accelerated Ollama models, and an offline testbed.", bullet),
        Paragraph("• <b>Implemented interactive human-in-the-loop approval gates</b> with <code>rich</code> and <code>difflib</code>, rendering visual unified diff previews with fail-safe default-to-deny authorization and automated secret redaction.", bullet),
        Paragraph("• <b>Achieved 100% test pass rate across 288 automated pytest suites</b> and zero static analysis issues under strict <code>mypy</code> typing and <code>ruff</code> linting across 38 source files.", bullet),
        Spacer(1, 4),
        Paragraph("<b>ATS-Friendly Keywords:</b> Python 3.11, Agentic Workflows, Tool Calling, Function Calling, Ollama, OpenAI API, Qwen2.5-Coder, Deterministic Policy, Path Traversal Defense, Shell Injection Defense, Human-in-the-Loop, Typer, Rich, Pydantic v2, Pytest, Mypy, Ruff, Subprocess Security, Secret Redaction.", body),
    ]
    story.append(create_card("Resume & Portfolio Presentation", resume_content, sec_h2, bg_color=BG_ACCENT, border_color=ACCENT_BLUE))
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECTION 10: INTERVIEW PREPARATION (15 QUESTIONS & ANSWERS)
    # =========================================================================
    story.append(Paragraph("10. Technical Interview Preparation (15 Core Q&A)", sec_h1))
    
    qa_list = [
        ("Q1: What is 'authority separation' in TermiCode AI?",
         "Authority separation decouples the thinking entity from the executing entity. The LLM acts as an untrusted planner that can only propose actions in structured JSON. Deterministic Python policy code evaluates whether the action is safe and asks the developer for confirmation before any tool modifies files or executes commands."),
        
        ("Q2: Why did you implement shell=False in subprocess execution?",
         "When shell=True, the OS invokes a shell interpreter (sh/cmd.exe) that evaluates characters like ;, |, &&, and $(), enabling command injection. Using shell=False and passing arguments as an explicit array (['pytest', '-v']) ensures the OS treats every token literally, eliminating injection vulnerabilities."),
        
        ("Q3: How do you prevent an AI model from reading files outside the project directory?",
         "Through WorkspaceBoundary.resolve_safe(). It resolves target paths to canonical absolute paths via pathlib.Path.resolve(), resolving symlinks and ../ traversals, then checks resolved.is_relative_to(workspace_root). If false, it immediately raises a WorkspaceEscapeError."),
        
        ("Q4: Why build an OpenAI-compatible adapter with urllib.request instead of the official SDK?",
         "To eliminate heavy external dependency graphs and potential version conflicts. Standard library urllib.request allows TermiCode to speak the standard OpenAI REST protocol (/chat/completions) directly to both OpenAI cloud servers and local Ollama servers with zero third-party overhead."),
        
        ("Q5: What happens if an AI agent gets stuck in an infinite repair loop?",
         "AgentRuntime enforces two deterministic bounds: max_steps (8–12 steps depending on workflow profile) and max_repairs (maximum of 3 consecutive repair attempts). On the 4th consecutive failure, execution terminates immediately with StopReason.MAX_REPAIRS_REACHED."),
        
        ("Q6: What is the purpose of the 6 workflow profiles?",
         "To enforce the principle of least privilege. In read-only workflows like understand and review, mutating tools (write_file, apply_patch, run_command) are completely excluded from tool declarations sent to the model, and the PolicyEngine strictly denies any mutating attempts."),
        
        ("Q7: How does your tool registry connect to the AI model?",
         "Tools inherit from BaseTool and define a Pydantic args_schema. ToolRegistry calls args_schema.model_json_schema() to generate OpenAI-compatible JSON function declarations that inform the model of tool names, descriptions, and required argument types."),
        
        ("Q8: How does the interactive approval gate protect against accidental file overwrites?",
         "For mutating tools (write_file, apply_patch), the CLI computes a unified diff between current disk content and proposed content using difflib. It displays the colored diff in a Rich panel and prompts the user. If the user enters anything other than 'y' or 'yes', it defaults strictly to DENY."),
        
        ("Q9: What happens if TermiCode runs in a non-interactive CI/CD pipeline?",
         "cli.py checks sys.stdin.isatty(). In non-interactive or piped sessions, approval prompts automatically default to DENY unless explicitly run with the --yes flag, preventing automated pipelines from hanging indefinitely."),
        
        ("Q10: How do you protect sensitive credentials like .env files from being exposed to the AI?",
         "WorkspaceBoundary.is_sensitive() checks file paths against a blocklist of sensitive directories (.git, .ssh, .aws), filenames (.env, credentials.json, id_rsa), and extensions (.pem, .key), raising SensitivePathError. Additionally, redact_secrets() masks API keys before writing transcripts."),
        
        ("Q11: How does the application handle local models that output tool calls in markdown rather than JSON?",
         "OpenAIProvider._extract_fallback_tool_call() uses regular expressions to capture <tool_call> or ```json blocks containing 'name' and 'arguments', parsing them into valid ToolCall objects to maintain compatibility with smaller open-weights models."),
        
        ("Q12: Why did you choose flat-file JSONL transcripts over an SQLite database?",
         "For a local developer CLI, transcripts must be human-readable, zero-configuration, and easily inspectable in any text editor. JSON Lines allows append-only logging in under a millisecond without database locks and is easily wiped per repository without schema migrations."),
        
        ("Q13: What are the command execution restrictions in validate_command?",
         "Only 5 executables are allowlisted: python, pytest, ruff, mypy, git. Git is restricted strictly to read-only subcommands (status, diff, log, branch, show), and Python execution is limited to approved test and lint modules (pytest, unittest, ruff, mypy)."),
        
        ("Q14: What is the purpose of the --offline flag?",
         "It routes execution to _create_smart_offline_provider(), which dynamically inspects workspace files and produces typed, production-ready Python modules and architecture summaries without requiring an internet connection or an active LLM server."),
        
        ("Q15: What are the honest security limitations of TermiCode AI?",
         "TermiCode provides application-level policy boundaries, not kernel-level virtualization (no Docker/seccomp containers). Approved commands execute under the user's host permissions, and safety relies on developer verification before approving diffs."),
    ]

    for q, a in qa_list:
        story.append(Paragraph(q, qa_q))
        story.append(Paragraph(a, qa_a))

    doc.build(story, canvasmaker=NumberedCanvas)
    return str(pdf_path)


if __name__ == "__main__":
    out_file = sys.argv[1] if len(sys.argv) > 1 else "TermiCode_Technical_Portfolio_Documentation.pdf"
    res = build_portfolio_pdf(out_file)
    print(f"Generated PDF successfully at: {res}")
