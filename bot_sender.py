import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import List

from config import Settings, load_settings
from filter_deals import DealItem, filter_deals, format_deal_post_ru


def get_sample_deals() -> List[DealItem]:
    return [
        DealItem(
            title="Тестовый товар: USB-C хаб с быстрой зарядкой",
            price_usd=29.99,
            free_shipping=True,
            url="https://www.amazon.com/dp/B000000001",
            asin="B000000001",
            old_price_usd=39.99,
            notes="Проверка лимита до $74.90 и бесплатной доставки в IL",
        ),
        DealItem(
            title="Отбракованный товар: дороже лимита",
            price_usd=89.00,
            free_shipping=True,
            url="https://www.amazon.com/dp/B000000002",
            asin="B000000002",
        ),
        DealItem(
            title="Отбракованный товар: платная доставка",
            price_usd=45.00,
            free_shipping=False,
            url="https://www.amazon.com/dp/B000000003",
            asin="B000000003",
        ),
    ]


def send_telegram_message(
    bot_token: str,
    channel_id: str,
    text: str,
    timeout_sec: int = 20,
) -> bool:
    if not bot_token or not channel_id:
        raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID must be set")

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": channel_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
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
        print(f"Telegram HTTP error {exc.code}: {err_body}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Telegram send error: {exc}", file=sys.stderr)
        return False


def run_pipeline(settings: Settings, deals: List[DealItem]) -> int:
    valid_deals = filter_deals(
        items=deals,
        max_price_usd=settings.max_price_usd,
        require_free_shipping=settings.require_free_shipping,
        max_items=settings.max_items_per_run,
    )

    print(f"Total incoming deals: {len(deals)} | Passed filter: {len(valid_deals)}")
    if not valid_deals:
        return 0

    sent_count = 0
    for idx, item in enumerate(valid_deals):
        post_text = format_deal_post_ru(item, partner_tag=settings.amazon_partner_tag)
        if settings.dry_run:
            print(f"[DRY_RUN] Would send:\n{post_text}\n---")
            sent_count += 1
        else:
            ok = send_telegram_message(
                bot_token=settings.bot_token,
                channel_id=settings.channel_id,
                text=post_text,
                timeout_sec=settings.request_timeout_sec,
            )
            if ok:
                sent_count += 1
                print(f"Sent deal: {item.title}")
            else:
                print(f"Failed to send deal: {item.title}", file=sys.stderr)

        if idx < len(valid_deals) - 1 and settings.sleep_between_posts_sec > 0:
            time.sleep(settings.sleep_between_posts_sec)

    return sent_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deal bot sender")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run with built-in sample deals to verify filter and Telegram posting",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()

    if args.test:
        deals = get_sample_deals()
    else:
        print("No live Amazon source wired yet. Pass --test to run sample deals.")
        return 0

    sent = run_pipeline(settings=settings, deals=deals)
    print(f"Done. Posts sent: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
