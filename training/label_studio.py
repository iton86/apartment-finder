"""
Step 3b of the POI pipeline: human review in Label Studio.

Each POI is one highlighted span of the ad — the span IS the evidence —
labeled with its category. Clicking a span shows three per-region fields:
name, unit, value. The contract itself lives in apartment_finder.domain.label_studio; this
module is the file-based (legacy) import/export around it.

    config       print the labeling config to paste into the project
    to-tasks     raw ads + teacher labels -> Label Studio import file,
                 with the teacher's POIs pre-drawn as predictions
    from-export  Label Studio JSON export -> data/labels.jsonl

    uv run python -m training.label_studio config > data/label_studio_config.xml
    uv run python -m training.label_studio to-tasks
    uv run python -m training.label_studio from-export data/ls_export.json

Gold rows (split == "gold") are the held-out test set. They are chosen
by a hash of the ad text, so the same ad stays gold across re-exports
and new ads never reshuffle the set.
"""

import argparse
import hashlib
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from apartment_finder.domain.label_studio import (
    TEXT,
    annotation_to_extraction,
    labeling_config,
    prediction_results,
)
from apartment_finder.domain.poi import find_problems
from apartment_finder.infrastructure.jsonl import read_jsonl, write_jsonl
from apartment_finder.interface.logging_config import configure_logging

logger = logging.getLogger(__name__)


def is_gold(content_hash: str, gold_percent: int) -> bool:
    """Return whether an ad belongs to the gold set, from a stable hash of its content hash."""
    bucket = int(hashlib.sha256(f"gold:{content_hash}".encode()).hexdigest()[:8], 16) % 100
    return bucket < gold_percent


def build_tasks(raw_rows: list[dict], teacher_rows: list[dict], gold_percent: int) -> list[dict]:
    """Build Label Studio import tasks from raw ads, pre-filled with teacher predictions.

    Each task is assigned a gold/train split and carries review notes about the
    teacher's result (or its absence).
    """
    # Last line wins: a retry appended after an error supersedes it.
    teacher = {r["content_hash"]: r for r in teacher_rows}
    tasks = []
    for row in raw_rows:
        label = teacher.get(row["content_hash"])
        notes = []
        predictions = []
        if label and label["pois"] is not None:
            results, notes = prediction_results(label["pois"], row["description"])
            notes.extend(label["problems"])
            predictions.append({"model_version": label["model"], "result": results})
        elif label:
            notes.append(f"Teacher {label['status']}: {'; '.join(label['problems'])}")
        else:
            notes.append("No teacher label — annotate from scratch.")

        split = "gold" if is_gold(row["content_hash"], gold_percent) else "train"
        tasks.append(
            {
                "data": {
                    TEXT: row["description"],
                    "title": row["title"],
                    "meta": f"{split.upper()} | {row['city']} / {row['area']} | {row['url']}",
                    "review_notes": "\n".join(notes),
                    "id": row["id"],
                    "content_hash": row["content_hash"],
                    "split": split,
                },
                "predictions": predictions,
            }
        )
    return tasks


def convert_export(
    tasks: list[dict], teacher_rows: list[dict], include_teacher: bool
) -> tuple[list[dict], list[str]]:
    """Convert a Label Studio JSON export into labeled rows.

    Uses the latest non-cancelled annotation per task; invalid ones are reported in
    the rejected list. With ``include_teacher``, unreviewed train rows fall back to
    clean teacher labels. One row is kept per ad, preferring human and newer labels.

    Returns:
        The labeled rows and human-readable rejection reasons.

    Raises:
        ValueError: If the export is not a list of tasks with the required data fields.
    """
    if not isinstance(tasks, list):
        raise ValueError("Expected a Label Studio JSON export containing a list of tasks")
    required = ("id", "content_hash", TEXT, "split")
    for index, task in enumerate(tasks, 1):
        data = task.get("data") if isinstance(task, dict) else None
        if not isinstance(data, dict):
            raise ValueError(f"Export task {index}: missing data object")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(
                f"Export task {index}: missing data fields: {', '.join(missing)}. "
                "Import the generated ls_tasks JSON into Label Studio, review the tasks, "
                "then export that project using Export > JSON. "
                "The labeling-interface preview is not a training task."
            )
    teacher = {r["content_hash"]: r for r in teacher_rows}
    rows, rejected = [], []
    for task in tasks:
        data = task["data"]
        annotations = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        base = {
            "id": data["id"],
            "content_hash": data["content_hash"],
            "description": data[TEXT],
            "split": data["split"],
        }
        if annotations:
            latest = max(annotations, key=lambda a: a.get("updated_at") or "")
            try:
                extraction = annotation_to_extraction(latest, data[TEXT])
            except ValidationError as e:
                rejected.append(f"task {task.get('id')} ({data['id']}): {e.errors()[0]['msg']}")
                continue
            problems = find_problems(extraction, data[TEXT])
            if problems:
                rejected.append(f"task {task.get('id')} ({data['id']}): {'; '.join(problems)}")
                continue
            rows.append(
                {
                    **base,
                    "label_source": "human",
                    "annotator": latest.get("completed_by"),
                    "annotated_at": latest.get("updated_at"),
                    "pois": extraction.model_dump(mode="json")["pois"],
                }
            )
        elif include_teacher and data["split"] == "train":
            # Gold is never filled from the teacher: a test set the
            # teacher wrote only measures agreement with the teacher.
            label = teacher.get(data["content_hash"])
            if label and label["status"] == "ok":
                rows.append({**base, "label_source": "teacher", "pois": label["pois"]})

    # An ad imported twice is two tasks; keep one row per ad — human over
    # teacher, and the newest human label over older ones.
    def rank(row: dict) -> tuple[bool, str]:
        return row["label_source"] == "human", row.get("annotated_at") or ""

    best: dict[str, dict] = {}
    for row in rows:
        current = best.get(row["content_hash"])
        if current is None or rank(row) > rank(current):
            best[row["content_hash"]] = row
    return list(best.values()), rejected


