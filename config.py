"""
Configuration for the Skills Executor.
Supports Azure OpenAI (via Foundry or standalone), direct OpenAI, and Anthropic.
"""

from dataclasses import dataclass, field
from enum import Enum
import os


class LLMProvider(Enum):
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class SandboxMode(Enum):
    SUBPROCESS = "subprocess"  # Subprocess execution
    NONE = "none"              # No execution - dry run


@dataclass
class LLMConfig:
    provider: LLMProvider = LLMProvider.AZURE_OPENAI
    model: str = "gpt-4.1"

    # Azure OpenAI / Foundry
    azure_endpoint: str = ""
    azure_deployment: str = ""
    azure_api_version: str = "2024-12-01-preview"

    # Direct OpenAI
    openai_api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Shared
    max_tokens: int = 8192
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider_str = os.getenv("LLM_PROVIDER", "azure_openai")
        try:
            provider = LLMProvider(provider_str)
        except ValueError:
            valid = ", ".join(p.value for p in LLMProvider)
            raise ValueError(
                f"Invalid LLM_PROVIDER='{provider_str}'. Must be one of: {valid}"
            )

        config = cls(
            provider=provider,
            model=os.getenv("LLM_MODEL", "gpt-4.1"),
            azure_endpoint=os.getenv("AZURE_AI_PROJECT_ENDPOINT", ""),
            azure_deployment=os.getenv("AZURE_DEPLOYMENT_NAME", ""),
            azure_api_version=os.getenv("AZURE_API_VERSION", "2024-12-01-preview"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        )
        config.validate()
        return config

    def validate(self):
        """Validate that required fields are set for the chosen provider."""
        match self.provider:
            case LLMProvider.AZURE_OPENAI:
                if not self.azure_endpoint:
                    raise ValueError(
                        "AZURE_AI_PROJECT_ENDPOINT is required when LLM_PROVIDER=azure_openai"
                    )
            case LLMProvider.OPENAI:
                if not self.openai_api_key:
                    raise ValueError(
                        "OPENAI_API_KEY is required when LLM_PROVIDER=openai"
                    )
            case LLMProvider.ANTHROPIC:
                if not self.anthropic_api_key:
                    raise ValueError(
                        "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
                    )


@dataclass
class SandboxConfig:
    mode: SandboxMode = SandboxMode.SUBPROCESS
    timeout_seconds: int = 120

    # Azure Document Intelligence (for PDF extraction)
    document_intelligence_endpoint: str = ""

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        mode_str = os.getenv("SANDBOX_MODE", "subprocess")
        try:
            mode = SandboxMode(mode_str)
        except ValueError:
            valid = ", ".join(m.value for m in SandboxMode)
            raise ValueError(
                f"Invalid SANDBOX_MODE='{mode_str}'. Must be one of: {valid}"
            )
        return cls(
            mode=mode,
            timeout_seconds=int(os.getenv("SANDBOX_TIMEOUT", "120")),
            document_intelligence_endpoint=os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", ""),
        )


@dataclass
class ExecutorConfig:
    llm: LLMConfig = field(default_factory=LLMConfig.from_env)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig.from_env)

    # Skill directories to scan (in order of priority)
    skill_paths: list[str] = field(default_factory=lambda: [
        ".claude/skills",
        ".agents/skills",
        ".github/skills",
        os.path.expanduser("~/.claude/skills"),
    ])

    # Agent loop limits
    max_turns: int = 30
    max_skill_body_tokens: int = 5000  # approx token budget for a single skill

    # API authentication
    api_key: str = ""

    # Session limits
    max_sessions: int = 100
    session_ttl_seconds: int = 1800  # 30 minutes

    @classmethod
    def from_env(cls) -> "ExecutorConfig":
        skill_paths_env = os.getenv("SKILL_PATHS", "")
        skill_paths = (
            skill_paths_env.split(":") if skill_paths_env
            else [".claude/skills", ".agents/skills", os.path.expanduser("~/.claude/skills")]
        )
        return cls(
            llm=LLMConfig.from_env(),
            sandbox=SandboxConfig.from_env(),
            skill_paths=skill_paths,
            max_turns=int(os.getenv("MAX_TURNS", "30")),
            api_key=os.getenv("API_KEY", ""),
            max_sessions=int(os.getenv("MAX_SESSIONS", "100")),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "1800")),
        )
