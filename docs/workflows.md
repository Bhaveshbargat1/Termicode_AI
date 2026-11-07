# TermiCode AI — Workflow Profiles

TermiCode AI operates using a **single bounded agent runtime** governed by explicit workflow profiles. Rather than delegating tasks to autonomous subagent swarms that introduce coordination overhead and unpredictable tool authority, TermiCode configures a single agent loop with exact tool permissions, risk levels, behavioral prompts, and step budgets tailored to each software engineering objective.

---

## 1. Single Runtime vs. Multi-Agent Sprawl

| Approach | TermiCode AI Single-Agent Architecture | Multi-Agent Architecture |
|---|---|---|
| **Determinism** | Fully deterministic loop with strict step bounds (8–12 steps). | Unpredictable agent spawning, message loops, and hidden costs. |
| **Authority** | Enforced at the policy layer per workflow profile. | Variable authority across subagents; hard to audit in real time. |
| **Auditability** | Single chronological JSONL transcript per run. | Dispersed transcripts across multiple agent conversations. |
| **Safety** | Human-in-the-loop approvals for every mutating action. | Approvals can be bypassed or obscured in subagent call trees. |

---

## 2. Tool Permission Matrix

The table below illustrates which tools are accessible to each workflow profile:

| Tool | Risk Level | `understand` | `review` | `generate` | `debug` | `tests` | `refactor` | `--plan` |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `list_files` | READ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `read_file` | READ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `search_text` | READ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `write_file` | WRITE (Approval) | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `apply_patch` | WRITE (Approval) | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `run_command` | EXECUTE (Approval)| ❌ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Step Budget** | — | **8** | **8** | **12** | **10** | **10** | **10** | **1** |

> [!NOTE]
> Read-only profiles (`understand`, `review`, and `--plan`) **never declare or permit** write or execute tools. Even if the LLM attempts to propose `write_file` or `run_command`, the deterministic policy engine immediately rejects the action without prompting the user.

---

## 3. Workflow Profiles Detailed Reference

### 3.1 `understand` (Read-Only)
- **Primary Goal:** Rapidly comprehend project structure, code flow, configuration, and architectural patterns without modifying disk or running commands.
- **Allowed Tools:** `list_files`, `read_file`, `search_text`.
- **Policy:** Strictly `READ` risk level only.
- **Step Budget:** 8 steps.
- **Success Criteria:** Accurate, clear, and comprehensive explanation of the project, architecture, or component with zero file modifications or command executions.
- **CLI Invocations:**
  ```bash
  termicode understand "Explain the authentication flow in auth.py"
  # or
  termicode run "Explain the authentication flow in auth.py" --workflow understand
  ```

### 3.2 `review` (Read-Only)
- **Primary Goal:** Inspect existing files, recent changes, and code patterns to detect defects, security bugs, performance bottlenecks, or style inconsistencies.
- **Allowed Tools:** `list_files`, `read_file`, `search_text`.
- **Policy:** Strictly `READ` risk level only.
- **Step Budget:** 8 steps.
- **Success Criteria:** Thorough, prioritized review findings citing specific files and issues, with zero file modifications or command executions.
- **CLI Invocations:**
  ```bash
  termicode review "Review src/termicode/runner for command injection risks"
  # or
  termicode run "Review src/termicode/runner for command injection risks" --workflow review
  ```

### 3.3 `generate` (Read / Write / Execute)
- **Primary Goal:** Implement small features, add utility functions, or construct new modules while following existing conventions.
- **Allowed Tools:** All standard tools (`list_files`, `read_file`, `search_text`, `write_file`, `apply_patch`, `run_command`).
- **Policy:** `READ`, `WRITE` (user approval required), `EXECUTE` (user approval required).
- **Step Budget:** 12 steps.
- **Success Criteria:** The requested feature is implemented cleanly with minimal diff, passes relevant tests, and introduces no regressions.
- **CLI Invocations:**
  ```bash
  termicode generate "Add a health check endpoint in api/routes.py"
  # or
  termicode run "Add a health check endpoint in api/routes.py" --workflow generate
  ```

### 3.4 `debug` (Read / Write / Execute)
- **Primary Goal:** Inspect failing test output, trace execution paths to identify root causes, and propose targeted, minimal bug fixes.
- **Allowed Tools:** All standard tools.
- **Policy:** `READ`, `WRITE` (user approval required), `EXECUTE` (user approval required).
- **Step Budget:** 10 steps.
- **Success Criteria:** The failing test passes, the root cause is clearly diagnosed, minimal code changes are made, and existing tests remain green.
- **CLI Invocations:**
  ```bash
  termicode debug "Diagnose failing test_path_traversal in test_workspace.py"
  # or
  termicode run "Diagnose failing test_path_traversal in test_workspace.py" --workflow debug
  ```

### 3.5 `tests` (Read / Write / Execute)
- **Primary Goal:** Increase test coverage, construct regression test cases, or add edge-case fixtures.
- **Allowed Tools:** All standard tools.
- **Policy:** `READ`, `WRITE` (user approval required), `EXECUTE` (user approval required).
- **Step Budget:** 10 steps.
- **Success Criteria:** New or improved tests are created, cover targeted scenarios and edge cases, pass cleanly, and maintain test suite reliability.
- **CLI Invocations:**
  ```bash
  termicode tests "Add parameterized unit tests for resolve_safe edge cases"
  # or
  termicode run "Add parameterized unit tests for resolve_safe edge cases" --workflow tests
  ```

### 3.6 `refactor` (Read / Write / Execute)
- **Primary Goal:** Clean up code duplication, extract helper functions, or simplify complex conditionals without altering external behavior.
- **Allowed Tools:** All standard tools.
- **Policy:** `READ`, `WRITE` (user approval required), `EXECUTE` (user approval required).
- **Step Budget:** 10 steps.
- **Success Criteria:** Targeted refactor improves code structure while preserving existing behavior, passes 100% of existing tests, and has explicit user approval.
- **CLI Invocations:**
  ```bash
  termicode refactor "Extract duplicate validation logic from tools/filesystem.py"
  # or
  termicode run "Extract duplicate validation logic from tools/filesystem.py" --workflow refactor
  ```

---

## 4. Special Modes: Plan & Dry-Run

### 4.1 Plan Mode (`--plan` / `termicode plan`)
Plan mode forces the agent to generate a structured, read-only architectural blueprint without executing any mutations or commands:
- **No Disk Changes:** Generates pure analysis and implementation blueprints.
- **Step Budget:** 1 step.
- **Tool Access:** Strictly read-only tools if needed.
- **CLI Invocations:**
  ```bash
  termicode plan "Architect a pluggable caching layer for database queries"
  # or
  termicode run "Architect a pluggable caching layer for database queries" --plan
  ```

### 4.2 Dry-Run Mode (`--dry-run`)
Dry-run mode can be combined with any modifying workflow (`generate`, `debug`, `tests`, `refactor`). The agent progresses through its normal reasoning loop and proposes concrete actions, but:
- Proposed file writes and patches are rendered as diffs and logged, but **never applied to disk**.
- Proposed subprocess commands are validated, but **never launched**.
- Approvals are simulated as `[DRY-RUN]` without modifying state.
- **CLI Invocations:**
  ```bash
  termicode generate "Add timestamp field to user model" --dry-run
  termicode debug "Fix IndexError in parser.py" --dry-run
  ```

---

## 5. Selecting the Right Workflow Profile

```text
Do you need to modify files or run tests?
 ├── NO:
 │    ├── Exploring, learning, or searching? ──────► `understand`
 │    └── Reviewing PRs, code quality, or diffs? ──► `review`
 └── YES:
      ├── Fixing a broken test or bug? ────────────► `debug`
      ├── Adding new tests or improving coverage? ─► `tests`
      ├── Restructuring code without changing logic? ► `refactor`
      └── Building a new feature or utility? ──────► `generate`
```
