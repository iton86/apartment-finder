"""
Label Studio contract: how a POI extraction is drawn, reviewed and read back.

Each POI is one highlighted span of the ad (the span IS the evidence), labeled
with its category. Clicking a span shows three per-region fields: name, unit
and value. That maps 1:1 onto ``PointOfInterest``, so nothing is lost converting
either way.

This module is pure (no HTTP, no database) so both the application use cases
and the offline training tools can share it:

* ``labeling_config``              - the project's XML labeling interface.
* ``prediction_results``           - teacher POIs -> Label Studio regions.
* ``annotation_to_extraction``     - saved annotation -> ``PoiExtraction``.
* ``task_payload`` and friends     - persisted ``LabelingTask`` <-> Label Studio task.
"""

from __future__ import annotations

import json
from hashlib import sha256
from xml.sax.saxutils import quoteattr

from pydantic import ValidationError

from apartment_finder.domain.labeling import (
    LabelingTask,
    LabelStudioTaskPayload,
    LabelStudioTaskState,
    TeacherStatus,
)
from apartment_finder.domain.poi import (
    DistanceUnit,
    PoiCategory,
    PoiExtraction,
    find_evidence,
    find_problems,
)

TEXT = "text"
CATEGORY = "category"
NAME = "name"
UNIT = "unit"
VALUE = "value"

# Distinct enough to tell neighbouring spans apart; cycles past the end.
_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4", "#46f0f0",
    "#f032e6", "#bcf60c", "#fabebe", "#008080", "#e6beff", "#9a6324", "#fffac8",
    "#800000", "#aaffc3", "#808000", "#ffd8b1", "#000075", "#808080", "#a9a9a9",
]  # fmt: skip


def labeling_config() -> str:
    """Build the Label Studio labeling-interface XML from the POI categories and units."""
    labels = "\n".join(
        f"    <Label value={quoteattr(c.value)} background={quoteattr(_COLORS[i % len(_COLORS)])}/>"
        for i, c in enumerate(PoiCategory)
    )
    units = "\n".join(f"          <Choice value={quoteattr(u.value)}/>" for u in DistanceUnit)
    return f"""<View>
  <Header value="$title"/>
  <Text name="meta" value="$meta"/>
  <Labels name="{CATEGORY}" toName="{TEXT}" showInline="true">
{labels}
  </Labels>
  <View style="display: flex; align-items: flex-start; gap: 1.5em">
    <View style="flex: 2">
      <Text name="{TEXT}" value="$text"/>
    </View>
    <View style="flex: 1; position: sticky; top: 0">
      <View visibleWhen="region-selected">
        <Header value="Selected POI" size="5"/>
        <TextArea name="{NAME}" toName="{TEXT}" perRegion="true" maxSubmissions="1"
                  editable="true" transcription="true"
                  placeholder="Required, as in the ad: Южен парк, 33 СУ, автобус 204"/>
        <Choices name="{UNIT}" toName="{TEXT}" perRegion="true" required="true"
                 choice="single-radio">
{units}
        </Choices>
        <Number name="{VALUE}" toName="{TEXT}" perRegion="true" min="0" step="0.1"/>
      </View>
      <Text name="review_notes" value="$review_notes"/>
    </View>
  </View>
</View>
"""


def prediction_results(pois: list[dict], text: str) -> tuple[list[dict], list[str]]:
    """Teacher POIs -> Label Studio regions. Unplaceable ones become notes."""
    results, notes = [], []
    for i, poi in enumerate(pois):
        span = find_evidence(poi["evidence"], text)
        if span is None:
            poi_json = json.dumps(poi, ensure_ascii=False)
            notes.append(f"Teacher POI not drawn (evidence not in text): {poi_json}")
            continue
        start, end = span
        region = {"start": start, "end": end, "text": text[start:end]}
        region_id = f"t{i}"
        results.append(
            {
                "id": region_id,
                "from_name": CATEGORY,
                "to_name": TEXT,
                "type": "labels",
                "value": {**region, "labels": [poi["category"]]},
            }
        )
        results.append(
            {
                "id": region_id,
                "from_name": UNIT,
                "to_name": TEXT,
                "type": "choices",
                "value": {**region, "choices": [poi["distance"]["unit"]]},
            }
        )
        if poi["name"]:
            # Per-region TextArea results keep the typed text under "text",
            # shadowing the region's own text key.
            results.append(
                {
                    "id": region_id,
                    "from_name": NAME,
                    "to_name": TEXT,
                    "type": "textarea",
                    "value": {"start": start, "end": end, "text": [poi["name"]]},
                }
            )
        if poi["distance"]["value"] is not None:
            results.append(
                {
                    "id": region_id,
                    "from_name": VALUE,
                    "to_name": TEXT,
                    "type": "number",
                    "value": {**region, "number": poi["distance"]["value"]},
                }
            )
    return results, notes


