"""
ModelRouter — selects the right provider and model for a given agent task.

Routing strategy (hackathon — all free via Groq):
  ┌─────────────────────┬──────────────────────────┬─────────────────────────┐
  │ Task                │ Model                    │ Why                     │
  ├─────────────────────┼──────────────────────────┼─────────────────────────┤
  │ orchestration       │ groq/compound            │ Built-in tool use       │
  │ evidence_analysis   │ openai/gpt-oss-120b      │ Biggest — complex patt. │
  │ risk_prioritization │ qwen/qwen3-32b           │ Best structured JSON    │
  │ pattern_analysis    │ openai/gpt-oss-20b       │ Mid-tier deep reasoning │
  │ narrative_generation│ llama-3.3-70b-versatile  │ Best prose quality      │
  │ classification      │ llama-3.1-8b-instant     │ Ultra fast label tasks  │
  │ escalation_decision │ qwen/qwen3-32b           │ Structured bool output  │
  │ case_summary        │ llama-3.3-70b-versatile  │ Clear summarisation     │
  └─────────────────────┴──────────────────────────┴─────────────────────────┘

The router is the only place where provider/model selection logic lives.
Agents declare their task_type; the router decides the model.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from agents.llm.base import LLMConfig, LLMProvider, LLMResponse, Message

logger = logging.getLogger(__name__)

# Task → (model_id, max_tokens)
# All tasks route through the single GroqProvider instance.
_ROUTING_TABLE: dict[str, tuple[str, int]] = {
    "orchestration":         ("groq/compound",               4096),
    "evidence_analysis":     ("llama-3.3-70b-versatile",     2048),
    "risk_prioritization":   ("qwen/qwen3-32b",              2048),
    "pattern_analysis":      ("llama-3.3-70b-versatile",     2048),
    "narrative_generation":  ("llama-3.3-70b-versatile",     2048),
    "classification":        ("llama-3.1-8b-instant",        1024),
    "escalation_decision":   ("qwen/qwen3-32b",              1024),
    "case_summary":          ("llama-3.3-70b-versatile",     2048),
    "default":               ("llama-3.3-70b-versatile",     2048),
}


class ModelRouter:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def route(
        self,
        task_type: str,
        messages: list[Message],
        override_model: Optional[str] = None,
        override_max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Route a completion request to the model assigned to this task type.
        Each task gets its own dedicated Groq model.
        """
        model, max_tokens = _ROUTING_TABLE.get(
            task_type, _ROUTING_TABLE["default"]
        )

        config = LLMConfig(
            model=override_model or model,
            max_tokens=override_max_tokens or max_tokens,
            temperature=0.1,
            response_format="json",
        )

        logger.info(
            "Routing task=%s → model=%s (max_tokens=%d)",
            task_type, config.model, config.max_tokens,
        )

        return self._provider.complete(messages, config)


def build_router_from_env() -> ModelRouter:
    """
    Constructs a ModelRouter from environment variables.
    Priority: GROQ_API_KEY → ANTHROPIC_API_KEY → OPENAI_API_KEY
    """
    from agents.llm.groq_provider import GroqProvider
    from agents.llm.anthropic_provider import AnthropicProvider
    from agents.llm.openai_provider import OpenAIProvider

    try:
        from config.settings import settings as _settings
        groq_key      = os.getenv("GROQ_API_KEY")
        groq_base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        anthropic_key = (
            _settings.anthropic_api_key.get_secret_value()
            if getattr(_settings, "anthropic_api_key", None) else None
        )
        openai_key = (
            _settings.openai_api_key.get_secret_value()
            if getattr(_settings, "openai_api_key", None) else None
        )
    except Exception:
        groq_key      = os.getenv("GROQ_API_KEY")
        groq_base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key    = os.getenv("OPENAI_API_KEY")

    if groq_key:
        provider = GroqProvider(api_key=groq_key, base_url=groq_base_url)
        logger.info("LLM router initialized: provider=groq (6-model split routing)")
    elif anthropic_key:
        provider = AnthropicProvider(api_key=anthropic_key)
        logger.info("LLM router initialized: provider=anthropic")
    elif openai_key:
        provider = OpenAIProvider(api_key=openai_key)
        logger.info("LLM router initialized: provider=openai")
    else:
        raise EnvironmentError(
            "No LLM API key configured. Set GROQ_API_KEY, ANTHROPIC_API_KEY, "
            "or OPENAI_API_KEY in your .env file."
        )

    return ModelRouter(provider=provider)


# ── Module-level lazy singleton ───────────────────────────────────────────────
# Agents import `llm_router` directly. The router is built on first access
# so that missing API keys raise at call time, not at import time.
_router_instance: Optional[ModelRouter] = None


def get_llm_router() -> ModelRouter:
    """
    Return the shared ModelRouter singleton, initializing it on first call.
    Raises EnvironmentError if no LLM key is configured.
    """
    global _router_instance
    if _router_instance is None:
        _router_instance = build_router_from_env()
    return _router_instance
