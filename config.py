import os
from dataclasses import dataclass
from typing import Tuple

DEFAULT_RSS_FEEDS: Tuple[str, ...] = (
    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1",
    "https://ca.camelcamelcamel.com/top_drops/feed",
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channel_id: str
    country_code: str = "IL"
    language: str = "ru"
    max_price_usd: float = 74.9
    free_shipping_threshold_usd: float = 49.0
    require_free_shipping: bool = True
    amazon_partner_tag: str = ""
    feed_urls: Tuple[str, ...] = DEFAULT_RSS_FEEDS
    request_timeout_sec: int = 20
    max_items_per_run: int = 5
    sleep_between_posts_sec: float = 1.5
    dry_run: bool = False


def _parse_feed_urls(raw: str) -> Tuple[str, ...]:
    if not raw.strip():
        return DEFAULT_RSS_FEEDS
    parts = [u.strip() for u in raw.replace("\n", ",").split(",")]
    parsed = tuple(u for u in parts if u)
    return parsed or DEFAULT_RSS_FEEDS


def load_settings() -> Settings:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()

    try:
        max_price_usd = float(os.getenv("MAX_PRICE_USD", "74.9").strip())
    except ValueError:
        max_price_usd = 74.9

    if max_price_usd > 74.9:
        max_price_usd = 74.9

    try:
        threshold_usd = float(os.getenv("FREE_SHIPPING_THRESHOLD_USD", "49.0").strip())
    except ValueError:
        threshold_usd = 49.0

    require_free_shipping = os.getenv("REQUIRE_FREE_SHIPPING", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    dry_run = os.getenv("DRY_RUN", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    return Settings(
        bot_token=bot_token,
        channel_id=channel_id,
        max_price_usd=max_price_usd,
        free_shipping_threshold_usd=threshold_usd,
        require_free_shipping=require_free_shipping,
        amazon_partner_tag=os.getenv("AMAZON_PARTNER_TAG", "").strip(),
        feed_urls=_parse_feed_urls(os.getenv("AMAZON_FEED_URLS", "")),
        dry_run=dry_run,
    )
