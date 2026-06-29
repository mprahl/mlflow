"""block unsafe iceberg publication

Create Date: 2026-07-12 23:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("iceberg_trace_publication_state") as batch_op:
        batch_op.add_column(
            sa.Column(
                "publication_blocked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("iceberg_trace_publication_state") as batch_op:
        batch_op.drop_column("publication_blocked")
