# Running Local LLMs with TermiCode AI

This guide explains how to run TermiCode AI completely offline using local open-source language models via **Ollama**, **LM Studio**, or **vLLM**.

---

## 1. Why Local Models Matter

In enterprise, defense, fintech, and healthcare environments, strict data privacy regulations (e.g. GDPR, HIPAA, SOC 2) prohibit developers from transmitting proprietary source code, internal schemas, and credentials to cloud LLM APIs.

TermiCode AI solves this with **first-class Local LLM integration**:
- **Zero Data Exfiltration:** Source code never leaves your local machine.
- **Zero API Billing:** Free and unlimited usage.
- **Air-Gapped Operation:** Functions without internet connectivity.
- **Enterprise-Grade Compliance:** Matches corporate zero-trust security postures.

---

## 2. Recommended Open-Weights Models

For agentic coding (generating unified diffs, reading files, and function calling), model quality varies significantly. We recommend the following tested models:

| Model | Parameter Size | Minimum RAM / VRAM | Strengths | Ollama Command |
| :--- | :--- | :--- | :--- | :--- |
| **Qwen 2.5 Coder 7B** *(Default)* | 7 Billion | 8 GB RAM / 6 GB VRAM | **#1 Overall**: Exceptional tool-calling precision, compact diff formatting, fast inference. | `ollama pull qwen2.5-coder:7b` |
| **Qwen 2.5 Coder 14B** | 14 Billion | 16 GB RAM / 10 GB VRAM | High reasoning capacity: Better at complex multi-file debugging. | `ollama pull qwen2.5-coder:14b` |
| **DeepSeek-Coder-V2-Lite** | 16 Billion | 16 GB RAM / 10 GB VRAM | Strong algorithmic understanding across Python and TypeScript. | `ollama pull deepseek-coder-v2:16b` |
| **Llama 3.1 8B-Instruct** | 8 Billion | 8 GB RAM / 6 GB VRAM | Robust instruction adherence and conversational clarity. | `ollama pull llama3.1:8b` |

---

## 3. Quickstart Guide (3 Steps)

### Step 1: Install and Start Ollama
1. Download Ollama from [ollama.com](https://ollama.com).
2. Start the Ollama background daemon or desktop application.

### Step 2: Download the Recommended Model
In your terminal, pull the model:
```bash
ollama pull qwen2.5-coder:7b
```

### Step 3: Configure TermiCode AI
In your project directory, set `TERMICODE_PROVIDER=ollama` in your `.env` or PowerShell:

```powershell
$env:TERMICODE_PROVIDER="ollama"
$env:TERMICODE_MODEL="qwen2.5-coder:7b"
```

Verify your connection:
```powershell
.\.venv\Scripts\termicode status
```
The status panel will report:
```text
Provider: ollama (Local Offline)
Model: qwen2.5-coder:7b
Endpoint: http://localhost:11434/v1
Provider Status: Connected
```

---

## 4. Advanced: Expanding Context Window to 16,384 Tokens

By default, Ollama initializes models with a **2,048 token** context window, which can truncate large code files. For agentic workflows, expanding the context window to **16,384 tokens** is strongly recommended.

### Creating a Custom 16k Modelfile
1. Create a text file named `Modelfile`:
   ```dockerfile
   FROM qwen2.5-coder:7b
   PARAMETER num_ctx 16384
   PARAMETER temperature 0.1
   ```

2. Build the optimized model in Ollama:
   ```bash
   ollama create qwen-coder-16k -f Modelfile
   ```

3. Update your `.env`:
   ```env
   TERMICODE_PROVIDER=ollama
   TERMICODE_MODEL=qwen-coder-16k
   ```

---

## 5. Troubleshooting & FAQ

### Issue: "Local Ollama server is not reachable at 'http://localhost:11434/v1'"
- **Cause:** Ollama is not running in the background.
- **Fix:** Launch the Ollama desktop app or run `ollama serve` in a terminal window.

### Issue: Agent responses take a long time on CPU
- **Cause:** System is running on CPU without GPU hardware acceleration.
- **Fix:**
  - Increase the HTTP timeout in `.env`: `TERMICODE_PROVIDER_TIMEOUT=180.0`.
  - Use smaller 4-bit quantized models: `ollama pull qwen2.5-coder:7b-instruct-q4_K_M`.
  - If you have an NVIDIA GPU, ensure CUDA drivers are up to date so Ollama automatically offloads layers to VRAM.

---

## 6. Interview Talking Points

When discussing this architecture in technical interviews:

1. **Decoupled Provider Architecture:**  
   *"TermiCode AI uses a protocol-driven provider layer. By implementing the OpenAI wire specification, the agent seamlessly toggles between cloud APIs (GPT-4o) and local inference servers (Ollama, vLLM) with zero code modifications."*

2. **Zero-Trust Enterprise Positioning:**  
   *"In security-sensitive environments, code exfiltration is a critical barrier to AI adoption. TermiCode's local-first architecture ensures that AST parsing, code review, and feature synthesis run within the client's firewall."*

3. **Inference Latency vs. Cost Trade-offs:**  
   *"While cloud models offer higher raw FLOPs, local quantized models (e.g. Qwen 2.5 Coder 7B at Q4_K_M) achieve ~40 tokens/sec on modern consumer GPUs with zero marginal cost per token and zero rate limits."*
