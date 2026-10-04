"""
OpenAI teacher — Chat Completions with strict JSON-schema output, plus
the Batch API (half price, completes within 24 h).

Also works with any OpenAI-compatible server (Ollama, vLLM, LM Studio,
OpenRouter, ...): set OPENAI_BASE_URL and OPENAI_API_KEY, which the SDK
reads from the environment. Those servers usually lack the Batch API —
use the `run` command — and may not enforce the schema strictly;
label_with_teacher validates every response either way.

Credentials: OPENAI_API_KEY from platform.openai.com. A ChatGPT
subscription does not include API access.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI
from openai.types.chat import ChatCompletion

from training.jsonl import write_jsonl
from training.schema import PoiExtraction
from training.teacher.base import BatchProgress, Outcome, TeacherOutput, TeacherProvider
from training.teacher.prompt import SYSTEM_PROMPT, user_message

CHAT_COMPLETIONS = "/v1/chat/completions"

# Strict mode makes the API guarantee schema-valid JSON. It requires every
# object to list all its properties as required and forbid extras — which
# the pydantic models already do (no defaults, extra="forbid");
# test_training_teacher_openai checks that stays true.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "poi_extraction",
        "strict": True,
        "schema": PoiExtraction.model_json_schema(),
    },
}
# Routes requests sharing the system prompt to the same cache.
PROMPT_CACHE_KEY = "apartment-finder-poi-teacher"
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}


def completion_to_output(completion: ChatCompletion) -> TeacherOutput:
    usage = completion.usage.model_dump(exclude_none=True) if completion.usage else {}
    choice = completion.choices[0]
    if choice.message.refusal or choice.finish_reason == "content_filter":
        detail = choice.message.refusal or choice.finish_reason
        return TeacherOutput(Outcome.REFUSED, detail=detail, usage=usage)
    if choice.finish_reason == "length":
        return TeacherOutput(Outcome.TRUNCATED, usage=usage)
    return TeacherOutput(Outcome.COMPLETE, text=choice.message.content, usage=usage)


def batch_line_to_output(line: dict) -> TeacherOutput:
    """One line of a batch output or error file -> TeacherOutput."""
    response = line.get("response") or {}
    if line.get("error") or response.get("status_code") != 200:
        error = line.get("error") or (response.get("body") or {}).get("error") or {}
        message = error.get("message") or f"HTTP {response.get('status_code')}"
        return TeacherOutput(Outcome.FAILED, detail=f"batch request failed: {message}")
    return completion_to_output(ChatCompletion.model_validate(response["body"]))


class OpenAIProvider(TeacherProvider):
    name = "openai"
    default_model = "gpt-5.5"
    default_effort = "medium"

    def __init__(self, model: str | None = None, effort: str | None = None):
        super().__init__(model, effort)
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        # Created on first use, so constructing a provider (e.g. in tests)
        # doesn't require credentials.
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def request_body(self, row: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(row)},
            ],
            "response_format": RESPONSE_FORMAT,
            # Reasoning models spend part of this on hidden reasoning; too
            # low and the JSON gets cut off (finish_reason "length").
            "max_completion_tokens": 16000,
            "prompt_cache_key": PROMPT_CACHE_KEY,
        }
        # Non-reasoning models reject reasoning_effort.
        if self.effort is not None:
            body["reasoning_effort"] = self.effort
        return body

    def label(self, row: dict) -> TeacherOutput:
        return completion_to_output(self.client.chat.completions.create(**self.request_body(row)))

    def submit_batch(self, rows: Sequence[dict], workdir: Path) -> dict:
        # The Batch API takes requests as an uploaded JSONL file, kept on
        # disk so a failed batch can be inspected.
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        input_path = Path(workdir) / f"teacher_batch_input_{stamp}.jsonl"
        write_jsonl(
            input_path,
            (
                {
                    "custom_id": row["content_hash"],
                    "method": "POST",
                    "url": CHAT_COMPLETIONS,
                    "body": self.request_body(row),
                }
                for row in rows
            ),
        )
        with input_path.open("rb") as f:
            uploaded = self.client.files.create(file=f, purpose="batch")
        batch = self.client.batches.create(
            input_file_id=uploaded.id,
            endpoint=CHAT_COMPLETIONS,
            completion_window="24h",
            metadata={"purpose": "apartment-finder POI teacher labels"},
        )
        return {"batch_id": batch.id, "input_file": str(input_path)}

    def batch_progress(self, state: dict) -> BatchProgress:
        batch = self.client.batches.retrieve(state["batch_id"])
        counts = batch.request_counts
        summary = (
            f"{counts.completed}/{counts.total} done, {counts.failed} failed" if counts else ""
        )
        if batch.status == "failed" and batch.errors:
            # Whole-batch failure (usually input validation): nothing ran.
            summary += " | " + "; ".join(str(e.message) for e in batch.errors.data or [])
        return BatchProgress(batch.status in TERMINAL_STATUSES, batch.status, summary)

    def batch_results(self, state: dict) -> dict[str, TeacherOutput]:
        batch = self.client.batches.retrieve(state["batch_id"])
        # Expired and cancelled batches still return the requests that
        # finished; the rest are simply absent and count as failed.
        lines = []
        for file_id in (batch.output_file_id, batch.error_file_id):
            if file_id:
                text = self.client.files.content(file_id).text
                lines += [json.loads(line) for line in text.splitlines() if line.strip()]
        return {line["custom_id"]: batch_line_to_output(line) for line in lines}
