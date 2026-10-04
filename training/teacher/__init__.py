"""
Teacher model providers for POI pre-labeling. See base.py for the
contract and for how to add a provider.

Providers are imported lazily, so an SDK only has to be installed for
the provider you actually use.
"""

import importlib

from training.teacher.base import TeacherProvider

# name -> "module:Class"
PROVIDERS = {
    "openai": "training.teacher.openai_provider:OpenAIProvider",
    "anthropic": "training.teacher.anthropic_provider:AnthropicProvider",
    # Free tier / local — for testing the pipeline and reference labels.
    "gemini": "training.teacher.openai_compatible:GeminiProvider",
    "groq": "training.teacher.openai_compatible:GroqProvider",
    "ollama": "training.teacher.openai_compatible:OllamaProvider",
}


def provider_class(name: str) -> type[TeacherProvider]:
    try:
        module_name, class_name = PROVIDERS[name].split(":")
    except KeyError:
        raise ValueError(
            f"Unknown provider {name!r} — expected one of {sorted(PROVIDERS)}"
        ) from None
    return getattr(importlib.import_module(module_name), class_name)


def get_provider(name: str, model: str | None = None, effort: str | None = None) -> TeacherProvider:
    return provider_class(name)(model=model, effort=effort)
