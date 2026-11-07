"""Script to generate a clean, professional, and visually engaging PDF guide for TermiCode AI."""

from __future__ import annotations

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

# Color Palette
PRIMARY = HexColor("#0F172A")       # Deep Slate / Navy
ACCENT_BLUE = HexColor("#2563EB")   # Vibrant Blue
ACCENT_GREEN = HexColor("#059669")  # Emerald Green
ACCENT_AMBER = HexColor("#D97706")  # Amber / Warning
TEXT_DARK = HexColor("#1E293B")     # Body Text Slate
TEXT_MUTED = HexColor("#64748B")    # Secondary Muted Slate
BG_LIGHT = HexColor("#F8FAFC")      # Off-white card background
BG_ACCENT = HexColor("#EFF6FF")     # Soft Blue highlight background
BORDER_COLOR = HexColor("#E2E8F0")  # Subtle Card Border


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
        self.setFont("Helvetica", 9)
        self.setFillColor(HexColor("#94A3B8"))

        # Top Running Header (Pages 2+)
        if self._pageNumber > 1:
            self.drawString(54, 750, "TermiCode AI — Architecture & Workflow Simple Guide")
            self.setStrokeColor(HexColor("#E2E8F0"))
            self.setLineWidth(0.5)
            self.line(54, 742, 558, 742)

        # Bottom Running Footer
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(558, 36, page_str)
        self.drawString(54, 36, "TermiCode AI · Local-First Defense-in-Depth Coding Agent")
        self.setStrokeColor(HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(54, 48, 558, 48)
        self.restoreState()


def build_pdf(filename: str = "TermiCode_Architecture_and_Workflow_Guide.pdf") -> str:
    pdf_path = Path(filename).resolve()
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()

    # Custom Typography Styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=PRIMARY,
        spaceAfter=4,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=ACCENT_BLUE,
        spaceAfter=14,
    )

    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=PRIMARY,
        spaceBefore=14,
        spaceAfter=8,
        keepWithNext=True,
    )

    h2_style = ParagraphStyle(
        "SectionH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=ACCENT_BLUE,
        spaceBefore=8,
        spaceAfter=4,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=TEXT_DARK,
        spaceAfter=6,
    )

    bullet_style = ParagraphStyle(
        "BulletText",
        parent=body_style,
        leftIndent=12,
        firstLineIndent=-10,
        spaceAfter=4,
    )

    code_style = ParagraphStyle(
        "CodeText",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8.5,
        leading=11.5,
        textColor=PRIMARY,
    )

    card_text_style = ParagraphStyle(
        "CardText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12.5,
        textColor=TEXT_DARK,
    )

    story = []

    # --- TITLE BANNER ---
    story.append(Paragraph("TermiCode AI — Simple Guide", title_style))
    story.append(
        Paragraph(
            "How It Works, System Architecture & Workflow Profiles (Made Simple for Anyone)",
            subtitle_style,
        )
    )
    story.append(HRFlowable(width="100%", thickness=1.5, color=ACCENT_BLUE, spaceAfter=14))

    # --- 1. THE 10-SECOND ELEVATOR PITCH ---
    story.append(Paragraph("1. The 10-Second Elevator Pitch (Explain to a Friend)", h1_style))
    story.append(
        Paragraph(
            "<b>Imagine an AI assistant like ChatGPT or Claude, but instead of chatting in a browser, "
            "it lives directly inside your terminal.</b> It can read your code files, diagnose failing tests, "
            "and write fixes. But unlike standard AI agents that can run wild and accidentally delete files, "
            "<b>TermiCode AI has a strict 'Safety Bouncer'</b> built around it:",
            body_style,
        )
    )

    pitch_data = [
        [
            Paragraph("<b>🛡 Zero-Trust Jail:</b> It mathematically cannot escape your project folder or touch sensitive files like <code>.env</code> or <code>.ssh</code>.", card_text_style),
        ],
        [
            Paragraph("<b>✋ Human-in-the-Loop:</b> It CANNOT write a single file or run a command without showing you a green/red diff and asking <code>[y/N]</code> permission.", card_text_style),
        ],
        [
            Paragraph("<b>🔒 100% Local & Free:</b> It runs completely offline using local open-source models (Ollama / Qwen 2.5 Coder) with $0 API costs and zero data leaks.", card_text_style),
        ],
    ]
    pitch_table = Table(pitch_data, colWidths=[504])
    pitch_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_ACCENT),
            ("BOX", (0, 0), (-1, -1), 1, ACCENT_BLUE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, HexColor("#BFDBFE")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ])
    )
    story.append(pitch_table)
    story.append(Spacer(1, 12))

    # --- 2. THE 4-LAYER SYSTEM ARCHITECTURE ---
    story.append(Paragraph("2. The 4-Layer System Architecture", h1_style))
    story.append(
        Paragraph(
            "TermiCode AI is designed like a bank vault with 4 distinct protective layers:",
            body_style,
        )
    )

    layers_data = [
        [
            Paragraph("<b>Layer</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
            Paragraph("<b>Component</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
            Paragraph("<b>Plain English Explanation</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
        ],
        [
            Paragraph("<b>1. CLI</b>", body_style),
            Paragraph("Typer & Rich Console", body_style),
            Paragraph("The user interface. Formats beautiful tables, status boards, and interactive unified diff prompts.", card_text_style),
        ],
        [
            Paragraph("<b>2. Runtime</b>", body_style),
            Paragraph("ReAct Agent Loop", body_style),
            Paragraph("The 'Brain'. Reads files, thinks, and proposes actions. Strictly capped at 10-25 steps so it never enters infinite loops.", card_text_style),
        ],
        [
            Paragraph("<b>3. Policy</b>", body_style),
            Paragraph("Risk-Tier Engine", body_style),
            Paragraph("The 'Bouncer'. Classifies actions into <b>READ</b> (safe), <b>WRITE</b> (needs approval), and <b>EXECUTE</b> (needs approval).", card_text_style),
        ],
        [
            Paragraph("<b>4. Sandbox</b>", body_style),
            Paragraph("Workspace Boundary", body_style),
            Paragraph("The 'Jail'. Canonicalizes paths, blocks <code>../</code> escapes, guards secrets, and runs subprocesses with <code>shell=False</code>.", card_text_style),
        ],
    ]
    layer_table = Table(layers_data, colWidths=[65, 125, 314])
    layer_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(layer_table)
    story.append(Spacer(1, 14))

    # --- 3. THE 6 WORKFLOW PROFILES ---
    story.append(Paragraph("3. The 6 Workflow Profiles (Role-Based AI)", h1_style))
    story.append(
        Paragraph(
            "Instead of a generic agent that tries to do everything, TermiCode AI uses <b>specialized workflow profiles</b>. "
            "Each profile has hard-coded tool restrictions and strict step budgets:",
            body_style,
        )
    )

    wf_data = [
        [
            Paragraph("<b>Workflow</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY)),
            Paragraph("<b>Policy</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY)),
            Paragraph("<b>Budget</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY)),
            Paragraph("<b>Permitted Tools</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY)),
            Paragraph("<b>What it Does</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=8.5, textColor=PRIMARY)),
        ],
        [
            Paragraph("<b>understand</b>", code_style),
            Paragraph("<font color='#059669'><b>Read-Only</b></font>", card_text_style),
            Paragraph("10 steps", card_text_style),
            Paragraph("list, read, search", card_text_style),
            Paragraph("Explains architecture or answers questions. Zero chance of editing files.", card_text_style),
        ],
        [
            Paragraph("<b>review</b>", code_style),
            Paragraph("<font color='#059669'><b>Read-Only</b></font>", card_text_style),
            Paragraph("10 steps", card_text_style),
            Paragraph("read, search, git diff", card_text_style),
            Paragraph("Inspects git diffs and critiques code for security, style, and logic bugs.", card_text_style),
        ],
        [
            Paragraph("<b>generate</b>", code_style),
            Paragraph("<font color='#D97706'><b>Read/Write/Exec</b></font>", card_text_style),
            Paragraph("25 steps", card_text_style),
            Paragraph("All tools (gated)", card_text_style),
            Paragraph("Implements a requested feature or module after explicit user approval.", card_text_style),
        ],
        [
            Paragraph("<b>debug</b>", code_style),
            Paragraph("<font color='#D97706'><b>Read/Write/Exec</b></font>", card_text_style),
            Paragraph("20 steps", card_text_style),
            Paragraph("All tools (gated)", card_text_style),
            Paragraph("Runs pytest, analyzes stack traces, and applies a minimal targeted patch.", card_text_style),
        ],
        [
            Paragraph("<b>tests</b>", code_style),
            Paragraph("<font color='#D97706'><b>Read/Write/Exec</b></font>", card_text_style),
            Paragraph("20 steps", card_text_style),
            Paragraph("All tools (gated)", card_text_style),
            Paragraph("Generates unit test suites and runs them to verify coverage.", card_text_style),
        ],
        [
            Paragraph("<b>refactor</b>", code_style),
            Paragraph("<font color='#D97706'><b>Read/Write/Exec</b></font>", card_text_style),
            Paragraph("15 steps", card_text_style),
            Paragraph("All tools (gated)", card_text_style),
            Paragraph("Restructures code for clean architecture while verifying tests pass.", card_text_style),
        ],
    ]
    wf_table = Table(wf_data, colWidths=[70, 75, 48, 115, 196])
    wf_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(wf_table)

    story.append(PageBreak())  # PAGE 2

    # --- 4. STEP-BY-STEP EXECUTION LIFECYCLE ---
    story.append(Paragraph("4. Step-by-Step Task Lifecycle (A Real Example)", h1_style))
    story.append(
        Paragraph(
            "Here is the exact lifecycle of what happens when you type: "
            "<code>termicode generate \"Add a helper to format byte sizes\"</code>",
            body_style,
        )
    )

    steps_data = [
        [
            Paragraph("<b>Step 1: Workspace Check</b>", h2_style),
            Paragraph("TermiCode locks the current directory as the root. Any path containing <code>../</code>, <code>.env</code>, or external drives is instantly forbidden.", card_text_style),
        ],
        [
            Paragraph("<b>Step 2: Model Inspection</b>", h2_style),
            Paragraph("The AI asks to call <code>list_files()</code>. Policy Engine sees Risk Level = <b>READ</b>. This is safe, so it executes automatically.", card_text_style),
        ],
        [
            Paragraph("<b>Step 3: Mutation Proposal</b>", h2_style),
            Paragraph("The AI crafts the code and asks to call <code>write_file(path='src/helpers.py', content=...)</code>. Policy Engine sees Risk Level = <b>WRITE</b>. It immediately <b>HALTS</b> execution.", card_text_style),
        ],
        [
            Paragraph("<b>Step 4: Interactive Approval</b>", h2_style),
            Paragraph("A colorized diff preview is shown in the terminal: additions in green (<code>+</code>) and deletions in red (<code>-</code>). TermiCode asks: <code>Approve? [y/N]:</code>. If you press Enter or type 'n', it safely aborts.", card_text_style),
        ],
        [
            Paragraph("<b>Step 5: Execution & Memory</b>", h2_style),
            Paragraph("If you type 'y', the file is written. The full conversation, tool duration, and decisions are written to <code>.termicode/runs/run_xxx.jsonl</code> with all API keys automatically redacted.", card_text_style),
        ],
    ]
    step_table = Table(steps_data, colWidths=[140, 364])
    step_table.setStyle(
        TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("BACKGROUND", (0, 0), (0, -1), BG_LIGHT),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(step_table)
    story.append(Spacer(1, 14))

    # --- 5. THE 3 WAYS TO RUN IT ---
    story.append(Paragraph("5. The 3 Ways to Run TermiCode AI", h1_style))
    story.append(
        Paragraph(
            "TermiCode AI supports 3 distinct execution engines for maximum flexibility:",
            body_style,
        )
    )

    modes_data = [
        [
            Paragraph("<b>Mode</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
            Paragraph("<b>Command to Activate</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
            Paragraph("<b>Best For</b>", ParagraphStyle("Hdr", fontName="Helvetica-Bold", fontSize=9, textColor=PRIMARY)),
        ],
        [
            Paragraph("<b>1. Local Ollama</b><br/>(Local AI)", body_style),
            Paragraph("<code>$env:TERMICODE_PROVIDER=\"ollama\"</code><br/><code>termicode status</code>", code_style),
            Paragraph("<b>Interviews & Enterprise:</b> Real local AI (Qwen 2.5 Coder) running on your PC. 100% free, private, no API key needed.", card_text_style),
        ],
        [
            Paragraph("<b>2. Fake Mode</b><br/>(Mock Testbed)", body_style),
            Paragraph("<code>$env:TERMICODE_PROVIDER=\"fake\"</code><br/><code>termicode understand \"...\"</code>", code_style),
            Paragraph("<b>Instant Demo & Testing:</b> Zero setup, zero downloads. Tests approval prompts and diff previews with 0 tokens.", card_text_style),
        ],
        [
            Paragraph("<b>3. OpenAI Cloud</b><br/>(Cloud API)", body_style),
            Paragraph("<code>$env:OPENAI_API_KEY=\"sk-...\"</code><br/><code>termicode generate \"...\"</code>", code_style),
            Paragraph("<b>Production Heavy Coding:</b> Powered by GPT-4o for massive multi-file feature synthesis.", card_text_style),
        ],
    ]
    mode_table = Table(modes_data, colWidths=[95, 185, 224])
    mode_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ])
    )
    story.append(mode_table)
    story.append(Spacer(1, 14))

    # --- 6. WHY THIS IMPRESSES IN INTERVIEWS ---
    story.append(Paragraph("6. Key Takeaways to Tell Your Friend or Interviewer", h1_style))
    story.append(
        Paragraph(
            "If someone asks: <i>'Isn't this just another ChatGPT wrapper?'</i>, here are the 4 points that prove it is serious systems engineering:",
            body_style,
        )
    )

    takeaways = [
        "<b>1. Defense-in-Depth Security:</b> It enforces real containment (path canonicalization via <code>os.path.realpath</code>, symlink blocking, sensitive credential protection for <code>.env</code> and <code>.ssh</code>).",
        "<b>2. Subprocess Containment:</b> Shell execution uses <code>shell=False</code>, strict command allowlisting (<code>python</code>, <code>pytest</code>, <code>ruff</code>, <code>mypy</code>, <code>git</code>), 30s timeouts, and 64 KB output buffer caps.",
        "<b>3. Deterministic Test Suite:</b> Achieves <b>285 test cases (284 passed, 1 skipped) in 10 seconds</b> in CI using the built-in <code>FakeProvider</code> testbed with 100% Mypy and Ruff compliance.",
        "<b>4. Complete Data Privacy:</b> Solves the #1 enterprise blocker for generative AI by keeping all code and inference strictly on localhost via Ollama.",
    ]
    for pt in takeaways:
        story.append(Paragraph(f"• {pt}", bullet_style))

    story.append(Spacer(1, 10))

    # Footer note card
    summary_card = [
        [
            Paragraph(
                "<b>Quick Command Summary:</b><br/>"
                "• Check status: <code>termicode status</code><br/>"
                "• View workflows: <code>termicode workflows</code><br/>"
                "• Read-only check: <code>termicode understand \"explain project\"</code><br/>"
                "• Safe dry-run: <code>termicode generate \"new feature\" --dry-run</code><br/>"
                "• Run tests: <code>pytest</code> (285 automated tests)",
                card_text_style,
            )
        ]
    ]
    summary_table = Table(summary_card, colWidths=[504])
    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_COLOR),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ])
    )
    story.append(summary_table)

    # Build document
    doc.build(story, canvasmaker=NumberedCanvas)
    return str(pdf_path)


if __name__ == "__main__":
    out_file = build_pdf()
    print(f"Generated PDF successfully at: {out_file}")
