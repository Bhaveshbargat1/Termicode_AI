# TermiCode AI

TermiCode AI is a secure, bounded terminal coding assistant designed around the core principle of **deterministic authority separation**. 

The Large Language Model (LLM) acts as an untrusted planner proposing actions. Python software boundaries validate policy, confine file operations strictly to an approved workspace, enforce human-in-the-loop approvals with diff previews, and restrict subprocess execution to an allowlist with `shell=False`.

> [!NOTE]
> **Honest Boundary Notice:** TermiCode AI enforces application-level policy boundaries and human approval gates. It does **not** provide kernel-level virtualization, Docker containers, or OS-level sandboxing (no cgroups or seccomp). Approved commands run as child processes under your local user account.

---

## Documentation

- 📐 **[Architecture](docs/architecture.md)** — Authority separation, component model, task lifecycle, and local memory.
- 🛡️ **[Threat Model](docs/threat-model.md)** — Trust boundaries, attack vectors, mitigated vs. unmitigated threats, and sandbox reality.
- 🔒 **[Safety Guarantees](docs/safety.md)** — Workspace containment, approval engine, command allowlists, secret redaction, and MVP limitations.
- 🔄 **[Workflow Profiles](docs/workflows.md)** — Single-agent profiles (`generate`, `debug`, `understand`, `tests`, `review`, `refactor`), tool permission matrix, and step budgets.
- 🔌 **[ToolBridge MCP Gateway Integration](docs/INTEGRATION.md)** — Integration interface, REST API contracts, and extended tool configuration.

---

## Installation & Setup

### Prerequisites
- Python 3.11 or later
- Git (optional, for version control)

### 1. Clone & Navigate to Repository
```bash
git clone https://github.com/example/termicode-ai.git
cd "Termicode AI"
```

### 2. Create and Activate Virtual Environment
**On Windows (PowerShell):**
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**On Linux / macOS:**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. Install Package & Dependencies
Install in editable development mode:
```bash
pip install --upgrade pip
pip install -e ".[dev]"
```

### 4. Running TermiCode AI: Three Workable Modes

TermiCode AI works out-of-the-box with zero mandatory setup, offering three execution modes:

#### Option A: Built-in Offline Engine (Zero External Dependencies)
Run immediately without API keys or external servers:
```bash
# Add --offline to any command
termicode understand "explain project structure" --offline

# Or specify the fake provider
termicode run "explain main.py" -p fake
```
*Interactive Fallback:* If you run TermiCode without an API key or when Ollama is offline, TermiCode will automatically prompt:
`Would you like to run with TermiCode's offline engine? [Y/n]: `
Simply press **Enter** to proceed smoothly!

#### Option B: Local AI with Ollama (100% Private, Free, No API Keys)
Run local coding models privately on your hardware:
```bash
# 1. Start Ollama server
ollama serve

# 2. Pull the recommended coding model
ollama pull qwen2.5-coder:7b

# 3. Run TermiCode pointing to Ollama
termicode run "create a helper function" -p ollama -m qwen2.5-coder:7b
```

#### Option C: Cloud AI (OpenAI API)
```bash
# In PowerShell:
$env:OPENAI_API_KEY = "sk-..."

# In Bash / macOS:
export OPENAI_API_KEY="sk-..."

# Run with standard OpenAI models
termicode run "explain project architecture"
```

#### Option D: Interactive Terminal Chatbot (REPL)
Launch an interactive, multi-turn conversational session with streaming markdown and syntax highlighting:
```bash
# Start conversational chatbot in the active workspace
termicode chat

# Or launch in 100% offline mode
termicode chat --offline

# Or specify a custom model or provider
termicode chat -p ollama -m qwen2.5-coder:7b
```

Inside the chat session, you can use:
- `/help` — Display command guide and controls.
- `/upload <path>` — Attach PDFs, notes, or code files to the active session.
- `/docs` — List all attached documents.
- `/drop <id>` — Remove an attached document (with interactive confirmation).
- `/steps [on|off]` — Toggle intermediate planner and safety steps panel.
- `/session list` — List persistent conversation sessions stored in SQLite.
- `/session switch <id>` — Switch to a previous session and restore message history.
- `/session new [title]` — Create and activate a new persistent session.
- `/session info` — View current session metadata, message count, and memory summary.
- `/mcp servers` — Inspect configured ToolBridge MCP Gateway servers.
- `/mcp list [server]` — Discover all available namespaced MCP tools.

