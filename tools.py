"""
Tool definitions and dispatcher.

The LLM receives these tools, which provide progressive disclosure and sandboxed
execution. The tools fall into three categories:

1. SKILL DISCOVERY: list_skills, load_skill, read_skill_file
   → Progressive disclosure: metadata → instructions → supporting files

2. SANDBOX EXECUTION: execute_command, write_file, read_file, list_files
   → Run skill scripts and arbitrary commands in isolation

3. LIFECYCLE: finish
   → Signal that the task is complete
"""

import json
from typing import Any

from skill_registry import SkillRegistry
from sandbox import Sandbox

# ─── Tool schemas (OpenAI function calling format) ───────────────────────────
# These same schemas work with Azure OpenAI and can be adapted for Anthropic.

TOOL_DEFINITIONS = [
    # ── Skill discovery ──────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": (
                "List all available skills with their metadata. Use this to discover "
                "what skills are available before loading one. Returns name, description, "
                "tags, and whether the skill has bundled scripts."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "Load the full instructions for a skill by name. This reads the SKILL.md "
                "body and returns the complete instructions, plus a manifest of supporting "
                "files and scripts available in the skill. You MUST call this before using "
                "a skill's instructions or running its scripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The name of the skill to load (from list_skills).",
                    },
                },
                "required": ["skill_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_file",
            "description": (
                "Read a supporting file from a loaded skill's directory. Use this to read "
                "reference documentation, examples, script source code, or templates that "
                "the skill's instructions reference. The path is relative to the skill root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The name of the skill.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": (
                            "Relative path within the skill directory. "
                            "e.g., 'reference.md' or 'scripts/generate.py'"
                        ),
                    },
                },
                "required": ["skill_name", "file_path"],
            },
        },
    },

    # ── Sandbox execution ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Execute a shell command in the sandboxed workspace. The skill's files "
                "are available at /skill/ (read-only), input files at /input/ (read-only), "
                "and /workspace/ is the working directory (read-write). "
                "Write output files to /output/ for collection. "
                "Python, Node.js, and common CLI tools are available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file in the workspace. Path is relative to /workspace/. "
                "Use this to create scripts, config files, or input data before executing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file from the workspace, input directory, or skill directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (relative to workspace, input, or skill dir).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory path relative to workspace. Default: '.'",
                        "default": ".",
                    },
                },
                "required": [],
            },
        },
    },
]


# ─── Anthropic tool format adapter ──────────────────────────────────────────
def tools_to_anthropic_format() -> list[dict]:
    """Convert OpenAI-format tool definitions to Anthropic format."""
    anthropic_tools = []
    for tool in TOOL_DEFINITIONS:
        fn = tool["function"]
        anthropic_tools.append({
            "name": fn["name"],
            "description": fn["description"],
            "input_schema": fn["parameters"],
        })
    return anthropic_tools


# ─── Tool dispatcher ────────────────────────────────────────────────────────

class ToolDispatcher:
    """
    Routes tool calls from the LLM to the appropriate handler.

    Tracks which skills have been loaded (for progressive disclosure state)
    and which sandbox session to use for execution.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        sandbox: Sandbox,
        session_id: str,
    ):
        self.registry = registry
        self.sandbox = sandbox
        self.session_id = session_id
        self._loaded_skills: set[str] = set()
        self._active_skill: str | None = None

    # Required string arguments per tool, used for validation.
    _REQUIRED_ARGS: dict[str, list[str]] = {
        "load_skill": ["skill_name"],
        "read_skill_file": ["skill_name", "file_path"],
        "execute_command": ["command"],
        "write_file": ["path", "content"],
        "read_file": ["path"],
    }

    def _validate_arguments(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        """Validate tool arguments. Returns an error string or None if valid."""
        required = self._REQUIRED_ARGS.get(tool_name, [])
        for key in required:
            if key not in arguments:
                return f"Missing required argument: '{key}'"
            if not isinstance(arguments[key], str):
                return f"Argument '{key}' must be a string, got {type(arguments[key]).__name__}"
            if not arguments[key].strip():
                return f"Argument '{key}' must not be empty"
        return None

    def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""
        try:
            # Validate arguments before dispatching
            error = self._validate_arguments(tool_name, arguments)
            if error:
                return f"Validation error ({tool_name}): {error}"

            match tool_name:
                # Skill discovery
                case "list_skills":
                    return json.dumps(self.registry.list_skills(), indent=2)

                case "load_skill":
                    return self._handle_load_skill(arguments["skill_name"])

                case "read_skill_file":
                    return self._handle_read_skill_file(
                        arguments["skill_name"],
                        arguments["file_path"],
                    )

                # Sandbox execution
                case "execute_command":
                    result = self.sandbox.execute(self.session_id, arguments["command"])
                    return result.to_tool_response()

                case "write_file":
                    return self.sandbox.write_file(
                        self.session_id,
                        arguments["path"],
                        arguments["content"],
                    )

                case "read_file":
                    return self.sandbox.read_file(
                        self.session_id,
                        arguments["path"],
                    )

                case "list_files":
                    return self.sandbox.list_files(
                        self.session_id,
                        arguments.get("directory", "."),
                    )

                case _:
                    return f"Unknown tool: {tool_name}"

        except Exception as e:
            return f"Tool error ({tool_name}): {e}"

    def _handle_load_skill(self, skill_name: str) -> str:
        """Load a skill and optionally mount it in the sandbox."""
        content = self.registry.load_skill(skill_name)
        if content is None:
            return f"Skill '{skill_name}' not found. Use list_skills to see available skills."

        self._loaded_skills.add(skill_name)
        self._active_skill = skill_name

        # Mount the skill directory in the sandbox if it has scripts
        skill_dir = self.registry.get_skill_dir(skill_name)
        if skill_dir:
            # Re-create the sandbox session with this skill mounted
            # (In production you might support multiple skill mounts)
            self.sandbox.create_session(
                session_id=self.session_id,
                skill_dir=skill_dir,
            )

        # Build the response
        parts = [
            f"# Skill: {content.metadata.name}",
            "",
            content.instructions,
        ]

        if content.supporting_files:
            parts.append("\n## Supporting files (use read_skill_file to load):")
            for fname, preview in content.supporting_files.items():
                parts.append(f"  - {fname}: {preview}")

        if content.scripts:
            parts.append("\n## Bundled scripts (available at /skill/ in sandbox):")
            for script in content.scripts:
                parts.append(f"  - /skill/{script}")

        return "\n".join(parts)

    def _handle_read_skill_file(self, skill_name: str, file_path: str) -> str:
        """Read a supporting file from a skill."""
        if skill_name not in self._loaded_skills:
            return (
                f"Skill '{skill_name}' has not been loaded yet. "
                "Call load_skill first."
            )

        content = self.registry.read_skill_file(skill_name, file_path)
        if content is None:
            return f"File '{file_path}' not found in skill '{skill_name}'."
        return content
