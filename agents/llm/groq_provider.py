"""
Groq provider — OpenAI-compatible client pointed at api.groq.com.

Groq exposes the same /chat/completions endpoint as OpenAI, so we reuse
the OpenAI SDK with a custom base_url. No extra dependency needed.

Models routed through this provider (hackathon free tier):
  - groq/compound             : Orchestrator (built-in tool use)
  - openai/gpt-oss-120b       : Evidence analysis + Deep analysis
  - qwen/qwen3-32b            : Risk scoring (structured JSON)
  - openai/gpt-oss-20b        : Deep analysis (mid-tier reasoning)
  - llama-3.3-70b-versatile   : Narrative generation
  - llama-3.1-8b-instant      : Classification (fast + cheap)
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

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Models that do NOT support json_object response_format on Groq
_NO_JSON_MODE = {"groq/compound", "groq/compound-mini"}


class GroqProvider(LLMProvider):
    """
    Groq provider — same interface as OpenAIProvider, different base URL.
    All 6 hackathon agent models run through a single instance of this class.
    """

    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, base_url: str = GROQ_BASE_URL) -> None:
        try:
            import openai as _openai
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        import openai as _openai
        self._client = _openai.OpenAI(api_key=api_key, base_url=base_url)

    def provider_name(self) -> str:
        return "groq"

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
            "model":      model,
            "messages":   oai_messages,
            "max_tokens": config.max_tokens,
            "timeout":    config.timeout_seconds,
        }

        # compound models don't support json_object mode — skip it
        use_json_mode = (
            config.response_format == "json"
            and model not in _NO_JSON_MODE
        )
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)

        raw_content = response.choices[0].message.content or ""
        usage = response.usage

        structured = None
        if config.response_format == "json":
            structured = _extract_json(raw_content)

        logger.debug(
            "Groq call | model=%s | in=%d out=%d | latency=%dms",
            model,
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
            latency_ms,
        )

        return LLMResponse(
            content=raw_content,
            structured=structured,
            model_id=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
        )
