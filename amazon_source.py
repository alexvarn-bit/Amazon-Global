import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Iterable, List, Optional, Set
from urllib.parse import unquote, urlparse

from filter_deals import DealItem

POSTED_ASINS_FILE = "posted_asins.json"

_ASIN_RE = re.compile(
    r"(?:/dp/|/gp/product/|/exec/obidos/ASIN/|/o/ASIN/|/product/|asin=|/aw/d/)([A-Z0-9]{10})",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_IMG_SRC_RE = re.compile(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["']og:image["'][^>]+content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_AMAZON_IMG_RE = re.compile(
    r"""https?://m\.media-amazon\.com/images/[IP]/[^"'\\\s<>]+\.(?:jpg|jpeg|png|webp)""",
    re.IGNORECASE,
)
_ANY_URL_RE = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)
_PRICE_RE = re.compile(r"(?:\$|USD\s*)(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)", re.IGNORECASE)
_OLD_PRICE_RE = re.compile(
    r"(?:was|list(?:\s*price)?|reg(?:\.|\s*price)?|<s>|<del>)[^\d$]{0,15}\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_FREE_SHIP_RE = re.compile(
    r"\b(?:free\s+shipping|free\s+international\s+shipping|free\s+s&h|free\s+delivery|бесплатная\s+доставка)\b",
    re.IGNORECASE,
)
_PAID_SHIP_RE = re.compile(
    r"\b(?:shipping\s*:?\s*\$\s*\d+(?:\.\d{1,2})?|\$\s*\d+(?:\.\d{1,2})?\s+shipping|платная\s+доставка)\b",
    re.IGNORECASE,
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_posted_asins(filepath: str = POSTED_ASINS_FILE) -> Set[str]:
    if not os.path.exists(filepath):
        return set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x).strip().upper() for x in data if str(x).strip()}
        if isinstance(data, dict) and isinstance(data.get("posted"), list):
            return {str(x).strip().upper() for x in data["posted"] if str(x).strip()}
    except Exception as exc:
        print(f"Warning: failed to load {filepath}: {exc}", file=sys.stderr)
    return set()


