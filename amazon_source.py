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

_ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/exec/obidos/ASIN/|/o/ASIN/)([A-Z0-9]{10})", re.IGNORECASE)
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


def _extract_asin(url: str, text: str) -> str:
    for candidate in (url, unquote(url), text):
        if not candidate:
            continue
        match = _ASIN_RE.search(candidate)
        if match:
            return match.group(1).upper()
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
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
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
        link = _child_text(entry, ("link", "guid", "id"))
        if not link:
            for child in entry:
                if child.tag.split("}", 1)[-1].lower() == "link":
                    href = child.attrib.get("href", "").strip()
                    if href:
                        link = href
                        break

        description = _child_text(entry, ("description", "summary", "encoded", "content"))
        combined_text = f"{title} {description} {link}"

        price_usd, old_price_usd = _extract_prices(combined_text)
        if price_usd is None:
            continue

        asin = _extract_asin(link, combined_text)
        free_shipping = _detect_free_shipping(combined_text)

        clean_title = re.sub(r"<[^>]+>", " ", title)
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        items.append(
            DealItem(
                title=clean_title,
                price_usd=price_usd,
                free_shipping=free_shipping,
                url=link.strip(),
                asin=asin,
                old_price_usd=old_price_usd,
            )
        )

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