#### Option E: Browser Web Interface & Local Companion
Launch the secure local companion service and open the browser-facing control center:
```bash
# Launch companion and automatically open web interface
termicode web

# Or run companion explicitly on custom port/token
termicode companion --port 8765
```

**Security & Privacy Guarantees in Web/Companion Mode:**
- **Zero Cloud Code Upload:** The companion runs strictly on `127.0.0.1`. Your code, documents, and credentials never leave your computer.
- **CSRF & Origin Protection:** Rejects unauthorized cross-origin browser requests (HTTP 403).
- **Token Authentication:** Requires an ephemeral cryptographically random companion token (`X-Companion-Token`) for all operations (HTTP 401).
- **Human Approval Gate:** All file mutations (`write_file`, `apply_patch`), shell executions, and remote MCP actions require explicit approval with diff previews in the browser before any disk write occurs.
- **Hosted Execution Gate:** Hosted arbitrary execution in the cloud is disabled by design. All real workspace modifications run safely through this local companion.
- `/mcp inspect <tool>` — View tool input schema, sensitivity tier, and parameters.
- `/mcp call <server> <tool> [args]` — Execute remote MCP tool with interactive confirmation.
- `/checkpoint [msg]` — Save a manual Git workspace checkpoint.
- `/rollback [id]` — Revert workspace to last checkpoint (with confirmation).
- `/checkpoints` — List recent Git checkpoints.
- `/new` — Clear history and start a fresh turn.
- `/history` — View previous conversation turns.
- `/settings` — Inspect or update active model/provider/ToolBridge configuration.
- `/multiline` — Enter multi-line input mode (or start input with `"""`).
- `exit` or `/exit` — Quit the chat session.

---

## First-Run Tutorial: Disposable Sample Project

This hands-on walkthrough guides you through creating a temporary demo project, exploring it with read-only tools, previewing changes with dry-run mode, interactively approving fixes, and auditing the local run history.

### Step 1: Create a Disposable Sample Project

Create a temporary directory `sample_project` with a small buggy function and a failing test:

**On Linux / macOS / Bash:**
```bash
mkdir -p sample_project/src sample_project/tests
cat << 'EOF' > sample_project/src/calculator.py
def divide(a: float, b: float) -> float:
    # BUG: No check for division by zero
    return a / b
EOF

cat << 'EOF' > sample_project/tests/test_calculator.py
import pytest
from src.calculator import divide

def test_divide_valid():
    assert divide(10, 2) == 5.0

def test_divide_by_zero():
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide(10, 0)
EOF
```

**On Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Path sample_project/src, sample_project/tests -Force

Set-Content -Path sample_project/src/calculator.py -Value @"
def divide(a: float, b: float) -> float:
    # BUG: No check for division by zero
    return a / b
"@

Set-Content -Path sample_project/tests/test_calculator.py -Value @"
import pytest
from src.calculator import divide

def test_divide_valid():
    assert divide(10, 2) == 5.0

def test_divide_by_zero():
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide(10, 0)
"@
```

---

### Step 2: Verify Status & Workspace Containment

Point TermiCode to the disposable project using `--workspace`:

```bash
termicode --workspace ./sample_project status
```
*Output confirms the active workspace root, active provider, tool registry status, and safety settings.*

---

### Step 3: Understand the Project (Read-Only)

Use the read-only `understand` workflow to inspect the code. In this profile, the agent is restricted exclusively to read operations (`list_files`, `read_file`, `search_text`):

```bash
termicode --workspace ./sample_project understand "Explain what functions exist and what tests are defined"
```
*TermiCode inspects `src/calculator.py` and `tests/test_calculator.py` and produces an explanation with zero risk of modifying files.*

---

### Step 4: Generate a Read-Only Plan

Before writing code, generate an architectural plan:

```bash
termicode --workspace ./sample_project plan "Fix divide to handle division by zero and satisfy test_calculator"
```
*Produces a step-by-step implementation blueprint without mutating files or running commands.*

---

### Step 5: Test Safe Dry-Run Mode

Run the `debug` workflow with `--dry-run` to preview the actions proposed by the model without applying them to disk:

```bash
termicode --workspace ./sample_project run "Fix failing divide by zero test" --workflow debug --dry-run
```
*TermiCode displays proposed changes with `[DRY-RUN]` markers, demonstrating the exact unified diff without touching the filesystem.*

---

### Step 6: Fix the Bug with Interactive Human Approval

Now run the task with interactive execution:

```bash
termicode --workspace ./sample_project run "Fix failing divide by zero test and run pytest" --workflow debug
```

When TermiCode proposes modifying `src/calculator.py`, you will see a human-readable approval prompt:

```text
Action Proposal: write_file [Risk: WRITE]
Target: src/calculator.py
--- src/calculator.py
+++ src/calculator.py
@@ -1,3 +1,5 @@
 def divide(a: float, b: float) -> float:
