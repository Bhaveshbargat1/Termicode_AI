# TermiCode AI & ToolBridge Integration Interface Guide

> **Document Version:** 1.0.0 (Phase 1 Contract Specification)  
> **Target Audience:** Systems Architects, Core Developers, and Integration Engineers  
> **Status:** Official Specification  

---

## 1. Executive Overview

This document specifies the technical interface, communication protocols, authentication rules, and data payloads governing the integration between:
1. **TermiCode AI:** A deterministic, bounded terminal coding assistant operating on single-agent workflow profiles (`generate`, `debug`, `understand`, `tests`, `review`, `refactor`).
2. **ToolBridge:** An enterprise Model Context Protocol (MCP) gateway that connects upstream servers (PostgreSQL, GitHub, sandboxed Filesystem) and multiplexes their tools to downstream AI applications.

### Architectural Principle: Decoupled Process Boundary
- **Independent Execution:** TermiCode AI (Python 3.11+) and ToolBridge (Python 3.12+) run as separate processes in their respective virtual environments.
- **Zero Monolithic Merging:** Neither project imports internal modules from the other. All interaction occurs over standard network protocols (HTTP REST) or open protocol streams (MCP stdio).
- **Separation of Authority:** TermiCode AI retains exclusive authority over the user's active codebase directory with its interactive diff previews and approval gates. ToolBridge acts as an external tool broker for infrastructure (databases, GitHub, external MCP servers).

---

## 2. Interaction Mechanisms: REST API vs MCP Server

ToolBridge provides two distinct communication interfaces. TermiCode AI interacts with ToolBridge primarily via the **FastAPI REST API**, with optional support for the **MCP `stdio` stream**.

```
┌────────────────────────────────────────────────────────┐
│                      TermiCode AI                      │
│    (CLI · AgentRuntime · PolicyEngine · ToolRegistry)  │
└──────────────────────────┬─────────────────────────────┘
                           │
       ┌───────────────────┴───────────────────┐
       │ (Option A: Primary REST API)          │ (Option B: Downstream MCP Server)
       │ HTTP GET/POST (JSON)                  │ stdio Child Process (JSON-RPC)
       │ Port 8000 + X-API-Key                 │ Standard I/O Pipes
       ▼                                       ▼
┌────────────────────────────────────────────────────────┐
│                   ToolBridge Gateway                   │
│   (FastAPI REST :8000  ·  mcp.server.lowlevel: stdio)  │
│   (Rate Limiter · RBAC · Sanitizer · SQLite Audit DB)  │
└──────────────────────────┬─────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  PostgreSQL  │    │    GitHub    │    │  Filesystem  │
│  (Read-Only) │    │  (REST API)  │    │  (Sandboxed) │
└──────────────┘    └──────────────┘    └──────────────┘
```

---

## 3. Option A: FastAPI REST API (Primary Integration)

