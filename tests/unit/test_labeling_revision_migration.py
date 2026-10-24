"""Compile migration SQL offline; no database is required."""

import importlib.util
from io import StringIO
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from apartment_finder.infrastructure.persistence.models import LabelingTaskModel


def test_revision_columns_use_bigint():
    for name in ("human_revision", "approved_revision"):
        assert isinstance(LabelingTaskModel.__table__.c[name].type, sa.BigInteger)


def test_revision_migration_generates_postgresql_bigint_ddl():
    path = (
        Path(__file__).resolve().parents[2]
        / "db_migrations/versions/0008_labeling_revision_bigint.py"
    )
    spec = importlib.util.spec_from_file_location("revision_bigint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0007"

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        module.upgrade()

    sql = output.getvalue()
    assert "ALTER COLUMN human_revision TYPE BIGINT" in sql
    assert "ALTER COLUMN approved_revision TYPE BIGINT" in sql
    assert "DROP" not in sql
