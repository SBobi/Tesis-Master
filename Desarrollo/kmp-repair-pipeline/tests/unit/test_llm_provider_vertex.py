"""Unit tests for LLM provider selection and Vertex model aliasing."""

from __future__ import annotations

import os

from kmp_repair_pipeline.utils.llm_provider import (
    ClaudeProvider,
    FakeLLMProvider,
    VertexProvider,
    get_default_provider,
)


def test_get_default_provider_returns_fake_when_flag_enabled(monkeypatch) -> None:
    monkeypatch.setenv("KMP_LLM_FAKE", "1")
    monkeypatch.setenv("KMP_LLM_PROVIDER", "vertex")

    provider = get_default_provider()

    assert isinstance(provider, FakeLLMProvider)


def test_get_default_provider_returns_vertex_from_env(monkeypatch) -> None:
    monkeypatch.delenv("KMP_LLM_FAKE", raising=False)
    monkeypatch.setenv("KMP_LLM_PROVIDER", "vertex")
    monkeypatch.setenv("KMP_VERTEX_PROJECT", "demo-project")

    provider = get_default_provider(model_id="gemini-fast")

    assert isinstance(provider, VertexProvider)
    assert provider.model_id == "gemini-2.5-flash"


def test_get_default_provider_returns_claude_by_default(monkeypatch) -> None:
    monkeypatch.delenv("KMP_LLM_FAKE", raising=False)
    monkeypatch.delenv("KMP_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("KMP_LLM_MODEL", raising=False)

    provider = get_default_provider()

    assert isinstance(provider, ClaudeProvider)


def test_provider_name_alias_gemini_maps_to_vertex(monkeypatch) -> None:
    monkeypatch.delenv("KMP_LLM_FAKE", raising=False)
    monkeypatch.setenv("KMP_VERTEX_PROJECT", "demo-project")

    provider = get_default_provider(provider_name="gemini", model_id="gemini-fast")

    assert isinstance(provider, VertexProvider)
    assert provider.model_id == "gemini-2.5-flash"


def test_unknown_provider_raises(monkeypatch) -> None:
    monkeypatch.delenv("KMP_LLM_FAKE", raising=False)
    monkeypatch.setenv("KMP_LLM_PROVIDER", "unknown")

    try:
        get_default_provider()
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Unknown LLM provider" in str(exc)