def save_posted_asins(posted: Iterable[str], filepath: str = POSTED_ASINS_FILE) -> None:
    cleaned = sorted({str(x).strip().upper() for x in posted if str(x).strip()})
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _is_amazon_url(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        host = ""
    return "amazon." in host or "amzn." in host or host == "a.co"


def _unwrap_redirect_url(raw_url: str) -> str:
    candidate = html.unescape(raw_url).strip().rstrip(".,);]}>")
    decoded = unquote(candidate)
    nested_matches = _ANY_URL_RE.findall(decoded)
    for m in nested_matches:
        clean_m = m.rstrip(".,);]}>")
        if _is_amazon_url(clean_m):
            return clean_m
    return candidate


def _resolve_amazon_redirect(url: str, timeout_sec: int = 10) -> str:
    clean = (url or "").strip()
    if not clean or not _is_amazon_url(clean):
        return clean
    host = (urlparse(clean).hostname or "").lower()
    if host not in {"amzn.to", "www.amzn.to", "a.co"} and "/gp/redirect" not in clean:
        return clean

    req = urllib.request.Request(clean, headers=_BROWSER_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            final_url = resp.geturl() or clean
            return _unwrap_redirect_url(final_url)
    except Exception:
        return clean


def _extract_amazon_url(primary_link: str, raw_text: str) -> str:
    unescaped_text = html.unescape(raw_text)
    candidates: List[str] = []

    if primary_link:
        candidates.append(_unwrap_redirect_url(primary_link))

    for m in _HREF_RE.findall(unescaped_text):
        candidates.append(_unwrap_redirect_url(m))

    for m in _ANY_URL_RE.findall(unescaped_text):
        candidates.append(_unwrap_redirect_url(m))

    for m in _ANY_URL_RE.findall(unquote(unescaped_text)):
        candidates.append(_unwrap_redirect_url(m))

    for cand in candidates:
        if _is_amazon_url(cand):
            return _resolve_amazon_redirect(cand)

    return _unwrap_redirect_url(primary_link)


def _fetch_amazon_page_image(amazon_url: str, timeout_sec: int = 10) -> str:
    if not amazon_url or not _is_amazon_url(amazon_url):
        return ""
    req = urllib.request.Request(amazon_url, headers=_BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read(250_000)
        encoding = resp.headers.get_content_charset() or "utf-8"
        page_html = raw.decode(encoding, errors="replace")
    except Exception:
        return ""

    og_match = _OG_IMAGE_RE.search(page_html)
    if og_match:
        og_url = html.unescape(og_match.group(1)).strip()
        if og_url.startswith(("http://", "https://")) and "captcha" not in og_url.lower():
            return og_url

    img_matches = _AMAZON_IMG_RE.findall(page_html)
    for img_url in img_matches:
        clean_img = html.unescape(img_url).strip()
        if "_SS40_" not in clean_img and "_US40_" not in clean_img:
            return clean_img

    return ""


def _extract_image_url(
    entry: ET.Element,
    raw_description: str,
    resolved_url: str = "",
    asin: str = "",
) -> str:
    for child in entry.iter():
        local = child.tag.split("}", 1)[-1].lower()
        if local in {"enclosure", "content", "thumbnail"}:
            url_attr = (child.attrib.get("url") or "").strip()
            media_type = (child.attrib.get("type") or "").lower()
            if url_attr.startswith(("http://", "https://")) and (
                "image" in media_type
                or local == "thumbnail"
                or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", url_attr, re.I)
            ):
                return html.unescape(url_attr)

    unescaped = html.unescape(raw_description)
    img_match = _IMG_SRC_RE.search(unescaped)
    if img_match:
        img_url = img_match.group(1).strip()
        if img_url.startswith(("http://", "https://")):
            return html.unescape(img_url)

    page_img = _fetch_amazon_page_image(resolved_url)
    if page_img:
        return page_img

    clean_asin = (asin or "").strip().upper()
    if len(clean_asin) == 10 and not clean_asin.startswith("0"):
        return f"https://m.media-amazon.com/images/P/{clean_asin}.01._SCLZZZZZZZ_.jpg"

    return ""


def _extract_asin(url: str, text: str) -> str:
    for candidate in (url, unquote(url), html.unescape(text), unquote(html.unescape(text))):
        if not candidate:
            continue
        match = _ASIN_RE.search(candidate)
        if match:
            asin = match.group(1).upper()
            if not asin.startswith("0"):
                return asin
    return ""


def _parse_price_num(raw: str) -> Optional[float]:
    try:
        val = float(raw.replace(",", "").strip())
        return val if val > 0 else None
    except ValueError:
        return None


def _extract_prices(text: str) -> tuple[Optional[float], Optional[float]]:
    matches = [_parse_price_num(m) for m in _PRICE_RE.findall(text)]
    prices = [p for p in matches if p is not None]
    if not prices:
        return None, None

    current_price = prices[0]
    old_price: Optional[float] = None

    old_match = _OLD_PRICE_RE.search(text)
    if old_match:
        parsed_old = _parse_price_num(old_match.group(1))
        if parsed_old and parsed_old > current_price:
            old_price = parsed_old

    if old_price is None and len(prices) > 1:
        higher = [p for p in prices[1:] if p > current_price]
        if higher:
            old_price = max(higher)

    return current_price, old_price


def _detect_free_shipping(text: str) -> bool:
    if _PAID_SHIP_RE.search(text) and not _FREE_SHIP_RE.search(text):
        return False
    return bool(_FREE_SHIP_RE.search(text))


def _fetch_xml(url: str, timeout_sec: int = 20) -> Optional[str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _BROWSER_HEADERS["User-Agent"],
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as exc:
        print(f"Feed HTTP error {exc.code} for {url}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"Feed fetch error for {url}: {exc}", file=sys.stderr)
        return None


def _child_text(elem: ET.Element, names: tuple[str, ...]) -> str:
    for child in elem:
        local = child.tag.split("}", 1)[-1].lower()
        if local in names:
            if child.text and child.text.strip():
                return child.text.strip()
            combined = "".join(child.itertext()).strip()
            if combined:
                return combined
    return ""


def _parse_feed_items(xml_text: str) -> List[DealItem]:
    items: List[DealItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"XML parse error: {exc}", file=sys.stderr)
        return items

    entries: List[ET.Element] = []
    for elem in root.iter():
        local = elem.tag.split("}", 1)[-1].lower()
        if local in {"item", "entry"}:
            entries.append(elem)

    for entry in entries:
        title = _child_text(entry, ("title",))
        raw_link = _child_text(entry, ("link", "guid", "id"))
        if not raw_link:
            for child in entry:
                if child.tag.split("}", 1)[-1].lower() == "link":
                    href = child.attrib.get("href", "").strip()
                    if href:
                        raw_link = href
                        break

        description = _child_text(entry, ("description", "summary", "encoded", "content"))
        combined_text = f"{title} {description} {raw_link}"

        price_usd, old_price_usd = _extract_prices(combined_text)
        if price_usd is None:
            continue

        resolved_url = _extract_amazon_url(raw_link, combined_text)
        asin = _extract_asin(resolved_url, combined_text)
        free_shipping = _detect_free_shipping(combined_text)
        image_url = _extract_image_url(entry, description, resolved_url=resolved_url, asin=asin)

        clean_title = html.unescape(re.sub(r"<[^>]+>", " ", title))
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        item = DealItem(
            title=clean_title,
            price_usd=price_usd,
            free_shipping=free_shipping,
            url=resolved_url,
            asin=asin,
            old_price_usd=old_price_usd,
        )
        setattr(item, "image_url", image_url)
        items.append(item)

    return items


def _short_feed_label(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or url
    path = parsed.path.rstrip("/")
    return f"{host}{path}" if path else host


def _log_per_feed_counts(per_feed_counts: List[tuple[str, int]]) -> None:
    if not per_feed_counts:
        return
    parts = [f"{_short_feed_label(u)}: {cnt}" for u, cnt in per_feed_counts]
    print("Per-feed incoming deals -> " + " | ".join(parts))


def fetch_live_deals(
    feed_urls: List[str],
    timeout_sec: int = 20,
    posted_asins: Optional[Set[str]] = None,
) -> List[DealItem]:
    seen_keys: Set[str] = {k.strip().upper() for k in (posted_asins or set()) if k.strip()}
    all_deals: List[DealItem] = []
    per_feed_counts: List[tuple[str, int]] = []

    for url in feed_urls:
        clean_url = url.strip()
        if not clean_url:
            continue

        xml_text = _fetch_xml(clean_url, timeout_sec=timeout_sec)
        if not xml_text:
            per_feed_counts.append((clean_url, 0))
            continue

        parsed_items = _parse_feed_items(xml_text)
        added_from_feed = 0

        for item in parsed_items:
            dedup_key = (item.asin or item.url).strip().upper()
            if dedup_key and dedup_key in seen_keys:
                continue
            if dedup_key:
                seen_keys.add(dedup_key)
            all_deals.append(item)
            added_from_feed += 1

        per_feed_counts.append((clean_url, added_from_feed))

    _log_per_feed_counts(per_feed_counts)
    return all_deals
