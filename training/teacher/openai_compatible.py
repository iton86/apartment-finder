"""
Free-tier and local teachers, all through their OpenAI-compatible APIs.

Meant for trying the pipeline end to end and for reference labels to
compare against, not for the final training set: free tiers are slow
(rate-limited), may train on what you send, and their smaller models
follow the labeling rules less reliably. Every response still goes
through the same validation as the paid providers.

    gemini  Google AI Studio free tier. Key: GEMINI_API_KEY from
            aistudio.google.com/apikey (no billing needed).
    groq    Groq free plan. Key: GROQ_API_KEY from console.groq.com/keys.
    ollama  A model running locally (ollama.com). No key, no limits, no
            data leaving the machine; quality depends on what fits the GPU.

Free-tier limits change often and differ per model; the defaults below
were set in October 2026. If you start seeing 429s, raise the pacing
with TEACHER_MIN_INTERVAL (seconds between requests). Your current limits:
aistudio.google.com/rate-limit and console.groq.com/settings/limits.

Adding another OpenAI-compatible service is a subclass with an Endpoint.
"""

import copy
import json
import os
import time
from dataclasses import dataclass
from typing import ClassVar

from openai import OpenAI

from training.schema import PoiExtraction
from training.teacher.base import BatchNotSupportedError, TeacherOutput
from training.teacher.openai_provider import OpenAIProvider
from training.teacher.prompt import SYSTEM_PROMPT

# Retries on 429 (honouring Retry-After) before a request counts as failed.
# Higher than the SDK default of 2, because on a free tier a 429 usually
# means "wait a few seconds", not "something is broken".
FREE_TIER_MAX_RETRIES = 6


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    # Env var holding the key; None for local servers that need none.
    api_key_env: str | None
    # Seconds between requests, to stay under the free tier's per-minute
    # request AND token limits (a request here is ~2–3K tokens).
    min_interval: float
    # False: the server can't enforce a JSON schema, so ask for plain JSON
    # mode and put the schema in the prompt instead.
    supports_json_schema: bool = True


def inline_refs(schema: dict) -> dict:
    """Replace every $ref with the definition it points to.

    Compatible servers translate JSON Schema into their own format, and
    $ref/$defs is the part they most often don't support. Our schema has
    no recursion, so inlining is always possible.
    """
    defs = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].removeprefix("#/$defs/")]
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**copy.deepcopy(target), **extra})
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


INLINE_SCHEMA = inline_refs(PoiExtraction.model_json_schema())


class OpenAICompatibleProvider(OpenAIProvider):
    endpoint: ClassVar[Endpoint]
    # Effort values vary per server and model; send none unless asked.
    default_effort = None

    def __init__(self, model: str | None = None, effort: str | None = None):
        super().__init__(model, effort)
        override = os.environ.get("TEACHER_MIN_INTERVAL")
        self.min_interval = float(override) if override else self.endpoint.min_interval
        self._last_request = 0.0

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            env = self.endpoint.api_key_env
            api_key = os.environ.get(env) if env else "not-needed"
            if not api_key:
                raise RuntimeError(f"{self.name} needs {env} — see training/README.md")
            self._client = OpenAI(
                base_url=self.endpoint.base_url,
                api_key=api_key,
                max_retries=FREE_TIER_MAX_RETRIES,
            )
        return self._client

    def request_body(self, row: dict) -> dict:
        body = super().request_body(row)
        # OpenAI-only parameters some compatible servers reject outright.
        body.pop("prompt_cache_key")
        body["max_tokens"] = body.pop("max_completion_tokens")

        if self.endpoint.supports_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "poi_extraction", "strict": True, "schema": INLINE_SCHEMA},
            }
        else:
            body["response_format"] = {"type": "json_object"}
            body["messages"][0] = {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\n\nRespond with only a JSON object matching this JSON Schema:\n"
                + json.dumps(INLINE_SCHEMA, ensure_ascii=False),
            }
        return body

    def label(self, row: dict) -> TeacherOutput:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return super().label(row)
        finally:
            self._last_request = time.monotonic()

    # Free tiers don't offer OpenAI's Batch API — use the `run` command.
    def submit_batch(self, rows, workdir) -> dict:
        raise BatchNotSupportedError(f"{self.name} has no batch support — use `run`")

    def batch_progress(self, state):
        raise BatchNotSupportedError(self.name)

    def batch_results(self, state):
        raise BatchNotSupportedError(self.name)


class GeminiProvider(OpenAICompatibleProvider):
    name = "gemini"
    # Flash-Lite: the free tier's daily quota is far larger than Flash's
    # (hundreds vs. ~20 requests/day), enough for a few hundred ads.
    default_model = "gemini-3.5-flash-lite"
    endpoint = Endpoint(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        min_interval=6.0,  # ~10 requests/min
    )


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    # One of the few Groq models with strict JSON-schema decoding.
    default_model = "openai/gpt-oss-120b"
    endpoint = Endpoint(
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        # The binding limit is tokens, not requests: 8K tokens/min on the
        # free plan is ~3 of our requests per minute.
        min_interval=20.0,
    )


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"
    # ~4.7 GB at 4-bit: fits the 6 GB GTX 1060 with room for context.
    # Bulgarian is passable, not great — fine for testing the pipeline.
    default_model = "qwen2.5:7b"
    endpoint = Endpoint(
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key_env=None,
        min_interval=0.0,
    )
