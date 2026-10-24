import argparse
import os
from uuid import UUID

from dotenv import load_dotenv

from apartment_finder.application.use_cases.approve_batch import ApproveBatch
from apartment_finder.application.use_cases.export_selected_batch import ExportSelectedBatch
from apartment_finder.application.use_cases.select_for_labeling import (
    DEFAULT_LABELING_BATCH_SIZE,
    SelectForLabeling,
)
from apartment_finder.application.use_cases.sync_annotations import SyncAnnotations
from apartment_finder.infrastructure.label_studio.client import LabelStudioClient
from apartment_finder.infrastructure.label_studio.teacher_adapter import (
    ExistingTeacherAnnotationProvider,
)
from apartment_finder.infrastructure.persistence.postgres_labeling_repository import (
    PostgresLabelingRepository,
)
from apartment_finder.infrastructure.persistence.settings import build_postgres_connection_url
from apartment_finder.interface.logging_config import configure_logging
from training.jsonl import write_jsonl
from training.label_studio import labeling_config
from training.label_with_teacher import DEFAULT_PROVIDER, resolve_model_and_effort
from training.teacher import get_provider


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Apartment Finder labeling pipeline")
    parser.add_argument(
        "--log-level", default=None, choices=["debug", "info", "warning", "error", "critical"]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    select = sub.add_parser("select", help="Reserve scraped listings for labeling")
    select.add_argument("--batch-size", type=int, default=DEFAULT_LABELING_BATCH_SIZE)

    export = sub.add_parser("export", help="Export a selected batch to Label Studio")
    export.add_argument("--batch-size", type=int, default=DEFAULT_LABELING_BATCH_SIZE)
    export.add_argument("--batch-id", type=UUID, default=None)
    export.add_argument("--project-id", type=int, default=None)
    export.add_argument("--teacher-provider", default=os.environ.get("TEACHER_PROVIDER"))
    export.add_argument("--teacher-model", default=None)
    export.add_argument("--teacher-effort", default=None)

    approve = sub.add_parser("approve", help="Approve a reviewed Label Studio batch")
    approve.add_argument("batch_id", type=UUID)
    approve.add_argument("--actor", required=True)

    sync = sub.add_parser("sync", help="Reconcile saved Label Studio annotations")
    sync.add_argument("batch_id", type=UUID)

    sync_task = sub.add_parser("sync-task", help="Reconcile one Label Studio task by ID")
    sync_task.add_argument("label_studio_task_id", type=int)

    sub.add_parser("check-label-studio", help="Print Label Studio API version info")

    sub.add_parser("config", help="Print the Label Studio labeling config XML")

    training = sub.add_parser("export-training", help="Export approved labels for model training")
    training.add_argument("--out", default="data/labels.jsonl")

    args = parser.parse_args()
    if args.command == "config":
        print(labeling_config(), end="")
        return

    configure_logging(args.log_level)
    repository = PostgresLabelingRepository(build_postgres_connection_url())

    if args.command == "select":
        result = SelectForLabeling(repository).execute(batch_size=args.batch_size)
        print(f"Reserved {result.count} listing(s) for labeling.")
        for task_id in result.selected_task_ids:
            print(f"  - {task_id}")
        return

    if args.command == "export-training":
        rows = repository.approved_training_rows()
        write_jsonl(args.out, rows)
        print(f"Wrote {len(rows)} approved label row(s) to {args.out}.")
        return

    client = _label_studio_client()

    if args.command == "export":
        project_id = args.project_id or int(os.environ["LABEL_STUDIO_PROJECT_ID"])
        teacher = None
        if args.teacher_provider:
            model, effort = resolve_model_and_effort(
                args.teacher_provider,
                args.teacher_model,
                args.teacher_effort,
            )
            teacher = ExistingTeacherAnnotationProvider(
                get_provider(args.teacher_provider or DEFAULT_PROVIDER, model, effort)
            )
        result = ExportSelectedBatch(repository, client, teacher).execute(
            project_id=project_id,
            batch_size=args.batch_size,
            batch_id=args.batch_id,
        )
        print(
            f"Batch {result.batch_id}: exported {result.exported} task(s), {result.failed} failed."
        )
        return

    if args.command == "approve":
        result = ApproveBatch(repository, client).execute(batch_id=args.batch_id, actor=args.actor)
        print(
            f"Batch {result.batch_id}: approved {result.approved} task(s), {result.failed} failed."
        )
        return

    if args.command == "sync":
        result = SyncAnnotations(repository, client).execute(batch_id=args.batch_id)
        print(
            f"Synced {result.synced} task(s), {result.skipped} skipped, {result.failed} failed."
        )
        if result.failed:
            raise SystemExit(1)
        return

    if args.command == "sync-task":
        result = SyncAnnotations(repository, client).execute_label_studio_task(
            label_studio_task_id=args.label_studio_task_id
        )
        print(
            f"Synced {result.synced} task(s), {result.skipped} skipped, {result.failed} failed."
        )
        if result.failed:
            raise SystemExit(1)
        return

    if args.command == "check-label-studio":
        print(client.version())


def _label_studio_client() -> LabelStudioClient:
    return LabelStudioClient(
        base_url=os.environ.get("LABEL_STUDIO_URL", "http://localhost:8080"),
        api_token=os.environ["LABEL_STUDIO_API_TOKEN"],
    )


if __name__ == "__main__":
    main()
