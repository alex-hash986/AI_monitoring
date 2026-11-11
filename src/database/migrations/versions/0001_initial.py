"""Create the monitoring schema and the price history hypertable."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "competitors",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("website_url", sa.Text),
        sa.Column("tg_channel", sa.String(255)),
        sa.Column("is_active", sa.Boolean, server_default=sa.true(), nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("competitor_id", sa.BigInteger, sa.ForeignKey("competitors.id"), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.String(255)),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("sku", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("competitor_id", "external_id", name="uq_products_competitor_external_id"),
    )
    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.BigInteger, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("price", sa.Numeric(16, 4), nullable=False),
        sa.Column("old_price", sa.Numeric(16, 4)),
        sa.Column("discount_percent", sa.Numeric(5, 2)),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), primary_key=True),
        sa.CheckConstraint("price >= 0 AND (old_price IS NULL OR old_price >= 0)", name="ck_price_history_prices"),
        sa.CheckConstraint("discount_percent BETWEEN 0 AND 100", name="ck_price_history_discount"),
    )
    op.execute(
        "SELECT create_hypertable('price_history', by_range('timestamp'), "
        "create_default_indexes => false)"
    )
    op.create_index(
        "ix_price_history_product_timestamp", "price_history", ["product_id", sa.text("timestamp DESC")]
    )
    op.create_table(
        "promotions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("competitor_id", sa.BigInteger, sa.ForeignKey("competitors.id"), nullable=False),
        sa.Column("source", sa.String(3), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("summary", sa.Text),
        sa.Column("promo_type", sa.String(100)),
        sa.Column("start_date", sa.Date),
        sa.Column("end_date", sa.Date),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('web', 'tg')", name="ck_promotions_source"),
        sa.CheckConstraint("start_date <= end_date", name="ck_promotions_dates"),
    )
    op.create_index(
        "ix_promotions_competitor_timestamp", "promotions", ["competitor_id", sa.text("timestamp DESC")]
    )
    op.create_table(
        "ai_insights",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("report_type", sa.String(100), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("recommendations_json", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_ai_insights_type_created_at", "ai_insights", ["report_type", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    op.drop_table("ai_insights")
    op.drop_table("promotions")
    op.drop_table("price_history")
    op.drop_table("products")
    op.drop_table("competitors")
