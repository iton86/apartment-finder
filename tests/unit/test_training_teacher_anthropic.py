"""
Tests for the Anthropic teacher adapter: normalizing Messages API output
into TeacherOutput, and the request shape (cacheable system prompt).
"""

import pytest

types = pytest.importorskip("anthropic.types")

from training.teacher.anthropic_provider import AnthropicProvider, message_to_output  # noqa: E402
from training.teacher.base import Outcome  # noqa: E402

ROW = {
    "id": "imot.bg:1",
    "content_hash": "a" * 64,
    "title": "Продава 2-СТАЕН",
    "city": "София",
    "area": "Манастирски ливади",
    "description": "Двустаен до ЮЖЕН ПАРК.",
}


def message(text: str, stop_reason: str = "end_turn"):
    return types.Message.model_construct(
        id="msg_1",
        type="message",
        role="assistant",
        model="claude-opus-5",
        content=[types.TextBlock.model_construct(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=types.Usage.model_construct(input_tokens=10, output_tokens=5),
    )


class TestMessageToOutput:
    def test_complete(self):
        output = message_to_output(message('{"pois": []}'))
        assert output.outcome is Outcome.COMPLETE and output.text == '{"pois": []}'
        assert output.usage["input_tokens"] == 10

    @pytest.mark.parametrize(
        ("stop", "outcome"),
        [("refusal", Outcome.REFUSED), ("max_tokens", Outcome.TRUNCATED)],
    )
    def test_abnormal_stop(self, stop, outcome):
        assert message_to_output(message("", stop)).outcome is outcome


class TestRequestParams:
    def test_system_prompt_is_identical_across_ads_so_it_caches(self):
        provider = AnthropicProvider(effort="medium")
        a = provider.request_params(ROW, "1h")
        b = provider.request_params({**ROW, "description": "друго"}, "1h")
        assert a["system"] == b["system"]
        assert a["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert "друго" in b["messages"][0]["content"]

    def test_effort_none_omits_the_parameter(self):
        with_effort = AnthropicProvider(effort="low").request_params(ROW, "5m")
        without = AnthropicProvider().request_params(ROW, "5m")
        assert with_effort["output_config"]["effort"] == "low"
        assert "effort" not in without["output_config"]
