# Skills Executor

A Python framework for executing [Agent Skills](https://agentskills.io/) (SKILL.md format) programmatically, with progressive disclosure, sandboxed script execution, and multi-provider LLM support.

## What This Solves

Agent Skills (SKILL.md files) are more than formatted system prompts when they include **bundled scripts, reference docs, and progressive disclosure**. This executor handles the full pattern:

| Feature | System Prompt Approach | This Executor |
|---------|----------------------|---------------|
| Skill instructions | Stuffed into system prompt | Loaded on-demand via tool call |
| Multiple skills | All in context at once (~tokens explode) | Only metadata in context; body loaded when needed |
| Bundled scripts | Not supported | Executed in sandboxed subprocess |
| Supporting docs | Not supported | Read on-demand by the LLM |
| File I/O | Not supported | Full read/write in sandbox workspace |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Agent Loop                        │
│                                                      │
│  1. System prompt includes skill CATALOG only        │
│     (name + description per skill, ~100 tokens each) │
│                                                      │
│  2. LLM calls load_skill → gets full SKILL.md body   │
│                                                      │
│  3. LLM calls read_skill_file → gets reference docs  │
│                                                      │
│  4. LLM calls execute_command → runs bundled scripts  │
│     in sandboxed container with skill files mounted   │
│                                                      │
│  5. Loop continues until LLM produces final response  │
└────────┬────────────────────────┬────────────────────┘
         │                        │
    ┌────▼────┐            ┌──────▼──────┐
    │   LLM   │            │   Sandbox   │
    │ Client  │            │(subprocess) │
    │         │            │             │
    ├─────────┤            ├─────────────┤
    │ Azure   │            │ /skill/  RO │
    │ OpenAI  │            │ /input/  RO │
    │ OpenAI  │            │ /workspace/ │
    │Anthropic│            │ /output/ RW │
    └─────────┘            └─────────────┘
```

### Progressive Disclosure (3 levels)

```
Level 0 — Always in context:
  "repo-analyzer: Analyze a code repository for structure and quality metrics."
  (~20 tokens per skill × N skills)

Level 1 — Loaded when LLM calls load_skill("repo-analyzer"):
  Full SKILL.md body with workflow, examples, best practices
  (~500-2000 tokens, loaded once per task)

Level 2 — Loaded when LLM calls read_skill_file("repo-analyzer", "patterns.md"):
  Supporting reference docs, script source, templates
  (variable size, loaded only if needed)
```

## Project Structure

```
skills_executor/
├── config.py           # Configuration (providers, sandbox, paths)
├── skill_registry.py   # Skill discovery and progressive disclosure
├── sandbox.py          # Sandboxed execution (subprocess)
├── tools.py            # Tool definitions and dispatcher
├── llm_client.py       # Multi-provider LLM client
├── agent.py            # Agent loop (single-shot and multi-turn)
├── main.py             # CLI entry point
├── server.py           # FastAPI service for deployment
├── Dockerfile          # Container image for Azure Container Apps
├── .dockerignore       # Excludes .venv, .azure, __pycache__, etc.
├── azure.yaml          # azd project manifest
├── requirements.txt
├── infra/              # Terraform infrastructure-as-code
│   ├── provider.tf     # azurerm + azurecaf providers
│   ├── variables.tf    # Input variables (env name, location, model)
│   ├── main.tf         # All Azure resources
│   ├── main.tfvars.json # Variable template for azd
│   └── outputs.tf      # Outputs (URLs, resource names)
└── skills/             # Skill directories
    └── repo-analyzer/
        ├── SKILL.md
        ├── patterns.md
        └── scripts/
            └── analyze.py
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure LLM provider

**Azure OpenAI (via AIServices — used in production):**
```bash
export LLM_PROVIDER=azure_openai
export AZURE_AI_PROJECT_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
export AZURE_DEPLOYMENT_NAME=gpt-oss-120b
export LLM_MODEL=gpt-oss-120b
export AZURE_API_VERSION=2024-12-01-preview
# Uses DefaultAzureCredential (Managed Identity on Azure, az login locally)
```

**Direct OpenAI:**
```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4.1
export OPENAI_API_KEY=sk-...
```

**Anthropic:**
```bash
export LLM_PROVIDER=anthropic
export LLM_MODEL=claude-sonnet-4-5-20250929
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Configure sandbox

```bash
# Subprocess (default) - runs commands as local processes
export SANDBOX_MODE=subprocess

# Dry run (no execution)
export SANDBOX_MODE=none
```

### 4. Add skills

Place skill directories in any configured skill path:

```bash
export SKILL_PATHS=./skills:~/.claude/skills

# Or install from skills.sh
npx skills add anthropics/skills
# → installs to .claude/skills/
```

## Usage

### CLI (one-shot)

```bash
# Simple task
python main.py "Analyze the repository structure"

# With input files
python main.py "Generate a PDF report from this data" --input data.csv

# Custom output directory
python main.py "Create a presentation" --output-dir ./results
```

### CLI (interactive)

```bash
python main.py --interactive
```

### Python library

```python
from agent import Agent
from config import ExecutorConfig

config = ExecutorConfig.from_env()
agent = Agent(config)

result = agent.run(
    task="Analyze this codebase and create a quality report",
    input_files={"src.zip": Path("./my-project.zip")},
    output_dir=Path("./reports"),
)

print(result.response)
print(f"Skills used: {result.skills_used}")
print(f"Output files: {result.output_files}")
```

### Multi-turn session

```python
from agent import MultiTurnAgent
from config import ExecutorConfig

agent = MultiTurnAgent(ExecutorConfig.from_env())
agent.start_session()

r1 = agent.send("What skills are available?")
print(r1.response)

r2 = agent.send("Use the repo-analyzer skill on the input files")
print(r2.response)

agent.end_session()
```

### REST API

```bash
# Start server
uvicorn server:app --host 0.0.0.0 --port 8000

# One-shot execution
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"task": "Analyze the Python project structure"}'

