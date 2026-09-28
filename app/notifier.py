"""Discord webhook notifications."""

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from app.config import get_settings
from app.db import get_setting
from app.ebay import Listing
from app.rules import UNDER_MARKET, Match

log = logging.getLogger(__name__)

COLOR_UNDER_MARKET = 0x2ECC71
COLOR_NEAR_MARKET = 0xF1C40F


def webhook_url() -> str:
    return get_setting("discord_webhook_url") or get_settings().discord_webhook_url


def action_links(listing: Listing) -> str:
    """Markdown links that jump straight into checkout / the offer layer."""
    links = []
    if listing.buy_now_url:
        links.append(f"[Buy It Now]({listing.buy_now_url})")
    if listing.offer_url:
        links.append(f"[Make Offer]({listing.offer_url})")
    links.append(f"[View listing]({listing.url})")
    return " · ".join(links)


def build_embed(listing: Listing, watch: Mapping[str, Any], match: Match, market_price: float) -> dict[str, Any]:
    delta = listing.total_price - market_price
    return {
        "title": listing.title[:250],
        "url": listing.url,
        "color": COLOR_UNDER_MARKET if match.reason == UNDER_MARKET else COLOR_NEAR_MARKET,
        "description": f"**{match.label}** — {match.pct_of_market * 100:.0f}% of market",
        "fields": [
            {"name": "Buy It Now", "value": f"${listing.price:,.2f}", "inline": True},
            {
                "name": "Shipping",
                "value": "Free" if listing.shipping == 0 else f"${listing.shipping:,.2f}",
                "inline": True,
            },
            {"name": "Total", "value": f"${listing.total_price:,.2f}", "inline": True},
            {"name": "TCG market", "value": f"${market_price:,.2f}", "inline": True},
            {"name": "Difference", "value": f"{'+' if delta >= 0 else '-'}${abs(delta):,.2f}", "inline": True},
            {"name": "Best Offer", "value": "Yes" if listing.best_offer else "No", "inline": True},
            {"name": "Actions", "value": action_links(listing), "inline": False},
        ],
        "thumbnail": {"url": listing.image_url} if listing.image_url else None,
        "footer": {"text": f"Watch: {watch.get('label', '')}"},
    }


def send_alert(listing: Listing, watch: Mapping[str, Any], match: Match, market_price: float) -> bool:
    url = webhook_url()
    if not url:
        log.warning("no Discord webhook configured; skipping notification")
        return False
    embed = {k: v for k, v in build_embed(listing, watch, match, market_price).items() if v is not None}
    payload = {"username": "Poke Watcher", "embeds": [embed]}
    try:
        with httpx.Client(timeout=get_settings().request_timeout_seconds) as client:
            response = client.post(url, json=payload)
        if response.status_code >= 300:
            log.error("discord webhook failed (%s): %s", response.status_code, response.text[:200])
            return False
        return True
    except httpx.HTTPError as exc:
        log.error("discord webhook error: %s", exc)
        return False


def send_notice(content: str) -> bool:
    """Post a plain-text message (used for eBay account deletion notices)."""
    url = webhook_url()
    if not url:
        log.warning("no Discord webhook configured; skipping notice: %s", content)
        return False
    try:
        with httpx.Client(timeout=get_settings().request_timeout_seconds) as client:
            response = client.post(url, json={"username": "Poke Watcher", "content": content[:1900]})
        return response.status_code < 300
    except httpx.HTTPError as exc:
        log.error("discord webhook error: %s", exc)
        return False


def send_test_message() -> bool:
    url = webhook_url()
    if not url:
        return False
    with httpx.Client(timeout=get_settings().request_timeout_seconds) as client:
        response = client.post(
            url,
            json={
                "username": "Poke Watcher",
                "content": "Poke Watcher is connected and watching for deals.",
            },
        )
    return response.status_code < 300
