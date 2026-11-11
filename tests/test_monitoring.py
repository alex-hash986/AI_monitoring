import json
import os
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from config import get_settings
from src.ai.client import InsightReport, analyze
from src.database.crud import save_scraped_data, seed_demo_data
from src.database.models import AIInsight, Base, Competitor, PriceHistory, Product, Promotion
from src.database.queries import product_price_rows
from src.scrapers.base import ScrapedProduct, ScrapedPromotion
from src.scrapers.telegram import TelegramChannelScraper
from src.scrapers.web import WebScraper


class ScraperTests(unittest.TestCase):
    def test_web_catalog_and_unavailable_site(self):
        html = """
        <script type="application/ld+json">
        {"@graph":[{"@type":"Product","sku":"P1","name":"Наушники","category":"Аудио",
        "offers":{"price":"12 490,50","oldPrice":15990,"url":"/p1"}}]}
        </script>
        <article data-product-id="P2" data-title="Монитор" data-price="29990"
                 data-old-price="31990" data-category="Мониторы"><a href="/p2">Товар</a></article>
        <p class="promotion">Промокод AUDIO</p>
        """
        transport = httpx.MockTransport(
            lambda request: httpx.Response(503 if request.url.path == "/down" else 200, text=html)
        )
        with httpx.Client(transport=transport) as client:
            scraper = WebScraper(client, "https://store.test/")
            first, second = scraper.fetch_products()
            self.assertEqual((first.external_id, first.price, first.old_price, first.category, first.url), (
                "P1", Decimal("12490.50"), Decimal("15990"), "Аудио", "https://store.test/p1",
            ))
            self.assertEqual((second.external_id, second.price, second.old_price), (
                "P2", Decimal("29990"), Decimal("31990"),
            ))
            self.assertEqual(scraper.fetch_promotions()[0].promo_type, "промокод")
            demo = WebScraper(client, "https://store.test/down").fetch_products()
            self.assertEqual(len(demo), 6)
            self.assertTrue(all(item.is_demo for item in demo))

    def test_telegram_preserves_timestamp_and_classifies_posts(self):
        html = "".join(
            f'<div class="tgme_widget_message"><div class="tgme_widget_message_text">{message}</div>'
            '<time datetime="2026-11-11T08:00:00+07:00"></time></div>'
            for message in ("Скидки 20%", "Промокод SALE", "Новая поставка")
        )

        def respond(request):
            self.assertEqual(str(request.url), "https://t.me/s/store")
            return httpx.Response(200, text=html)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            scraper = TelegramChannelScraper(client, "@store")
            posts = scraper.fetch_promotions()
            self.assertEqual([post.promo_type for post in posts], ["скидка", "промокод", "анонс"])
            self.assertEqual(posts[0].timestamp.isoformat(), "2026-11-11T08:00:00+07:00")
            self.assertEqual(scraper.fetch_products(), [])


class InsightTests(unittest.TestCase):
    @patch("src.ai.client.get_settings", return_value=SimpleNamespace(openai_api_key=None))
    @patch("src.ai.client.OpenAI")
    def test_fallback_is_deterministic_and_offline(self, openai, settings):
        data = {"products_observed": 3, "price_decreases": 2, "critical_drops": 1}
        report = analyze("trends", data)
        self.assertEqual(report, analyze("trends", data))
        self.assertEqual(report.recommendations[0].priority, "high")
        self.assertEqual(InsightReport.model_validate_json(report.model_dump_json()), report)
        openai.assert_not_called()

    @patch("src.ai.client.get_settings")
    @patch("src.ai.client.OpenAI")
    def test_validates_chat_completion(self, openai, settings):
        settings.return_value = SimpleNamespace(
            openai_api_key=SecretStr("test"), openai_model="configured-model", http_timeout_seconds=5
        )
        create = openai.return_value.__enter__.return_value.chat.completions.create
        create.return_value.choices = [SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(
                refusal=None, content='{"summary":"OK","trends":[],"recommendations":[]}'
            ),
        )]
        self.assertEqual(analyze("trends", {}).summary, "OK")
        self.assertTrue(create.call_args.kwargs["response_format"]["json_schema"]["strict"])


