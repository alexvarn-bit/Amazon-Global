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
    require_free_shipping: bool = True,
    require_amazon: bool = True,
) -> bool:
    if not item.title.strip() or not item.url.strip():
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
    require_free_shipping: bool = True,
    require_amazon: bool = True,
    max_items: int = 5,
) -> List[DealItem]:
    result: List[DealItem] = []
    for item in items:
        if is_valid_deal(
            item=item,
            max_price_usd=max_price_usd,
            require_free_shipping=require_free_shipping,
            require_amazon=require_amazon,
        ):
            result.append(item)
            if len(result) >= max_items:
                break
    return result


def format_deal_post_ru(item: DealItem, partner_tag: str = "") -> str:
    final_url = attach_partner_tag(item.url, partner_tag)
    shipping_line = (
        "Бесплатная доставка в Израиль" if item.free_shipping else "Доставка платная"
    )

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
