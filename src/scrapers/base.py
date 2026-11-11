from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


Money = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ScrapedProduct(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    external_id: Identifier
    title: str = Field(min_length=1)
    price: Money
    old_price: Money | None = None
    category: str = Field(max_length=255)
    url: str = Field(min_length=1)
    is_demo: bool = False


class ScrapedPromotion(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source: Literal["web", "tg"]
    raw_text: str = Field(min_length=1)
    promo_type: str = Field(min_length=1, max_length=100)
    timestamp: AwareDatetime | None = None


class BaseScraper(ABC):
    @abstractmethod
    def fetch_products(self) -> list[ScrapedProduct]:
        """Return normalized product observations."""

    @abstractmethod
    def fetch_promotions(self) -> list[ScrapedPromotion]:
        """Return normalized promotion observations."""


def promotion_type(text: str) -> str:
    normalized = text.casefold()
    if any(word in normalized for word in ("промокод", "купон", "promo code", "coupon")):
        return "промокод"
    if any(word in normalized for word in ("скид", "распродаж", "discount", "sale", "%")):
        return "скидка"
    return "анонс"