@unittest.skipUnless(os.environ.get("MONITORING_TEST_DATABASE_URL"), "requires PostgreSQL")
class DatabaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        url = os.environ["MONITORING_TEST_DATABASE_URL"]
        self.schema = "monitoring_test_" + uuid4().hex
        self.admin = create_engine(url)
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)
        self.engine = create_engine(url, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def drop_schema(self):
        with self.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()

    def test_scrape_upsert_price_delta_and_promotion_deduplication(self):
        product = ScrapedProduct(
            external_id="sku1", title="Товар", price=100, old_price=125,
            category="Электроника", url="https://store.test/item",
        )
        promotion = ScrapedPromotion(source="web", raw_text="Скидка", promo_type="скидка")
        with self.sessions.begin() as session:
            self.assertEqual(seed_demo_data(session), 2)
            self.assertEqual(seed_demo_data(session), 0)
            competitor = session.scalar(select(Competitor).order_by(Competitor.id))
            self.assertEqual(save_scraped_data(session, competitor.id, [product], [promotion]), [])
            self.assertEqual(save_scraped_data(session, competitor.id, [
                product.model_copy(update={"price": Decimal(90), "title": "Новое название"}),
            ], [promotion]), [])
            alerts = save_scraped_data(session, competitor.id, [
                product.model_copy(update={"price": Decimal(80)}),
            ], [promotion])
            self.assertEqual(len(alerts), 1)
            self.assertEqual(alerts[0].percent, Decimal("11.11"))
            self.assertEqual(session.scalar(select(func.count()).select_from(Product)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(Promotion)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(PriceHistory)), 3)
            self.assertEqual(product_price_rows(session)[0].previous_price, Decimal(90))

    def test_full_demo_run_persists_report_and_respects_daily_limit(self):
        from streamlit.testing.v1 import AppTest

        from src.tasks.monitoring import run_full_monitoring

        with (
            patch("src.tasks.monitoring.get_session_factory", return_value=self.sessions),
            patch("src.tasks.monitoring.get_settings", return_value=SimpleNamespace(
                demo_mode=True, http_timeout_seconds=1, telegram_bot_token=None, telegram_chat_id=None,
            )),
            patch("src.ai.client.get_settings", return_value=SimpleNamespace(openai_api_key=None)),
        ):
            result = run_full_monitoring.run()
            repeated = run_full_monitoring.run()
            forced = run_full_monitoring.run(force_report=True)

        self.assertEqual((result["status"], result["seeded"], result["price_snapshots"]),
                         ("completed", 2, 12))
        self.assertIsNotNone(result["insight_id"])
        self.assertIsNone(repeated["insight_id"])
        self.assertIsNotNone(forced["insight_id"])
        with self.sessions() as session:
            report = session.get(AIInsight, result["insight_id"])
            self.assertIn("12", json.loads(report.content)["summary"])
            self.assertEqual(len(report.recommendations_json), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(Promotion)), 2)

        with (
            patch("src.database.session.get_session_factory", return_value=self.sessions),
            patch("config.get_settings", return_value=SimpleNamespace(local_demo=False)),
            patch.object(run_full_monitoring, "apply_async", return_value=SimpleNamespace(id="test-task")) as enqueue,
            patch("src.tasks.celery_app.app.AsyncResult", return_value=SimpleNamespace(
                state="SUCCESS", result=forced,
            )),
        ):
            dashboard_path = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"
            dashboard = AppTest.from_file(str(dashboard_path), default_timeout=20).run()
            self.assertFalse(dashboard.exception)
            self.assertEqual(len(dashboard.tabs), 4)
            dashboard.button[1].click().run()
            self.assertFalse(dashboard.exception)
            self.assertEqual(enqueue.call_args.kwargs["kwargs"], {"force_report": True})

    def test_telegram_alert_runs_after_real_price_commit(self):
        from src.tasks.monitoring import run_full_monitoring

        with self.sessions.begin() as session:
            session.add(Competitor(name="Store", website_url="https://store.test"))

        client_class = httpx.Client
        prices = iter((100, 89))

        def respond(request):
            return httpx.Response(
                200, text=f'<article data-product-id="sku" data-title="Товар" data-price="{next(prices)}">'
                '<a href="/item">Товар</a></article>',
            )

        async def assert_price_committed(message):
            with self.sessions() as session:
                self.assertEqual(product_price_rows(session)[0].price, Decimal(89))
            self.assertIn("11.00%", message)

        with (
            patch("src.tasks.monitoring.get_session_factory", return_value=self.sessions),
            patch("src.tasks.monitoring.get_settings", return_value=SimpleNamespace(
                demo_mode=False, http_timeout_seconds=1, telegram_bot_token="test", telegram_chat_id="test",
            )),
            patch("src.ai.client.get_settings", return_value=SimpleNamespace(openai_api_key=None)),
            patch("src.tasks.monitoring.httpx.Client", side_effect=lambda **kwargs: client_class(
                transport=httpx.MockTransport(respond), **kwargs
            )),
            patch("src.tasks.monitoring.send_alert", side_effect=assert_price_committed) as send,
        ):
            self.assertEqual(run_full_monitoring.run()["price_drops"], 0)
            result = run_full_monitoring.run()
        self.assertEqual((result["price_drops"], result["alerts_sent"]), (1, 1))
        send.assert_awaited_once()


if __name__ == "__main__":
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    get_settings.cache_clear()
    unittest.main()
