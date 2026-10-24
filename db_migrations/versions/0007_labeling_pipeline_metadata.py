"""Track Label Studio export, review, approval, and sync metadata.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("labeling_status", "labeling_tasks", type_="check")
    op.create_check_constraint(
        "labeling_status",
        "labeling_tasks",
        "status IN ('pending', 'exported', 'approved', 'in_progress', 'completed', 'failed')",
    )
    op.create_table(
        "labeling_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("label_studio_project_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_actor", sa.String(), nullable=True),
    )
    op.add_column(
        "labeling_tasks",
        sa.Column("selection_batch_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "labeling_tasks",
        sa.Column("label_studio_project_id", sa.Integer(), nullable=True),
    )
    op.add_column("labeling_tasks", sa.Column("label_studio_task_id", sa.Integer(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column(
            "export_status",
            sa.Enum(
                "not_exported",
                "exported",
                "failed",
                name="labeling_export_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="not_exported",
            nullable=False,
        ),
    )
    op.add_column(
        "labeling_tasks",
        sa.Column(
            "sync_status",
            sa.Enum(
                "not_synced",
                "synced",
                "failed",
                name="labeling_sync_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="not_synced",
            nullable=False,
        ),
    )
    op.add_column("labeling_tasks", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("teacher_provider", sa.String(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("teacher_model", sa.String(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("teacher_prompt_version", sa.String(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column(
            "teacher_status",
            sa.Enum(
                "missing",
                "ok",
                "invalid",
                "refused",
                "error",
                name="teacher_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="missing",
            nullable=False,
        ),
    )
    op.add_column("labeling_tasks", sa.Column("teacher_result", sa.JSON(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("teacher_problems", sa.JSON(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column("teacher_labeled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("labeling_tasks", sa.Column("human_annotation_id", sa.Integer(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("human_result", sa.JSON(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("human_revision", sa.Integer(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column("human_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("labeling_tasks", sa.Column("approved_result", sa.JSON(), nullable=True))
    op.add_column("labeling_tasks", sa.Column("approved_revision", sa.Integer(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("labeling_tasks", sa.Column("approval_actor", sa.String(), nullable=True))
    op.add_column(
        "labeling_tasks",
        sa.Column("approval_invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_labeling_tasks_selection_batch_id",
        "labeling_tasks",
        "labeling_batches",
        ["selection_batch_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_labeling_tasks_label_studio_task_id",
        "labeling_tasks",
        ["label_studio_task_id"],
    )
    op.create_index(
        "idx_labeling_tasks_batch",
        "labeling_tasks",
        ["selection_batch_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_labeling_tasks_batch", table_name="labeling_tasks")
    op.drop_constraint("uq_labeling_tasks_label_studio_task_id", "labeling_tasks", type_="unique")
    op.drop_constraint("fk_labeling_tasks_selection_batch_id", "labeling_tasks", type_="foreignkey")
    for column in (
        "approval_invalidated_at",
        "approval_actor",
        "approved_at",
        "approved_revision",
        "approved_result",
        "human_updated_at",
        "human_revision",
        "human_result",
        "human_annotation_id",
        "teacher_labeled_at",
        "teacher_problems",
        "teacher_result",
        "teacher_status",
        "teacher_prompt_version",
        "teacher_model",
        "teacher_provider",
        "error",
        "sync_status",
        "export_status",
        "label_studio_task_id",
        "label_studio_project_id",
        "selection_batch_id",
    ):
        op.drop_column("labeling_tasks", column)
    op.drop_table("labeling_batches")
    op.drop_constraint("labeling_status", "labeling_tasks", type_="check")
    op.create_check_constraint(
        "labeling_status",
        "labeling_tasks",
        "status IN ('pending', 'in_progress', 'completed', 'failed')",
    )
