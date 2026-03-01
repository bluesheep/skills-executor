"""
LLM Client: unified interface for Azure OpenAI, OpenAI, and Anthropic.

Each provider has different API shapes for tool use. This module normalises
them into a common interface so the agent loop doesn't care which backend
is being used.
"""

from dataclasses import dataclass
from typing import Any
import json
import logging

from config import LLMConfig, LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Normalised tool call from any provider."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalised response from any provider."""
    text: str | None  # Final text content (None if only tool calls)
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn", "tool_use", "max_tokens", etc.
    usage: dict[str, int]  # {"input_tokens": ..., "output_tokens": ...}

    # Provider-specific raw response for multi-turn continuation
    _raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    @property
    def is_done(self) -> bool:
        return not self.has_tool_calls and self.stop_reason in ("end_turn", "stop")


class LLMClient:
    """Unified LLM client supporting multiple providers."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self._init_client()

    def _init_client(self):
        match self.config.provider:
            case LLMProvider.AZURE_OPENAI:
                self._init_azure_openai()
            case LLMProvider.OPENAI:
                self._init_openai()
            case LLMProvider.ANTHROPIC:
                self._init_anthropic()

    def _init_azure_openai(self):
        from openai import AzureOpenAI
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        self._client = AzureOpenAI(
            azure_endpoint=self.config.azure_endpoint,
            azure_ad_token_provider=token_provider,
            api_version=self.config.azure_api_version,
        )
        self._model = self.config.azure_deployment or self.config.model

    def _init_openai(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=self.config.openai_api_key)
        self._model = self.config.model

    def _init_anthropic(self):
        import anthropic
        self._client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        self._model = self.config.model

    # ─── Unified chat interface ──────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        """
        Send messages and tools to the LLM, return a normalised response.

        Args:
            messages: Conversation history in provider-neutral format.
                      Will be adapted per-provider internally.
            tools: Tool definitions in OpenAI format.
            system: System message (handled differently per provider).
        """
        match self.config.provider:
            case LLMProvider.AZURE_OPENAI | LLMProvider.OPENAI:
                return self._chat_openai(messages, tools, system)
            case LLMProvider.ANTHROPIC:
                return self._chat_anthropic(messages, tools, system)

    def _chat_openai(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | None,
    ) -> LLMResponse:
        """OpenAI / Azure OpenAI chat completion with tools."""
        # Build messages list with system message
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})

        for msg in messages:
            api_messages.append(self._to_openai_message(msg))

        kwargs = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        # Extract tool calls
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        return LLMResponse(
            text=choice.message.content,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage={
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            },
            _raw=choice.message,
        )

    def _chat_anthropic(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | None,
    ) -> LLMResponse:
        """Anthropic Messages API with tools."""
        from tools import tools_to_anthropic_format

        # Convert messages to Anthropic format
        api_messages = []
        for msg in messages:
            api_messages.append(self._to_anthropic_message(msg))

        kwargs = {
            "model": self._model,
            "max_tokens": self.config.max_tokens,
            "messages": api_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools_to_anthropic_format()

        response = self._client.messages.create(**kwargs)

        # Extract text and tool calls
        text = None
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason="tool_use" if response.stop_reason == "tool_use" else "end_turn",
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            _raw=response,
        )

    # ─── Message format adapters ─────────────────────────────────────────

    def _to_openai_message(self, msg: dict) -> dict:
        """Convert our internal message format to OpenAI format."""
        role = msg["role"]

        if role == "assistant" and "tool_calls" in msg:
            # Assistant message with tool calls
            return {
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in msg["tool_calls"]
                ],
            }
        elif role == "tool":
            return {
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": msg["content"],
            }
        else:
            return {"role": role, "content": msg["content"]}

    def _to_anthropic_message(self, msg: dict) -> dict:
        """Convert our internal message format to Anthropic format."""
        role = msg["role"]

        if role == "assistant" and "tool_calls" in msg:
            content = []
            if msg.get("content"):
                content.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                content.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["arguments"],
                })
            return {"role": "assistant", "content": content}

        elif role == "tool":
            # Anthropic wraps tool results in user messages
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg["content"],
                }],
            }
        else:
            return {"role": role, "content": msg["content"]}

    def build_tool_results_message(
        self,
        tool_calls: list[ToolCall],
        results: list[str],
    ) -> list[dict]:
        """
        Build the message(s) to send tool results back.
        Returns a list because Anthropic and OpenAI handle this differently.
        """
        messages = []
        match self.config.provider:
            case LLMProvider.AZURE_OPENAI | LLMProvider.OPENAI:
                for tc, result in zip(tool_calls, results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            case LLMProvider.ANTHROPIC:
                # Anthropic: all tool results go in a single user message
                for tc, result in zip(tool_calls, results):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
        return messages
