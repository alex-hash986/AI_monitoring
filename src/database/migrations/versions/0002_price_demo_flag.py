"""Track demo price observations separately from real observations."""

from alembic import op
import sqlalchemy as sa


revision = "0002_price_demo_flag"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "price_history",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("price_history", "is_demo")
