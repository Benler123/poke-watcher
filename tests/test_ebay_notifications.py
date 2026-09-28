import hashlib

from fastapi.testclient import TestClient

from app.db import init_db, set_setting
from app.ebay_notifications import challenge_response, generate_token
from app.main import app

ENDPOINT = "https://example.com/ebay/notifications"
TOKEN = "a" * 32


def _configure():
    init_db()
    set_setting("ebay_verification_token", TOKEN)
    set_setting("ebay_notification_endpoint", ENDPOINT)


def test_challenge_response_matches_ebay_spec():
    expected = hashlib.sha256(f"code123{TOKEN}{ENDPOINT}".encode()).hexdigest()
    assert challenge_response("code123", TOKEN, ENDPOINT) == expected


def test_generated_token_is_in_ebays_length_range():
    token = generate_token()
    assert 32 <= len(token) <= 80


def test_challenge_endpoint():
    _configure()
    with TestClient(app) as client:
        response = client.get("/ebay/notifications", params={"challenge_code": "code123"})
    assert response.status_code == 200
    assert response.json() == {"challengeResponse": challenge_response("code123", TOKEN, ENDPOINT)}


def test_challenge_endpoint_unconfigured():
    init_db()
    set_setting("ebay_verification_token", "")
    set_setting("ebay_notification_endpoint", "")
    with TestClient(app) as client:
        response = client.get("/ebay/notifications", params={"challenge_code": "code123"})
    assert response.status_code == 503


def test_notification_post_acks_and_notifies(monkeypatch):
    _configure()
    set_setting("forward_deletion_notices", "1")
    sent: list[str] = []
    monkeypatch.setattr("app.notifier.send_notice", lambda content: sent.append(content) or True)
    payload = {
        "metadata": {"topic": "MARKETPLACE_ACCOUNT_DELETION"},
        "notification": {"data": {"username": "buyer1", "userId": "abc123"}},
    }
    with TestClient(app) as client:
        response = client.post("/ebay/notifications", json=payload)
    assert response.status_code == 204
    assert sent == ["eBay account deletion notification for buyer1 (userId abc123)"]


def test_notification_post_stays_quiet_by_default(monkeypatch):
    _configure()
    set_setting("forward_deletion_notices", "0")
    sent: list[str] = []
    monkeypatch.setattr("app.notifier.send_notice", lambda content: sent.append(content) or True)
    with TestClient(app) as client:
        response = client.post("/ebay/notifications", json={"notification": {"data": {}}})
    assert response.status_code == 204
    assert sent == []
