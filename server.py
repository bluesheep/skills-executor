"""
FastAPI service for the Skills Executor.

Deploy on Azure Container Apps, App Service, or AKS.

    uvicorn server:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /run          - Execute a task (one-shot)
    GET  /skills       - List available skills
    POST /sessions     - Create an interactive session
    POST /sessions/{id}/send - Send a message to a session
    DELETE /sessions/{id}    - End a session
"""

from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import secrets
import tempfile
import time
import uuid
import logging

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from config import ExecutorConfig
from agent import Agent, MultiTurnAgent

logger = logging.getLogger(__name__)

# ─── Global state ────────────────────────────────────────────────────────────

config = ExecutorConfig.from_env()
agent: Agent | None = None
sessions: dict[str, dict] = {}  # session_id -> {"agent": MultiTurnAgent, "created_at": float}
_cleanup_task: asyncio.Task | None = None


# ─── Authentication ──────────────────────────────────────────────────────────

security = HTTPBearer()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Validate Bearer token against configured API_KEY."""
    if not config.api_key:
        raise HTTPException(
            status_code=500,
            detail="Server misconfigured: API_KEY environment variable is not set.",
        )
    if not secrets.compare_digest(credentials.credentials, config.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return credentials.credentials


# ─── Session cleanup ─────────────────────────────────────────────────────────

async def _session_cleanup_loop():
    """Background task that expires stale sessions."""
    while True:
        await asyncio.sleep(60)  # check every minute
        now = time.monotonic()
        expired = [
            sid for sid, info in sessions.items()
            if now - info["created_at"] > config.session_ttl_seconds
        ]
        for sid in expired:
            info = sessions.pop(sid, None)
            if info:
                info["agent"].end_session()
                logger.info(f"Expired session {sid} (TTL {config.session_ttl_seconds}s)")


# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _cleanup_task
    agent = Agent(config)
    _cleanup_task = asyncio.create_task(_session_cleanup_loop())
    logger.info(
        f"Skills Executor started. "
        f"Provider: {config.llm.provider.value}, "
        f"Skills: {len(agent.registry._skills)}"
    )
    yield
    # Cleanup
    _cleanup_task.cancel()
    for sid, info in sessions.items():
        info["agent"].end_session()
    sessions.clear()


app = FastAPI(
    title="Skills Executor",
    description="Programmatic agent with skill-based progressive disclosure",
    version="0.1.0",
    lifespan=lifespan,
)


# ─── Request / Response models ───────────────────────────────────────────────

class RunRequest(BaseModel):
    task: str
    extra_context: str = ""


class RunResponse(BaseModel):
    response: str
    output_files: list[str]
    turns: int
    skills_used: list[str]
    input_tokens: int
    output_tokens: int
    duration_seconds: float


class SkillInfo(BaseModel):
    name: str
    description: str
    tags: list[str]
    has_scripts: bool
    supporting_files: list[str]


class SessionResponse(BaseModel):
    session_id: str


class SendRequest(BaseModel):
    message: str


class SendResponse(BaseModel):
    response: str
    turns: int
    skills_used: list[str]
    input_tokens: int
    output_tokens: int


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/skills", response_model=list[SkillInfo])
async def list_skills(_key: str = Depends(verify_api_key)):
    """List all available skills with metadata."""
    return agent.registry.list_skills()


@app.post("/run", response_model=RunResponse)
async def run_task(request: RunRequest, _key: str = Depends(verify_api_key)):
    """
    Execute a task using available skills (one-shot).

    The agent will discover relevant skills, load them, follow their
    instructions, and execute any bundled scripts in a sandboxed environment.
    """
    output_dir = Path(tempfile.mkdtemp(prefix="output-"))

    result = agent.run(
        task=request.task,
        extra_context=request.extra_context,
        output_dir=output_dir,
    )

    return RunResponse(
        response=result.response,
        output_files=[str(f) for f in result.output_files],
        turns=result.turns,
        skills_used=result.skills_used,
        input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens,
        duration_seconds=result.duration_seconds,
    )


@app.post("/run-with-files", response_model=RunResponse)
async def run_task_with_files(
    task: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    _key: str = Depends(verify_api_key),
):
    """Execute a task with uploaded input files."""
    # Save uploaded files to temp dir — sanitize filenames
    input_files = {}
    for upload in files:
        safe_name = Path(upload.filename).name if upload.filename else "unnamed"
        if not safe_name or safe_name.startswith("."):
            safe_name = f"upload_{uuid.uuid4().hex[:8]}"
        tmp = Path(tempfile.mkdtemp(prefix="upload-")) / safe_name
        with open(tmp, "wb") as f:
            content = await upload.read()
            f.write(content)
        input_files[safe_name] = tmp

    output_dir = Path(tempfile.mkdtemp(prefix="output-"))

    result = agent.run(
        task=task,
        input_files=input_files if input_files else None,
        output_dir=output_dir,
    )

    # Clean up temp input files
    for tmp in input_files.values():
        tmp.unlink(missing_ok=True)

    return RunResponse(
        response=result.response,
        output_files=[str(f) for f in result.output_files],
        turns=result.turns,
        skills_used=result.skills_used,
        input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens,
        duration_seconds=result.duration_seconds,
    )


# ─── Multi-turn sessions ────────────────────────────────────────────────────

@app.post("/sessions", response_model=SessionResponse)
async def create_session(_key: str = Depends(verify_api_key)):
    """Create a new interactive multi-turn session."""
    if len(sessions) >= config.max_sessions:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum concurrent sessions ({config.max_sessions}) reached. "
                   "Try again later or delete an existing session.",
        )
    session_id = str(uuid.uuid4())
    session_agent = MultiTurnAgent(config)
    session_agent.start_session()
    sessions[session_id] = {
        "agent": session_agent,
        "created_at": time.monotonic(),
    }
    return SessionResponse(session_id=session_id)


@app.post("/sessions/{session_id}/send", response_model=SendResponse)
async def send_message(
    session_id: str,
    request: SendRequest,
    _key: str = Depends(verify_api_key),
):
    """Send a message to an existing session."""
    info = sessions.get(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    result = info["agent"].send(request.message)
    return SendResponse(
        response=result.response,
        turns=result.turns,
        skills_used=result.skills_used,
        input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens,
    )


@app.delete("/sessions/{session_id}")
async def end_session(session_id: str, _key: str = Depends(verify_api_key)):
    """End and clean up a session."""
    info = sessions.pop(session_id, None)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")
    info["agent"].end_session()
    return {"status": "ended"}


# ─── Health check (unauthenticated) ──────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
