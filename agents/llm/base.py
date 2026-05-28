"""
LLM Provider abstraction — unified interface for Anthropic and OpenAI.

Agents call provider.complete() and receive an LLMResponse without knowing
which underlying SDK is in use. Model routing, retries, and token accounting
are handled here, not in agent business logic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Message:
    role: str       # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    content: str                         # raw text response
    structured: dict | None           # parsed JSON if response_format="json"
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMConfig:
    model: str
    max_tokens: int = 4096
    temperature: float = 0.1            # low temp for deterministic compliance work
    timeout_seconds: int = 60
    max_retries: int = 3
    response_format: str = "json"       # "json" | "text"
    system_prompt: str | None = None


class LLMProvider(ABC):
    """
    Abstract base for all LLM providers.
    Implementations must be stateless — one instance shared across agents.
    """

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send messages and return a structured response."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        ...

    def supports_cache(self) -> bool:
        """Override to True if the provider supports prompt caching."""
        return False