def main() -> None:
    """Command-line entry point with ``config``, ``to-tasks`` and ``from-export`` commands."""
    parser = argparse.ArgumentParser(description="Label Studio import/export for POI labels")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="Print the labeling config XML")

    to_tasks = sub.add_parser("to-tasks", help="Build the Label Studio import file")
    to_tasks.add_argument("--raw", type=Path, default=Path("data/raw.jsonl"))
    to_tasks.add_argument("--teacher", type=Path, default=Path("data/teacher_labels.jsonl"))
    to_tasks.add_argument("--out", type=Path, default=Path("data/ls_tasks.json"))
    to_tasks.add_argument(
        "--gold-percent",
        type=int,
        default=15,
        help="Share of ads held out as the human-verified test set (default: 15)",
    )
    to_tasks.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="Label Studio export(s) whose ads are already imported; skip them. Repeatable.",
    )

    export = sub.add_parser("from-export", help="Convert a Label Studio JSON export")
    export.add_argument("export", type=Path, help="File from Export -> JSON in Label Studio")
    export.add_argument("--teacher", type=Path, default=Path("data/teacher_labels.jsonl"))
    export.add_argument("--out", type=Path, default=Path("data/labels.jsonl"))
    export.add_argument(
        "--include-teacher",
        action="store_true",
        help="Fill unreviewed TRAIN rows with clean teacher labels (gold stays human-only)",
    )
    for p in (to_tasks, export):
        p.add_argument(
            "--log-level", default=None, choices=["debug", "info", "warning", "error", "critical"]
        )

    args = parser.parse_args()
    if args.command == "config":
        print(labeling_config(), end="")
        return
    configure_logging(args.log_level)

    if args.command == "to-tasks":
        imported = {
            task["data"]["content_hash"]
            for path in args.exclude
            for task in json.loads(path.read_text(encoding="utf-8"))
        }
        raw_rows = [r for r in read_jsonl(args.raw) if r["content_hash"] not in imported]
        tasks = build_tasks(raw_rows, read_jsonl(args.teacher), args.gold_percent)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
        gold = sum(t["data"]["split"] == "gold" for t in tasks)
        prefilled = sum(bool(t["predictions"]) for t in tasks)
        logger.info(
            "Wrote %d tasks to %s (%d gold, %d with teacher predictions)",
            len(tasks),
            args.out,
            gold,
            prefilled,
        )
        return

    tasks = json.loads(args.export.read_text(encoding="utf-8"))
    try:
        rows, rejected = convert_export(tasks, read_jsonl(args.teacher), args.include_teacher)
    except ValueError as exc:
        parser.error(str(exc))
    write_jsonl(args.out, rows)
    by_source: dict[str, int] = {}
    for row in rows:
        key = f"{row['split']}/{row['label_source']}"
        by_source[key] = by_source.get(key, 0) + 1
    logger.info("Wrote %d labeled rows to %s: %s", len(rows), args.out, by_source)
    for reason in rejected:
        logger.warning("Rejected — fix in Label Studio and re-export: %s", reason)


if __name__ == "__main__":
    main()
