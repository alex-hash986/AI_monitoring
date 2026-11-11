import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from functools import cached_property
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag
from pydantic import ValidationError

from src.scrapers.base import BaseScraper, ScrapedProduct, ScrapedPromotion, promotion_type


DEMO_CATALOG = (
    ("phone", "Samsung Galaxy A55 128 ГБ", "Смартфоны", 32990, 36990),
    ("laptop", "Lenovo IdeaPad Slim 5 16 ГБ / 512 ГБ", "Ноутбуки", 72990, 79990),
    ("headphones", "Sony WH-CH720N", "Наушники", 10490, 11990),
    ("monitor", "LG UltraGear 27″ 144 Гц", "Мониторы", 24990, 27990),
    ("ssd", "Samsung 990 EVO Plus 1 ТБ", "Накопители", 8990, 9990),
    ("tablet", "Xiaomi Pad 7 128 ГБ", "Планшеты", 29990, 32990),
)


def parse_price(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        number = Decimal(str(value))
        return number if number.is_finite() and number >= 0 else None
    match = re.search(r"\d[\d\s.,]*", str(value))
    if not match or str(value)[:match.start()].rstrip().endswith(("-", "−")):
        return None
    number = re.sub(r"\s+", "", match.group()).rstrip(".,")
    separator = max(number.rfind("."), number.rfind(","))
    if separator >= 0 and 0 < len(number) - separator - 1 <= 2:
        number = re.sub(r"[.,]", "", number[:separator]) + "." + number[separator + 1:]
    else:
        number = re.sub(r"[.,]", "", number)
    try:
        return Decimal(number)
    except InvalidOperation:
        return None


def structured_products(node):
    if isinstance(node, list):
        for child in node:
            yield from structured_products(child)
    elif isinstance(node, dict):
        node_type = node.get("@type", [])
        types = [node_type] if isinstance(node_type, str) else node_type
        if isinstance(types, list) and any(
            str(value).rsplit("/", 1)[-1] == "Product" for value in types
        ):
            yield node
        for child in node.values():
            if isinstance(child, (dict, list)):
                yield from structured_products(child)


def field(card: Tag, name: str, selector: str | None = None) -> str | None:
    attr = card.get(f"data-{name}")
    if isinstance(attr, str) and attr.strip():
        return attr.strip()
    node = card.select_one(f'[itemprop="{name}"], [data-field="{name}"]')
    if node is None and selector:
        node = card.select_one(selector)
    if node is None:
        return None
    if name == "url" and node.get("href"):
        return node["href"]
    return node.get("content") or node.get_text(" ", strip=True)


class WebScraper(BaseScraper):
    def __init__(self, client: httpx.Client, url: str | None, *, demo_mode: bool = False,
                 demo_key: str = "TechStore") -> None:
        self.client = client
        self.url = url
        self.demo_mode = demo_mode
        self.demo_key = demo_key

    @cached_property
    def page(self) -> tuple[str, BeautifulSoup] | None:
        if self.demo_mode or not self.url:
            return None
        try:
            response = self.client.get(self.url)
            response.raise_for_status()
        except (httpx.HTTPError, httpx.InvalidURL):
            return None
        return str(response.url), BeautifulSoup(response.text, "html.parser")

    def _demo_products(self) -> list[ScrapedProduct]:
        seed = hashlib.sha256(self.demo_key.encode()).digest()[0]
        return [
            ScrapedProduct(
                external_id=f"demo:{sku}", title=title, category=category,
                price=Decimal(price + seed * 10), old_price=Decimal(old_price + seed * 10),
                url=f"https://demo.invalid/{sku}", is_demo=True,
            )
            for sku, title, category, price, old_price in DEMO_CATALOG
        ]

    def _product(self, page_url: str, *, sku, title, price, old_price, category, link):
        if not isinstance(title, str) or price is None or not isinstance(link, str) or not link.strip():
            return None
        try:
            url = urljoin(page_url, link)
            parsed_url = urlsplit(url)
        except ValueError:
            return None
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            return None
        identity = str(sku).strip() if isinstance(sku, (str, int)) else ""
        if not identity or identity.startswith("demo:") or len(identity) > 255:
            identity = "url:" + hashlib.sha256(url.encode()).hexdigest()
        try:
            return ScrapedProduct(
                external_id=identity, title=title, price=price, old_price=old_price,
                category=str(category or "Без категории")[:255], url=url,
            )
        except ValidationError:
            return None

    def fetch_products(self) -> list[ScrapedProduct]:
        if self.page is None:
            return self._demo_products() if self.url or self.demo_mode else []

        url, soup = self.page
        products = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.get_text(), parse_float=Decimal)
            except (ValueError, RecursionError):
                continue
            for item in structured_products(payload):
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = next(
                        (offer for offer in offers if isinstance(offer, dict)
                         and parse_price(offer.get("price", offer.get("lowPrice"))) is not None),
                        {},
                    )
                if not isinstance(offers, dict):
                    continue
                product = self._product(
                    url, sku=item.get("sku") or item.get("productID") or item.get("mpn"),
                    title=item.get("name"), price=parse_price(offers.get("price", offers.get("lowPrice"))),
                    old_price=parse_price(offers.get("oldPrice", item.get("oldPrice"))),
                    category=item.get("category"),
                    link=item.get("url") or offers.get("url") or (url if isinstance(payload, dict)
                          and item is payload else None),
                )
                if product:
                    products[product.external_id] = product

        known_urls = {product.url for product in products.values()}
        for card in soup.select('[itemscope][itemtype$="/Product"], [data-product-id], .product-card'):
            anchor = card.select_one("a[href]")
            product = self._product(
                url, sku=field(card, "sku") or card.get("data-product-id"),
                title=field(card, "name", ".product-title") or card.get("data-title"),
                price=parse_price(field(card, "price", ".current-price, .product-price")),
                old_price=parse_price(field(card, "old-price", ".old-price") or field(card, "oldPrice")),
                category=field(card, "category", ".product-category"),
                link=field(card, "url") or (anchor.get("href") if anchor else None) or url,
            )
            if product and product.url not in known_urls and product.external_id not in products:
                products[product.external_id] = product
                known_urls.add(product.url)
        return list(products.values())

    def fetch_promotions(self) -> list[ScrapedPromotion]:
        if self.page is None:
            if self.url or self.demo_mode:
                return [
                    ScrapedPromotion(source="web", raw_text="Демо: скидки на электронику из каталога.",
                                     promo_type="скидка")
                ]
            return []
        _, soup = self.page
        texts = [node.get_text(" ", strip=True) for node in soup.select("[data-promotion], .promotion")]
        return [
            ScrapedPromotion(source="web", raw_text=text, promo_type=promotion_type(text))
            for text in dict.fromkeys(filter(None, texts))
        ]
