import re
from dataclasses import dataclass
from typing import Iterable, List, Optional
from urllib.parse import urlparse


@dataclass
class DealItem:
    title: str
    price_usd: float
    free_shipping: bool
    url: str
    asin: str = ""
    old_price_usd: Optional[float] = None
    notes: str = ""


_EXCLUDED_MEDIA_PATTERNS = (
    # Фильмы и сериалы
    r"\bblu-?ray\b",
    r"\b4k\s*uhd\b",
    r"\bdvd\b",
    r"\bprime\s*video\b",
    r"\bdigital\s*video\b",
    r"\bmovie\b",
    r"\bcomplete\s*series\b",
    r"\bseason\s+\d+\b",
    # Книги и аудиокниги
    r"\bkindle\b",
    r"\bpaperback\b",
    r"\bhardcover\b",
    r"\baudiobook\b",
    r"\baudible\b",
    r"\bebook\b",
    r"\bboard\s*book\b",
    r"\bmass\s*market\b",
)

_EXCLUDED_MEDIA_RE = re.compile(
    "|".join(_EXCLUDED_MEDIA_PATTERNS),
    flags=re.IGNORECASE,
)


def is_excluded_media(item: DealItem) -> bool:
    haystack = " ".join(
        part.strip()
        for part in (item.title, item.notes, item.url)
        if part and part.strip()
    )
    if not haystack:
        return False
    return bool(_EXCLUDED_MEDIA_RE.search(haystack))


def is_amazon_deal(item: DealItem) -> bool:
    if item.asin and len(item.asin.strip()) == 10:
        return True
    try:
        host = (urlparse(item.url.strip()).hostname or "").lower()
    except Exception:
        host = ""
    return "amazon." in host or "amzn." in host


def attach_partner_tag(url: str, partner_tag: str) -> str:
    clean_url = url.strip()
    tag = partner_tag.strip()
    if not clean_url or not tag:
        return clean_url
    if "tag=" in clean_url:
        return clean_url
    sep = "&" if "?" in clean_url else "?"
    return f"{clean_url}{sep}tag={tag}"


def is_valid_deal(
    item: DealItem,
    max_price_usd: float = 74.9,
    require_free_shipping: bool = False,
    require_amazon: bool = True,
    exclude_books_and_movies: bool = True,
) -> bool:
    if not item.title.strip() or not item.url.strip():
        return False
    if exclude_books_and_movies and is_excluded_media(item):
        return False
    if require_amazon and not is_amazon_deal(item):
        return False
    if item.price_usd <= 0 or item.price_usd > max_price_usd:
        return False
    if require_free_shipping and not item.free_shipping:
        return False
    return True


def filter_deals(
    items: Iterable[DealItem],
    max_price_usd: float = 74.9,
    require_free_shipping: bool = False,
    require_amazon: bool = True,
    exclude_books_and_movies: bool = True,
    max_items: int = 5,
) -> List[DealItem]:
    result: List[DealItem] = []
    for item in items:
        if is_valid_deal(
            item=item,
            max_price_usd=max_price_usd,
            require_free_shipping=require_free_shipping,
            require_amazon=require_amazon,
            exclude_books_and_movies=exclude_books_and_movies,
        ):
            result.append(item)
            if len(result) >= max_items:
                break
    return result


def format_deal_post_ru(
    item: DealItem,
    partner_tag: str = "",
    free_shipping_threshold_usd: float = 49.0,
) -> str:
    final_url = attach_partner_tag(item.url, partner_tag)

    if item.price_usd < free_shipping_threshold_usd:
        shipping_line = (
            f"Доставка в Израиль: проверьте условия (бесплатно при заказе от "
            f"${free_shipping_threshold_usd:.0f} на участвующие товары)"
        )
    elif item.free_shipping:
        shipping_line = "Бесплатная доставка в Израиль (от $49)"
    else:
        shipping_line = "Доставка в Израиль: проверьте наличие бесплатной доставки на странице товара"

    price_line = f"Цена: <b>${item.price_usd:.2f}</b>"
    if item.old_price_usd and item.old_price_usd > item.price_usd:
        discount_pct = int(round((1 - item.price_usd / item.old_price_usd) * 100))
        price_line += f" <s>${item.old_price_usd:.2f}</s> (-{discount_pct}%)"

    lines = [
        f"<b>{item.title.strip()}</b>",
        "",
        price_line,
        shipping_line,
    ]
    if item.notes.strip():
        lines.append(item.notes.strip())

    lines.extend([
        "",
        f'<a href="{final_url}">Открыть на Amazon</a>',
    ])
    return "\n".join(lines)
