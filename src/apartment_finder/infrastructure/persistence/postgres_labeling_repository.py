import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, make_url, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from apartment_finder.application.ports.labeling_repository import LabelingRepository
from apartment_finder.domain.labeling import (
    MIN_DESCRIPTION_LENGTH,
    LabelingBatch,
    LabelingExportStatus,
    LabelingStatus,
    LabelingSyncStatus,
    LabelingTask,
    TeacherAnnotation,
)
from apartment_finder.infrastructure.persistence.models import (
    LabelingBatchModel,
    LabelingTaskModel,
    ListingModel,
)

logger = logging.getLogger(__name__)


class PostgresLabelingRepository(LabelingRepository):
    def __init__(self, connection_url: str):
        self._engine = create_engine(connection_url, pool_pre_ping=True)
        safe_url = make_url(connection_url).render_as_string(hide_password=True)
        logger.info("Connected to %s", safe_url)

    def reserve_candidates(self, limit: int) -> list[LabelingTask]:
        if limit < 1:
            raise ValueError("limit must be positive")

        reserved: list[LabelingTask] = []
        seen_listing_ids: set[UUID] = set()
        with Session(self._engine) as session:
            while len(reserved) < limit:
                candidates = self._load_locked_candidates(
                    session,
                    limit=limit - len(reserved),
                    seen_listing_ids=seen_listing_ids,
                )
                if not candidates:
                    break

                for candidate in candidates:
                    seen_listing_ids.add(candidate.id)
                    try:
                        task = LabelingTask.create(
                            listing_id=candidate.id,
                            description=candidate.description or "",
                        )
                    except ValueError:
                        continue

                    inserted = session.execute(
                        pg_insert(LabelingTaskModel)
                        .values(
                            id=task.id,
                            listing_id=task.listing_id,
                            content_hash=task.content_hash,
                            description_snapshot=task.description_snapshot,
                            status=task.status,
                            selection_score=task.selection_score,
                        )
                        .on_conflict_do_nothing()
                        .returning(LabelingTaskModel)
                    ).scalar_one_or_none()
                    if inserted is None:
                        continue
                    reserved.append(self._to_entity(inserted))
                    if len(reserved) == limit:
                        break

            session.commit()

        logger.info("Reserved %d listing(s) for labeling", len(reserved))
        return reserved

    def get_pending(self, limit: int) -> list[LabelingTask]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with Session(self._engine) as session:
            models = (
                session.execute(
                    select(LabelingTaskModel)
                    .where(LabelingTaskModel.status == LabelingStatus.PENDING)
                    .order_by(LabelingTaskModel.created_at.asc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._to_entity(model) for model in models]

    def create_export_batch(
        self,
        *,
        project_id: int,
        limit: int,
        batch_id: UUID | None = None,
    ) -> LabelingBatch:
        if limit < 1:
            raise ValueError("limit must be positive")
        batch_id = batch_id or uuid4()
        with Session(self._engine) as session:
            batch = session.get(LabelingBatchModel, batch_id)
            if batch is None:
                batch = LabelingBatchModel(id=batch_id, label_studio_project_id=project_id)
                session.add(batch)
                session.flush()
                candidates = (
                    session.execute(
                        select(LabelingTaskModel)
                        .where(
                            LabelingTaskModel.selection_batch_id.is_(None),
                            LabelingTaskModel.export_status == LabelingExportStatus.NOT_EXPORTED,
                            LabelingTaskModel.status == LabelingStatus.PENDING,
                        )
                        .order_by(LabelingTaskModel.created_at.asc())
                        .limit(limit)
                        .with_for_update(of=LabelingTaskModel, skip_locked=True)
                    )
                    .scalars()
                    .all()
                )
                for task in candidates:
                    task.selection_batch_id = batch_id
                    task.label_studio_project_id = project_id
                result = self._to_batch(batch)
                session.commit()
            else:
                result = self._to_batch(batch)
            return result

    def get_batch_tasks(self, batch_id: UUID) -> list[LabelingTask]:
        with Session(self._engine) as session:
            models = (
                session.execute(
                    select(LabelingTaskModel)
                    .where(LabelingTaskModel.selection_batch_id == batch_id)
                    .order_by(LabelingTaskModel.created_at.asc())
                )
                .scalars()
                .all()
            )
            return [self._to_entity(model) for model in models]

    def get_by_label_studio_task_id(self, label_studio_task_id: int) -> LabelingTask | None:
        with Session(self._engine) as session:
            model = session.scalar(
                select(LabelingTaskModel).where(
                    LabelingTaskModel.label_studio_task_id == label_studio_task_id
                )
            )
            return self._to_entity(model) if model else None

    def save_teacher_annotation(self, task_id: UUID, annotation: TeacherAnnotation) -> None:
        with Session(self._engine) as session:
            model = session.get(LabelingTaskModel, task_id)
            if model is None:
                raise KeyError(f"Labeling task not found: {task_id}")
            model.teacher_provider = annotation.provider
            model.teacher_model = annotation.model
            model.teacher_prompt_version = annotation.prompt_version
            model.teacher_status = annotation.status
            model.teacher_result = annotation.result
            model.teacher_problems = annotation.problems
            model.teacher_labeled_at = annotation.labeled_at or datetime.now(UTC)
            model.error = None
            session.commit()

    def mark_exported(self, task_id: UUID, *, project_id: int, label_studio_task_id: int) -> None:
        with Session(self._engine) as session:
            model = session.get(LabelingTaskModel, task_id)
            if model is None:
                raise KeyError(f"Labeling task not found: {task_id}")
            model.label_studio_project_id = project_id
            model.label_studio_task_id = label_studio_task_id
            model.export_status = LabelingExportStatus.EXPORTED
            model.status = LabelingStatus.EXPORTED
            model.error = None
            session.commit()

    def record_export_error(self, task_id: UUID, error: str) -> None:
        with Session(self._engine) as session:
            model = session.get(LabelingTaskModel, task_id)
            if model is None:
                raise KeyError(f"Labeling task not found: {task_id}")
            model.export_status = LabelingExportStatus.FAILED
            model.error = error
            session.commit()

    def approve_batch(
        self,
        batch_id: UUID,
        *,
        actor: str,
        approved_task_ids: list[UUID],
    ) -> None:
        now = datetime.now(UTC)
        approved = set(approved_task_ids)
        with Session(self._engine) as session:
            batch = session.get(LabelingBatchModel, batch_id)
            if batch is None:
                raise KeyError(f"Labeling batch not found: {batch_id}")
            models = (
                session.execute(
                    select(LabelingTaskModel).where(
                        LabelingTaskModel.id.in_(approved),
                        LabelingTaskModel.selection_batch_id == batch_id,
                    )
                )
                .scalars()
                .all()
            )
            if len(models) != len(approved):
                raise ValueError("Approved task IDs must belong to the batch")
            for model in models:
                if model.sync_status != LabelingSyncStatus.SYNCED or model.human_result is None:
                    raise ValueError(f"Task {model.id} has no synced saved annotation")
                model.approved_result = model.human_result
                model.approved_revision = model.human_revision
                model.approved_at = now
                model.approval_actor = actor
                model.approval_invalidated_at = None
                model.status = LabelingStatus.APPROVED
            batch.approved_at = now
            batch.approval_actor = actor
            session.commit()

    def sync_annotation(
        self,
        task_id: UUID,
        *,
        annotation_id: int | None,
        result: list[dict] | None,
        revision: int | None,
        updated_at,
        deleted: bool = False,
    ) -> None:
        with Session(self._engine) as session:
            model = session.get(LabelingTaskModel, task_id)
            if model is None:
                raise KeyError(f"Labeling task not found: {task_id}")

            changed_after_approval = model.approved_at is not None and (
                deleted or model.approved_revision != revision or model.approved_result != result
            )
            parsed_updated_at = _coerce_datetime(updated_at)
            if deleted:
                model.human_annotation_id = None
                model.human_result = None
                model.human_revision = None
                model.human_updated_at = parsed_updated_at
                model.sync_status = LabelingSyncStatus.SYNCED
            else:
                model.human_annotation_id = annotation_id
                model.human_result = result
                model.human_revision = revision
                model.human_updated_at = parsed_updated_at
                model.sync_status = LabelingSyncStatus.SYNCED
            if changed_after_approval:
                model.approval_invalidated_at = datetime.now(UTC)
                model.status = LabelingStatus.EXPORTED
            model.error = None
            session.commit()

    def record_sync_error(self, task_id: UUID, error: str) -> None:
        with Session(self._engine) as session:
            model = session.get(LabelingTaskModel, task_id)
            if model is None:
                raise KeyError(f"Labeling task not found: {task_id}")
            model.sync_status = LabelingSyncStatus.FAILED
            model.error = error
            session.commit()

    def approved_training_rows(self) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(
                select(
                    LabelingTaskModel.id,
                    LabelingTaskModel.content_hash,
                    LabelingTaskModel.description_snapshot,
                    LabelingTaskModel.approved_result,
                    LabelingTaskModel.approved_revision,
                    LabelingTaskModel.approved_at,
                    LabelingTaskModel.approval_actor,
                    ListingModel.source,
                    ListingModel.external_id,
                    ListingModel.url,
                    ListingModel.title,
                    ListingModel.city,
                    ListingModel.area,
                )
                .join(ListingModel, ListingModel.id == LabelingTaskModel.listing_id)
                .where(
                    LabelingTaskModel.approved_at.is_not(None),
                    LabelingTaskModel.approval_invalidated_at.is_(None),
                    LabelingTaskModel.approved_revision == LabelingTaskModel.human_revision,
                    LabelingTaskModel.approved_result.is_not(None),
                )
                .order_by(LabelingTaskModel.approved_at.asc())
            ).all()
            return [
                {
                    "id": str(row.id),
                    "content_hash": row.content_hash,
                    "description": row.description_snapshot,
                    "pois": row.approved_result,
                    "approved_revision": row.approved_revision,
                    "approved_at": row.approved_at.isoformat() if row.approved_at else None,
                    "approval_actor": row.approval_actor,
                    "source": row.source,
                    "external_id": row.external_id,
                    "url": row.url,
                    "title": row.title,
                    "city": row.city,
                    "area": row.area,
                    "label_source": "human",
                }
                for row in rows
            ]

    @staticmethod
    def _load_locked_candidates(
        session: Session,
        *,
        limit: int,
        seen_listing_ids: set[UUID],
    ) -> list[ListingModel]:
        normalized_description = func.regexp_replace(
            func.btrim(ListingModel.description),
            r"\s+",
            " ",
            "g",
        )
        area_rank = func.row_number().over(
            partition_by=ListingModel.area,
            order_by=ListingModel.scraped_at.desc(),
        )
        ranked_candidates = (
            select(
                ListingModel.id.label("listing_id"),
                area_rank.label("area_rank"),
            )
            .outerjoin(
                LabelingTaskModel,
                LabelingTaskModel.listing_id == ListingModel.id,
            )
            .where(
                LabelingTaskModel.id.is_(None),
                ListingModel.description.is_not(None),
                func.length(normalized_description) >= MIN_DESCRIPTION_LENGTH,
            )
            .subquery()
        )

        stmt = (
            select(ListingModel)
            .join(ranked_candidates, ranked_candidates.c.listing_id == ListingModel.id)
            .order_by(ranked_candidates.c.area_rank.asc(), ListingModel.scraped_at.desc())
            .limit(limit)
            .with_for_update(of=ListingModel, skip_locked=True)
        )
        if seen_listing_ids:
            stmt = stmt.where(ListingModel.id.not_in(seen_listing_ids))

        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def _to_entity(model: LabelingTaskModel) -> LabelingTask:
        return LabelingTask(
            id=model.id,
            listing_id=model.listing_id,
            content_hash=model.content_hash,
            description_snapshot=model.description_snapshot,
            status=model.status,
            selection_score=float(model.selection_score)
            if model.selection_score is not None
            else None,
            created_at=model.created_at,
            updated_at=model.updated_at,
            selection_batch_id=model.selection_batch_id,
            label_studio_project_id=model.label_studio_project_id,
            label_studio_task_id=model.label_studio_task_id,
            teacher_provider=model.teacher_provider,
            teacher_model=model.teacher_model,
            teacher_prompt_version=model.teacher_prompt_version,
            teacher_status=model.teacher_status,
            teacher_result=model.teacher_result,
            teacher_problems=model.teacher_problems or [],
            human_annotation_id=model.human_annotation_id,
            human_result=model.human_result,
            human_revision=model.human_revision,
            approved_revision=model.approved_revision,
            approved_result=model.approved_result,
        )

    @staticmethod
    def _to_batch(model: LabelingBatchModel) -> LabelingBatch:
        return LabelingBatch(
            id=model.id,
            label_studio_project_id=model.label_studio_project_id,
            created_at=model.created_at,
            approved_at=model.approved_at,
            approval_actor=model.approval_actor,
        )


def _coerce_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Expected datetime, ISO string, or None, got {type(value).__name__}")

