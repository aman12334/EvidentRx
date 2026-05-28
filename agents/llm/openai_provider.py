"""
OpenAI provider — wraps the openai SDK with the same interface as AnthropicProvider.
Used as a fallback or for specific model routing decisions.
"""
from __future__ import annotations

import logging
import time

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents.llm.anthropic_provider import _extract_json
from agents.llm.base import LLMConfig, LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)

try:
    import openai as _openai
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class OpenAIProvider(LLMProvider):
    """
    OpenAI provider with:
      - JSON mode via response_format={"type": "json_object"}
      - Exponential backoff on rate limits
      - Token accounting (no cache tracking — OpenAI doesn't expose this)
    """

    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, api_key: str | None = None) -> None:
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "openai package not installed. Run: pip install openai"
            )
        self._client = _openai.OpenAI(api_key=api_key)

    def provider_name(self) -> str:
        return "openai"

    def complete(self, messages: list[Message], config: LLMConfig) -> LLMResponse:
        return self._complete_with_retry(messages, config)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _complete_with_retry(
        self, messages: list[Message], config: LLMConfig
    ) -> LLMResponse:
        model = config.model or self.DEFAULT_MODEL
        t0 = time.monotonic()

        oai_messages = [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout_seconds,
        }
        if config.response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        raw_content = response.choices[0].message.content or ""
        usage = response.usage

        structured = None
        if config.response_format == "json":
            structured = _extract_json(raw_content)

        return LLMResponse(
            content=raw_content,
            structured=structured,
            model_id=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )
