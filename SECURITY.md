# TermiCode AI Security Policy

## Security Architecture & Philosophy

TermiCode AI is designed from the ground up around **Deterministic Authority Separation**. In our design philosophy:
- The **Large Language Model (LLM)** is treated as an untrusted reasoning planner that proposes candidate actions.
- The **Deterministic Python Safety Boundary** acts as the authoritative gatekeeper, validating every proposed action against strict policy constraints before execution.
- The **Human User** holds ultimate supervisory authority for any state-modifying action.

---

## 7-Layer Defense-in-Depth Model

TermiCode AI implements seven distinct security layers to prevent unintended harm or data leakage:

```
[ User Instruction ]
         │
         ▼
[ 1. LLM Planner (Untrusted Action Proposal) ]
         │
         ▼
[ 2. Workspace Boundary Containment (No Path Traversal ../) ]
         │
         ▼
[ 3. Credential & Sensitive File Shield (.env, .git, keys blocked) ]
         │
         ▼
[ 4. Secret Redaction Engine (API keys & Bearer tokens scrubbed) ]
         │
         ▼
[ 5. Human-in-the-Loop Approval Gate (Diff previews, fail-closed) ]
         │
         ▼
[ 6. Command Allowlist & Subprocess Policy (shell=False, no pipes) ]
         │
         ▼
[ 7. Resource Bounding & Audit Trail (Timeouts, output caps, logs) ]
```

### 1. Workspace Boundary Containment
- All file operations are verified using `WorkspaceBoundary.resolve_safe()`.
- Directory traversal attempts (`../`, `..\`, absolute system paths like `/etc/shadow` or `C:\Windows\System32`) trigger immediate `WorkspaceEscapeError`.
- Symlink resolution strictly prohibits escaping the approved workspace root.

### 2. Credential & Sensitive File Shield
- TermiCode strictly prohibits reading, writing, or inspecting sensitive project files, including:
  - Environment variable files: `.env`, `.env.*` (except `.env.example`)
  - Cryptographic keys: `id_rsa`, `id_ed25519`, `*.pem`, `*.key`
  - Version control internals: `.git/`
  - Cloud credentials: `.aws/`, `.ssh/`, `.gnupg/`
- Direct attempts raise `SensitivePathError`.

### 3. Secret Redaction Engine
- Multi-engine regex filters automatically scrub credentials before storing in session databases, printing in terminals, or writing to log files:
  - OpenAI API keys (`sk-...`)
  - Google Gemini API keys (`AIza...`)
  - Bearer tokens in headers and transcripts (`Bearer ...`)
  - Custom environment secrets registered in settings.

### 4. Human-in-the-Loop Approval Engine
- Write, patch, and execute operations require interactive user confirmation.
- The terminal displays unified colored diff previews before applying modifications.
- Piped non-interactive stdin or unparseable responses fail closed (**DENY**).

### 5. Deterministic Command Allowlist (`shell=False`)
- Autonomous arbitrary shell execution is strictly disallowed.
- Only vetted developer utilities can run (`python`, `pytest`, `ruff`, `mypy`, `git`).
- Shell metacharacters (`|`, `;`, `&&`, `||`, `$()`, backticks) are rejected by argument validators.
- Subprocesses run with `shell=False` to eliminate shell injection vulnerabilities.

### 6. Execution & Resource Bounding
- Subprocesses enforce a strict 30-second execution timeout.
- Command outputs are capped at 64 KB (and 1 MB hard ceiling) to prevent memory exhaustion.
- Agent loops are constrained by strict step budgets (8–12 steps) and repair counters (max 3 repairs).

### 7. Auditing & Technical Observability
- All operations, step decisions, and policy results are logged to workspace audit trails.
- Technical stack traces are captured in `.termicode/logs/debug.log` while presenting clean, non-leaking error summaries to users.

---

## Sandbox Reality & Honest Boundaries

> [!IMPORTANT]
> **Container vs. Application Policy:**
> TermiCode AI enforces application-level policy boundaries and human approval gates. It does **not** provide OS kernel virtualization, cgroups, or seccomp isolation without containerization. Approved commands run as child processes under the permissions of your local operating system user.
> 
> For untrusted code execution or multi-tenant hosting, always run TermiCode AI inside a containerized sandbox (such as the provided `Dockerfile` or `docker-compose.yml`).

---

## Vulnerability Reporting

If you discover a security vulnerability in TermiCode AI:
1. **Do not open a public issue.**
2. Send a detailed report to `security@termicode.local` (or file a private GitHub Advisory).
3. Include reproduction steps, environment details, and attack impact.
4. The security team will acknowledge receipt within 24 hours and provide a remediation timeline.