# With file upload
curl -X POST http://localhost:8000/run-with-files \
  -F "task=Generate a report from this CSV" \
  -F "files=@data.csv"

# Interactive session
SESSION=$(curl -s -X POST http://localhost:8000/sessions | jq -r .session_id)
curl -X POST "http://localhost:8000/sessions/$SESSION/send" \
  -H "Content-Type: application/json" \
  -d '{"message": "What skills do you have?"}'
```

## Azure Deployment

The project includes full Terraform infrastructure and an `azd` manifest for deploying to Azure Container Apps with Azure OpenAI (AIServices).

### What gets deployed

| Resource | Purpose |
|----------|---------|
| Resource Group | Container for all resources |
| User-Assigned Managed Identity | Passwordless auth (Container App → OpenAI) |
| Azure Container Registry | Stores the Docker image |
| Log Analytics Workspace | Container App logging |
| Container Apps Environment | Hosting environment |
| Container App | Runs the FastAPI server |
| Azure AI Services (OpenAI) | LLM endpoint (gpt-oss-120b) |
| Role Assignments | AcrPull + Cognitive Services OpenAI User |

### Deploy with azd

```bash
# Install azd (macOS)
brew install azure-dev

# Login and initialize
azd auth login
azd init
azd env new dev

# Provision infrastructure + deploy
azd up
```

### Deploy manually (Terraform + ACR)

```bash
# Provision infrastructure
cd infra
terraform init
terraform apply -var="environment_name=dev" -var="location=eastus2"

# Build image in the cloud (no local Docker needed)
cd ..
az acr build --registry <acr-name> --image skills-executor:latest .

# Update the container app
az containerapp update --name <app-name> --resource-group <rg-name> \
  --image <acr-name>.azurecr.io/skills-executor:latest
```

### Authentication

The deployed app uses **Managed Identity** for Azure OpenAI access — no API keys needed. The Terraform config creates a User-Assigned Managed Identity, assigns it the `Cognitive Services OpenAI User` role on the AI Services account, and attaches it to the Container App. The `AZURE_CLIENT_ID` env var tells `DefaultAzureCredential` which identity to use.

### Tear down

```bash
azd down --purge
# or
cd infra && terraform destroy -var="environment_name=dev" -var="location=eastus2"
```

## Writing Skills

### Minimal skill

```
my-skill/
└── SKILL.md
```

```markdown
---
name: my-skill
description: Short description of when to use this skill (max 1024 chars).
---

# My Skill

Instructions for the LLM...
```

### Skill with scripts

```
my-skill/
├── SKILL.md
├── reference.md          # Loaded on demand
└── scripts/
    └── process.py        # Executed in sandbox at /skill/scripts/process.py
```

The SKILL.md should tell the LLM to run the scripts:

```markdown
## Workflow

1. Run the processing script:
   \`\`\`bash
   python /skill/scripts/process.py /input/data.csv --output /output/result.json
   \`\`\`
2. Review the output and summarize findings.
```

### Key principles

- **Description is critical**: It's the only thing the LLM sees by default. Make it specific about when to trigger.
- **Instructions guide, scripts execute**: The LLM decides *when* to run scripts based on instructions. Scripts produce deterministic output.
- **Reference docs are optional**: Link them in SKILL.md with `[see reference](reference.md)`. The LLM loads them only if needed.
- **Output to /output/**: Files written here are collected and returned to the caller.

## How It Compares

| | Claude API Skills | OpenAI Shell Tool | This Executor |
|---|---|---|---|
| Runtime | Anthropic-hosted container | OpenAI-hosted container | Your infrastructure |
| LLM | Claude only | GPT only | Any (Azure OpenAI, OpenAI, Anthropic) |
| Skill format | SKILL.md | SKILL.md | SKILL.md |
| Progressive disclosure | Built-in | Built-in | Implemented here |
| Sandbox | Managed | Managed | Subprocess |
| Deployment | API calls | API calls | Azure Container Apps (Terraform + azd) |
| Control | Limited | Limited | Full (auth, networking, observability) |

## License

MIT
