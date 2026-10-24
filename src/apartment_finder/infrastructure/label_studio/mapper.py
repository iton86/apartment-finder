from __future__ import annotations

import sys
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from apartment_finder.domain.labeling import (
    LabelingTask,
    LabelStudioTaskPayload,
    LabelStudioTaskState,
    TeacherStatus,
)

try:
    from training.label_studio import (
        TEXT,
        annotation_to_extraction,
        prediction_results,
    )
    from training.schema import find_problems
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from training.label_studio import (
        TEXT,
        annotation_to_extraction,
        prediction_results,
    )
    from training.schema import find_problems


def task_payload(task: LabelingTask) -> LabelStudioTaskPayload:
    notes: list[str] = []
    predictions: list[dict] = []
    if task.teacher_result is not None and task.teacher_status in {
        TeacherStatus.OK,
        TeacherStatus.INVALID,
    }:
        result, unplaceable = prediction_results(task.teacher_result, task.description_snapshot)
        notes.extend(unplaceable)
        notes.extend(task.teacher_problems)
        predictions.append(
            {
                "model_version": _model_version(task),
                "result": result,
            }
        )
    elif task.teacher_status is not TeacherStatus.MISSING:
        notes.extend(task.teacher_problems or [f"Teacher status: {task.teacher_status.value}"])
    else:
        notes.append("No teacher label available; annotate from scratch.")

    return LabelStudioTaskPayload(
        task_id=task.id,
        data={
            TEXT: task.description_snapshot,
            "title": f"Listing {task.listing_id}",
            "meta": (
                f"Listing: {task.listing_id} | Task: {task.id} | "
                f"Batch: {task.selection_batch_id or 'unassigned'}"
            ),
            "review_notes": "\n".join(notes),
            "labeling_task_id": str(task.id),
            "listing_id": str(task.listing_id),
            "content_hash": task.content_hash,
            "selection_batch_id": str(task.selection_batch_id) if task.selection_batch_id else "",
            "teacher_provider": task.teacher_provider or "",
            "teacher_model": task.teacher_model or "",
            "teacher_prompt_version": task.teacher_prompt_version or "",
        },
        predictions=predictions,
    )


def validate_prediction_offsets(task: LabelingTask) -> list[str]:
    if task.teacher_result is None:
        return []
    _result, notes = prediction_results(task.teacher_result, task.description_snapshot)
    return notes


def saved_annotation_state(state: LabelStudioTaskState) -> dict | None:
    annotations = [
        annotation
        for annotation in state.annotations
        if not annotation.get("was_cancelled") and not annotation.get("deleted")
    ]
    if not annotations:
        return None
    return max(
        annotations,
        key=lambda annotation: (
            annotation.get("updated_at") or "",
            annotation.get("id") or 0,
        ),
    )


def prediction_annotation_payload(state: LabelStudioTaskState) -> list[dict] | None:
    if saved_annotation_state(state) is not None:
        return None
    if not state.predictions:
        return None
    latest = max(
        state.predictions,
        key=lambda prediction: (
            prediction.get("created_at") or "",
            prediction.get("id") or 0,
        ),
    )
    return latest.get("result") or []


def annotation_revision(annotation: dict) -> int:
    seed = f"{annotation.get('id') or ''}:{annotation.get('updated_at') or ''}"
    return int(sha256(seed.encode("utf-8")).hexdigest()[:12], 16)


def validate_annotation_result(result: list[dict], text: str) -> tuple[list[dict], list[str]]:
    try:
        extraction = annotation_to_extraction({"result": result}, text)
    except (ValidationError, KeyError, IndexError, TypeError) as exc:
        return [], [f"invalid Label Studio annotation: {exc}"]
    problems = find_problems(extraction, text)
    if problems:
        return [], problems
    return extraction.model_dump(mode="json")["pois"], []


def state_matches_task(state: LabelStudioTaskState, task: LabelingTask) -> bool:
    return (
        state.data.get("labeling_task_id") == str(task.id)
        and state.data.get("content_hash") == task.content_hash
        and state.data.get(TEXT) == task.description_snapshot
    )


def _model_version(task: LabelingTask) -> str:
    parts = [
        task.teacher_provider or "unknown-provider",
        task.teacher_model or "unknown-model",
        task.teacher_prompt_version or "unknown-prompt",
    ]
    return "/".join(parts)
