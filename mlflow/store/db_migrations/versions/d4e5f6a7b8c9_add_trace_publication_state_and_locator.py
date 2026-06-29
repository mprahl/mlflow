"""add trace publication state and archived trace locator tables

Create Date: 2026-07-04 17:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c1a2b3c4d5e6"
branch_labels = None
depends_on = None

_ARCHIVED_TRACE_LOCATOR_PARTITIONS = 32


def _create_publication_state_table() -> None:
    op.create_table(
        "iceberg_trace_publication_state",
        sa.Column("state_key", sa.String(length=32), nullable=False),
        sa.Column("trace_index_metadata_location", sa.String(length=1000), nullable=True),
        sa.Column("trace_tag_index_metadata_location", sa.String(length=1000), nullable=True),
        sa.Column("span_index_metadata_location", sa.String(length=1000), nullable=True),
        sa.Column("assessment_index_metadata_location", sa.String(length=1000), nullable=True),
        sa.Column("trace_index_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("trace_tag_index_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("span_index_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("assessment_index_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("published_at_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("state_key", name="iceberg_trace_publication_state_pk"),
    )


def _create_archived_trace_locators_table() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE TABLE archived_trace_locators (
                workspace VARCHAR(255) NOT NULL,
                experiment_id INTEGER NOT NULL,
                trace_id VARCHAR(50) NOT NULL,
                request_time_ms BIGINT NOT NULL,
                request_day DATE NOT NULL,
                archive_uri VARCHAR(2000),
                published_at_ms BIGINT NOT NULL,
                CONSTRAINT archived_trace_locators_pk PRIMARY KEY (workspace, experiment_id, trace_id)
            ) PARTITION BY HASH (experiment_id)
            """
        )
        for remainder in range(_ARCHIVED_TRACE_LOCATOR_PARTITIONS):
            op.execute(
                f"""
                CREATE TABLE archived_trace_locators_p{remainder:02d}
                PARTITION OF archived_trace_locators
                FOR VALUES WITH (MODULUS {_ARCHIVED_TRACE_LOCATOR_PARTITIONS}, REMAINDER {remainder})
                """
            )
        op.create_index(
            "index_archived_trace_locators_workspace_trace_id",
            "archived_trace_locators",
            ["workspace", "trace_id"],
        )
        op.create_index(
            "index_archived_trace_locators_experiment_id_request_time_ms",
            "archived_trace_locators",
            ["experiment_id", "request_time_ms"],
        )
        op.create_index(
            "index_archived_trace_locators_experiment_id_request_day",
            "archived_trace_locators",
            ["experiment_id", "request_day"],
        )
        return

    if dialect == "mysql":
        op.execute(
            f"""
            CREATE TABLE archived_trace_locators (
                workspace VARCHAR(255) NOT NULL,
                experiment_id INTEGER NOT NULL,
                trace_id VARCHAR(50) NOT NULL,
                request_time_ms BIGINT NOT NULL,
                request_day DATE NOT NULL,
                archive_uri VARCHAR(2000),
                published_at_ms BIGINT NOT NULL,
                PRIMARY KEY (workspace, experiment_id, trace_id),
                KEY index_archived_trace_locators_workspace_trace_id (workspace, trace_id),
                KEY index_archived_trace_locators_experiment_id_request_time_ms (
                    experiment_id, request_time_ms
                ),
                KEY index_archived_trace_locators_experiment_id_request_day (
                    experiment_id, request_day
                )
            )
            PARTITION BY HASH(experiment_id)
            PARTITIONS {_ARCHIVED_TRACE_LOCATOR_PARTITIONS}
            """
        )
        return

    op.create_table(
        "archived_trace_locators",
        sa.Column("workspace", sa.String(length=255), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=50), nullable=False),
        sa.Column("request_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("request_day", sa.Date(), nullable=False),
        sa.Column("archive_uri", sa.String(length=2000), nullable=True),
        sa.Column("published_at_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace",
            "experiment_id",
            "trace_id",
            name="archived_trace_locators_pk",
        ),
    )
    op.create_index(
        "index_archived_trace_locators_workspace_trace_id",
        "archived_trace_locators",
        ["workspace", "trace_id"],
    )
    op.create_index(
        "index_archived_trace_locators_experiment_id_request_time_ms",
        "archived_trace_locators",
        ["experiment_id", "request_time_ms"],
    )
    op.create_index(
        "index_archived_trace_locators_experiment_id_request_day",
        "archived_trace_locators",
        ["experiment_id", "request_day"],
    )


def upgrade():
    _create_publication_state_table()
    _create_archived_trace_locators_table()


def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TABLE IF EXISTS archived_trace_locators CASCADE")
    else:
        op.drop_table("archived_trace_locators")
    op.drop_table("iceberg_trace_publication_state")
