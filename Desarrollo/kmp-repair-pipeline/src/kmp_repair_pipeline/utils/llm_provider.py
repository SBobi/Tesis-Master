"""LLM provider abstraction for the three thesis agents.

Three implementations:
  - ClaudeProvider  — real Anthropic API (used in production)
  - FakeLLMProvider — deterministic stub (used in unit tests, no network)
  - NoOpProvider    — raises immediately (used to verify tests don't call LLM)

Usage
-----
    from kmp_repair_pipeline.utils.llm_provider import ClaudeProvider, LLMResponse

    provider = ClaudeProvider(model_id="claude-sonnet-4-6")
    resp = provider.complete(prompt="Explain KMP", system="You are a KMP expert.")
    print(resp.content)
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResponse:
    content: str
    model_id: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    stop_reason: str = "end_turn"


class LLMProvider(ABC):
    """Minimal interface used by all three agents."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        ...

    @property
    @abstractmethod
    def model_id(self) -> str:
        ...


# ---------------------------------------------------------------------------
# Claude (Anthropic SDK)
# ---------------------------------------------------------------------------


class ClaudeProvider(LLMProvider):
    """Calls the Anthropic Messages API using the `anthropic` SDK."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model_id: Optional[str] = None) -> None:
        self._model = model_id or os.environ.get("KMP_LLM_MODEL", self.DEFAULT_MODEL)

    @property
    def model_id(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic SDK not installed — run: pip install anthropic>=0.25"
            ) from exc

        client = anthropic.Anthropic()

        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if temperature != 0.0:
            kwargs["temperature"] = temperature

        t0 = time.monotonic()
        response = client.messages.create(**kwargs)
        latency_s = time.monotonic() - t0

        content = response.content[0].text if response.content else ""
        return LLMResponse(
            content=content,
            model_id=response.model,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_s=latency_s,
            stop_reason=response.stop_reason or "end_turn",
        )


# ---------------------------------------------------------------------------
# FakeLLM (deterministic, no network)
# ---------------------------------------------------------------------------


class FakeLLMProvider(LLMProvider):
    """Returns pre-programmed responses; used in unit/integration tests.

    The ``responses`` list is consumed in order. If exhausted, returns the
    ``default`` response. Calling code can inspect ``calls`` to verify prompts.
    """

    def __init__(
        self,
        responses: Optional[list[str]] = None,
        default: str = '{"candidates": []}',
        model: str = "fake-model-1.0",
    ) -> None:
        self._responses = list(responses or [])
        self._default = default
        self._model = model
        self.calls: list[dict] = []

    @property
    def model_id(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append({"prompt": prompt, "system": system})
        content = self._responses.pop(0) if self._responses else self._default
        return LLMResponse(
            content=content,
            model_id=self._model,
            tokens_in=len(prompt) // 4,  # rough estimate
            tokens_out=len(content) // 4,
            latency_s=0.001,
        )


# ---------------------------------------------------------------------------
# NoOpProvider (fails fast in tests that must not call LLM)
# ---------------------------------------------------------------------------


class NoOpProvider(LLMProvider):
    """Raises if called — used to guard tests that must not hit an LLM."""

    @property
    def model_id(self) -> str:
        return "no-op"

    def complete(self, prompt: str, **kwargs) -> LLMResponse:  # type: ignore[override]
        raise AssertionError("LLM provider called unexpectedly in this test")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_default_provider(model_id: Optional[str] = None) -> LLMProvider:
    """Return ClaudeProvider, unless KMP_LLM_FAKE=1 is set (test shortcut)."""
    if os.environ.get("KMP_LLM_FAKE") == "1":
        return FakeLLMProvider()
    return ClaudeProvider(model_id=model_id)