-    # BUG: No check for division by zero
+    if b == 0:
+        raise ValueError("Cannot divide by zero")
     return a / b

Approve action? [y/N]: y
```

1. Type `y` to approve the proposed edit.
2. TermiCode writes the file and subsequently proposes running `pytest`.
3. Review the command arguments: `["pytest"]` and type `y` to approve test execution.
4. The test passes! A final summary table displays changed files, tests run, stop reason (`completed`), and transcript location.

---

### Step 7: Inspect Run History & Audit Transcripts

Every action and decision is saved locally under `sample_project/.termicode/`:

```bash
# List all recorded runs in the workspace
termicode --workspace ./sample_project runs

# Display compact summary of the latest run
termicode --workspace ./sample_project summary

# View the chronological JSONL audit transcript
termicode --workspace ./sample_project transcript
```

---

### Step 8: Clean Up Disposable Project

When finished, safely delete the disposable directory:
```bash
# On Linux / macOS:
rm -rf sample_project

# On Windows (PowerShell):
Remove-Item -Recurse -Force sample_project
```

---

## 🔌 ToolBridge MCP Gateway Integration

TermiCode AI integrates with the **[ToolBridge Enterprise MCP Gateway](../Toolbridge%20-%20mcp%20gateway/README.md)** to securely access external infrastructure tools (e.g., PostgreSQL database queries, GitHub repository issues, and external MCP servers) through an enterprise REST API or low-level MCP `stdio` stream.

- **Contract & API Guide:** Detailed request/response payloads, authentication keys, and curl examples are documented in **[docs/INTEGRATION.md](docs/INTEGRATION.md)**.
- **Client Scaffolding:** Stub contract and HTTP client are available in `termicode.tools.toolbridge` (`ToolBridgeClientStub`).
- **Configuration (.env):**
  ```ini
  TERMICODE_TOOLBRIDGE_ENABLED=true
  TERMICODE_TOOLBRIDGE_API_URL=http://127.0.0.1:8000
  TERMICODE_TOOLBRIDGE_API_KEY="tb_dC9QQ7j_VY2aAxyqlIthznCS_6-OckoYtCrCIxECBlQ"
  TERMICODE_TOOLBRIDGE_ROLE=operator
  ```

---

## 🛡️ Version Control: Git Checkpoints & Rollback

TermiCode AI provides automatic and manual Git checkpoints, allowing safe code experimentation and instant rollback when actions fail or are cancelled.

### Key Capabilities

1. **Workspace Git Verification & Initialization:**
   TermiCode ensures the active workspace is a Git repository. Run `termicode init` to initialize Git version control and configure standard `.gitignore` rules (including automatic `.termicode/` exclusion).
2. **Automated Mutating Checkpoints:**
   Before any file-modifying tool (`write_file`, `apply_patch`) executes in the agent loop, TermiCode automatically creates a Git checkpoint capturing both modified and untracked files.
3. **Automated & Interactive Rollback:**
   If a planned step fails, exceeds its repair budget, or is cancelled by the user, TermiCode prompts to rollback the workspace to the pre-action checkpoint. In automated runs (`--yes` / `-y`), failing tools automatically revert workspace changes.
4. **Uncommitted Changes Protection:**
   TermiCode detects uncommitted modifications, logs warnings, and warns users before overwriting uncommitted files during rollbacks.
5. **Post-Task Commits:**
   Optionally commit workspace modifications after successful plan completion and test execution via `--commit` / `-c`.

### CLI Commands

```bash
# 1. Initialize Git repository and TermiCode metadata
termicode init

# 2. Manually save a workspace checkpoint
termicode checkpoint "before major refactor"

# 3. List recent checkpoints
termicode checkpoints

# 4. Rollback to the most recent checkpoint (or specify an ID)
termicode rollback
termicode rollback cp_a1b2c3d4 -y

# 5. Run a task with auto-commit on success
termicode run "Implement helper functions in src/utils.py" --commit -m "Add helper utilities"
```

---

## 🎯 Interactive "Try It Out" Walkthrough

This rapid 5-minute walkthrough tests document question-answering (RAG), ToolBridge MCP tool discovery, and Git rollback protection:

### 1. Initialize Workspace & Git Version Control
```bash
termicode init
```
*Output: Confirms workspace root validation, `.termicode/` metadata setup, and Git repository initialization.*

### 2. Create Sample Document & Ask Questions via RAG
Create a sample architecture note:
```bash
# In Bash / macOS / Linux:
echo -e "# Architecture Notes\n\nAuthentication is handled via OAuth2 Bearer tokens with 15-minute expiry." > notes.md

