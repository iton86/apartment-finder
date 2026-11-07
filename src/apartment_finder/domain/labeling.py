"""Domain objects for human-labeling workflows.

The domain owns the stable meaning of a labeling task: a snapshot of a
listing description reserved for future annotation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

MIN_DESCRIPTION_LENGTH = 80


class LabelingStatus(StrEnum):
    """Lifecycle of a labeling task, from reservation to approved training data."""

    PENDING = "pending"
    EXPORTED = "exported"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class LabelingExportStatus(StrEnum):
    """Whether the task has been pushed to Label Studio."""

    NOT_EXPORTED = "not_exported"
    EXPORTED = "exported"
    FAILED = "failed"


class LabelingSyncStatus(StrEnum):
    """Whether the task's annotation was pulled back from Label Studio."""

    NOT_SYNCED = "not_synced"
    SYNCED = "synced"
    FAILED = "failed"


class TeacherStatus(StrEnum):
    """Outcome of asking the teacher model to pre-annotate a task."""

    MISSING = "missing"
    OK = "ok"
    INVALID = "invalid"
    REFUSED = "refused"
    ERROR = "error"


@dataclass(frozen=True)
class LabelingTask:
    """A listing description snapshot moving through teacher, human review and approval.

    The snapshot and its hash pin the exact text that was labeled, so later changes to the
    listing cannot silently invalidate an annotation.
    """

    id: UUID
    listing_id: UUID
    content_hash: str
    description_snapshot: str
    status: LabelingStatus = LabelingStatus.PENDING
    selection_score: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    selection_batch_id: UUID | None = None
    label_studio_project_id: int | None = None
    label_studio_task_id: int | None = None
    teacher_provider: str | None = None
    teacher_model: str | None = None
    teacher_prompt_version: str | None = None
    teacher_status: TeacherStatus = TeacherStatus.MISSING
    teacher_result: list[dict] | None = None
    teacher_problems: list[str] = field(default_factory=list)
    human_annotation_id: int | None = None
    human_result: list[dict] | None = None
    human_revision: int | None = None
    approved_revision: int | None = None
    approved_result: list[dict] | None = None

    @classmethod
    def create(
        cls,
        *,
        listing_id: UUID,
        description: str,
        selection_score: float | None = None,
    ) -> "LabelingTask":
        """Create a pending task from a listing description.

        Raises:
            ValueError: If the whitespace-normalized description is shorter than
                ``MIN_DESCRIPTION_LENGTH``.
        """
        description_snapshot = normalize_description(description)
        if len(description_snapshot) < MIN_DESCRIPTION_LENGTH:
            raise ValueError("description must be at least 80 characters after normalization")
        return cls(
            id=uuid4(),
            listing_id=listing_id,
            content_hash=description_content_hash(description_snapshot),
            description_snapshot=description_snapshot,
            selection_score=selection_score,
        )


def normalize_description(description: str | None) -> str:
    """Collapse all whitespace runs to single spaces; None becomes an empty string."""
    return " ".join((description or "").split())


def description_content_hash(description: str) -> str:
    """Return the SHA-256 hex digest of the normalized description."""
    normalized = normalize_description(description)
    return sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LabelingBatch:
    """A group of tasks exported to one Label Studio project and approved together."""

    id: UUID
    label_studio_project_id: int
    created_at: datetime | None = None
    approved_at: datetime | None = None
    approval_actor: str | None = None


@dataclass(frozen=True)
class TeacherAnnotation:
    """A teacher model's pre-annotation of a task, with provenance and validation problems."""

    provider: str
    model: str
    prompt_version: str
    status: TeacherStatus
    result: list[dict] | None
    problems: list[str] = field(default_factory=list)
    labeled_at: datetime | None = None


@dataclass(frozen=True)
class LabelStudioTaskPayload:
    """A task ready to import into Label Studio: its data and teacher predictions."""

    task_id: UUID
    data: dict
    predictions: list[dict]


@dataclass(frozen=True)
class LabelStudioTaskState:
    """A task as currently stored in Label Studio, with its predictions and annotations."""

    task_id: int
    data: dict
    predictions: list[dict] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    updated_at: datetime | None = None
