import json
import sys
import time
import urllib.error
import urllib.request
from typing import List

from amazon_source import fetch_live_deals, load_posted_asins, save_posted_asins
from config import Settings, load_settings
from filter_deals import (
    DealItem,
    filter_deals,
    format_deal_post_ru,
    is_amazon_deal,
    is_excluded_category,
)

_MAX_CAPTION_LEN = 1024


def _post_telegram_json(
    bot_token: str,
    method: str,
    payload: dict,
    timeout_sec: int = 20,
) -> bool:
    api_url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return bool(body.get("ok"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"Telegram {method} HTTP error {exc.code}: {err_body}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Telegram {method} error: {exc}", file=sys.stderr)
        return False


def send_telegram_message(
    bot_token: str,
    channel_id: str,
    text: str,
    timeout_sec: int = 20,
) -> bool:
    if not bot_token or not channel_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID must be set")

    payload = {
        "chat_id": channel_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    return _post_telegram_json(bot_token, "sendMessage", payload, timeout_sec=timeout_sec)


def send_telegram_photo(
    bot_token: str,
    channel_id: str,
    photo_url: str,
    caption: str,
    timeout_sec: int = 20,
) -> bool:
    if not bot_token or not channel_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID must be set")

    safe_caption = caption
    if len(safe_caption) > _MAX_CAPTION_LEN:
        safe_caption = safe_caption[: _MAX_CAPTION_LEN - 1] + "…"

    payload = {
        "chat_id": channel_id,
        "photo": photo_url,
        "caption": safe_caption,
        "parse_mode": "HTML",
    }
    return _post_telegram_json(bot_token, "sendPhoto", payload, timeout_sec=timeout_sec)


def _send_deal_post(
    settings: Settings,
    item: DealItem,
    post_text: str,
) -> bool:
    image_url = (getattr(item, "image_url", "") or "").strip()
    if image_url:
        sent_photo = send_telegram_photo(
            bot_token=settings.bot_token,
            channel_id=settings.channel_id,
            photo_url=image_url,
            caption=post_text,
            timeout_sec=settings.request_timeout_sec,
        )
        if sent_photo:
            return True
        print(f"Photo failed ({image_url}), fallback to text for: {item.title}")
    else:
        print(f"No image_url found, sending text for: {item.title}")

    return send_telegram_message(
        bot_token=settings.bot_token,
        channel_id=settings.channel_id,
        text=post_text,
        timeout_sec=settings.request_timeout_sec,
    )


def _log_filter_diagnostics(
    deals: List[DealItem],
    max_price_usd: float,
    require_free_shipping: bool,
) -> None:
    excluded_cat = 0
    not_amazon = 0
    over_price = 0
    no_free_shipping = 0
    samples: List[str] = []

    for item in deals:
        cat_excluded = is_excluded_category(item)
        amazon_ok = is_amazon_deal(item)
        price_ok = 0 < item.price_usd <= max_price_usd
        ship_ok = (not require_free_shipping) or item.free_shipping

        reason = ""
        if cat_excluded:
            excluded_cat += 1
            reason = "excluded_cat"
        elif not amazon_ok:
            not_amazon += 1
            reason = "not_amazon"
        elif not price_ok:
            over_price += 1
            reason = "over_price"
        elif not ship_ok:
            no_free_shipping += 1
            reason = "no_free_shipping"

        if reason and len(samples) < 5:
            samples.append(
                f"[{reason}] ${item.price_usd:.2f} | asin={item.asin or '-'} | "
                f"{item.url[:60]} | {item.title[:50]}"
            )

    print(
        f"Filter breakdown -> excluded_cat: {excluded_cat} | "
        f"not_amazon: {not_amazon} | "
        f"over_${max_price_usd:.2f}: {over_price} | "
        f"no_free_shipping_tag: {no_free_shipping}"
    )
    for s in samples:
        print(f"Sample drop: {s}")


def run_pipeline(settings: Settings, deals: List[DealItem], persist_posted: bool = False) -> int:
    require_free_shipping = bool(getattr(settings, "require_free_shipping", False))

    valid_deals = filter_deals(
        items=deals,
        max_price_usd=settings.max_price_usd,
        require_free_shipping=require_free_shipping,
        max_items=settings.max_items_per_run,
    )

    print(f"Total incoming deals: {len(deals)} | Passed filter: {len(valid_deals)}")
    if deals and not valid_deals:
        _log_filter_diagnostics(
            deals=deals,
            max_price_usd=settings.max_price_usd,
            require_free_shipping=require_free_shipping,
        )
    if not valid_deals:
        return 0

    posted_asins = load_posted_asins() if persist_posted else set()
    newly_posted: List[str] = []
    sent_count = 0

    for idx, item in enumerate(valid_deals):
        post_text = format_deal_post_ru(
            item,
            partner_tag=settings.amazon_partner_tag,
            free_shipping_threshold_usd=settings.free_shipping_threshold_usd,
        )
        ok = _send_deal_post(settings=settings, item=item, post_text=post_text)
        if ok:
            sent_count += 1
            print(f"Sent deal: {item.title}")
            key = (item.asin or item.url).strip().upper()
            if key:
                newly_posted.append(key)
        else:
            print(f"Failed to send deal: {item.title}", file=sys.stderr)

        if idx < len(valid_deals) - 1 and settings.sleep_between_posts_sec > 0:
            time.sleep(settings.sleep_between_posts_sec)

    if persist_posted and newly_posted:
        posted_asins.update(newly_posted)
        save_posted_asins(posted_asins)
        print(f"Saved {len(newly_posted)} new posted keys to posted_asins.json")

    return sent_count


def main() -> int:
    settings = load_settings()
    if not settings.feed_urls:
        print("AMAZON_FEED_URLS is empty.")
        return 0

    deals = fetch_live_deals(
        feed_urls=list(settings.feed_urls),
        timeout_sec=settings.request_timeout_sec,
    )
    sent = run_pipeline(settings=settings, deals=deals, persist_posted=True)
    print(f"Done. Posts sent: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