# In Windows PowerShell:
Set-Content -Path notes.md -Value "# Architecture Notes`n`nAuthentication is handled via OAuth2 Bearer tokens with 15-minute expiry."
```
Now query TermiCode to extract answers from your notes:
```bash
termicode understand "What is the token expiry time mentioned in notes.md?" --offline
```
*Or attach it interactively in chat:*
```bash
termicode chat --offline
termicode > /upload notes.md
termicode > What authentication method and expiry time is specified in notes?
```

### 3. Connect to ToolBridge MCP Gateway
If ToolBridge is running (`python -m toolbridge.api.app` on port 8000):
```bash
# Register ToolBridge Gateway
termicode toolbridge add-server gateway http://127.0.0.1:8000 --token tb_admin_secret_key_123

# List all 11 discovered tools across Filesystem, Postgres, and GitHub
termicode toolbridge list-tools gateway

# Inspect and execute a safe query
termicode toolbridge call-tool gateway filesystem__read_file '{"path": "README.md"}'
```

### 4. Test Automated Safety Checkpoint & Rollback
Save a manual checkpoint, experiment freely, and rollback:
```bash
# 1. Create a checkpoint
termicode checkpoint "Clean state before test"

# 2. Modify or corrupt a test file
echo "experimental garbage" >> notes.md

# 3. Rollback changes cleanly
termicode rollback -y
```
*Output: Workspace is instantly restored to its clean checkpoint state!*

---

## ❓ Troubleshooting & FAQ

| Error / Symptom | Probable Cause | Actionable Fix |
| :--- | :--- | :--- |
| **`Provider Authentication Error`** | No API key found in `.env` or environment. | Set `OPENAI_API_KEY="sk-..."` in `.env` or run with `--offline` (`-p fake`) for zero-dependency execution. |
| **`Workspace Validation Error`** | File or command path targets a directory outside the approved workspace root. | TermiCode is strictly confined to your workspace root. Ensure paths are relative to the root or specify `--workspace <path>`. |
| **`ToolBridge Gateway Unreachable`** | ToolBridge API service is not running on `http://127.0.0.1:8000`. | Start ToolBridge: `python -m toolbridge.api.app` or verify URL using `termicode toolbridge list-servers`. |
| **`Not a Git repository`** | Workspace has not been initialized with Git. | Run `termicode init` or `git init` to initialize Git version control and enable checkpoints. |
| **`Session Not Found`** | Provided session ID does not exist in `.termicode/chat_sessions.db`. | Run `termicode session list` to view active sessions, or `termicode session new "My Session"` to create one. |
| **Where are full technical debug logs?** | TermiCode hides raw stack traces from the console to keep output clean and readable. | Full timestamped tracebacks are logged automatically to `.termicode/logs/debug.log` inside your workspace root. |

---

## Core Safety Architecture

1. **Workspace Boundary:**
   `WorkspaceBoundary.resolve_safe()` strictly anchors all file operations inside the project root, blocking directory traversal (`../../`) and protecting credentials (`.env`, `.git`, SSH keys).
2. **Approval Engine & Fail-Safe Defaults:**
   Mutating actions require interactive approval. Piped/non-interactive inputs and invalid responses default strictly to **DENY**.
3. **Command Allowlist (`shell=False`):**
   Only allowlisted development binaries (`python`, `pytest`, `ruff`, `mypy`, `git`) can run. Metacharacters (`|`, `;`, `&&`, `$()`) and dangerous commands (`rm`, `sudo`, `curl`, `pip`) are deterministically rejected.
4. **Execution Limits:**
   Subprocesses are capped with a 30-second timeout and 64 KB output buffer.
5. **Bounded Agent Loops:**
   Tasks enforce strict step budgets (8–12 steps) and repair counters (max 3 repairs).
6. **Local Memory & Secret Redaction:**
   Data is stored exclusively in `.termicode/` within the workspace. API keys (`sk-...`, `AIza...`) and bearer tokens are automatically redacted.

---

## Quality Checks & Verification

Run the full automated test suite, linter, and static type checker:

```bash
# Run pytest test suite
pytest

# Check code formatting and linting
ruff check .
ruff format --check .

# Run static type checking
mypy src tests
```
