import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Set
from urllib.parse import urlparse

from filter_deals import DealItem

ASIN_PATTERN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")

FREE_SHIPPING_MARKERS = (
    "free shipping",
    "free international shipping",
    "free s&h",
    "free delivery",
    "бесплатная доставка",
)
PAID_OR_NO_SHIPPING_MARKERS = (
    "no shipping",
    "shipping extra",
    "plus shipping",
    "in-store only",
    "pickup only",
)


def extract_asin(url: str) -> str:
    match = ASIN_PATTERN.search(url or "")
    return match.group(1).upper() if match else ""


def _is_amazon_url_or_text(url: str, description: str = "") -> bool:
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower()
    except Exception:
        host = ""
    if "amazon." in host or "amzn." in host:
        return True
    combined = f"{url} {description}".lower()
    return "amazon.com" in combined or "amzn.to" in combined or "/dp/" in combined


def normalize_amazon_url(url: str, asin: str = "") -> str:
    clean = (url or "").strip()
    detected_asin = asin or extract_asin(clean)
    if detected_asin:
        return f"https://www.amazon.com/dp/{detected_asin}"
    return clean


def load_posted_asins(state_file: str = "posted_asins.json") -> Set[str]:
    if not os.path.exists(state_file):
        return set()
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(x).strip().upper() for x in data if str(x).strip()}
    except Exception:
        pass
    return set()


def save_posted_asins(asins: Set[str], state_file: str = "posted_asins.json", limit: int = 500) -> None:
    trimmed = sorted(asins)[-limit:]
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def _fetch_text(url: str, timeout_sec: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DealFinderBot/1.0)",
            "Accept": "application/rss+xml, application/xml, application/json, text/xml;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _detect_free_shipping(text_lower: str) -> bool:
    if any(bad in text_lower for bad in PAID_OR_NO_SHIPPING_MARKERS):
        return False
    return any(marker in text_lower for marker in FREE_SHIPPING_MARKERS)


def parse_rss_deals(xml_text: str) -> List[DealItem]:
    deals: List[DealItem] = []
    root = ET.fromstring(xml_text)

    for item_el in root.findall(".//item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        description = (item_el.findtext("description") or "").strip()

        if not title or not link:
            continue

        asin = extract_asin(link) or extract_asin(description)
        if not asin and not _is_amazon_url_or_text(link, description):
            continue

        clean_url = normalize_amazon_url(link, asin)

        combined_text = f"{title} {description}"
        price_matches = PRICE_PATTERN.findall(combined_text)
        if not price_matches:
            continue

        prices = [float(p) for p in price_matches if float(p) > 0]
        if not prices:
            continue

        current_price = min(prices)
        old_price = max(prices) if len(prices) > 1 and max(prices) > current_price else None
        free_shipping = _detect_free_shipping(combined_text.lower())

        deals.append(
            DealItem(
                title=title,
                price_usd=current_price,
                free_shipping=free_shipping,
                url=clean_url,
                asin=asin,
                old_price_usd=old_price,
            )
        )

    return deals


def parse_json_deals(json_text: str) -> List[DealItem]:
    data = json.loads(json_text)
    raw_items = data if isinstance(data, list) else data.get("items", [])
    deals: List[DealItem] = []

    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("url", "")).strip()
        asin = str(entry.get("asin", "")).strip().upper() or extract_asin(url)
        notes = str(entry.get("notes", "")).strip()

        if not asin and not _is_amazon_url_or_text(url, notes):
            continue

        try:
            price_usd = float(entry.get("price_usd", 0))
        except (TypeError, ValueError):
            continue

        old_price_raw = entry.get("old_price_usd")
        old_price_usd = float(old_price_raw) if old_price_raw else None
        free_shipping = bool(entry.get("free_shipping", False))

        if title and (url or asin) and price_usd > 0:
            deals.append(
                DealItem(
                    title=title,
                    price_usd=price_usd,
                    free_shipping=free_shipping,
                    url=normalize_amazon_url(url, asin),
                    asin=asin,
                    old_price_usd=old_price_usd,
                    notes=notes,
                )
            )
    return deals


def fetch_live_deals(feed_urls: List[str], timeout_sec: int = 20) -> List[DealItem]:
    posted_asins = load_posted_asins()
    collected: List[DealItem] = []
    seen_in_run: Set[str] = set()

    for feed_url in feed_urls:
        clean_feed = feed_url.strip()
        if not clean_feed:
            continue

        parsed_url = urlparse(clean_feed)
        if parsed_url.scheme not in {"http", "https"}:
            continue

        try:
            raw_text = _fetch_text(clean_feed, timeout_sec=timeout_sec)
            stripped = raw_text.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                items = parse_json_deals(raw_text)
            else:
                items = parse_rss_deals(raw_text)
        except (urllib.error.URLError, ET.ParseError, json.JSONDecodeError) as exc:
            print(f"Feed error ({clean_feed}): {exc}")
            continue

        for item in items:
            dedup_key = item.asin or item.url
            if not dedup_key or dedup_key in posted_asins or dedup_key in seen_in_run:
                continue
            seen_in_run.add(dedup_key)
            collected.append(item)

    return collected
