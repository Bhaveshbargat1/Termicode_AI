# TermiCode AI — Threat Model & Security Boundaries

This document outlines the threat model, trust boundaries, attacker capabilities, mitigated vs. unmitigated risks, and honest architectural limitations of TermiCode AI.

---

## 1. System Overview & Trust Boundaries

TermiCode AI operates on the fundamental assumption that **the Large Language Model (LLM) is an untrusted planner**. The model may hallucinate, experience prompt injection (direct or indirect), or attempt destructive actions. 

Security and safety guarantees are therefore **not** delegated to model prompt instructions; they are deterministically enforced by Python boundaries in the runtime.

```text
       ┌─────────────────────────────────────────────────────────────┐
       │                       UNTRUSTED INPUT                       │
       │  Direct User Prompts  ·  Untrusted Repository Files / Diffs │
       └──────────────────────────────┬──────────────────────────────┘
                                      │
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │                   UNTRUSTED MODEL PLANNER                   │
       │      LLM Provider API (OpenAI-compatible / FakeProvider)    │
       │      Generates ProposedToolCall (Tool Name & Arguments)     │
       └──────────────────────────────┬──────────────────────────────┘
                                      │
   ═══════════════════════════════════╪═══════════════════════════════════
   TRUST BOUNDARY 1: Deterministic Policy & Tool Registry Validation
   ═══════════════════════════════════╪═══════════════════════════════════
                                      │
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │                  DETERMINISTIC AGENT RUNTIME                │
       │  • Step & repair counters                                   │
       │  • Workflow tool allowlists & risk profiles                 │
       │  • Tool argument Pydantic validation                        │
       └──────────────────────────────┬──────────────────────────────┘
                                      │
   ═══════════════════════════════════╪═══════════════════════════════════
   TRUST BOUNDARY 2: Workspace Boundary (resolve_safe) & Command Validator
   ═══════════════════════════════════╪═══════════════════════════════════
                                      │
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │                     APPROVAL CONTROLLER                     │
       │  • Evaluates risk level (READ vs WRITE vs EXECUTE)          │
       │  • Generates unified diff / command preview                 │
       │  • Queries user [y/N] (Default to DENY on invalid / piped)  │
       └──────────────────────────────┬──────────────────────────────┘
                                      │
   ═══════════════════════════════════╪═══════════════════════════════════
   TRUST BOUNDARY 3: Host Operating System & Subprocess Execution
   ═══════════════════════════════════╪═══════════════════════════════════
                                      │
                                      ▼
       ┌─────────────────────────────────────────────────────────────┐
       │                     LOCAL OPERATING SYSTEM                  │
       │  • Workspace Filesystem (read/write bounded)                │
       │  • Subprocesses: python, pytest, ruff, mypy, git            │
       │  • CWD locked to workspace; shell=False; timeouts & caps    │
       │  • Running with local user privileges (NO kernel sandbox)   │
       └─────────────────────────────────────────────────────────────┘
```

---

## 2. Threat Scenarios & Attacker Capabilities

### 2.1 Threat Actors & Vectors
1. **Malicious Repository Content (Indirect Prompt Injection):**
   An attacker commits malicious instructions inside repository files (e.g. `README.md`, docstrings, issue templates, test files). When TermiCode reads these files via `read_file` or `search_text`, the text enters the LLM's context window and attempts to hijack execution (e.g., instructing the agent to overwrite sensitive system files or run malicious commands).
2. **Adversarial / Compromised Model Outputs:**
   The model produces hallucinated or hostile tool arguments attempting path traversal (`../../`), arbitrary command execution, or infinite loops.
3. **Accidental User Instruction:**
   A legitimate user accidentally requests a broad or destructive operation (e.g. "delete all files", "format project").
4. **Environment Credential Leakage:**
   The model or subprocess outputs attempt to expose or commit local secrets (`.env`, SSH keys, cloud credentials, API tokens).

---

## 3. Mitigated Threats vs. Unmitigated Threats

