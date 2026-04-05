"""LLM provider abstraction for the three thesis agents.

Implementations:
    - ClaudeProvider  — Anthropic Messages API
    - VertexProvider  — Vertex AI Gemini via google-genai
    - FakeLLMProvider — deterministic stub (used in tests, no network)
    - NoOpProvider    — raises immediately (used to verify tests don't call LLM)

Usage
-----
    from kmp_repair_pipeline.utils.llm_provider import get_default_provider, LLMResponse

    provider = get_default_provider(model_id="gemini-fast", provider_name="vertex")
    resp = provider.complete(prompt="Explain KMP", system="You are a KMP expert.")
    print(resp.content)
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


_DEFAULT_PROVIDER = "anthropic"
_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "vertex": "vertex",
    "gemini": "vertex",
}
_MODEL_ALIASES = {
    "gemini-fast": "gemini-2.5-flash",
}


def _normalize_model_id(model_id: str) -> str:
    return _MODEL_ALIASES.get(model_id.strip().lower(), model_id)


def _resolve_provider_name(provider_name: Optional[str] = None) -> str:
    requested = (provider_name or os.environ.get("KMP_LLM_PROVIDER", _DEFAULT_PROVIDER)).strip().lower()
    resolved = _PROVIDER_ALIASES.get(requested)
    if not resolved:
        valid = ", ".join(sorted(set(_PROVIDER_ALIASES.keys())))
        raise ValueError(f"Unknown LLM provider {requested!r}. Valid values: {valid}")
    return resolved


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
        self._model = _normalize_model_id(model_id or os.environ.get("KMP_LLM_MODEL", self.DEFAULT_MODEL))

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
# Vertex AI Gemini (google-genai SDK)
# ---------------------------------------------------------------------------


class VertexProvider(LLMProvider):
    """Calls Vertex AI Gemini through the `google-genai` SDK."""

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        model_id: Optional[str] = None,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        self._model = _normalize_model_id(model_id or os.environ.get("KMP_LLM_MODEL", self.DEFAULT_MODEL))
        self._project_id = (
            project_id
            or os.environ.get("KMP_VERTEX_PROJECT")
            or os.environ.get("GCP_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
        self._location = (
            location
            or os.environ.get("KMP_VERTEX_LOCATION")
            or os.environ.get("GEMINI_REGION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
            or "us-central1"
        )

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
        if not self._project_id:
            raise RuntimeError(
                "Vertex provider requires KMP_VERTEX_PROJECT, GCP_PROJECT_ID, or GOOGLE_CLOUD_PROJECT"
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai SDK not installed — run: pip install google-genai>=0.8"
            ) from exc

        client = genai.Client(
            vertexai=True,
            project=self._project_id,
            location=self._location,
        )

        config_kwargs: dict = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            config_kwargs["system_instruction"] = system

        t0 = time.monotonic()
        response = client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        latency_s = time.monotonic() - t0

        content = getattr(response, "text", "") or _extract_vertex_text(response)
        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
        stop_reason = _extract_vertex_stop_reason(response)

        resolved_model = (
            getattr(response, "model_version", None)
            or getattr(response, "model", None)
            or self._model
        )

        return LLMResponse(
            content=content,
            model_id=str(resolved_model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_s=latency_s,
            stop_reason=stop_reason,
        )


def _extract_vertex_text(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    texts: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = getattr(part, "text", None)
            if part_text:
                texts.append(str(part_text))
    return "\n".join(texts).strip()


def _extract_vertex_stop_reason(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "end_turn"
    finish_reason = getattr(candidates[0], "finish_reason", None)
    if finish_reason is None:
        return "end_turn"
    name = getattr(finish_reason, "name", None)
    if name:
        return str(name).lower()
    return str(finish_reason).lower()


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


def get_default_provider(
    model_id: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> LLMProvider:
    """Return the configured provider, unless KMP_LLM_FAKE=1 is set."""
    if os.environ.get("KMP_LLM_FAKE") == "1":
        return FakeLLMProvider()

    resolved_provider = _resolve_provider_name(provider_name)
    if resolved_provider == "vertex":
        return VertexProvider(model_id=model_id)
    return ClaudeProvider(model_id=model_id)
