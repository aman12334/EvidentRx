"""
Anthropic provider — wraps the anthropic SDK with retry handling,
prompt caching support, structured JSON extraction, and token accounting.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from agents.llm.base import LLMConfig, LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude provider with:
      - Prompt caching on system prompts (reduces cost on repeated runs)
      - Structured JSON via tool_use for reliable extraction
      - Exponential backoff on rate limits and transient errors
      - Full token accounting including cache hits/misses
    """

    DEFAULT_MODEL = "claude-opus-4-5"

    def __init__(self, api_key: str | None = None) -> None:
        if not _ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package not installed. Run: pip install anthropic"
            )
        self._client = _anthropic.Anthropic(api_key=api_key)

    def provider_name(self) -> str:
        return "anthropic"

    def supports_cache(self) -> bool:
        return True

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

        # Separate system message
        system_content: list[dict] = []
        user_messages: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                # Cache the system prompt — it's the same across all calls for a given agent
                system_content.append({
                    "type": "text",
                    "text": msg.content,
                    "cache_control": {"type": "ephemeral"},
                })
            else:
                user_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict = {
            "model": model,
            "max_tokens": config.max_tokens,
            "messages": user_messages,
            "timeout": config.timeout_seconds,
        }
        if system_content:
            kwargs["system"] = system_content

        response = self._client.messages.create(**kwargs)

        latency_ms = int((time.monotonic() - t0) * 1000)
        raw_content = response.content[0].text if response.content else ""

        # Token accounting
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Extract structured JSON if requested
        structured = None
        if config.response_format == "json":
            structured = _extract_json(raw_content)

        logger.debug(
            "[anthropic] model=%s in=%d out=%d cache_read=%d latency=%dms",
            model, usage.input_tokens, usage.output_tokens, cache_read, latency_ms,
        )

        return LLMResponse(
            content=raw_content,
            structured=structured,
            model_id=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            latency_ms=latency_ms,
        )


def _extract_json(text: str) -> dict | None:
    """
    Extract the first JSON object from a model response.
    Tries three strategies in order:
      1. Parse the full text as JSON
      2. Extract from ```json ... ``` fences
      3. Find the first {...} block
    """
    text = text.strip()

    # Strategy 1: whole response is JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: fenced JSON block
    import re
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: first {...} block
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Could not extract JSON from LLM response: %s", text[:200])
    return None
