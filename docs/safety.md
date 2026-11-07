# TermiCode AI — Safety Guarantees & Security Architecture

TermiCode AI is designed with **defense-in-depth deterministic safety boundaries**. In TermiCode, the Large Language Model (LLM) is treated as an untrusted proposer: it never holds filesystem handles, shell access, or execution authority. 

All safety policies are enforced deterministically by Python code before any action can take effect.

---

## 1. Authority Separation

```text
┌─────────────────────────────────┐
│     Untrusted Model Proposer    │
│  Suggests: ToolName + Arguments │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│   Deterministic Policy Engine   │
│  • Tool Allowlist Check         │
│  • Risk Evaluation              │
│  • Path Containment Validation  │
│  • Argument Security Validation │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│     Human Approval Controller   │
│  • Unified diff preview         │
│  • Relative path display        │
│  • Default to DENY on invalid   │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│    Bounded Tool Execution       │
│  • shell=False                  │
│  • cwd = workspace root         │
│  • Timeout & output caps        │
└─────────────────────────────────┘
```

---

## 2. Workspace Boundary Containment

All file system operations are strictly anchored to the resolved workspace root via `WorkspaceBoundary`.

### 2.1 Canonical Path Resolution (`resolve_safe`)
Before any file is read, written, or patched, `WorkspaceBoundary.resolve_safe(target)` performs canonical path validation:
1. **Absolute Canonicalization:** Resolves symlinks and relative path components (`.`, `..`) using `pathlib.Path.resolve()`.
2. **Containment Verification:** Checks `resolved.is_relative_to(workspace_root)`. If a path escapes the root (e.g. `../../etc/passwd` or `C:\Windows\System32`), execution is blocked immediately with a `WorkspaceEscapeError`.
3. **Symlink Boundary Checks:** Symlinks pointing outside the workspace resolve to their target path during canonicalization, causing `is_relative_to` to return `False` and triggering boundary rejection.

### 2.2 Sensitive File Protection
Even within a permitted workspace, `WorkspaceBoundary.is_sensitive(path)` actively blocks access to credential and version control files:
- **Protected Directories:** `.git`, `.ssh`, `.aws`, `.gnupg`
- **Protected Files:** `.env` and `.env.*` (with exception for `.env.example`), `id_rsa`, `id_ed25519`, `id_ecdsa`, `id_dsa`, `credentials`, `credentials.json`
- **Protected Extensions:** `.pem`, `.key`

Attempting to read or write any protected path raises a `SensitivePathError`.

---

## 3. Interactive Approval Model

Mutating the filesystem or running processes introduces risk. TermiCode AI enforces an interactive approval gate for all non-read actions.

### 3.1 Risk Classification
Every tool declares a strict `RiskLevel`:
- `RiskLevel.READ` (`list_files`, `read_file`, `search_text`): Automatically executed if permitted by the workflow profile.
- `RiskLevel.WRITE` (`write_file`, `apply_patch`): Requires explicit interactive confirmation.
- `RiskLevel.EXECUTE` (`run_command`): Requires explicit interactive confirmation.

### 3.2 Human-Readable Prompts
When approval is required, TermiCode renders a structured Rich approval panel containing:
- **Action & Risk:** e.g. `write_file [Risk: WRITE]` or `run_command [Risk: EXECUTE]`.
- **Target Path:** Formatted as a workspace-relative path (e.g. `src/utils.py` instead of `/Users/name/.../src/utils.py`).
- **Command Arguments:** Printed as a clean array `["pytest", "-k", "test_auth"]`.
- **Unified Diff Preview:** Colorized unified diff showing exact deletions (`- red`) and additions (`+ green`) before changes hit disk.

```text
Action Proposal: write_file [Risk: WRITE]
Target: src/utils.py
--- src/utils.py
+++ src/utils.py
@@ -10,2 +10,4 @@
 def calculate_total(items):
+    if not items:
+        return 0
     return sum(item.price for item in items)

Approve action? [y/N]:
```

### 3.3 Fail-Safe Defaults
- **Default to DENY:** Any input other than explicit `'y'` or `'yes'` (case-insensitive) results in a denial.
- **Non-Interactive Detection:** In automated environments, cron jobs, or piped stdin without a TTY (`sys.stdin.isatty() == False`), approval defaults to **DENY** unless explicitly run with `--yes`.
- **Ctrl+C Safe:** Pressing `Ctrl+C` cleanly aborts the task with stop reason `USER_CANCELLED` and writes the transcript without python traceback dumps.
- **No Unrestricted `--force`:** TermiCode does not offer a `--force` flag that bypasses safety checks or policy validation.

---

## 4. Secure Subprocess Execution (`CommandRunner`)

Running commands on a local machine is high-risk. TermiCode AI restricts execution using strict deterministic rules:

