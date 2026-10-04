"""
Tests for the OpenAI teacher adapter: normalizing Chat Completions and
Batch API output lines into TeacherOutput, and the request body shape.
"""

import pytest

pytest.importorskip("openai")

from openai.types.chat import ChatCompletion  # noqa: E402

from training.teacher.base import Outcome  # noqa: E402
from training.teacher.openai_provider import (  # noqa: E402
    RESPONSE_FORMAT,
    OpenAIProvider,
    batch_line_to_output,
    completion_to_output,
)

ROW = {
    "id": "imot.bg:1",
    "content_hash": "a" * 64,
    "title": "Продава 2-СТАЕН",
    "city": "София",
    "area": "Манастирски ливади",
    "description": "Двустаен до ЮЖЕН ПАРК.",
}


def completion_body(
    content: str | None, finish_reason: str = "stop", refusal: str | None = None
) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.5",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def completion(*args, **kwargs) -> ChatCompletion:
    return ChatCompletion.model_validate(completion_body(*args, **kwargs))


class TestCompletionToOutput:
    def test_complete(self):
        output = completion_to_output(completion('{"pois": []}'))
        assert output.outcome is Outcome.COMPLETE and output.text == '{"pois": []}'
        assert output.usage["prompt_tokens"] == 10

    def test_refusal(self):
        output = completion_to_output(completion(None, refusal="I can't help with that"))
        assert output.outcome is Outcome.REFUSED

    @pytest.mark.parametrize(
        ("finish", "outcome"),
        [("content_filter", Outcome.REFUSED), ("length", Outcome.TRUNCATED)],
    )
    def test_abnormal_finish(self, finish, outcome):
        assert completion_to_output(completion('{"pois": [', finish)).outcome is outcome


class TestBatchLine:
    def test_success_line(self):
        line = {
            "custom_id": ROW["content_hash"],
            "response": {"status_code": 200, "body": completion_body('{"pois": []}')},
            "error": None,
        }
        assert batch_line_to_output(line).outcome is Outcome.COMPLETE

    def test_http_error_line(self):
        line = {
            "custom_id": ROW["content_hash"],
            "response": {
                "status_code": 400,
                "body": {"error": {"message": "Unsupported parameter: 'reasoning_effort'"}},
            },
            "error": None,
        }
        output = batch_line_to_output(line)
        assert output.outcome is Outcome.FAILED and "reasoning_effort" in output.detail

    def test_error_file_line(self):
        line = {"custom_id": ROW["content_hash"], "response": None, "error": {"message": "boom"}}
        assert "boom" in batch_line_to_output(line).detail


class TestRequestBody:
    def test_system_prompt_is_identical_across_ads_so_it_caches(self):
        provider = OpenAIProvider(effort="medium")
        a = provider.request_body(ROW)
        b = provider.request_body({**ROW, "description": "друго"})
        assert a["messages"][0] == b["messages"][0]
        assert a["prompt_cache_key"] == b["prompt_cache_key"]
        assert "друго" in b["messages"][1]["content"]

    def test_effort_none_omits_the_parameter(self):
        assert OpenAIProvider(effort="high").request_body(ROW)["reasoning_effort"] == "high"
        assert "reasoning_effort" not in OpenAIProvider("gpt-4.1").request_body(ROW)

    def test_no_credentials_needed_to_construct(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert OpenAIProvider().model == "gpt-5.5"


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _objects(item)


class TestStrictSchema:
    def test_every_object_meets_strict_mode_rules(self):
        # OpenAI strict mode rejects the whole request otherwise: all
        # properties required, no additional properties.
        objects = list(_objects(RESPONSE_FORMAT["json_schema"]["schema"]))
        assert len(objects) == 3  # PoiExtraction, PointOfInterest, Distance
        for obj in objects:
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])
