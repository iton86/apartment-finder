"""
SQLAlchemy models — the infrastructure-layer mirror of the database schema.

These are deliberately kept separate from domain.entities.Listing: the
domain entity is what the business logic works with, while these classes
describe how that data is persisted.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from apartment_finder.domain.entities import ListingStatus, TransactionType
from apartment_finder.domain.labeling import (
    LabelingExportStatus,
    LabelingStatus,
    LabelingSyncStatus,
    TeacherStatus,
)


class Base(DeclarativeBase):
    pass


class ApartmentModel(Base):
    __tablename__ = "apartments"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    listings: Mapped[list["ListingModel"]] = relationship(back_populates="apartment")


class ListingModel(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "external_id", "price_amount"),
        Index("idx_listings_city_area", "city", "area"),
        Index("idx_listings_price_per_sqm", "area_sqm", "price_amount"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            name="transaction_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String)
    area_sqm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    building_floors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    area: Mapped[str] = mapped_column(Text, nullable=False)
    street: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    agency_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[ListingStatus] = mapped_column(
        Enum(
            ListingStatus,
            name="listing_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=ListingStatus.ACTIVE,
        server_default="active",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    apartment_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("apartments.id"), nullable=True
    )

    apartment: Mapped[ApartmentModel | None] = relationship(back_populates="listings")
    images: Mapped[list["ListingImageModel"]] = relationship(
        back_populates="listing", cascade="all, delete-orphan"
    )


class ListingImageModel(Base):
    __tablename__ = "listing_images"
    __table_args__ = (UniqueConstraint("listing_id", "sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)

    listing: Mapped["ListingModel"] = relationship(back_populates="images")


class TrainingAdSelectionModel(Base):
    __tablename__ = "training_ad_selections"
    __table_args__ = (
        UniqueConstraint("dataset", "listing_id"),
        Index("idx_training_ad_selections_run", "selection_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String, nullable=False)
    listing_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    selection_run: Mapped[str] = mapped_column(String, nullable=False)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class LabelingTaskModel(Base):
    __tablename__ = "labeling_tasks"
    __table_args__ = (
        UniqueConstraint("listing_id"),
        UniqueConstraint("content_hash"),
        Index("idx_labeling_tasks_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    selection_batch_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("labeling_batches.id"), nullable=True
    )
    listing_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    description_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[LabelingStatus] = mapped_column(
        Enum(
            LabelingStatus,
            name="labeling_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=LabelingStatus.PENDING,
        server_default=LabelingStatus.PENDING.value,
    )
    selection_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    label_studio_project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label_studio_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    export_status: Mapped[LabelingExportStatus] = mapped_column(
        Enum(
            LabelingExportStatus,
            name="labeling_export_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=LabelingExportStatus.NOT_EXPORTED,
        server_default=LabelingExportStatus.NOT_EXPORTED.value,
    )
    sync_status: Mapped[LabelingSyncStatus] = mapped_column(
        Enum(
            LabelingSyncStatus,
            name="labeling_sync_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=LabelingSyncStatus.NOT_SYNCED,
        server_default=LabelingSyncStatus.NOT_SYNCED.value,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    teacher_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    teacher_model: Mapped[str | None] = mapped_column(String, nullable=True)
    teacher_prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    teacher_status: Mapped[TeacherStatus] = mapped_column(
        Enum(
            TeacherStatus,
            name="teacher_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=TeacherStatus.MISSING,
        server_default=TeacherStatus.MISSING.value,
    )
    teacher_result: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    teacher_problems: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    teacher_labeled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    human_annotation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_result: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    human_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_result: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    approved_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_actor: Mapped[str | None] = mapped_column(String, nullable=True)
    approval_invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    listing: Mapped[ListingModel] = relationship()
    batch: Mapped["LabelingBatchModel | None"] = relationship(back_populates="tasks")


class LabelingBatchModel(Base):
    __tablename__ = "labeling_batches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    label_studio_project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_actor: Mapped[str | None] = mapped_column(String, nullable=True)

    tasks: Mapped[list[LabelingTaskModel]] = relationship(back_populates="batch")
