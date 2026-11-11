import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select

from config import get_settings
from src.ai.client import analyze
from src.bot.alerts import send_alert
from src.database.crud import DEMO_COMPETITORS, save_ai_insight, save_scraped_data, seed_demo_data
from src.database.models import AIInsight, Competitor
from src.database.queries import monitoring_observations
from src.database.session import get_session_factory
from src.scrapers.telegram import TelegramChannelScraper
from src.scrapers.web import WebScraper
from src.tasks.celery_app import app


logger = logging.getLogger(__name__)
REPORT_TYPE = "trends"


@app.task
def active_competitor_ids() -> list[int]:
    session_factory = get_session_factory()
    with session_factory() as session:
        return list(
            session.scalars(
                select(Competitor.id).where(Competitor.is_active.is_(True)).order_by(Competitor.id)
            )
        )


def generate_report(session_factory, force: bool) -> int | None:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        latest = session.scalar(
            select(AIInsight.created_at).where(AIInsight.report_type == REPORT_TYPE)
            .order_by(AIInsight.created_at.desc()).limit(1)
        )
        if not force and latest and latest.astimezone(timezone.utc).date() == now.date():
            return None
        observations = monitoring_observations(session, now)

    report = analyze(REPORT_TYPE, observations)
    with session_factory.begin() as session:
        insight = save_ai_insight(
            session, REPORT_TYPE,
            json.dumps(report.model_dump(exclude={"recommendations"}), ensure_ascii=False),
            [item.model_dump() for item in report.recommendations],
        )
        return insight.id


async def deliver_alerts(messages: list[str]) -> int:
    failures = 0
    for message in messages:
        try:
            await send_alert(message)
        except Exception as error:
            logger.warning("Telegram alert delivery failed (%s)", type(error).__name__)
            failures += 1
    return failures


def collect_monitoring(session_factory, force_report: bool) -> dict:
    settings = get_settings()
    with session_factory.begin() as session:
        seeded = seed_demo_data(session)
        competitors = list(session.scalars(
            select(Competitor).where(Competitor.is_active.is_(True)).order_by(Competitor.id)
        ))

    messages, issues = [], []
    product_count = 0
    with httpx.Client(timeout=settings.http_timeout_seconds, follow_redirects=True) as client:
        for competitor in competitors:
            products, promotions = [], []
            demo_mode = settings.demo_mode or (
                competitor.name in DEMO_COMPETITORS and not competitor.website_url
            )
            if not (demo_mode or competitor.website_url or competitor.tg_channel):
                issues.append(f"{competitor.name}: не настроены источники мониторинга.")
                continue
            try:
                web = WebScraper(
                    client, competitor.website_url,
                    demo_mode=demo_mode,
                    demo_key=competitor.name,
                )
                products = web.fetch_products()
                promotions = web.fetch_promotions()
                if competitor.website_url and not settings.demo_mode and not products:
                    issues.append(f"{competitor.name}: в веб-каталоге не найдены товары.")
                if competitor.website_url and not settings.demo_mode and any(p.is_demo for p in products):
                    issues.append(f"{competitor.name}: сайт недоступен, записаны демо-цены.")
            except (httpx.HTTPError, ValueError):
                logger.exception("Web collection failed for competitor %s", competitor.id)
                issues.append(f"{competitor.name}: ошибка сбора с сайта.")

            if competitor.tg_channel and not settings.demo_mode:
                try:
                    promotions.extend(TelegramChannelScraper(client, competitor.tg_channel).fetch_promotions())
                except (httpx.HTTPError, ValueError):
                    logger.exception("Telegram collection failed for competitor %s", competitor.id)
                    issues.append(f"{competitor.name}: ошибка сбора из Telegram.")

            if products or promotions:
                with session_factory.begin() as session:
                    drops = save_scraped_data(session, competitor.id, products, promotions)
                product_count += len(products)
                messages.extend(
                    f"{competitor.name}: {drop.title[:500]}\n"
                    f"{drop.previous_price:.2f} → {drop.price:.2f} (−{drop.percent}%)\n{drop.url[:1000]}"
                    for drop in drops
                )

    alerts_enabled = settings.telegram_bot_token is not None and settings.telegram_chat_id is not None
    failed_alerts = asyncio.run(deliver_alerts(messages)) if messages and alerts_enabled else 0
    if failed_alerts:
        issues.append(f"Не доставлено алертов в Telegram: {failed_alerts}.")
    insight_id = None
    try:
        insight_id = generate_report(session_factory, force_report)
    except Exception:
        logger.exception("Insight generation failed")
        issues.append("Не удалось сформировать AI-отчёт. Подробности в журнале Celery.")

    return {
        "status": "completed_with_issues" if issues else "completed",
        "seeded": seeded, "competitors": len(competitors), "price_snapshots": product_count,
        "price_drops": len(messages), "alerts_sent": len(messages) - failed_alerts if alerts_enabled else 0,
        "insight_id": insight_id, "issues": issues,
    }


@app.task
def run_full_monitoring(force_report: bool = False) -> dict:
    session_factory = get_session_factory()
    # A separate transaction owns the run lock while writes commit per competitor.
    with session_factory.begin() as lock_session:
        acquired = lock_session.scalar(select(func.pg_try_advisory_xact_lock(8401202)))
        if not acquired:
            return {"status": "already_running"}
        return collect_monitoring(session_factory, force_report)
