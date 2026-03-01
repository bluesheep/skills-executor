"""
Agent: the agentic loop that orchestrates skills, tools, and the LLM.

This is the core of the system. It implements the full pattern:

1. System prompt includes the skill CATALOG (names + descriptions only)
2. LLM decides which skill(s) to load via tool calls
3. Loaded skill instructions guide the LLM's subsequent decisions
4. LLM can read supporting files and execute scripts as needed
5. Loop continues until the LLM produces a final response or hits limits

The agent is STATELESS between invocations. Each run() call gets a fresh
sandbox and clean conversation. For multi-turn scenarios, the caller
manages conversation state.
"""

from dataclasses import dataclass, field
from pathlib import Path
import uuid
import json
import logging
import time

from config import ExecutorConfig
from skill_registry import SkillRegistry
from sandbox import Sandbox
from tools import ToolDispatcher, TOOL_DEFINITIONS
from llm_client import LLMClient, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_TEMPLATE = """\
You are an AI assistant with access to a library of skills. Skills are \
modular capabilities that provide domain-specific instructions, scripts, \
and resources for completing tasks.

{skill_catalog}

## How to work with skills

1. When you receive a task, check if any available skill matches the task.
2. Call `load_skill` to load the full instructions for a relevant skill.
3. Follow the skill's instructions, which may tell you to:
   - Read additional reference files with `read_skill_file`
   - Execute bundled scripts with `execute_command`
   - Write intermediate files with `write_file`
4. Output files should be written to /output/ in the sandbox.
5. You can use multiple skills in one task if needed.

## Sandbox environment

You have access to a sandboxed execution environment:
- /skill/    → Current skill's files (read-only)
- /input/    → User-provided input files (read-only)
- /workspace/ → Your working directory (read-write)
- /output/   → Place final outputs here for collection

Python 3.12, Node.js, and common CLI tools are available.

## Important

- ALWAYS load a skill's instructions before attempting to follow them.
- ALWAYS use the bundled scripts when a skill provides them — don't \
  reimplement from scratch.
- If a skill references supporting files (e.g., "see REFERENCE.md"), \
  read them with read_skill_file before proceeding.
- When you have completed the task, respond with your final answer.
"""


