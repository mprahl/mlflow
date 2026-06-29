"""add rollup publication state columns

Create Date: 2026-07-08 17:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("iceberg_trace_publication_state") as batch_op:
        batch_op.add_column(
            sa.Column("trace_metric_daily_rollups_metadata_location", sa.String(length=1000))
        )
        batch_op.add_column(
            sa.Column("span_cost_daily_rollups_metadata_location", sa.String(length=1000))
        )
        batch_op.add_column(
            sa.Column("assessment_daily_rollups_metadata_location", sa.String(length=1000))
        )
        batch_op.add_column(sa.Column("session_summary_metadata_location", sa.String(length=1000)))
        batch_op.add_column(sa.Column("trace_metric_daily_rollups_snapshot_id", sa.BigInteger()))
        batch_op.add_column(sa.Column("span_cost_daily_rollups_snapshot_id", sa.BigInteger()))
        batch_op.add_column(sa.Column("assessment_daily_rollups_snapshot_id", sa.BigInteger()))
        batch_op.add_column(sa.Column("session_summary_snapshot_id", sa.BigInteger()))


def downgrade():
    with op.batch_alter_table("iceberg_trace_publication_state") as batch_op:
        batch_op.drop_column("session_summary_snapshot_id")
        batch_op.drop_column("assessment_daily_rollups_snapshot_id")
        batch_op.drop_column("span_cost_daily_rollups_snapshot_id")
        batch_op.drop_column("trace_metric_daily_rollups_snapshot_id")
        batch_op.drop_column("assessment_daily_rollups_metadata_location")
        batch_op.drop_column("session_summary_metadata_location")
        batch_op.drop_column("span_cost_daily_rollups_metadata_location")
        batch_op.drop_column("trace_metric_daily_rollups_metadata_location")
