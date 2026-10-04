"""
The contract every teacher provider implements — the port, in the app's
clean-architecture terms. label_with_teacher.py only ever talks to this;
each SDK lives behind its own adapter module.

A provider's whole job is: send SYSTEM_PROMPT + one ad, constrained to the
PoiExtraction JSON schema, and report what came back as a TeacherOutput.
Parsing, validation against the ad text, retries and bookkeeping are
shared and live in label_with_teacher.py, so every provider is held to
exactly the same checks.

Adding a provider:
  1. Subclass TeacherProvider in training/teacher/<name>_provider.py.
  2. Implement label(). Implement the three batch methods too if the API
     has a cheaper asynchronous batch endpoint; otherwise leave them and
     use the `run` command, which works with any provider.
  3. Register it in PROVIDERS in training/teacher/__init__.py.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar


class Outcome(StrEnum):
    COMPLETE = "complete"  # model finished; text should hold the JSON
    REFUSED = "refused"  # safety refusal / content filter
    TRUNCATED = "truncated"  # hit the output token limit
    FAILED = "failed"  # request error, expired batch item, ...


@dataclass
class TeacherOutput:
    """One ad's result, normalized across SDKs."""

    outcome: Outcome
    text: str | None = None
    detail: str | None = None
    # Raw provider token counts — field names differ by SDK, kept as-is.
    usage: dict = field(default_factory=dict)


@dataclass
class BatchProgress:
    done: bool
    status: str  # provider's own status word, for logs
    summary: str = ""


class BatchNotSupportedError(NotImplementedError):
    pass


class TeacherProvider(ABC):
    name: ClassVar[str]
    default_model: ClassVar[str]
    # Used when no --effort / TEACHER_EFFORT is given. None sends no effort
    # parameter — the safe choice where accepted values aren't known.
    default_effort: ClassVar[str | None] = None

    def __init__(self, model: str | None = None, effort: str | None = None):
        self.model = model or self.default_model
        # None = don't send an effort/reasoning parameter at all.
        self.effort = effort

    @abstractmethod
    def label(self, row: dict) -> TeacherOutput:
        """Label one ad synchronously."""

    # --- Optional batch API ------------------------------------------------
    # State returned by submit_batch must be JSON-serializable: it is saved
    # to data/teacher_batches.json and handed back to the other two methods,
    # possibly days later in another process.

    def submit_batch(self, rows: Sequence[dict], workdir) -> dict:
        """Start an asynchronous batch. Item ids are each row's content_hash."""
        raise BatchNotSupportedError(f"{self.name} has no batch support — use `run`")

    def batch_progress(self, state: dict) -> BatchProgress:
        raise BatchNotSupportedError(self.name)

    def batch_results(self, state: dict) -> dict[str, TeacherOutput]:
        """Results keyed by content_hash. Missing items are treated as failed."""
        raise BatchNotSupportedError(self.name)
