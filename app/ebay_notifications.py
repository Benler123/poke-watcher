"""eBay Marketplace Account Deletion notification endpoint.

eBay requires every production application to expose a public HTTPS endpoint
that answers a challenge (GET) and accepts deletion notifications (POST):
https://developer.ebay.com/marketplace-account-deletion

The challenge response is ``sha256(challengeCode + verificationToken + endpoint)``
in hex, where ``endpoint`` is the exact URL registered in the developer portal.
"""

import hashlib
import logging
import secrets
from typing import Any

from app.config import get_settings
from app.db import get_setting

log = logging.getLogger(__name__)

TOKEN_MIN_LENGTH = 32
TOKEN_MAX_LENGTH = 80


def challenge_response(challenge_code: str, verification_token: str, endpoint: str) -> str:
    digest = hashlib.sha256()
    digest.update(challenge_code.encode())
    digest.update(verification_token.encode())
    digest.update(endpoint.encode())
    return digest.hexdigest()


def verification_token() -> str:
    return get_setting("ebay_verification_token") or get_settings().ebay_verification_token


def endpoint_url() -> str:
    return get_setting("ebay_notification_endpoint") or get_settings().ebay_notification_endpoint


def configured() -> bool:
    return bool(verification_token() and endpoint_url())


def generate_token() -> str:
    """A token in eBay's allowed range (32-80 chars, alphanumeric plus _-)."""
    return secrets.token_urlsafe(48).replace("=", "")[:64]


def summarize(payload: dict[str, Any]) -> str:
    """One-line description of a deletion notification, for the Discord post."""
    data = (payload.get("notification") or {}).get("data") or {}
    username = data.get("username") or "unknown user"
    user_id = data.get("userId") or "unknown id"
    return f"eBay account deletion notification for {username} (userId {user_id})"
