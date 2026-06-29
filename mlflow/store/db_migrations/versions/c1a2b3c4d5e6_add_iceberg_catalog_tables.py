"""add iceberg catalog tables

Create Date: 2026-07-02 17:20:03.428507

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c1a2b3c4d5e6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "iceberg_namespace_properties",
        sa.Column("catalog_name", sa.String(length=255), nullable=False),
        sa.Column("namespace", sa.String(length=255), nullable=False),
        sa.Column("property_key", sa.String(length=255), nullable=False),
        sa.Column("property_value", sa.String(length=1000), nullable=False),
        sa.PrimaryKeyConstraint(
            "catalog_name",
            "namespace",
            "property_key",
            name="iceberg_namespace_properties_pk",
        ),
    )
    op.create_table(
        "iceberg_tables",
        sa.Column("catalog_name", sa.String(length=255), nullable=False),
        sa.Column("table_namespace", sa.String(length=255), nullable=False),
        sa.Column("table_name", sa.String(length=255), nullable=False),
        sa.Column("metadata_location", sa.String(length=1000), nullable=True),
        sa.Column("previous_metadata_location", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint(
            "catalog_name",
            "table_namespace",
            "table_name",
            name="iceberg_tables_pk",
        ),
    )


def downgrade():
    op.drop_table("iceberg_tables")
    op.drop_table("iceberg_namespace_properties")
