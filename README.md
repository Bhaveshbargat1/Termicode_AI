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
