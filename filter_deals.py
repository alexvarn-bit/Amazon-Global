import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
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


_EXCLUDED_PATTERNS = (
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
    # Софт и цифровые коды
    r"\bsoftware\b",
    r"\blicense\s*key\b",
    r"\bactivation\s*code\b",
    r"\bdigital\s*code\b",
    r"\bwindows\s*1[01]\b",
    r"\boffice\s*365\b",
    r"\bantivirus\b",
    r"\bsubscription\b",
    # Игры и игровые платформы
    r"\bps[45]\b",
    r"\bplaystation\b",
    r"\bxbox\b",
    r"\bnintendo\s*switch\b",
    r"\bsteam\s*key\b",
    r"\bgame\s*code\b",
    r"\bvideo\s*game\b",
    # БАДы, витамины и добавки
    r"\bsupplements?\b",
    r"\bvitamins?\b",
    r"\bmelatonin\b",
    r"\bcollagen\b",
    r"\bcreatine\b",
    r"\bprobiotics?\b",
    r"\bomega-?3\b",
    r"\bgummies\b",
    # Еда, снеки, соусы и напитки
    r"\bfood\b",
    r"\bsnacks?\b",
    r"\bcandy\b",
    r"\bchocolate\b",
    r"\bcookies?\b",
    r"\bchips?\b",
    r"\bsauce\b",
    r"\bketchup\b",
    r"\bmayonnaise\b",
    r"\bsauerkraut\b",
    r"\bcoffee\b",
    r"\btea\b",
    r"\bjuice\b",
    r"\bsoda\b",
    r"\bbeer\b",
    r"\bwhisky\b",
    r"\bwhiskey\b",
    r"\barak\b",
    r"\bprotein\s+powder\b",
)

_EXCLUDED_RE = re.compile(
    "|".join(_EXCLUDED_PATTERNS),
    flags=re.IGNORECASE,
)

_AMAZON_ALLOWED_HOSTS = {
    "amazon.com",
    "www.amazon.com",
    "amzn.to",
    "www.amzn.to",
    "a.co",
}

_BAD_PATH_MARKERS = (
    "/f/",
    "/newsearch",
    "/redirect",
    "/gp/redirect",
    "/s?",
    "/s/",
)

_TRANSLATION_CACHE: Dict[str, str] = {}


def translate_title_to_ru(title: str, timeout_sec: int = 8) -> str:
    clean_title = re.sub(r"\s+", " ", (title or "").strip())
    if not clean_title:
        return ""
    if clean_title in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[clean_title]

    query = urllib.parse.urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "ru",
        "dt": "t",
        "q": clean_title,
    })
    url = f"https://translate.googleapis.com/translate_a/single?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
            parts = [
                seg[0]
                for seg in parsed[0]
                if isinstance(seg, list) and seg and isinstance(seg[0], str)
            ]
            translated = re.sub(r"\s+", " ", "".join(parts)).strip()
            if translated:
                _TRANSLATION_CACHE[clean_title] = translated
                return translated
    except urllib.error.HTTPError as exc:
        print(f"Translate HTTP error {exc.code}: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"Translate error: {exc}", file=sys.stderr)

    _TRANSLATION_CACHE[clean_title] = clean_title
    return clean_title


def is_excluded_category(item: DealItem) -> bool:
    haystack = " ".join(
        part.strip()
        for part in (item.title, item.notes, item.url)
        if part and part.strip()
    )
    if not haystack:
        return False
    return bool(_EXCLUDED_RE.search(haystack))


def has_clean_amazon_url(url: str) -> bool:
    clean = (url or "").strip()
    if not clean.startswith(("http://", "https://")):
        return False
    try:
        parsed = urlparse(clean)
        host = (parsed.hostname or "").lower()
        path_and_query = f"{parsed.path or ''}?{parsed.query or ''}".lower()
    except Exception:
        return False

    if host not in _AMAZON_ALLOWED_HOSTS:
        return False
    if any(marker in path_and_query for marker in _BAD_PATH_MARKERS):
        return False
    return True


def is_amazon_deal(item: DealItem) -> bool:
    return has_clean_amazon_url(item.url)


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
    exclude_unwanted_categories: bool = True,
) -> bool:
    if not item.title.strip() or not item.url.strip():
        return False
    if exclude_unwanted_categories and is_excluded_category(item):
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
    exclude_unwanted_categories: bool = True,
    max_items: int = 5,
) -> List[DealItem]:
    result: List[DealItem] = []
    for item in items:
        if is_valid_deal(
            item=item,
            max_price_usd=max_price_usd,
            require_free_shipping=require_free_shipping,
            require_amazon=require_amazon,
            exclude_unwanted_categories=exclude_unwanted_categories,
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
    ru_title = translate_title_to_ru(item.title)

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
        f"<b>{ru_title}</b>",
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
