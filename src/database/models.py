from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity,
    Index, Integer, JSON, Numeric, String, Text, TypeDecorator, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and dialect.name == "sqlite" and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and dialect.name == "sqlite":
            return value.replace(tzinfo=timezone.utc)
        return value


IDENTITY_TYPE = BigInteger().with_variant(Integer(), "sqlite")


class Competitor(Base):
    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    website_url: Mapped[str | None] = mapped_column(Text)
    tg_channel: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    products: Mapped[list[Product]] = relationship(back_populates="competitor")
    promotions: Mapped[list[Promotion]] = relationship(back_populates="competitor")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("competitor_id", "external_id", name="uq_products_competitor_external_id"),
    )

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, Identity(), primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    external_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    sku: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    competitor: Mapped[Competitor] = relationship(back_populates="products")
    price_history: Mapped[list[PriceHistory]] = relationship(back_populates="product")


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        CheckConstraint("price >= 0 AND (old_price IS NULL OR old_price >= 0)", name="ck_price_history_prices"),
        CheckConstraint("discount_percent BETWEEN 0 AND 100", name="ck_price_history_discount"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    price: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_demo: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime(), primary_key=True, server_default=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="price_history")


class Promotion(Base):
    __tablename__ = "promotions"
    __table_args__ = (
        CheckConstraint("source IN ('web', 'tg')", name="ck_promotions_source"),
        CheckConstraint("start_date <= end_date", name="ck_promotions_dates"),
    )

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, Identity(), primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"))
    source: Mapped[str] = mapped_column(String(3))
    raw_text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    promo_type: Mapped[str | None] = mapped_column(String(100))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    competitor: Mapped[Competitor] = relationship(back_populates="promotions")


class AIInsight(Base):
    __tablename__ = "ai_insights"

    id: Mapped[int] = mapped_column(IDENTITY_TYPE, Identity(), primary_key=True)
    report_type: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    recommendations_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


Index("ix_price_history_product_timestamp", PriceHistory.product_id, PriceHistory.timestamp.desc())
Index("ix_promotions_competitor_timestamp", Promotion.competitor_id, Promotion.timestamp.desc())
Index("ix_ai_insights_type_created_at", AIInsight.report_type, AIInsight.created_at.desc())