def annotation_to_extraction(annotation: dict, text: str) -> PoiExtraction:
    """Convert a saved annotation into a ``PoiExtraction``, with POIs in text order.

    Raises:
        pydantic.ValidationError: If the regions do not form a valid extraction.
        KeyError, IndexError, TypeError: If the annotation result is malformed.
    """
    regions: dict[str, dict] = {}
    for result in annotation["result"]:
        regions.setdefault(result["id"], {})[result["from_name"]] = result["value"]

    pois = []
    for parts in regions.values():
        span = parts.get(CATEGORY)
        if span is None:  # per-region field whose span was deleted
            continue
        name = (parts.get(NAME, {}).get("text") or [""])[0].strip()
        unit = (parts.get(UNIT, {}).get("choices") or [None])[0]
        pois.append(
            (
                span["start"],
                {
                    "category": span["labels"][0],
                    # Empty stays "", so find_problems names the actual
                    # problem instead of a bare type error.
                    "name": name,
                    "distance": {"value": parts.get(VALUE, {}).get("number"), "unit": unit},
                    # Slice the source text rather than trusting value.text,
                    # so evidence always matches the ad byte for byte.
                    "evidence": text[span["start"] : span["end"]],
                },
            )
        )

    # Text order — a stable, learnable output order for the student model.
    # POIs sharing one span ("между Южен парк и НДК") go by where their
    # name appears inside it.
    def text_order(item: tuple[int, dict]) -> tuple[int, int, str]:
        start, poi = item
        name_at = find_evidence(poi["name"], poi["evidence"]) if poi["name"] else None
        return start, name_at[0] if name_at else 0, poi["category"]

    pois.sort(key=text_order)
    return PoiExtraction.model_validate({"pois": [p for _, p in pois]})


def task_payload(task: LabelingTask) -> LabelStudioTaskPayload:
    """Build the Label Studio task for a labeling task, with the teacher result as a prediction.

    Problems and undrawable POIs go into ``review_notes`` so the reviewer sees them.
    """
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
    """Return notes for teacher POIs whose evidence cannot be located in the task text."""
    if task.teacher_result is None:
        return []
    _result, notes = prediction_results(task.teacher_result, task.description_snapshot)
    return notes


def saved_annotation_state(state: LabelStudioTaskState) -> dict | None:
    """Return the latest annotation that is neither cancelled nor deleted, or None."""
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
    """Return the latest prediction's result, only if the task has no saved annotation.

    Used to promote an unedited teacher prediction to an annotation on approval.
    """
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
    """Derive a stable integer revision from the annotation's ID and update time.

    It changes whenever the annotation is edited, so later edits can be detected.
    """
    seed = f"{annotation.get('id') or ''}:{annotation.get('updated_at') or ''}"
    return int(sha256(seed.encode("utf-8")).hexdigest()[:12], 16)


def validate_annotation_result(result: list[dict], text: str) -> tuple[list[dict], list[str]]:
    """Convert and check an annotation result against the task text.

    Returns:
        ``(pois, [])`` as JSON-ready dicts when valid, otherwise ``([], problems)``.
    """
    try:
        extraction = annotation_to_extraction({"result": result}, text)
    except (ValidationError, KeyError, IndexError, TypeError) as exc:
        return [], [f"invalid Label Studio annotation: {exc}"]
    problems = find_problems(extraction, text)
    if problems:
        return [], problems
    return extraction.model_dump(mode="json")["pois"], []


def state_matches_task(state: LabelStudioTaskState, task: LabelingTask) -> bool:
    """Return whether the Label Studio task still carries the snapshot selected for labeling.

    Guards against reviewing or approving a task whose text changed after export.
    """
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
