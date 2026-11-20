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
_PAID_SHIP_TO_ISRAEL_RE = re.compile(
    r"\$\s*\d+(?:\.\d{1,2})?\s+Shipping(?:\s+to\s+Israel)?",
    re.IGNORECASE,
)
_FREE_SHIPPING_PAGE_RE = re.compile(
    r"\bFREE\s+(?:International\s+)?Shipping(?:\s+to\s+Israel)?\b",
    re.IGNORECASE,
)
_FREE_SHIP_49_PAGE_RE = re.compile(
    r"\bFREE\s+(?:International\s+)?Shipping\s+on\s+(?:eligible\s+)?orders?\s+over\s+\$49\b",
    re.IGNORECASE,
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


def _detect_shipping_from_html(page: str) -> tuple[bool, bool]:
    if not page:
        return False, False

    plain_text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    plain_text = re.sub(r"\s+", " ", plain_text)

    if _PAID_SHIP_TO_ISRAEL_RE.search(plain_text) and not _FREE_SHIPPING_PAGE_RE.search(plain_text):
        return False, False

    has_free_49 = bool(_FREE_SHIP_49_PAGE_RE.search(plain_text))
    has_free_direct = bool(_FREE_SHIPPING_PAGE_RE.search(plain_text)) and not has_free_49
    return has_free_direct, (has_free_49 or has_free_direct)


def _scrape_basic_amazon_info(
    url: str, asin: str
) -> tuple[str, Optional[float], bool, bool, bool]:
    fallback_title = f"Amazon item {asin}".strip()
    if not url:
        return fallback_title, None, False, False, False

    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read(250_000)
        encoding = resp.headers.get_content_charset() or "utf-8"
        page = raw.decode(encoding, errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return fallback_title, None, True, False, False
        return fallback_title, None, False, False, False
    except Exception:
        return fallback_title, None, False, False, False

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

    det_free_ship, det_free_49 = _detect_shipping_from_html(page)
    return title, price, False, det_free_ship, det_free_49


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


def _print_audit(rows: List[dict]) -> None:
    if not rows:
        print("deals.json is empty.")
        return
    print(f"Total items in deals.json: {len(rows)}")
    for idx, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        asin = str(row.get("asin") or "").strip() or "-"
        price = row.get("price_usd")
        fs = bool(row.get("free_shipping", False))
        fs49 = bool(row.get("free_shipping_49", False))
        title = str(row.get("title") or "").strip()[:60]
        print(f"{idx:2d}. [{asin}] ${price} | free={fs} | free49={fs49} | {title}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Добавляет и обслуживает ссылки или ASIN с Amazon в deals.json"
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
        "--free-shipping",
        action="store_true",
        help="Явно пометить добавляемые товары как free_shipping=true",
    )
    parser.add_argument(
        "--free-shipping-49",
        action="store_true",
        help="Явно пометить добавляемые товары как участвующие в доставке от $49 (free_shipping_49=true)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Общая заметка/комментарий для добавляемых позиций",
    )
    parser.add_argument(
        "--reset-shipping",
        action="store_true",
        help="Сбросить free_shipping и free_shipping_49 в false у всех текущих записей в deals.json",
    )
    parser.add_argument(
        "--remove-unverified",
        action="store_true",
        help="Удалить из deals.json записи, у которых free_shipping=false и free_shipping_49=false",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Показать текущие записи в deals.json и их флаги доставки",
    )
    args = parser.parse_args()

    existing_rows = _load_existing_json(args.output)
    modified = False

    if args.reset_shipping:
        for row in existing_rows:
            if isinstance(row, dict):
                row["free_shipping"] = False
                row["free_shipping_49"] = False
        modified = True
        print(f"Reset shipping flags to False for {len(existing_rows)} item(s) in {args.output}")

    if args.remove_unverified:
        before_cnt = len(existing_rows)
        kept: List[dict] = []
        for row in existing_rows:
            if not isinstance(row, dict):
                continue
            fs = bool(row.get("free_shipping", False))
            fs49 = bool(row.get("free_shipping_49", False))
            if fs or fs49:
                kept.append(row)
            else:
                print(f"Removed unverified item [{row.get('asin') or '-'}]: {row.get('title')}")
        existing_rows = kept
        modified = True
        print(f"Removed {before_cnt - len(existing_rows)} unverified item(s); remaining: {len(existing_rows)}")

    raw_inputs = _collect_inputs(args.items, args.file)
    if raw_inputs:
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

            title, scraped_price, is_404, det_free_ship, det_free_49 = _scrape_basic_amazon_info(url, asin)
            if is_404:
                print(f"Skip 404 not found: {url}")
                continue

            final_price = scraped_price if scraped_price is not None else args.price
            free_ship_val = bool(args.free_shipping or det_free_ship)
            free_ship_49_val = bool(args.free_shipping_49 or args.free_shipping or det_free_49)

            new_row = {
                "title": title,
                "price_usd": round(float(final_price), 2),
                "old_price_usd": None,
                "free_shipping": free_ship_val,
                "free_shipping_49": free_ship_49_val,
                "url": url,
                "asin": asin,
                "notes": args.notes.strip(),
            }
            existing_rows.append(new_row)
            seen_keys.add(key)
            added_count += 1
            modified = True
            print(
                f"Added [{asin or 'NO-ASIN'}] ${new_row['price_usd']:.2f} "
                f"(free={free_ship_val}, free49={free_ship_49_val}): {title}"
            )

        if added_count > 0:
            print(f"Added {added_count} new deal(s) to {args.output}")

    if modified:
        _save_json(args.output, existing_rows)
        print(f"Saved updated {args.output}")

    if args.audit or (not modified and not raw_inputs):
        _print_audit(existing_rows)

    if not modified and not raw_inputs and not args.audit:
        print(
            "\nПодсказка: передай ссылки/ASIN или используй --audit / --reset-shipping / --remove-unverified."
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
