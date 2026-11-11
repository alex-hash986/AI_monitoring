from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session, aliased

from src.database.models import Competitor, PriceHistory, Product, Promotion


def product_price_rows(session: Session):
    if session.get_bind().dialect.name == "sqlite":
        history = PriceHistory.__table__
        current = history.alias("current_price")
        previous = history.alias("previous_price")
        current_id = (
            select(history.c.id).where(history.c.product_id == Product.id)
            .order_by(history.c.timestamp.desc(), history.c.id.desc()).limit(1)
            .correlate(Product).scalar_subquery()
        )
        previous_price = (
            select(previous.c.price).where(
                previous.c.product_id == Product.id,
                previous.c.is_demo == current.c.is_demo,
                (previous.c.timestamp < current.c.timestamp)
                | ((previous.c.timestamp == current.c.timestamp) & (previous.c.id < current.c.id)),
            ).order_by(previous.c.timestamp.desc(), previous.c.id.desc()).limit(1)
            .correlate(Product, current).scalar_subquery()
        )
        return session.execute(
            price_summary(current, previous_price).outerjoin(current, current.c.id == current_id)
        ).all()

    current = (
        select(PriceHistory.price, PriceHistory.timestamp, PriceHistory.id, PriceHistory.is_demo)
        .where(PriceHistory.product_id == Product.id)
        .order_by(PriceHistory.timestamp.desc(), PriceHistory.id.desc())
        .limit(1)
        .correlate(Product)
        .lateral("current_price")
    )
    history = aliased(PriceHistory)
    previous = (
        select(history.price)
        .where(
            history.product_id == Product.id,
            history.is_demo == current.c.is_demo,
            (history.timestamp < current.c.timestamp)
            | ((history.timestamp == current.c.timestamp) & (history.id < current.c.id)),
        )
        .order_by(history.timestamp.desc(), history.id.desc())
        .limit(1)
        .correlate(Product, current)
        .lateral("previous_price")
    )
    return session.execute(
        price_summary(current, previous.c.price)
        .outerjoin(current, true())
        .outerjoin(previous, true())
    ).all()


def price_summary(current, previous_price):
    return (
        select(
            Product.id, Product.title, Product.category, Product.external_id,
            Competitor.name.label("competitor"), current.c.price,
            previous_price.label("previous_price"), current.c.timestamp, current.c.is_demo,
        )
        .select_from(Product)
        .join(Competitor)
        .order_by(Competitor.name, Product.title, Product.id)
    )


def monitoring_observations(session: Session, now: datetime) -> dict:
    since = now - timedelta(hours=24)
    rows = [row for row in product_price_rows(session) if row.timestamp and row.timestamp >= since]
    real = [row for row in rows if not row.is_demo]
    comparable = [row for row in real if row.previous_price is not None]
    increases = sum(row.price > row.previous_price for row in comparable)
    decreases = sum(row.price < row.previous_price for row in comparable)
    drops = sum(row.price * 100 < row.previous_price * 90 for row in comparable)
    promotion_count = session.scalar(
        select(func.count()).select_from(Promotion).where(Promotion.timestamp >= since)
    )
    promotions = session.execute(
        select(Competitor.name, Promotion.source, Promotion.promo_type, Promotion.raw_text)
        .select_from(Promotion)
        .join(Competitor)
        .where(Promotion.timestamp >= since)
        .order_by(Promotion.timestamp.desc(), Promotion.id.desc())
        .limit(20)
    )
    changes = sorted(
        (row for row in comparable if row.price != row.previous_price),
        key=lambda row: abs(row.price - row.previous_price) / (row.previous_price or Decimal(1)),
        reverse=True,
    )
    return {
        "period_start": since.astimezone(timezone.utc).isoformat(),
        "products_observed": len(real),
        "demo_products_observed": len(rows) - len(real),
        "comparable_products": len(comparable),
        "price_increases": increases,
        "price_decreases": decreases,
        "critical_drops": drops,
        "promotion_count": promotion_count,
        "price_changes": [
            {
                "competitor": row.competitor, "product": row.title,
                "price": str(row.price), "previous_price": str(row.previous_price),
            }
            for row in changes[:30]
        ],
        "promotions": [
            {"competitor": name, "source": source, "type": kind, "text": raw[:500]}
            for name, source, kind, raw in promotions
        ],
    }