ToolBridge exposes a production-ready, asynchronous REST API powered by FastAPI and Uvicorn. This is the recommended interface for TermiCode AI because it requires zero modifications to ToolBridge and adds zero external dependencies to TermiCode AI (callable via Python's standard library `urllib.request`).

### 3.1 Service Host and Port
- **Default Base URL:** `http://127.0.0.1:8000`
- **Host Binding:** Restricted to loopback (`127.0.0.1`) for local developer security.
- **Config Variable:** `TERMICODE_TOOLBRIDGE_API_URL` (in TermiCode) / `TOOLBRIDGE_API_PORT` (in ToolBridge).

### 3.2 Authentication & Authorization (RBAC)
Every authenticated request to ToolBridge must supply an API key via the HTTP header:
```http
X-API-Key: <your-32-char-cryptographic-key>
```

#### Security Constraints
1. **Length Requirement:** All API keys must be at least 32 characters in length.
2. **Anti-Placeholder Validation:** ToolBridge automatically rejects placeholder keys (e.g. `changeme`, `tb-admin-key-999`, `your-secret-key-here`).
3. **Role Mapping:**
   - **`ADMIN` (`TOOLBRIDGE_ADMIN_KEY`):** Full access to add/remove servers, configure policies, and inspect audit logs. *(Never shared with TermiCode).*
   - **`OPERATOR` (`TOOLBRIDGE_OPERATOR_KEY`):** Permitted to list tools and invoke `SAFE`, `STANDARD`, and `RESTRICTED` tools. *(Recommended for TermiCode `generate`, `refactor`, `debug`).*
   - **`READONLY` (`TOOLBRIDGE_READONLY_KEY`):** Permitted to list tools and invoke `SAFE` and `STANDARD` read-only tools. *(Recommended for TermiCode `understand`, `review`).*

### 3.3 Rate Limiting
- ToolBridge enforces a sliding-window rate limit (default: **60 requests/minute** per caller).
- Exceeding the rate limit returns an **HTTP 429 Too Many Requests** response with a `Retry-After: <seconds>` header.

---

## 4. REST API Endpoint Reference

### 4.1 Health Check (`GET /health`)
Verifies service uptime, database connectivity, and connected upstream servers.

- **URL:** `GET /health` or `GET /health/ready`
- **Authentication:** None required (public health probe).
- **Request Headers:** None.
- **Success Response (HTTP 200 OK):**
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "uptime_seconds": 1245.8,
  "database": "connected",
  "active_servers": 3
}
```

---

### 4.2 List Available Tools (`GET /api/v1/tools`)
Dynamically queries all namespaced tools exposed by connected upstream servers.

- **URL:** `GET /api/v1/tools`
- **Authentication:** `X-API-Key` required (Role: `READONLY`, `OPERATOR`, or `ADMIN`).
- **Request Headers:**
  ```http
  Accept: application/json
  X-API-Key: tb_LfSd-M_LiaDv8TqkYM09YjWOgCkbO7hFJpoYckyf2nU
  ```
- **Success Response (HTTP 200 OK):**
```json
{
  "total": 9,
  "tools": [
    {
      "namespaced_name": "postgres__execute_query",
      "original_name": "execute_query",
      "server_id": "postgres",
      "description": "Execute an approved read-only parameterized SQL query (SELECT, WITH, EXPLAIN only).",
      "sensitivity_tier": "restricted",
      "input_schema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Parameterized SQL query using placeholders ($1, $2)."
          },
          "parameters": {
            "type": "array",
            "description": "List of parameter values.",
            "items": {},
            "default": []
          }
        },
        "required": ["query"]
      }
    },
    {
      "namespaced_name": "github__list_issues",
      "original_name": "list_issues",
      "server_id": "github",
      "description": "List issues for a repository with state filtering (open, closed, all).",
      "sensitivity_tier": "safe",
      "input_schema": {
        "type": "object",
        "properties": {
          "owner": { "type": "string" },
          "repo": { "type": "string" },
          "state": { "type": "string", "enum": ["open", "closed", "all"], "default": "open" }
        },
        "required": ["owner", "repo"]
      }
    }
  ]
}
```

---

### 4.3 Invoke Tool (`POST /api/v1/tools/invoke`)
Executes an upstream tool through ToolBridge with pre-flight schema validation, policy checks, rate limiting, and audit logging.

- **URL:** `POST /api/v1/tools/invoke`
- **Authentication:** `X-API-Key` required (Role: `READONLY` or higher for safe tools, `OPERATOR` for restricted tools).
- **Request Headers:**
  ```http
  Content-Type: application/json
  Accept: application/json
  X-API-Key: tb_dC9QQ7j_VY2aAxyqlIthznCS_6-OckoYtCrCIxECBlQ
  ```

#### Request Payload Structure
```json
{
  "tool_name": "postgres__execute_query",
  "arguments": {
    "query": "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema = $1",
    "parameters": ["public"]
  }
}
```

#### Success Response (HTTP 200 OK)
```json
{
  "status": "success",
  "tool_name": "postgres__execute_query",
  "duration_ms": 14.82,
  "result": "[{\"table_name\": \"users\", \"column_name\": \"email\", \"data_type\": \"character varying\"}]"
}
```

#### Error Responses
- **HTTP 400 Bad Request:** Payload failed pre-flight JSON Schema validation or upstream returned an execution error.
  ```json
  {
    "detail": "Input validation error: 'query' is a required property."
  }
  ```
- **HTTP 403 Forbidden:** The caller's role does not permit executing this tool according to PolicyEngine rules.
  ```json
  {
    "detail": "Role 'readonly' is not authorized to execute restricted tool 'postgres__execute_query'."
  }
  ```
- **HTTP 404 Not Found:** Tool name does not exist in the active registry.
  ```json
  {
    "detail": "Tool 'postgres__unknown_action' not found."
  }
  ```
- **HTTP 429 Too Many Requests:** Caller exceeded sliding-window rate limit.
  ```json
  {
    "detail": "Rate limit exceeded. Maximum 60 requests per minute.",
    "retry_after_seconds": 12.4
  }
  ```

---

## 5. Option B: Downstream MCP Server (`stdio`)

ToolBridge can also be spawned as a child process by TermiCode AI communicating over standard input and output (`stdio`) using JSON-RPC 2.0 frames per the official Model Context Protocol specification.

### 5.1 Subprocess Launch Parameters
- **Command:** `toolbridge` (or `<path-to-toolbridge-venv>/python -m toolbridge.server.gateway`)
- **Transport:** Standard I/O (`stdin` / `stdout`).
- **Environment Variables:**
  - `TOOLBRIDGE_STDIO_ROLE=operator` (Sets default downstream client role: `readonly`, `operator`, or `admin`).
  - Standard output is strictly reserved for JSON-RPC 2.0 protocol frames. All diagnostic logging is routed to `stderr`.

### 5.2 JSON-RPC MCP Handshake Lifecycle
1. **Initialize Request:**
   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "initialize",
     "params": {
       "protocolVersion": "2024-11-05",
       "capabilities": {},
       "clientInfo": { "name": "TermiCode-AI", "version": "0.1.0" }
     }
   }
   ```
