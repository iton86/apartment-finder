"""
Tests for the free-tier / local teachers (Gemini, Groq, Ollama) built on
OpenAI-compatible endpoints: schema inlining, request shape, pacing, and
failing loudly on a missing key or a batch request.
"""

import json

import pytest

pytest.importorskip("openai")

from training.teacher import openai_compatible as oc  # noqa: E402
from training.teacher.base import BatchNotSupportedError, Outcome, TeacherOutput  # noqa: E402

ROW = {
    "id": "imot.bg:1",
    "content_hash": "a" * 64,
    "title": "Продава 2-СТАЕН",
    "city": "София",
    "area": "Манастирски ливади",
    "description": "Двустаен до ЮЖЕН ПАРК.",
}


class NoSchemaServer(oc.OpenAICompatibleProvider):
    name = "plain-json"
    default_model = "m"
    endpoint = oc.Endpoint("http://localhost:1/v1", None, 0.0, supports_json_schema=False)


class TestInlineRefs:
    def test_no_refs_left_and_enums_inlined(self):
        text = json.dumps(oc.INLINE_SCHEMA)
        assert "$ref" not in text and "$defs" not in text
        poi = oc.INLINE_SCHEMA["properties"]["pois"]["items"]
        assert "park" in poi["properties"]["category"]["enum"]
        assert poi["properties"]["distance"]["properties"]["unit"]["enum"][-1] == "nearby"

    def test_sibling_keywords_next_to_ref_are_kept(self):
        schema = {
            "$defs": {"U": {"type": "string", "enum": ["m"]}},
            "properties": {"u": {"$ref": "#/$defs/U", "description": "unit"}},
        }
        assert oc.inline_refs(schema) == {
            "properties": {"u": {"type": "string", "enum": ["m"], "description": "unit"}}
        }


class TestRequestBody:
    def test_drops_openai_only_parameters(self):
        body = oc.GeminiProvider().request_body(ROW)
        assert "prompt_cache_key" not in body and "max_completion_tokens" not in body
        assert body["max_tokens"] == 16000
        assert "reasoning_effort" not in body  # free tiers send none by default

    def test_json_schema_is_inlined(self):
        fmt = oc.GroqProvider().request_body(ROW)["response_format"]
        assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
        assert fmt["json_schema"]["schema"] == oc.INLINE_SCHEMA

    def test_plain_json_mode_puts_schema_in_prompt(self):
        body = NoSchemaServer().request_body(ROW)
        assert body["response_format"] == {"type": "json_object"}
        assert '"nearby"' in body["messages"][0]["content"]

    def test_explicit_effort_is_passed_through(self):
        assert oc.GeminiProvider(effort="low").request_body(ROW)["reasoning_effort"] == "low"


class TestClient:
    def test_missing_key_names_the_env_var(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            oc.GeminiProvider().client

    def test_local_server_needs_no_key(self):
        assert str(oc.OllamaProvider().client.base_url).startswith("http://localhost:11434")

    def test_free_tier_retries_more(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        assert oc.GroqProvider().client.max_retries == oc.FREE_TIER_MAX_RETRIES


class TestPacing:
    def test_waits_min_interval_between_requests(self, monkeypatch):
        clock = {"now": 100.0}
        sleeps = []
        monkeypatch.setattr(oc.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(oc.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(
            oc.OpenAIProvider, "label", lambda self, row: TeacherOutput(Outcome.COMPLETE, "{}")
        )
        provider = oc.GroqProvider()
        provider.label(ROW)  # first request: no wait
        clock["now"] += 5
        provider.label(ROW)
        assert sleeps == [pytest.approx(15.0)]

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TEACHER_MIN_INTERVAL", "1.5")
        assert oc.GroqProvider().min_interval == 1.5


class TestNoBatch:
    @pytest.mark.parametrize("cls", [oc.GeminiProvider, oc.GroqProvider, oc.OllamaProvider])
    def test_batch_points_to_run(self, cls):
        with pytest.raises(BatchNotSupportedError, match="use `run`"):
            cls().submit_batch([ROW], ".")