| Threat Category | Specific Threat | Mitigation Mechanism | Status |
|---|---|---|---|
| **Path Traversal** | Accessing files outside the workspace via `../../` or absolute paths (`/etc/passwd`, `C:\Windows`) | `WorkspaceBoundary.resolve_safe()` canonicalizes paths via `resolve()` and verifies `is_relative_to(root)`. Rejects with `WorkspaceEscapeError`. | **MITIGATED** |
| **Credential Disclosure** | Reading or modifying `.env`, `.git`, SSH keys (`id_rsa`), AWS credentials, `.pem`, `.key` | `WorkspaceBoundary.is_sensitive()` blocks protected files and directories even if inside workspace. | **MITIGATED** |
| **Shell Injection** | Command chaining (`&&`, `||`, `;`), pipes (`|`), redirections (`>`, `<`), substitutions (`$()`, `` ` ``) | `subprocess.run(shell=False)` with argument arrays; `validate_command()` rejects metacharacters. | **MITIGATED** |
| **Destructive Commands** | System deletion (`rm`, `del`, `format`), privilege elevation (`sudo`, `runas`), shell spawns (`bash`, `pwsh`) | Executable allowlist: only `python`, `pytest`, `ruff`, `mypy`, `git`. Disallowed commands raise `DisallowedExecutableError`. | **MITIGATED** |
| **Unauthorized Git Mutations** | Remote data exfiltration or branch destruction via `git push`, `git reset`, `git checkout` | Git subcommand allowlist: only `status`, `diff`, `log`, `branch`, `show` permitted. `push` and `reset` strictly rejected. | **MITIGATED** |
| **Arbitrary Code Execution via Python** | Running inline scripts via `python -c "import os; ..."` or `python -m pip install` | `validate_command()` blocks `-c` and limits `-m` to `pytest`, `unittest`, `ruff`, `mypy`. | **MITIGATED** |
| **Unintended Modifications** | Silent disk writes or patch applications | Mutating tools (`write_file`, `apply_patch`, `run_command`) classified as `WRITE`/`EXECUTE`; require explicit human approval. | **MITIGATED** |
| **Runaway Execution Loops** | Model caught in an infinite loop consuming tokens or CPU | Hard step budget (8–12 per profile) and repair budget (max 3 consecutive malformed calls). | **MITIGATED** |
| **Subprocess Freezes / Floods** | Long-running subprocesses or command output flooding terminal/memory | Subprocess execution capped with 30s timeout and 64 KB stdout/stderr buffer truncation. | **MITIGATED** |
| **Token & Secret Leakage in Logs** | API keys or authorization headers written to disk | `redact_secrets()` sanitizes JSONL transcripts and summary JSON before writing to `.termicode/`. | **MITIGATED** |
| **Non-Interactive Bypasses** | Script or pipe auto-approving mutations | Defaults to **DENY** if `stdin` is not interactive or input is unrecognized. | **MITIGATED** |
| **Host OS Sandbox Isolation** | Containment via virtual machines, Docker containers, cgroups, or seccomp | **No OS-level sandbox is implemented in MVP.** Subprocesses run directly on host as the current user. | **UNMITIGATED (LIMITATION)** |
| **Network Access in Subprocesses** | Allowlisted tests or python commands calling external network endpoints | Subprocesses inherit host network access. No firewall or network namespace isolation in MVP. | **UNMITIGATED (LIMITATION)** |
| **Human Error on Approval** | User confirms a malicious patch or command without carefully inspecting the diff | If the user confirms (`y`), TermiCode faithfully applies the change or runs the command. | **UNMITIGATED (OPERATOR RISK)** |
| **Malicious Test Suite Code** | Running `pytest` on an untrusted test file containing malicious `os.system()` in `test_foo.py` | `pytest` executes Python code directly on host. If a repo has malicious tests, running them runs host code. | **UNMITIGATED (LIMITATION)** |

---

## 4. Honest Sandbox Assessment

> [!WARNING]
> **No Kernel-Level Sandbox Isolation**
> TermiCode AI does **NOT** provide a virtualized sandbox, Docker container, chroot jail, or OS-level security namespace (such as Linux cgroups, AppArmor, or seccomp). All permitted commands (`python`, `pytest`, `ruff`, `mypy`, `git`) execute as regular child processes of the current user.

### 4.1 What TermiCode AI Is
- An **application-level policy firewall** that prevents the LLM from requesting unauthorized binaries, invoking shell interpreters, traversing outside the designated repository, modifying files without showing a diff, or running without human confirmation.

### 4.2 What TermiCode AI Is NOT
- A container hypervisor or microVM (e.g. Firecracker, gVisor, Docker).
- An OS-level process boundary. A user who approves `pytest` on an untrusted repository is running code written by that repository author on their local machine.

### 4.3 Safe Usage Recommendations for Untrusted Repositories
1. **Use Read-Only Profiles for Assessment:** Always start with `termicode understand` or `termicode review`. These profiles restrict tools exclusively to `list_files`, `read_file`, and `search_text`—guaranteeing that no subprocesses or file writes can occur.
2. **Always Run in a Disposable Directory or Container for High-Risk Code:** When working with completely untrusted code from the internet, run TermiCode AI itself inside an isolated virtual machine or container.
3. **Inspect Approval Prompts Carefully:** Never type `y` to approve a file write or command execution without reviewing the target path, command arguments, and unified diff.
4. **Use `--dry-run` and `--plan`:** Preview the model's proposals before enabling interactive execution.
