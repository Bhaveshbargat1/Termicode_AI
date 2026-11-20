# TermiCode AI — Known Limitations & Operational Boundaries

> [!NOTE]
> Transparency is a foundational value of TermiCode AI. This document candidly details the operational boundaries, non-goals, and current technical limitations of the v1.0.0 release.

---

## 1. Operating System Sandboxing vs. Application Boundaries

- **No Native Kernel Isolation:**
  When executed directly on a host OS (Windows, macOS, Linux), TermiCode AI enforces application-level policy checks and human approval gates. It does **not** employ Linux namespaces, cgroups, seccomp filters, or virtual machine virtualization.
- **Process Privilege:**
  Allowlisted subprocesses run under the exact permissions and privileges of the executing operating system user.
- **Production Recommendation:**
  For multi-tenant environments or evaluation of untrusted public codebases, always execute TermiCode AI within an isolated container (see `Dockerfile` and `docker-compose.yml`).

---

## 2. Command Allowlist & Execution Scope

- **Curated Binaries Only:**
  Only safe, allowlisted developer executables are supported:
  - `python` (scripts, modules, interactive checks)
  - `pytest` (test discovery, test execution)
  - `ruff` (linting, formatting verification)
  - `mypy` (static type analysis)
  - `git` (status, diff, branch, checkout, stash, commit)
- **Arbitrary Scripting:**
  Commands such as `curl`, `wget`, `pip install`, `bash -c`, `powershell`, and raw shell pipelines (`|`, `;`, `&&`) are deterministically blocked by security policy. Any dependency installation or system reconfiguration must be performed manually by the user outside the agent loop.

---

## 3. Version Control & Checkpoint Requirements

- **Git Dependency for Rollbacks:**
  Automatic checkpoints (`git stash push -m "TermiCode checkpoint"`) and rollback operations require the workspace to be an active Git repository.
- **Uninitialized Workspaces:**
  If the workspace is not a Git repository, checkpointing will be skipped with a non-fatal warning, and rollbacks will be unavailable until `termicode init` or `git init` is executed.

---

## 4. LLM & Reasoning Guardrails

- **Semantic Correctness:**
  While TermiCode deterministically validates action structure, file paths, and argument schemas, it cannot guarantee that code authored by an LLM is bug-free or optimal. Always verify generated code with unit tests (`pytest`) before committing.
- **Token Windows & Memory Condensation:**
  Conversation history is capped at `max_memory_messages` (default: 20 turns). Beyond this limit, older turns are condensed into text summaries. Subtle historical details across hundreds of conversational turns may experience loss of specificity.

---

## 5. Single Workspace Confinement

- **Monorepo / Single Root Scope:**
  TermiCode is strictly anchored to a single approved workspace root per session (`--workspace <path>`). It cannot simultaneously edit or read files across distinct disjoint directory trees without switching active workspaces.
