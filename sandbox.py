"""
Sandbox: isolated execution environment for skill scripts.

Each skill invocation gets a fresh, isolated environment with:
- The skill's files available at /skill/
- A writable workspace at /workspace/
- User-provided input files at /input/
- Output files collected from /output/
"""

from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess
import tempfile
import shutil
import logging
import time

from config import SandboxConfig, SandboxMode

logger = logging.getLogger(__name__)

# ─── Command blocklist ───────────────────────────────────────────────────────
# Patterns that should never appear in sandbox commands.  Each entry is a
# compiled regex tested against the full command string.

_BLOCKED_COMMAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Network exfiltration piped to shell
    (re.compile(r"curl\s.*\|\s*(bash|sh|zsh|python|perl)", re.I), "network-to-shell pipe"),
    (re.compile(r"wget\s.*\|\s*(bash|sh|zsh|python|perl)", re.I), "network-to-shell pipe"),
    # Reverse / bind shells
    (re.compile(r"\b(nc|ncat|netcat)\b.*-[elp]", re.I), "reverse/bind shell"),
    (re.compile(r"/dev/tcp/", re.I), "bash reverse shell"),
    (re.compile(r"\bmkfifo\b", re.I), "named pipe (reverse shell)"),
    # Dangerous destructive commands
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/\s*$"), "recursive delete root"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/[a-z]+\s*$"), "recursive delete system dir"),
    # Sensitive host paths
    (re.compile(r"/proc/self/"), "proc self access"),
    (re.compile(r"/proc/\d+/"), "proc PID access"),
    (re.compile(r"/(etc/shadow|etc/passwd|etc/sudoers)"), "sensitive system file"),
    # Cloud metadata / IMDS
    (re.compile(r"169\.254\.169\.254"), "IMDS metadata endpoint"),
    (re.compile(r"metadata\.google\.internal"), "GCP metadata endpoint"),
    # Privilege escalation
    (re.compile(r"\bsudo\b"), "sudo"),
    (re.compile(r"\bsu\s+"), "su"),
    (re.compile(r"\bchmod\s+[0-7]*[sS]|\bchmod\s+[4267][0-7]{3}\b|\bchmod\s+[ugo]*\+[rwxst]*s"), "setuid/setgid/sticky bit"),
    (re.compile(r"\bchown\b"), "chown"),
    # System modification
    (re.compile(r"\bcrontab\b"), "cron modification"),
    (re.compile(r"/\.ssh/"), "SSH directory access"),
]


def _check_command_blocked(command: str) -> str | None:
    """Return a rejection message if the command matches a blocked pattern, else None."""
    for pattern, description in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            return (
                f"Command blocked by security policy: {description}. "
                "This command is not allowed in the sandbox."
            )
    return None