2. **List Tools Request:**
   ```json
   {
     "jsonrpc": "2.0",
     "id": 2,
     "method": "tools/list",
     "params": {}
   }
   ```
3. **Call Tool Request:**
   ```json
   {
     "jsonrpc": "2.0",
     "id": 3,
     "method": "tools/call",
     "params": {
       "name": "postgres__describe_table",
       "arguments": { "table_name": "users" }
     }
   }
   ```

---

## 6. Sensitivity Tier to Risk Level Mapping

TermiCode AI evaluates all tools through its deterministic `PolicyEngine`. When external tools are imported from ToolBridge, their `sensitivity_tier` maps deterministically to TermiCode's `RiskLevel`:

| ToolBridge Sensitivity Tier | TermiCode AI Risk Level | TermiCode Policy Behavior |
| :--- | :--- | :--- |
| **`SAFE`** (e.g. `github__get_repository`, `postgres__list_tables`) | `RiskLevel.READ` | Permitted automatically in all workflows (`understand`, `review`, etc.). |
| **`STANDARD`** (e.g. `github__list_issues`, `postgres__describe_table`) | `RiskLevel.READ` | Permitted automatically across all workflows. |
| **`RESTRICTED`** (e.g. `postgres__execute_query`, `filesystem__read_file`) | `RiskLevel.WRITE` | Prohibited in `understand`/`review`. Requires human approval prompt in `generate`/`debug`. |
| **`CRITICAL`** (e.g. `filesystem__write_file`, `filesystem__delete_file`) | `RiskLevel.EXECUTE` | Strict approval gate with argument and preview confirmation. |

---

## 7. Concrete Code Examples

### 7.1 cURL Examples

#### 1. Verify ToolBridge Health
```bash
curl -X GET "http://127.0.0.1:8000/health"
```

#### 2. Query Exposed MCP Tools
```bash
curl -X GET "http://127.0.0.1:8000/api/v1/tools" \
  -H "X-API-Key: tb_LfSd-M_LiaDv8TqkYM09YjWOgCkbO7hFJpoYckyf2nU"
```

#### 3. Execute Read-Only SQL Query
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/tools/invoke" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: tb_dC9QQ7j_VY2aAxyqlIthznCS_6-OckoYtCrCIxECBlQ" \
  -d '{
    "tool_name": "postgres__execute_query",
    "arguments": {
      "query": "SELECT count(*) FROM information_schema.tables WHERE table_schema = $1",
      "parameters": ["public"]
    }
  }'
```

---

### 7.2 TermiCode AI Scaffolding Client (Python Standard Library)

TermiCode AI contains built-in client scaffolding in `termicode.tools.toolbridge`:

```python
from termicode.tools.toolbridge import ToolBridgeClientStub, ToolBridgeConfig

# 1. Initialize configuration with local credentials
config = ToolBridgeConfig(
    api_url="http://127.0.0.1:8000",
    api_key="tb_dC9QQ7j_VY2aAxyqlIthznCS_6-OckoYtCrCIxECBlQ",
    role="operator",
    timeout_seconds=30.0,
)
client = ToolBridgeClientStub(config)

# 2. Check service connectivity
health = client.check_health()
print(f"ToolBridge Status: {health.get('status')}")

# 3. Discover remote tools
tools = client.list_tools()
for tool in tools:
    print(f"Discovered: {tool['namespaced_name']} ({tool.get('sensitivity_tier')})")

# 4. Invoke a remote tool
result = client.invoke_tool(
    tool_name="github__list_issues",
    arguments={"owner": "octocat", "repo": "Hello-World", "state": "open"}
)
print(f"Result Output: {result.get('result')}")
```

---

## 8. Environment Setup & Launch Checklist

To run both systems together:

### Step 1: Start ToolBridge Management API
In your ToolBridge repository terminal:
```powershell
cd "d:\Personal Projects\Toolbridge - mcp gateway"
.\.venv\Scripts\Activate.ps1

# Run the FastAPI Management & Tool Execution API
toolbridge-api
# -> Uvicorn running on http://127.0.0.1:8000
```

### Step 2: Configure TermiCode AI
In your TermiCode AI repository:
1. Open `.env` (or copy `.env.example .env`).
2. Set:
   ```ini
   TERMICODE_TOOLBRIDGE_ENABLED=true
   TERMICODE_TOOLBRIDGE_API_URL=http://127.0.0.1:8000
   TERMICODE_TOOLBRIDGE_API_KEY="tb_dC9QQ7j_VY2aAxyqlIthznCS_6-OckoYtCrCIxECBlQ"
   TERMICODE_TOOLBRIDGE_ROLE=operator
   ```

### Step 3: Run TermiCode AI
```powershell
cd "d:\Personal Projects\Termicode AI"
.\.venv\Scripts\Activate.ps1

# Inspect TermiCode status and connectivity
termicode status
```
