"""
Step 3a of the POI pipeline: a large "teacher" model pre-labels every ad.

These labels are drafts. Every one is checked against the schema and
against the ad text (training.schema.find_problems), then loaded into
Label Studio as predictions for a human to confirm or fix. Nothing here
is trusted blindly — the human-reviewed set is what training and
evaluation stand on.

The model behind it is pluggable (training/teacher/): pick it with
--provider / --model, or TEACHER_PROVIDER / TEACHER_MODEL in .env.

Commands:
    try      label a few ads and print them — tune the prompt with this
             before spending money on the full set
    run      label every pending ad one request at a time; works with any
             provider, including local OpenAI-compatible servers
    submit   send every pending ad as one asynchronous batch (half price,
             for providers with a batch API)
    collect  wait for submitted batches and append their results

    uv run python -m training.label_with_teacher try --limit 5
    uv run python -m training.label_with_teacher submit --provider anthropic
    uv run python -m training.label_with_teacher collect --wait

Re-running is safe: ads already labeled by the same provider and model,
or sitting in a pending batch, are skipped; failed ones are retried.
Phones and e-mails were already stripped in Step 2, so only public ad
text leaves the machine.
"""

import argparse
import json
import logging
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from apartment_finder.interface.logging_config import configure_logging
from training.jsonl import read_jsonl, write_jsonl
from training.schema import PoiExtraction, find_problems
from training.teacher import PROVIDERS, get_provider, provider_class
from training.teacher.base import BatchNotSupportedError, Outcome, TeacherOutput, TeacherProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"
DEFAULT_RAW = Path("data/raw.jsonl")
DEFAULT_LABELS = Path("data/teacher_labels.jsonl")
DEFAULT_BATCHES = Path("data/teacher_batches.json")
# `run` gives up after this many failures in a row: that's a bad key or a
# wrong model name, not a flaky ad, and every further call would fail too.
MAX_CONSECUTIVE_FAILURES = 3


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


def load_batches(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_batches(path: Path, batches: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batches, indent=2), encoding="utf-8")


def pending_rows(args: argparse.Namespace, provider: TeacherProvider) -> list[dict]:
    """Rows with no usable label from this provider+model, and not in flight."""
    done = {
        r["content_hash"]
        for r in read_jsonl(args.labels)
        if r["status"] in ("ok", "invalid")
        and r.get("provider") == provider.name
        and r["model"] == provider.model
    }
    in_flight = {
        h for b in load_batches(args.batches) if not b["collected"] for h in b["content_hashes"]
    }
    rows = [
        r
        for r in read_jsonl(args.raw)
        if r["content_hash"] not in done and r["content_hash"] not in in_flight
    ]
    return rows[: args.limit] if args.limit else rows


def label_safely(provider: TeacherProvider, row: dict) -> TeacherOutput:
    try:
        return provider.label(row)
    except Exception as e:  # noqa: BLE001 — any SDK's errors, recorded and retried later
        logger.warning("%s failed on %s: %s", provider.name, row["id"], e)
        return TeacherOutput(Outcome.FAILED, detail=f"{type(e).__name__}: {e}")


def cmd_try(args: argparse.Namespace, provider: TeacherProvider) -> None:
    for row in pending_rows(args, provider):
        record = to_record(row, provider, label_safely(provider, row))
        print(f"\n=== {row['id']}  [{record['status']}]  {row['url']}")
        print(row["description"][:600] + ("…" if len(row["description"]) > 600 else ""))
        print("---")
        print(json.dumps(record["pois"], ensure_ascii=False, indent=1))
        for problem in record["problems"]:
            print(f"!! {problem}")
        print(f"usage: {json.dumps(record['usage'])}")
        if args.save:
            write_jsonl(args.labels, [record], append=True)


def cmd_run(args: argparse.Namespace, provider: TeacherProvider) -> None:
    rows = pending_rows(args, provider)
    logger.info("Labeling %d ads with %s/%s", len(rows), provider.name, provider.model)
    statuses: Counter[str] = Counter()
    consecutive_failures = 0
    for i, row in enumerate(rows, 1):
        output = label_safely(provider, row)
        record = to_record(row, provider, output)
        # Written one at a time, so an interrupted run resumes where it stopped.
        write_jsonl(args.labels, [record], append=True)
        statuses[record["status"]] += 1

        consecutive_failures = consecutive_failures + 1 if output.outcome is Outcome.FAILED else 0
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            raise SystemExit(
                f"Stopping: {consecutive_failures} failures in a row — {output.detail}"
            )
        if i % 25 == 0 or i == len(rows):
            logger.info("%d/%d labeled: %s", i, len(rows), dict(statuses))


