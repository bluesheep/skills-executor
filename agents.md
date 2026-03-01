# Agent Architecture

## Overview

The Skills Executor implements an agentic loop where an LLM orchestrates skill-based task completion through tool calls. The LLM decides which skills to load, what scripts to run, and when the task is complete.

## Agent Loop

```
User task
  |
  v
Agent.run()
  |
  +-- Build system prompt (skill catalog injected)
  +-- Create sandbox session
  +-- Loop (max 30 turns):
  |     |
  |     +-- Send messages + tools to LLM
  |     +-- If no tool calls -> done, return response
  |     +-- For each tool call:
  |     |     +-- ToolDispatcher validates args
  |     |     +-- ToolDispatcher routes to handler
  |     |     +-- Result appended to conversation
  |     +-- Continue loop
  |
  +-- Collect output files from sandbox
  +-- Clean up sandbox
  +-- Return AgentResult
```

## Two Agent Modes

### Agent (one-shot)
- `agent.py:Agent` - stateless between invocations
- Each `run()` call gets a fresh sandbox and conversation
- Best for: CLI usage, API calls, batch processing

### MultiTurnAgent (interactive)
- `agent.py:MultiTurnAgent` - extends Agent, maintains state
- `start_session()` creates a persistent sandbox
- `send()` appends to the same conversation history
- `end_session()` cleans up
- Best for: interactive chat, iterative refinement

## Progressive Disclosure (3 levels)

The key design: the LLM sees minimal info upfront, loads more on demand.

**Level 0 - Catalog (always in system prompt)**
```
- repo-analyzer [analysis, code-quality]: Analyze a code repository...
```
~20 tokens per skill. Injected via `SkillRegistry.get_catalog()`.

**Level 1 - Full instructions (via `load_skill` tool call)**
The entire SKILL.md body plus a manifest of scripts and supporting files.
~500-2000 tokens. Loaded once per task via `ToolDispatcher._handle_load_skill()`.

**Level 2 - Supporting files (via `read_skill_file` tool call)**
Reference docs, templates, script source. Variable size.
Loaded only when the LLM decides it needs them.

## Tool Dispatch

`ToolDispatcher` in `tools.py` routes LLM tool calls:

| Tool | Handler | Purpose |
|------|---------|---------|
| `list_skills` | `SkillRegistry.list_skills()` | Metadata for all skills |
| `load_skill` | `_handle_load_skill()` | Full SKILL.md + mount in sandbox |
| `read_skill_file` | `_handle_read_skill_file()` | Supporting doc content |
| `execute_command` | `Sandbox.execute()` | Run shell command in sandbox |
| `write_file` | `Sandbox.write_file()` | Write to sandbox workspace |
| `read_file` | `Sandbox.read_file()` | Read from workspace/input/skill |
| `list_files` | `Sandbox.list_files()` | List sandbox directory contents |

Arguments are validated before dispatch (required fields, type checks, non-empty).

## Sandbox

The sandbox runs commands as local subprocesses. Absolute sandbox paths (`/skill/`, `/input/`, `/output/`, `/workspace/`) in SKILL.md instructions are rewritten to local temp directory paths via `_rewrite_sandbox_paths()`. Env vars are set for `SKILL_DIR`, `INPUT_DIR`, `OUTPUT_DIR`, `WORKSPACE_DIR`.

Session lifecycle: `create_session()` -> `execute()` (repeated) -> `collect_outputs()` -> `destroy()`.

`create_session()` is idempotent - calling it again for an existing session merges new parameters (e.g. mounting a skill dir) without discarding existing input files.

In the Azure Container Apps deployment, subprocess mode runs inside the container — the container itself provides isolation.

## LLM Client

`LLMClient` in `llm_client.py` normalizes three providers behind a single `chat()` method:

- **Azure OpenAI** - Uses `DefaultAzureCredential` with `get_bearer_token_provider` for auth, `AzureOpenAI` client. In production, authenticates via User-Assigned Managed Identity (set `AZURE_CLIENT_ID`). Locally, uses `az login` credentials.
- **OpenAI** - Direct API key auth, `OpenAI` client
- **Anthropic** - API key auth, `Anthropic` client

Key differences handled:
- Tool call format: OpenAI uses `function.arguments` (JSON string), Anthropic uses `tool_use.input` (dict)
- Tool results: OpenAI uses `role: "tool"` messages, Anthropic wraps in `role: "user"` with `tool_result` content blocks
- System prompt: OpenAI uses a system message in the messages array, Anthropic uses a separate `system` parameter

All responses normalized to `LLMResponse` with `text`, `tool_calls`, `stop_reason`, `usage`.

## Data Flow Example

User: "Analyze this codebase"

1. System prompt includes: `repo-analyzer: Analyze a code repository...`
2. LLM calls `load_skill("repo-analyzer")` -> gets full SKILL.md body
3. SKILL.md says: run `python /skill/scripts/analyze.py /input/ --output /output/report.json`
4. LLM calls `execute_command("python3 /skill/scripts/analyze.py /input/ --output /output/report.json")`
5. Sandbox rewrites paths, runs script, returns stdout/stderr
6. LLM reads the JSON output, writes a markdown summary
7. LLM calls `write_file` to save report to `/output/report.md`
8. LLM returns final response text
9. Agent collects files from sandbox output dir -> `AgentResult`