@dataclass
class AgentResult:
    """Result of an agent execution."""
    response: str
    output_files: list[Path] = field(default_factory=list)
    turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    skills_used: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class Agent:
    """
    The agentic loop. Coordinates the LLM, skill registry, sandbox, and tools.
    """

    def __init__(self, config: ExecutorConfig):
        self.config = config
        self.registry = SkillRegistry(config.skill_paths)
        self.sandbox = Sandbox(config.sandbox)
        self.llm = LLMClient(config.llm)

        # Discover skills on init
        self.registry.discover()

    def run(
        self,
        task: str,
        input_files: dict[str, Path] | None = None,
        output_dir: Path | None = None,
        extra_context: str = "",
    ) -> AgentResult:
        """
        Execute a task using available skills.

        Args:
            task: Natural language description of what to do.
            input_files: Dict of {filename: local_path} to make available.
            output_dir: Where to collect output files. Defaults to ./output/
            extra_context: Additional context to append to the user message.

        Returns:
            AgentResult with the LLM's response and any generated files.
        """
        start_time = time.monotonic()
        session_id = str(uuid.uuid4())
        output_dir = output_dir or Path("./output")

        # Set up sandbox
        self.sandbox.create_session(
            session_id=session_id,
            input_files=input_files,
        )

        # Set up tool dispatcher
        dispatcher = ToolDispatcher(
            registry=self.registry,
            sandbox=self.sandbox,
            session_id=session_id,
        )

        # Build system prompt with skill catalog
        skill_catalog = self.registry.get_catalog()
        system = SYSTEM_PROMPT_TEMPLATE.format(skill_catalog=skill_catalog)

        # Build initial user message
        user_content = task
        if extra_context:
            user_content += f"\n\n{extra_context}"
        if input_files:
            file_list = ", ".join(input_files.keys())
            user_content += f"\n\nInput files available at /input/: {file_list}"

        # Conversation history (provider-neutral format)
        messages: list[dict] = [
            {"role": "user", "content": user_content},
        ]

        # Track metrics
        total_input_tokens = 0
        total_output_tokens = 0
        skills_used = set()

        try:
            for turn in range(self.config.max_turns):
                logger.info(f"Turn {turn + 1}/{self.config.max_turns}")

                # Call the LLM
                response = self.llm.chat(
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    system=system,
                )

                total_input_tokens += response.usage.get("input_tokens", 0)
                total_output_tokens += response.usage.get("output_tokens", 0)

                # If no tool calls, we're done
                if not response.has_tool_calls:
                    logger.info(f"Agent finished after {turn + 1} turns")
                    output_files = self.sandbox.collect_outputs(session_id, output_dir)
                    return AgentResult(
                        response=response.text or "",
                        output_files=output_files,
                        turns=turn + 1,
                        total_input_tokens=total_input_tokens,
                        total_output_tokens=total_output_tokens,
                        skills_used=list(skills_used),
                        duration_seconds=time.monotonic() - start_time,
                    )

                # Add assistant message with tool calls to history
                assistant_msg = {
                    "role": "assistant",
                    "content": response.text,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call
                results = []
                for tc in response.tool_calls:
                    logger.info(f"  Tool call: {tc.name}({json.dumps(tc.arguments)[:100]})")

                    # Track which skills are loaded
                    if tc.name == "load_skill":
                        skills_used.add(tc.arguments.get("skill_name", ""))

                    result = dispatcher.dispatch(tc.name, tc.arguments)
                    results.append(result)

                    logger.debug(f"  Result: {result[:200]}")

                # Add tool results to conversation
                tool_messages = self.llm.build_tool_results_message(
                    response.tool_calls, results,
                )
                messages.extend(tool_messages)

            # Max turns reached
            logger.warning(f"Max turns ({self.config.max_turns}) reached")
            output_files = self.sandbox.collect_outputs(session_id, output_dir)
            return AgentResult(
                response=(
                    "I reached the maximum number of turns without completing the task. "
                    "Here is what I accomplished so far:\n\n"
                    + (response.text or "(no final response)")
                ),
                output_files=output_files,
                turns=self.config.max_turns,
                total_input_tokens=total_input_tokens,
                total_output_tokens=total_output_tokens,
                skills_used=list(skills_used),
                duration_seconds=time.monotonic() - start_time,
            )

        finally:
            # Always clean up the sandbox
            self.sandbox.destroy(session_id)


class MultiTurnAgent(Agent):
    """
    Extended agent that maintains conversation state across multiple
    user inputs (interactive / chat mode).
    """

    def __init__(self, config: ExecutorConfig):
        super().__init__(config)
        self._session_id: str | None = None
        self._messages: list[dict] = []
        self._system: str | None = None
        self._dispatcher: ToolDispatcher | None = None
        self._skills_used: set[str] = set()

    def start_session(self, input_files: dict[str, Path] | None = None):
        """Start a new multi-turn session."""
        self._session_id = str(uuid.uuid4())
        self._messages = []
        self._skills_used = set()

        self.sandbox.create_session(
            session_id=self._session_id,
            input_files=input_files,
        )
        self._dispatcher = ToolDispatcher(
            registry=self.registry,
            sandbox=self.sandbox,
            session_id=self._session_id,
        )

        skill_catalog = self.registry.get_catalog()
        self._system = SYSTEM_PROMPT_TEMPLATE.format(skill_catalog=skill_catalog)

    def send(self, user_input: str) -> AgentResult:
        """Send a message and get a response within the current session."""
        if self._session_id is None:
            self.start_session()

        start_time = time.monotonic()
        self._messages.append({"role": "user", "content": user_input})

        total_input_tokens = 0
        total_output_tokens = 0

        for turn in range(self.config.max_turns):
            response = self.llm.chat(
                messages=self._messages,
                tools=TOOL_DEFINITIONS,
                system=self._system,
            )
            total_input_tokens += response.usage.get("input_tokens", 0)
            total_output_tokens += response.usage.get("output_tokens", 0)

            if not response.has_tool_calls:
                self._messages.append({
                    "role": "assistant",
                    "content": response.text,
                })
                return AgentResult(
                    response=response.text or "",
                    turns=turn + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    skills_used=list(self._skills_used),
                    duration_seconds=time.monotonic() - start_time,
                )

            # Process tool calls
            assistant_msg = {
                "role": "assistant",
                "content": response.text,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            }
            self._messages.append(assistant_msg)

            results = []
            for tc in response.tool_calls:
                if tc.name == "load_skill":
                    self._skills_used.add(tc.arguments.get("skill_name", ""))
                result = self._dispatcher.dispatch(tc.name, tc.arguments)
                results.append(result)

            tool_messages = self.llm.build_tool_results_message(
                response.tool_calls, results,
            )
            self._messages.extend(tool_messages)

        return AgentResult(
            response="Max turns reached.",
            turns=self.config.max_turns,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            skills_used=list(self._skills_used),
            duration_seconds=time.monotonic() - start_time,
        )

    def end_session(self):
        """End the multi-turn session and clean up."""
        if self._session_id:
            self.sandbox.destroy(self._session_id)
            self._session_id = None
            self._messages = []
            self._dispatcher = None