def cmd_submit(args: argparse.Namespace, provider: TeacherProvider) -> None:
    rows = pending_rows(args, provider)
    if not rows:
        logger.info("Nothing to submit — every row is labeled or already in a batch")
        return
    try:
        state = provider.submit_batch(rows, args.batches.parent)
    except BatchNotSupportedError as e:
        raise SystemExit(str(e)) from None
    batches = load_batches(args.batches)
    batches.append(
        {
            **state,
            "provider": provider.name,
            "model": provider.model,
            "effort": provider.effort,
            "submitted_at": datetime.now(UTC).isoformat(),
            "content_hashes": [r["content_hash"] for r in rows],
            "collected": False,
        }
    )
    save_batches(args.batches, batches)
    logger.info("Submitted %s batch with %d ads — run `collect --wait`", provider.name, len(rows))


def cmd_collect(args: argparse.Namespace, _provider: TeacherProvider) -> None:
    rows_by_hash = {r["content_hash"]: r for r in read_jsonl(args.raw)}
    batches = load_batches(args.batches)
    for entry in (b for b in batches if not b["collected"]):
        # Each batch is collected with the provider that submitted it,
        # whatever --provider says today.
        provider = get_provider(entry["provider"], entry["model"], entry["effort"])
        progress = provider.batch_progress(entry)
        while not progress.done and args.wait:
            logger.info(
                "%s batch: %s (%s) — checking again in 60s",
                provider.name,
                progress.status,
                progress.summary,
            )
            time.sleep(60)
            progress = provider.batch_progress(entry)
        if not progress.done:
            logger.info("%s batch still %s — rerun with --wait", provider.name, progress.status)
            continue

        results = provider.batch_results(entry)
        records = []
        for content_hash in entry["content_hashes"]:
            row = rows_by_hash.get(content_hash)
            if row is None:
                logger.warning("Batch item %s matches no row in %s", content_hash, args.raw)
                continue
            # Absent = never ran (expired, cancelled, failed batch). Recorded
            # as an error, so the next `submit` sends it again.
            output = results.get(content_hash) or TeacherOutput(
                Outcome.FAILED, detail=f"no result (batch {progress.status})"
            )
            records.append(to_record(row, provider, output))

        write_jsonl(args.labels, records, append=True)
        entry["collected"] = True
        save_batches(args.batches, batches)
        statuses = Counter(r["status"] for r in records)
        logger.info(
            "Collected %s batch (%s): %s %s",
            provider.name,
            progress.status,
            dict(statuses),
            progress.summary,
        )


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


COMMANDS = {"try": cmd_try, "run": cmd_run, "submit": cmd_submit, "collect": cmd_collect}


def main() -> None:
    # .env holds the provider API key(s) and optional TEACHER_* settings.
    load_dotenv()
    parser = argparse.ArgumentParser(description="Pre-label ads with a teacher model")
    parser.add_argument("command", choices=list(COMMANDS))
    parser.add_argument(
        "--provider",
        default=os.environ.get("TEACHER_PROVIDER", DEFAULT_PROVIDER),
        choices=sorted(PROVIDERS),
        help=f"default: TEACHER_PROVIDER env var, else {DEFAULT_PROVIDER}",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="default: TEACHER_MODEL env var (if --provider is TEACHER_PROVIDER), "
        "else the provider's default model",
    )
    parser.add_argument(
        "--effort",
        default=None,
        help="Reasoning/thinking effort passed to the provider as-is (e.g. low, medium, "
        "high); 'default' sends none — needed for models without the setting. "
        "Default: TEACHER_EFFORT env var (if --provider is TEACHER_PROVIDER), "
        "else the provider's default",
    )
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--batches", type=Path, default=DEFAULT_BATCHES)
    parser.add_argument("--limit", type=int, default=None, help="Only the first N pending rows")
    parser.add_argument("--save", action="store_true", help="try: also append results to labels")
    parser.add_argument("--wait", action="store_true", help="collect: block until batches end")
    parser.add_argument(
        "--log-level", default=None, choices=["debug", "info", "warning", "error", "critical"]
    )
    args = parser.parse_args()
    configure_logging(args.log_level)
    if args.command == "try" and args.limit is None:
        args.limit = 3

    model, effort = resolve_model_and_effort(args.provider, args.model, args.effort)
    provider = get_provider(args.provider, model, effort)
    logger.info("Teacher: %s / %s (effort=%s)", provider.name, provider.model, effort or "default")
    COMMANDS[args.command](args, provider)


if __name__ == "__main__":
    main()
