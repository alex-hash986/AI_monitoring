import re
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from src.scrapers.base import BaseScraper, ScrapedProduct, ScrapedPromotion, promotion_type


class TelegramChannelScraper(BaseScraper):
    def __init__(self, client: httpx.Client, channel: str) -> None:
        self.client = client
        channel = channel.strip().rstrip("/")
        if "://" in channel:
            parsed = urlsplit(channel)
            if parsed.hostname not in {"t.me", "telegram.me"}:
                raise ValueError("Expected a public Telegram channel")
            channel = parsed.path.strip("/").removeprefix("s/")
        channel = channel.removeprefix("@")
        if not re.fullmatch(r"[A-Za-z0-9_]+", channel):
            raise ValueError("Expected a public Telegram channel username")
        self.url = f"https://t.me/s/{channel}"

    def fetch_products(self) -> list[ScrapedProduct]:
        return []

    def fetch_promotions(self) -> list[ScrapedPromotion]:
        response = self.client.get(self.url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        promotions = []
        for post in soup.select(".tgme_widget_message"):
            message = post.select_one(".tgme_widget_message_text")
            date = post.select_one("time[datetime]")
            if message is None or date is None:
                continue
            text = message.get_text(" ", strip=True)
            try:
                timestamp = datetime.fromisoformat(date["datetime"])
            except ValueError:
                continue
            if text and timestamp.tzinfo is not None:
                promotions.append(ScrapedPromotion(
                    source="tg", raw_text=text, promo_type=promotion_type(text), timestamp=timestamp,
                ))
        return promotions
