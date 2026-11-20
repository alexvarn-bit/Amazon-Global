import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import List, Optional, Set

from amazon_source import DEALS_JSON_FILE, _extract_asin, _resolve_amazon_redirect

_ASIN_ONLY_RE = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)
_TITLE_TAG_RE = re.compile(r"<span[^>]*id=[\"']productTitle[\"'][^>]*>(.*?)</span>", re.I | re.S)
_OG_TITLE_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.I,
)
_PRICE_SPAN_RE = re.compile(
    r"<span[^>]*class=[\"'][^\"']*a-price-whole[^\"']*[\"'][^>]*>([\d,]+)</span>\s*"
    r"<span[^>]*class=[\"'][^\"']*a-price-fraction[^\"']*[\"'][^>]*>(\d{2})</span>",
    re.I,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_input_to_url_and_asin(raw_entry: str) -> tuple[str, str]:
    token = (raw_entry or "").strip()
    if not token:
        return "", ""

    if _ASIN_ONLY_RE.match(token):
        asin = token.upper()
        return f"https://www.amazon.com/dp/{asin}", asin

    if not token.startswith(("http://", "https://")):
        token = "https://" + token

    resolved = _resolve_amazon_redirect(token)
    asin = _extract_asin(resolved, token)
    if asin and "/dp/" not in resolved and "/gp/product/" not in resolved:
        resolved = f"https://www.amazon.com/dp/{asin}"
    return resolved, asin


def _scrape_basic_amazon_info(url: str, asin: str) -> tuple[str, Optional[float], bool]:
    """
    Возвращает (title, price_usd, is_not_found_404).
    Если сервер вернул HTTP 404 — помечаем is_not_found_404=True, чтобы отбросить битую ссылку.
    """
    fallback_title = f"Amazon item {asin}".strip()
    if not url:
        return fallback_title, None, False

    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read(250_000)
        encoding = resp.headers.get_content_charset() or "utf-8"
        page = raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return fallback_title, None, True
        return fallback_title, None, False
    except Exception:
        return fallback_title, None, False

    title = ""
    m_title = _TITLE_TAG_RE.search(page)
    if m_title:
        title = html.unescape(re.sub(r"<[^>]+>", " ", m_title.group(1)))
        title = re.sub(r"\s+", " ", title).strip()

    if not title:
        m_og = _OG_TITLE_RE.search(page)
        if m_og:
            title = html.unescape(m_og.group(1)).strip()
            title = re.sub(r"^Amazon\.com\s*:\s*", "", title, flags=re.I).strip()

    if not title:
        title = fallback_title

    price: Optional[float] = None
    m_price = _PRICE_SPAN_RE.search(page)
    if m_price:
        whole = m_price.group(1).replace(",", "")
        frac = m_price.group(2)
        try:
            val = float(f"{whole}.{frac}")
            if val > 0:
                price = val
        except ValueError:
            price = None

    return title, price, False


def _load_existing_json(filepath: str) -> List[dict]:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"Warning: failed to read {filepath}: {exc}", file=sys.stderr)
        return []


def _save_json(filepath: str, rows: List[dict]) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _collect_inputs(cli_items: List[str], from_file: str) -> List[str]:
    collected: List[str] = []
    for item in cli_items:
        for part in re.split(r"[\s,]+", item.strip()):
            if part.strip():
                collected.append(part.strip())

    if from_file:
        if not os.path.exists(from_file):
            print(f"Input file not found: {from_file}", file=sys.stderr)
        else:
            with open(from_file, "r", encoding="utf-8") as f:
                for line in f:
                    clean = line.strip()
                    if not clean or clean.startswith("#"):
                        continue
                    for part in re.split(r"[\s,]+", clean):
                        if part.strip():
                            collected.append(part.strip())

    if not collected and not sys.stdin.isatty():
        for line in sys.stdin:
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            for part in re.split(r"[\s,]+", clean):
                if part.strip():
                    collected.append(part.strip())

    return collected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Добавляет проверенные ссылки или ASIN с Amazon в deals.json"
    )
    parser.add_argument(
        "items",
        nargs="*",
        help="Ссылки на Amazon или 10-символьные ASIN",
    )
    parser.add_argument(
        "-f",
        "--file",
        default="",
        help="Путь к текстовому файлу со списком ссылок/ASIN (по одной на строку)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEALS_JSON_FILE,
        help=f"Целевой JSON-файл (по умолчанию {DEALS_JSON_FILE})",
    )
    parser.add_argument(
        "--price",
        type=float,
        default=29.99,
        help="Цена по умолчанию, если не удалось распарсить со страницы (по умолчанию 29.99)",
    )
    parser.add_argument(
        "--allow-paid-shipping",
        action="store_true",
        help="Разрешить добавлять товары с free_shipping=false (по умолчанию добавляются только с free_shipping=true)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Общая заметка/комментарий для добавляемых позиций",
    )
    args = parser.parse_args()

    raw_inputs = _collect_inputs(args.items, args.file)
    if not raw_inputs:
        print(
            "Передай ссылки или ASIN аргументами или через --file links.txt.\n"
            "Пример: python add_deals.py B09C5RG6KV https://www.amazon.com/dp/B00004OCL8 --price 34.99"
        )
        return 1

    free_shipping_val = not bool(args.allow_paid_shipping)

    existing_rows = _load_existing_json(args.output)
    seen_keys: Set[str] = set()
    for row in existing_rows:
        if isinstance(row, dict):
            k = (str(row.get("asin") or "") or str(row.get("url") or "")).strip().upper()
            if k:
                seen_keys.add(k)

    added_count = 0
    for raw in raw_inputs:
        url, asin = _normalize_input_to_url_and_asin(raw)
        key = (asin or url).strip().upper()
        if not key or key in seen_keys:
            print(f"Skip duplicate or invalid: {raw}")
            continue

        title, scraped_price, is_404 = _scrape_basic_amazon_info(url, asin)
        if is_404:
            print(f"Skip 404 not found: {url}")
            continue

        final_price = scraped_price if scraped_price is not None else args.price

        new_row = {
            "title": title,
            "price_usd": round(float(final_price), 2),
            "old_price_usd": None,
            "free_shipping": free_shipping_val,
            "url": url,
            "asin": asin,
            "notes": args.notes.strip(),
        }
        existing_rows.append(new_row)
        seen_keys.add(key)
        added_count += 1
        print(f"Added [{asin or 'NO-ASIN'}] ${new_row['price_usd']:.2f}: {title}")

    if added_count > 0:
        _save_json(args.output, existing_rows)
        print(f"Saved {added_count} new deal(s) to {args.output}")
    else:
        print("No new deals added.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
