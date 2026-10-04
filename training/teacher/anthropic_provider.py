"""
Anthropic teacher — Messages API with structured JSON output, plus the
Message Batches API (half price, most batches finish within the hour).

Credentials: ANTHROPIC_API_KEY, or a profile from `ant auth login`; the
SDK finds either on its own.
"""

from collections.abc import Sequence
from pathlib import Path

import anthropic
from anthropic import transform_schema
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from training.schema import PoiExtraction
from training.teacher.base import BatchProgress, Outcome, TeacherOutput, TeacherProvider
from training.teacher.prompt import SYSTEM_PROMPT, user_message

OUTPUT_SCHEMA = transform_schema(PoiExtraction)
# On a safety refusal the API re-runs the request on a fallback model it
# picks by refusal category. Rejected by the Batches API, so only the
# synchronous path uses it; batch refusals are recorded and retried.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def message_to_output(message) -> TeacherOutput:
    """Message or BetaMessage -> TeacherOutput."""
    usage = message.usage.to_dict() if message.usage else {}
    if message.stop_reason == "refusal":
        return TeacherOutput(Outcome.REFUSED, detail=str(message.stop_details), usage=usage)
    if message.stop_reason == "max_tokens":
        return TeacherOutput(Outcome.TRUNCATED, usage=usage)
    text = next((b.text for b in message.content if b.type == "text"), None)
    return TeacherOutput(Outcome.COMPLETE, text=text, usage=usage)


class AnthropicProvider(TeacherProvider):
    name = "anthropic"
    default_model = "claude-opus-5"
    default_effort = "medium"

    def __init__(self, model: str | None = None, effort: str | None = None):
        super().__init__(model, effort)
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def request_params(self, row: dict, cache_ttl: str) -> MessageCreateParamsNonStreaming:
        output_config: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
        if self.effort is not None:
            output_config["effort"] = self.effort
        return MessageCreateParamsNonStreaming(
            model=self.model,
            max_tokens=16000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": cache_ttl},
                }
            ],
            messages=[{"role": "user", "content": user_message(row)}],
            output_config=output_config,
        )

    def label(self, row: dict) -> TeacherOutput:
        # 5-minute TTL: sequential calls arrive well within it.
        message = self.client.beta.messages.create(
            **self.request_params(row, "5m"),
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        return message_to_output(message)

    def submit_batch(self, rows: Sequence[dict], workdir: Path) -> dict:
        # custom_id must match ^[a-zA-Z0-9_-]{1,64}$ — the 64-char hex
        # content hash fits exactly. 1-hour TTL: batch items run over a
        # longer window than 5 minutes and all share the system prompt.
        batch = self.client.messages.batches.create(
            requests=[
                Request(custom_id=row["content_hash"], params=self.request_params(row, "1h"))
                for row in rows
            ]
        )
        return {"batch_id": batch.id}

    def batch_progress(self, state: dict) -> BatchProgress:
        batch = self.client.messages.batches.retrieve(state["batch_id"])
        c = batch.request_counts
        summary = f"processing={c.processing} succeeded={c.succeeded} errored={c.errored}"
        return BatchProgress(batch.processing_status == "ended", batch.processing_status, summary)

    def batch_results(self, state: dict) -> dict[str, TeacherOutput]:
        results = {}
        # Results arrive in any order — keyed by custom_id, never position.
        for result in self.client.messages.batches.results(state["batch_id"]):
            if result.result.type == "succeeded":
                results[result.custom_id] = message_to_output(result.result.message)
            else:  # errored / canceled / expired
                results[result.custom_id] = TeacherOutput(Outcome.FAILED, detail=result.result.type)
        return results
