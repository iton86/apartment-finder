"""
Teacher model providers for POI pre-labeling.

See ``base.py`` for the provider contract and for how to add a provider.
Providers are imported lazily, so an SDK only has to be installed for the
provider you actually use. This module also holds the helpers every caller
shares, so each provider is held to exactly the same checks:

* ``get_provider`` / ``provider_class`` - look a provider up by name.
* ``resolve_model_and_effort``           - flags > TEACHER_* env vars > defaults.
* ``label_safely``                       - call a provider, never raise.
* ``to_record``                          - validate a result into a labels-file line.
"""

import importlib
import logging
import os
from datetime import UTC, datetime

from pydantic import ValidationError

from apartment_finder.domain.poi import PoiExtraction, find_problems
from apartment_finder.infrastructure.teacher.base import Outcome, TeacherOutput, TeacherProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"

# name -> "module:Class"
PROVIDERS = {
    "openai": "apartment_finder.infrastructure.teacher.openai_provider:OpenAIProvider",
    "anthropic": "apartment_finder.infrastructure.teacher.anthropic_provider:AnthropicProvider",
    # Free tier / local — for testing the pipeline and reference labels.
    "gemini": "apartment_finder.infrastructure.teacher.openai_compatible:GeminiProvider",
    "groq": "apartment_finder.infrastructure.teacher.openai_compatible:GroqProvider",
    "ollama": "apartment_finder.infrastructure.teacher.openai_compatible:OllamaProvider",
}


def provider_class(name: str) -> type[TeacherProvider]:
    """Return the provider class registered under ``name``, importing it lazily.

    Raises:
        ValueError: If the name is not a known provider.
    """
    try:
        module_name, class_name = PROVIDERS[name].split(":")
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r} — expected one of {sorted(PROVIDERS)}"
        ) from None
    return getattr(importlib.import_module(module_name), class_name)


def get_provider(name: str, model: str | None = None, effort: str | None = None) -> TeacherProvider:
    """Instantiate the named provider with an optional model and effort override."""
    return provider_class(name)(model=model, effort=effort)


def label_safely(provider: TeacherProvider, row: dict) -> TeacherOutput:
    """Label one row, converting any provider exception into a ``FAILED`` output."""
    try:
        return provider.label(row)
    except Exception as e:  # noqa: BLE001 — any SDK's errors, recorded and retried later
        logger.warning("%s failed on %s: %s", provider.name, row["id"], e)
        return TeacherOutput(Outcome.FAILED, detail=f"{type(e).__name__}: {e}")


def to_record(row: dict, provider: TeacherProvider, output: TeacherOutput) -> dict:
    """Turn one normalized provider result into a labels-file line."""
    record = {
        "id": row["id"],
        "content_hash": row["content_hash"],
        "description": row["description"],
        "provider": provider.name,
        "model": provider.model,
        "labeled_at": datetime.now(UTC).isoformat(),
        "status": "error",
        "pois": None,
        "problems": [output.detail] if output.detail else [],
        "usage": output.usage,
    }
    if output.outcome is Outcome.REFUSED:
        record["status"] = "refused"
        return record
    if output.outcome is Outcome.TRUNCATED:
        record["problems"] = ["hit the output token limit — response truncated"]
        return record
    if output.outcome is Outcome.FAILED:
        return record

    try:
        extraction = PoiExtraction.model_validate_json(output.text or "")
    except ValidationError as e:
        record["problems"] = [f"schema validation failed: {e}"]
        return record

    record["pois"] = extraction.model_dump(mode="json")["pois"]
    record["problems"] = find_problems(extraction, row["description"])
    # "invalid" rows still go to Label Studio — a human fixing one bad
    # evidence quote is cheaper than relabeling the whole ad.
    record["status"] = "invalid" if record["problems"] else "ok"
    return record


def resolve_model_and_effort(
    provider: str, model: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    """Flags win; then TEACHER_MODEL / TEACHER_EFFORT; then provider defaults.

    The env vars belong to TEACHER_PROVIDER: `--provider gemini` with
    TEACHER_MODEL=gpt-5.5 in .env must not send gpt-5.5 to Gemini.
    """
    if provider == os.environ.get("TEACHER_PROVIDER", DEFAULT_PROVIDER):
        model = model or os.environ.get("TEACHER_MODEL") or None
        effort = effort or os.environ.get("TEACHER_EFFORT") or None
    if effort is None:
        effort = provider_class(provider).default_effort
    elif effort == "default":
        effort = None
    return model, effort