def _is_within(target: Path, base: Path) -> bool:
    """Check that resolved *target* is inside *base* (robust path-traversal guard)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


# Sandbox path prefixes that LLMs may include in file paths.
# These need to be stripped before joining with local base directories
# because Python's Path("/base") / "/input/file" discards the base.
_SANDBOX_PREFIXES = ("/skill/", "/input/", "/output/", "/workspace/")


def _strip_sandbox_prefix(path: str) -> str:
    """Strip sandbox path prefix so the path can be joined with a local base."""
    for prefix in _SANDBOX_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


@dataclass
class ExecutionResult:
    """Result of running a command in the sandbox."""
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    output_files: list[str] = field(default_factory=list)  # paths relative to /output/

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def to_tool_response(self) -> str:
        """Format for returning to the LLM as a tool result."""
        parts = []
        if self.stdout.strip():
            parts.append(f"STDOUT:\n{self.stdout[-4000:]}")  # truncate to last 4k chars
        if self.stderr.strip():
            parts.append(f"STDERR:\n{self.stderr[-2000:]}")
        parts.append(f"EXIT_CODE: {self.exit_code}")
        parts.append(f"DURATION: {self.duration_seconds:.1f}s")
        if self.output_files:
            parts.append(f"OUTPUT_FILES: {', '.join(self.output_files)}")
        return "\n".join(parts)


class Sandbox:
    """
    Manages isolated execution environments for skill scripts.

    Lifecycle:
    1. create_session() - set up workspace, mount skill files
    2. execute() - run commands (can be called multiple times)
    3. collect_outputs() - gather generated files
    4. destroy() - clean up
    """

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._sessions: dict[str, "_SandboxSession"] = {}

    def create_session(
        self,
        session_id: str,
        skill_dir: Path | None = None,
        input_files: dict[str, Path] | None = None,
    ) -> str:
        """
        Create or update a sandbox session.

        If a session with this ID already exists, update it (e.g. mount a
        skill directory) without discarding the existing workspace/input files.

        Args:
            session_id: Unique identifier for this session
            skill_dir: Path to skill directory (mounted read-only at /skill/)
            input_files: Dict of {filename: local_path} to mount at /input/

        Returns:
            The session_id for subsequent calls
        """
        existing = self._sessions.get(session_id)
        if existing is not None:
            # Update existing session — merge rather than replace
            if skill_dir is not None:
                existing.skill_dir = skill_dir
            if input_files:
                for filename, src_path in input_files.items():
                    shutil.copy2(src_path, existing.input_dir / filename)
            logger.info(f"Updated sandbox session {session_id} (skill_dir={skill_dir})")
            return session_id

        workspace = Path(tempfile.mkdtemp(prefix=f"skill-{session_id}-"))
        output_dir = workspace / "output"
        output_dir.mkdir()
        input_dir = workspace / "input"
        input_dir.mkdir()

        # Copy input files to the input directory
        if input_files:
            for filename, src_path in input_files.items():
                shutil.copy2(src_path, input_dir / filename)

        session = _SandboxSession(
            session_id=session_id,
            workspace=workspace,
            output_dir=output_dir,
            input_dir=input_dir,
            skill_dir=skill_dir,
            config=self.config,
        )
        self._sessions[session_id] = session
        logger.info(f"Created sandbox session {session_id} at {workspace}")
        return session_id

    def execute(self, session_id: str, command: str) -> ExecutionResult:
        """Execute a command in the sandbox session."""
        session = self._sessions.get(session_id)
        if session is None:
            return ExecutionResult(
                stdout="", stderr=f"Unknown session: {session_id}",
                exit_code=1, duration_seconds=0.0,
            )
        return session.execute(command)

    def write_file(self, session_id: str, path: str, content: str) -> str:
        """Write a file into the sandbox workspace."""
        session = self._sessions.get(session_id)
        if session is None:
            return f"Unknown session: {session_id}"

        # Ensure path is relative and within workspace
        target = (session.workspace / _strip_sandbox_prefix(path)).resolve()
        if not _is_within(target, session.workspace):
            return "Error: path traversal not allowed"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {path}"

    def read_file(self, session_id: str, path: str) -> str:
        """Read a file from the sandbox workspace, skill dir, or input dir."""
        session = self._sessions.get(session_id)
        if session is None:
            return f"Unknown session: {session_id}"

        relative = _strip_sandbox_prefix(path)

        # Try workspace first, then input, then skill dir
        for base in [
            session.workspace,
            session.input_dir,
            session.skill_dir,
        ]:
            if base is None:
                continue
            target = (base / relative).resolve()
            if _is_within(target, base) and target.is_file():
                try:
                    return target.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    return f"[Binary file: {target.name}, {target.stat().st_size} bytes]"

        return f"File not found: {path}"

    def read_pdf(self, session_id: str, path: str, pages: str | None = None) -> str:
        """Extract text from a PDF using Azure Document Intelligence."""
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.identity import DefaultAzureCredential

        session = self._sessions.get(session_id)
        if session is None:
            return f"Unknown session: {session_id}"

        endpoint = self.config.document_intelligence_endpoint
        if not endpoint:
            return (
                "Azure Document Intelligence is not configured. "
                "Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT to enable PDF extraction."
            )

        # Resolve file path — same search order as read_file
        relative = _strip_sandbox_prefix(path)
        resolved: Path | None = None
        for base in [session.workspace, session.input_dir, session.skill_dir]:
            if base is None:
                continue
            target = (base / relative).resolve()
            if _is_within(target, base) and target.is_file():
                resolved = target
                break

        if resolved is None:
            return f"File not found: {path}"

        # Convert 0-indexed page range to 1-indexed for DI API
        di_pages: str | None = None
        if pages:
            try:
                start_str, end_str = pages.split("-", 1)
                start = max(0, int(start_str))
                end = int(end_str)
                di_pages = f"{start + 1}-{end + 1}"
            except (ValueError, TypeError):
                return f"Invalid page range '{pages}'. Use format '0-4' (0-indexed)."

        try:
            client = DocumentIntelligenceClient(
                endpoint=endpoint,
                credential=DefaultAzureCredential(),
            )
            with open(resolved, "rb") as f:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=f,
                    pages=di_pages,
                )
            result = poller.result()
        except Exception as e:
            return f"Document Intelligence analysis failed: {e}"

        if not result.pages:
            return f"No extractable content found in {path}."

        parts: list[str] = []
        for page in result.pages:
            page_num = page.page_number
            lines = []
            if page.lines:
                for line in page.lines:
                    lines.append(line.content)
            if lines:
                # Display with 0-indexed page numbers to match the tool interface
                parts.append(f"--- Page {page_num - 1} ---\n" + "\n".join(lines))

        if not parts:
            return f"No extractable text found in {path} (pages may be image-based)."

        text = "\n".join(parts)

        max_chars = 50_000
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[Truncated — showed {max_chars} of {len(text)} chars. Use 'pages' to read specific sections.]"

        return text

    def list_files(self, session_id: str, directory: str = ".") -> str:
        """List files in the sandbox."""
        session = self._sessions.get(session_id)
        if session is None:
            return f"Unknown session: {session_id}"

        target = (session.workspace / directory).resolve()
        if not _is_within(target, session.workspace):
            return "Error: path traversal not allowed"
        if not target.is_dir():
            return f"Not a directory: {directory}"

        entries = []
        for item in sorted(target.iterdir()):
            if item.is_dir():
                entries.append(f"  {item.name}/")
            else:
                size = item.stat().st_size
                entries.append(f"  {item.name} ({size} bytes)")
        return "\n".join(entries) if entries else "(empty)"

    def collect_outputs(self, session_id: str, dest_dir: Path) -> list[Path]:
        """Copy output files from the sandbox to a destination directory."""
        session = self._sessions.get(session_id)
        if session is None:
            return []

        dest_dir.mkdir(parents=True, exist_ok=True)
        collected = []
        for f in session.output_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(session.output_dir)
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
                collected.append(dest)

        return collected

    def destroy(self, session_id: str):
        """Clean up a sandbox session."""
        session = self._sessions.pop(session_id, None)
        if session:
            shutil.rmtree(session.workspace, ignore_errors=True)
            logger.info(f"Destroyed sandbox session {session_id}")


class _SandboxSession:
    """Internal: manages a single sandbox session."""

    def __init__(
        self,
        session_id: str,
        workspace: Path,
        output_dir: Path,
        input_dir: Path,
        skill_dir: Path | None,
        config: SandboxConfig,
    ):
        self.session_id = session_id
        self.workspace = workspace
        self.output_dir = output_dir
        self.input_dir = input_dir
        self.skill_dir = skill_dir
        self.config = config

    def execute(self, command: str) -> ExecutionResult:
        if self.config.mode == SandboxMode.SUBPROCESS:
            return self._execute_subprocess(command)
        else:
            return ExecutionResult(
                stdout="", stderr="Sandbox mode is NONE - execution disabled",
                exit_code=1, duration_seconds=0.0,
            )

    def _execute_subprocess(self, command: str) -> ExecutionResult:
        """Run command as a local subprocess (dev/testing only)."""
        # Security: check command against blocklist BEFORE path rewriting
        # (so attackers can't bypass patterns via sandbox-path substitution)
        blocked = _check_command_blocked(command)
        if blocked:
            logger.warning(
                f"[SECURITY] Blocked command in session {self.session_id}: {command!r}"
            )
            return ExecutionResult(
                stdout="", stderr=blocked,
                exit_code=1, duration_seconds=0.0,
            )

        # Rewrite absolute sandbox paths to actual local paths so that
        # commands from SKILL.md (e.g. "python /skill/scripts/analyze.py
        # /input/ --output /output/report.json") work without Docker.
        command = self._rewrite_sandbox_paths(command)

        # Also check the rewritten command (catches patterns that only
        # appear after path expansion)
        blocked = _check_command_blocked(command)
        if blocked:
            logger.warning(
                f"[SECURITY] Blocked command (post-rewrite) in session "
                f"{self.session_id}: {command!r}"
            )
            return ExecutionResult(
                stdout="", stderr=blocked,
                exit_code=1, duration_seconds=0.0,
            )

        logger.info(f"[SANDBOX] Executing in session {self.session_id}: {command!r}")

        env = {
            "SKILL_DIR": str(self.skill_dir) if self.skill_dir else "",
            "WORKSPACE_DIR": str(self.workspace),
            "OUTPUT_DIR": str(self.output_dir),
            "INPUT_DIR": str(self.input_dir),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.workspace),
        }

        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                cwd=str(self.workspace),
                env=env,
            )
            duration = time.monotonic() - start
            return ExecutionResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_seconds=duration,
                output_files=self._scan_outputs(),
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return ExecutionResult(
                stdout="", stderr=f"Command timed out after {self.config.timeout_seconds}s",
                exit_code=124, duration_seconds=duration,
            )
        except Exception as e:
            duration = time.monotonic() - start
            return ExecutionResult(
                stdout="", stderr=str(e),
                exit_code=1, duration_seconds=duration,
            )

    def _rewrite_sandbox_paths(self, command: str) -> str:
        """Rewrite absolute sandbox paths to actual local paths for subprocess mode."""
        if self.skill_dir:
            command = command.replace("/skill/", f"{self.skill_dir}/")
        command = command.replace("/input/", f"{self.input_dir}/")
        command = command.replace("/output/", f"{self.output_dir}/")
        command = command.replace("/workspace/", f"{self.workspace}/")
        return command

    def _scan_outputs(self) -> list[str]:
        """List files in the output directory."""
        if not self.output_dir.exists():
            return []
        return [
            str(f.relative_to(self.output_dir))
            for f in self.output_dir.rglob("*")
            if f.is_file()
        ]

