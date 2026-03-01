# Skills Executor

A Python framework for executing Agent Skills (SKILL.md format) with progressive disclosure, sandboxed script execution, and multi-provider LLM support.

## Quick Start

```bash
source .venv/bin/activate
LLM_PROVIDER=anthropic \
ANTHROPIC_API_KEY=<key> \
SANDBOX_MODE=subprocess \
SKILL_PATHS=./skills \
python3 main.py "Your task here" --input file.txt
```

## Project Structure

All source files are in the project root (flat layout, no package directory):

| File | Purpose |
|------|---------|
| `config.py` | Configuration via env vars. `ExecutorConfig.from_env()` is the entry point. |
| `skill_registry.py` | Discovers SKILL.md files, provides 3-level progressive disclosure. |
| `sandbox.py` | Sandboxed execution via subprocess. |
| `tools.py` | Tool definitions (OpenAI function calling format) and `ToolDispatcher`. |
| `llm_client.py` | Unified LLM client: Azure OpenAI, OpenAI, Anthropic. Normalizes tool calling. |
| `agent.py` | Agent loop: `Agent` (one-shot) and `MultiTurnAgent` (interactive). |
| `main.py` | CLI entry point. |
| `server.py` | FastAPI REST API. |
| `Dockerfile` | Container image for Azure Container Apps deployment. |
| `azure.yaml` | azd project manifest (Terraform provider, containerapp host). |
| `infra/` | Terraform IaC: provider.tf, variables.tf, main.tf, outputs.tf, main.tfvars.json. |
| `.dockerignore` | Excludes .venv, .azure, .claude, __pycache__ from Docker builds. |

Skills live in `skills/<name>/` with a `SKILL.md`, optional `scripts/`, and optional reference docs.

## Architecture Patterns

**Progressive disclosure** - The LLM only sees skill names + descriptions in the system prompt. Full instructions are loaded on demand via `load_skill` tool call. Supporting files loaded via `read_skill_file`. This keeps token usage low with many skills.

**Provider-neutral message format** - Internal messages use a common dict format. `LLMClient` adapts to/from each provider's API (OpenAI tool_calls vs Anthropic content blocks).

**Sandbox path convention** - SKILL.md instructions reference absolute paths: `/skill/`, `/input/`, `/output/`, `/workspace/`. Subprocess mode rewrites them to local paths via `_rewrite_sandbox_paths()`.

**Azure deployment** - Deployed as an Azure Container App via Terraform + azd. Uses a User-Assigned Managed Identity for passwordless auth to Azure AI Services (OpenAI). The `AZURE_CLIENT_ID` env var tells `DefaultAzureCredential` which identity to use. Images are built in the cloud via `az acr build` (no local Docker needed).

## Environment Variables

### Required (provider-dependent)
- `LLM_PROVIDER` - `azure_openai`, `openai`, or `anthropic`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `AZURE_AI_PROJECT_ENDPOINT` - per provider

### Azure OpenAI specific
- `AZURE_AI_PROJECT_ENDPOINT` - AI Services endpoint (e.g. `https://xxx.cognitiveservices.azure.com/`)
- `AZURE_DEPLOYMENT_NAME` - model deployment name (e.g. `gpt-oss-120b`)
- `AZURE_API_VERSION` - API version (default: `2024-12-01-preview`)
- `AZURE_CLIENT_ID` - Managed Identity client ID (set automatically in Azure deployment)

### Optional
- `LLM_MODEL` - model name (default: `gpt-4.1`)
- `SANDBOX_MODE` - `subprocess` or `none` (default: `subprocess`)
- `SKILL_PATHS` - colon-separated skill directories (default: `.claude/skills:.agents/skills`)
- `MAX_TURNS` - agent loop limit (default: `30`)

## Dev Conventions

- Python 3.13+, no package manager beyond pip
- All config flows through `ExecutorConfig.from_env()` - validated on load
- Tool definitions in `TOOL_DEFINITIONS` list (OpenAI format), converted for Anthropic via `tools_to_anthropic_format()`
- `ToolDispatcher` validates arguments before dispatching
- `Sandbox.create_session()` merges into existing sessions (won't discard input files when mounting a skill)
- Session IDs are full UUIDs
- No tests yet - run live tests with `SANDBOX_MODE=subprocess`

## Running the API Server

```bash
# Local development
source .venv/bin/activate
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=<key> SANDBOX_MODE=subprocess SKILL_PATHS=./skills \
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints: `POST /run`, `POST /run-with-files`, `GET /skills`, `POST /sessions`, `GET /health`

## Azure Deployment

Infrastructure is managed with Terraform (`infra/`) and orchestrated by azd (`azure.yaml`).

```bash
# Deploy everything (infrastructure + container image)
azd up

# Rebuild and redeploy container only
az acr build --registry <acr-name> --image skills-executor:latest .
az containerapp update --name <app-name> --resource-group <rg-name> \
  --image <acr-name>.azurecr.io/skills-executor:latest

# Tear down
azd down --purge
```

Terraform resources: Resource Group, Managed Identity, ACR, Log Analytics, Container Apps Environment, Container App, AI Services (OpenAI), model deployment, role assignments (AcrPull + Cognitive Services OpenAI User).

## Testing

### Local (CLI)

```bash
source .venv/bin/activate
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=<key> SANDBOX_MODE=subprocess SKILL_PATHS=./skills \
python3 main.py "list available skills"
```

### Local (API server)

```bash
# Start server
source .venv/bin/activate
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=<key> SANDBOX_MODE=subprocess SKILL_PATHS=./skills \
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# In another terminal:

# Health check — should return {"status": "ok", "provider": "...", "model": "...", "skills_count": 1}
curl http://localhost:8000/health

# List skills — should return the repo-analyzer skill
curl http://localhost:8000/skills

# Run a task
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"task": "list available skills"}'

# Run with file upload (tests python-multipart + sandbox)
curl -X POST http://localhost:8000/run-with-files \
  -F 'task=Analyze this code using the repo-analyzer skill' \
  -F 'files=@server.py' \
  -F 'files=@config.py'
```

### Azure (deployed)

```bash
# Get the app URL from Terraform outputs
cd infra && terraform output API_BASE_URL

# Or from azd
azd env get-values | grep API_BASE_URL

# Health check — should return {"status": "ok", "provider": "azure_openai", "model": "gpt-oss-120b", "skills_count": 1}
curl https://<app-fqdn>/health

# List skills
curl https://<app-fqdn>/skills

# Run a task (tests LLM via Managed Identity)
curl -X POST https://<app-fqdn>/run \
  -H "Content-Type: application/json" \
  -d '{"task": "list available skills"}'

# Run with files (tests full pipeline: upload, skill loading, script execution, LLM)
curl -X POST https://<app-fqdn>/run-with-files \
  -F 'task=Analyze this code using the repo-analyzer skill' \
  -F 'files=@server.py' \
  -F 'files=@config.py'

# Check container logs if something fails
az containerapp logs show --name <app-name> --resource-group <rg-name> --type console --tail 50
az containerapp logs show --name <app-name> --resource-group <rg-name> --type system --tail 30
```

### What to verify

- `/health` returns `status: ok` with correct provider and model
- `/skills` returns at least `repo-analyzer`
- `/run` with a simple task gets an LLM response (confirms auth works)
- `/run-with-files` with source files produces `output_files` (confirms sandbox + skill execution work)
- On Azure: no 429 rate limit errors (check quota with `az cognitiveservices usage list -l <region>`)