### 4.1 No Shell (`shell=False`)
Commands are executed directly via `subprocess.run(argv, shell=False)`. No shell interpreter (`sh`, `bash`, `cmd.exe`, `powershell.exe`) is invoked, eliminating shell parsing vulnerabilities.

### 4.2 Executable Allowlist
Only explicitly vetted development tools are permitted:
- `python`
- `pytest`
- `ruff`
- `mypy`
- `git`

Any other executable (such as `curl`, `wget`, `rm`, `del`, `chmod`, `sudo`, `bash`, `npm`, `pip`) raises a `DisallowedExecutableError`.

### 4.3 Git Restrictions
To prevent accidental code destruction or unauthorized data exfiltration:
- **Permitted subcommands:** `status`, `diff`, `log`, `branch`, `show` (read-only).
- **Prohibited subcommands:** `push`, `reset`, `checkout`, `commit`, `clean`, `rm`, `rebase`, `merge`, `pull`, `clone`, `fetch`, `config`, `remote`.

### 4.4 Python Restrictions
- Inline code execution (`python -c "..."`) is prohibited.
- Arbitrary package installation (`python -m pip install ...`) is prohibited.
- `python -m` is restricted to approved testing and linting modules: `pytest`, `unittest`, `ruff`, `mypy`.

### 4.5 Shell Metacharacter Blocking
All command arguments are inspected for forbidden shell metacharacters and tokens:
`|`, `>`, `<`, `;`, `&&`, `||`, `` ` ``, `$()`, `${}`, `\n`, `\r`.

If any argument contains these patterns, `UnsafeArgumentError` is raised.

### 4.6 Execution Guardrails
- **CWD Forced to Workspace:** Subprocesses always run with `cwd` set strictly to the canonical workspace root.
- **Wall-Clock Timeout:** Execution is terminated after 30 seconds (`DEFAULT_TIMEOUT_SECONDS = 30`) to prevent hangs.
- **Output Capping:** Captured `stdout` and `stderr` are capped at 64 KB (`DEFAULT_MAX_OUTPUT_BYTES = 65536`) to protect memory.

---

## 5. Bounded Agent Loops

To prevent infinite loops and runaway API costs:
- **Step Limits:** Every workflow enforces a hard step budget (8 steps for `understand`/`review`, 10 steps for `debug`/`tests`/`refactor`, 12 steps for `generate`).
- **Repair Limits:** If the model proposes malformed tool calls or invalid arguments, it is given at most 3 repair attempts (`DEFAULT_MAX_REPAIRS = 3`). On the 4th failure, the loop aborts with `MAX_REPAIRS_REACHED`.
- **Explicit Stop Reasons:** Every execution terminates with an explicit reason (`completed`, `user_cancelled`, `max_steps_reached`, `max_repairs_reached`, `policy_rejected`, `tool_error`, `provider_error`).

---

## 6. Secret Redaction

TermiCode AI ensures credentials and API keys do not leak into terminal logs or persistent disk storage:
- **Environment Variables Only:** Provider API keys are configured strictly via environment variables (e.g. `OPENAI_API_KEY`).
- **HTTP Header Sanitization:** `Authorization`, `x-api-key`, and other sensitive headers are masked (`Bearer [REDACTED]`).
- **Automatic Regex Redaction:** Transcripts and summaries scrubbed through `redact_secrets()` before saving to `.termicode/`:
  - Bearer tokens
  - OpenAI keys (`sk-...`)
  - Google Gemini keys (`AIza...`)
  - Generic token patterns (`token-...`, `secret-...`, `key-...`)
  - URL query parameters (`?api_key=...`)

---

## 7. Dry-Run & Plan Modes

- **`--dry-run`:** Validates proposals, displays diff previews and proposed commands, but skips actual disk writes and subprocess executions.
- **`--plan` / `termicode plan`:** Forces the agent into a single-step, strictly read-only mode to produce an implementation architecture plan.

---

## 8. Known MVP Limitations & Honest Boundaries

> [!IMPORTANT]
> **What TermiCode AI Does NOT Guarantee:**
> 1. **No OS Kernel Sandbox:** TermiCode AI does **not** run inside a container (Docker), microVM, chroot jail, or seccomp sandbox. Permitted subprocesses (`pytest`, `python`, `git`) execute directly on your machine with your current user permissions.
> 2. **Network Access in Permitted Tools:** If you approve `pytest`, and your test suite initiates network calls, those network calls will execute. TermiCode does not isolate network sockets in MVP.
> 3. **Malicious Code in Approved Tests:** If an untrusted repository has malicious code inside a test file (e.g. `test_attack.py` containing `os.system(...)`), approving `pytest` will run that code.
> 4. **User Verification Required:** If the user approves a patch without reviewing it, TermiCode will apply it. Human-in-the-loop safety requires active human verification.
