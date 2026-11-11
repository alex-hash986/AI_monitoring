from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.database.models import AIInsight, Competitor, PriceHistory, Product, Promotion
from src.scrapers.base import ScrapedProduct, ScrapedPromotion


DEMO_COMPETITORS = ("TechStore", "GadgetHub")


@dataclass(frozen=True)
class PriceDrop:
    title: str
    url: str
    previous_price: Decimal
    price: Decimal
    percent: Decimal


def save_scraped_data(
    session: Session,
    competitor_id: int,
    products: list[ScrapedProduct],
    promotions: list[ScrapedPromotion],
) -> list[PriceDrop]:
    """Flush one competitor's observations in the caller's transaction."""
    sqlite = session.get_bind().dialect.name == "sqlite"
    if sqlite:
        # Acquire the SQLite write lock before reading prices or assigning snapshot IDs.
        session.execute(update(Competitor).where(Competitor.id == competitor_id)
                        .values(is_active=Competitor.is_active))
    session.scalars(
        select(Competitor.id).where(Competitor.id == competitor_id).with_for_update()
    ).one()

    products_by_id = {product.external_id: product for product in products}
    stored = {
        product.external_id: product
        for product in session.scalars(
            select(Product).where(
                Product.competitor_id == competitor_id,
                Product.external_id.in_(products_by_id),
            )
        )
    }
    for external_id, item in products_by_id.items():
        product = stored.get(external_id)
        if product is None:
            product = Product(competitor_id=competitor_id, external_id=external_id)
            session.add(product)
            stored[external_id] = product
        product.title = item.title
        product.category = item.category
        product.url = item.url
    session.flush()

    product_ids = [stored[key].id for key, item in products_by_id.items() if not item.is_demo]
    previous = {}
    if product_ids:
        query = (
            select(PriceHistory.product_id, PriceHistory.price)
            .where(PriceHistory.product_id.in_(product_ids), PriceHistory.is_demo.is_(False))
            .order_by(PriceHistory.product_id, PriceHistory.timestamp.desc(), PriceHistory.id.desc())
        )
        if not sqlite:
            query = query.distinct(PriceHistory.product_id)
        for product_id, price in session.execute(query):
            previous.setdefault(product_id, price)

    observed_at = datetime.now(timezone.utc)
    alerts = []
    # SQLite cannot autoincrement an ID within a composite primary key.
    last_id = (session.scalar(select(func.max(PriceHistory.id))) or 0) if sqlite and products else None
    for offset, (external_id, item) in enumerate(products_by_id.items(), start=1):
        product_id = stored[external_id].id
        discount = None
        if item.old_price and item.old_price >= item.price:
            discount = ((item.old_price - item.price) * 100 / item.old_price).quantize(Decimal("0.01"))
        snapshot = PriceHistory(
            product_id=product_id, price=item.price, old_price=item.old_price,
            discount_percent=discount, timestamp=observed_at, is_demo=item.is_demo,
        )
        if last_id is not None:
            snapshot.id = last_id + offset
        session.add(snapshot)
        last = previous.get(product_id)
        if not item.is_demo and last and item.price * 100 < last * 90:
            alerts.append(PriceDrop(
                item.title, item.url, last, item.price,
                ((last - item.price) * 100 / last).quantize(Decimal("0.01")),
            ))

    known = set()
    if promotions:
        known = {
            (row.source, row.raw_text, row.timestamp if row.source == "tg" else None)
            for row in session.execute(
                select(Promotion.source, Promotion.raw_text, Promotion.timestamp).where(
                    Promotion.competitor_id == competitor_id,
                    Promotion.raw_text.in_({item.raw_text for item in promotions}),
                )
            )
        }
    known_text = {(source, raw_text) for source, raw_text, _ in known}
    for item in promotions:
        timestamp = item.timestamp or observed_at
        identity = (item.source, item.raw_text, timestamp if item.source == "tg" else None)
        text_identity = (item.source, item.raw_text)
        if identity in known or (item.timestamp is None and text_identity in known_text):
            continue
        known.add(identity)
        known_text.add(text_identity)
        session.add(Promotion(
            competitor_id=competitor_id, source=item.source, raw_text=item.raw_text,
            promo_type=item.promo_type, timestamp=timestamp,
        ))
    session.flush()
    return alerts


def save_ai_insight(
    session: Session,
    report_type: str,
    content: str,
    recommendations_json: list[dict[str, Any]],
) -> AIInsight:
    insight = AIInsight(
        report_type=report_type, content=content, recommendations_json=recommendations_json
    )
    session.add(insight)
    session.flush()
    return insight


def seed_demo_data(session: Session) -> int:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(update(Competitor).where(False).values(is_active=Competitor.is_active))
    else:
        session.execute(select(func.pg_advisory_xact_lock(8401201)))
    if session.scalar(select(Competitor.id).limit(1)) is not None:
        return 0
    session.add_all([Competitor(name=name) for name in DEMO_COMPETITORS])
    session.flush()
    return len(DEMO_COMPETITORS)
