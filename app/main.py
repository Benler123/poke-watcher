import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import ebay, ebay_notifications, grading, monitor, notifier, tcg, watches
from app.config import get_settings
from app.db import get_setting, init_db, set_setting

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(monitor.poll_forever())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Poke Watcher", lifespan=lifespan)


class WatchIn(BaseModel):
    label: str = Field(min_length=1)
    ebay_query: str = Field(min_length=1)
    product_id: int | None = None
    sub_type_name: str | None = None
    set_name: str | None = None
    tcgplayer_url: str | None = None
    image_url: str | None = None
    manual_market_price: float | None = None
    grade_company: str = ""
    grade_value: str = ""
    grade_price_multiplier: float = 1.0
    bin_max_pct_of_market: float = 1.0
    offer_max_pct_of_market: float = 1.15
    min_price: float | None = None
    max_price: float | None = None
    exclude_terms: str = ""
    active: bool = True


class WatchPatch(BaseModel):
    label: str | None = None
    ebay_query: str | None = None
    sub_type_name: str | None = None
    manual_market_price: float | None = None
    grade_company: str | None = None
    grade_value: str | None = None
    grade_price_multiplier: float | None = None
    bin_max_pct_of_market: float | None = None
    offer_max_pct_of_market: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    exclude_terms: str | None = None
    active: bool | None = None


class SettingsIn(BaseModel):
    discord_webhook_url: str | None = None
    ebay_verification_token: str | None = None
    ebay_notification_endpoint: str | None = None


def _normalize_grade(data: dict[str, Any]) -> dict[str, Any]:
    if "grade_company" in data:
        data["grade_company"] = grading.normalize_company(data["grade_company"])
    if "grade_value" in data:
        data["grade_value"] = grading.normalize_value(data["grade_value"])
    return data


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": True,
        "ebay_source": "browse_api" if settings.ebay_configured else "html_scrape",
        "discord_configured": bool(notifier.webhook_url()),
        "poll_interval_seconds": settings.poll_interval_seconds,
        "monitor": monitor.status,
        "stats": monitor.stats(),
    }


@app.get("/api/cards/search")
def search_cards(q: str, limit: int = 25) -> dict[str, Any]:
    results = tcg.search_products(q, limit=limit)
    return {"results": results, "indexed": tcg.index_size()}


@app.post("/api/cards/index")
async def index_cards() -> dict[str, Any]:
    if monitor.status.get("indexing") and not monitor.status["indexing"].get("complete"):
        return {"status": "already_running", "progress": monitor.status["indexing"]}
    asyncio.create_task(monitor.build_index_background())
    return {"status": "started"}


@app.get("/api/cards/{product_id}/price")
def card_price(product_id: int) -> dict[str, Any]:
    try:
        return {"product_id": product_id, "prices": tcg.refresh_price(product_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/watches")
def get_watches() -> list[dict[str, Any]]:
    return watches.list_watches()


@app.post("/api/watches", status_code=201)
def post_watch(payload: WatchIn) -> dict[str, Any]:
    data = _normalize_grade(payload.model_dump())
    data["active"] = int(data["active"])
    watch = watches.create_watch(data)
    try:
        watches.record_market_price(watch["id"], monitor.resolve_market_price(watch, force=True))
    except Exception as exc:  # noqa: BLE001 - price lookup is best effort at create time
        watches.record_check(watch["id"], f"price lookup failed: {exc}")
    created = watches.get_watch(watch["id"])
    assert created is not None
    return created


@app.patch("/api/watches/{watch_id}")
def patch_watch(watch_id: int, payload: WatchPatch) -> dict[str, Any]:
    data = _normalize_grade(
        {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    )
    if "active" in data:
        data["active"] = int(data["active"])
    watch = watches.update_watch(watch_id, data)
    if watch is None:
        raise HTTPException(status_code=404, detail="watch not found")
    return watch


@app.delete("/api/watches/{watch_id}", status_code=204)
def remove_watch(watch_id: int) -> None:
    watches.delete_watch(watch_id)


@app.post("/api/watches/{watch_id}/check")
def check_watch_now(watch_id: int) -> dict[str, Any]:
    watch = watches.get_watch(watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="watch not found")
    try:
        alerts = monitor.check_watch(watch, ebay.get_client())
    except ebay.EbayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"alerts": alerts, "watch": watches.get_watch(watch_id)}


@app.get("/api/alerts")
def get_alerts(limit: int = 100, watch_id: int | None = None) -> list[dict[str, Any]]:
    return watches.list_alerts(limit=limit, watch_id=watch_id)


@app.post("/api/monitor/run")
def run_monitor() -> dict[str, Any]:
    return monitor.run_once()


@app.get("/api/settings")
def read_settings() -> dict[str, Any]:
    url = notifier.webhook_url()
    return {
        "discord_webhook_url": url,
        "discord_webhook_set": bool(url),
        "poll_interval_seconds": get_settings().poll_interval_seconds,
        "ebay_source": "browse_api" if get_settings().ebay_configured else "html_scrape",
        "ebay_verification_token": ebay_notifications.verification_token(),
        "ebay_notification_endpoint": ebay_notifications.endpoint_url(),
    }


@app.put("/api/settings")
def write_settings(payload: SettingsIn) -> dict[str, Any]:
    if payload.discord_webhook_url is not None:
        set_setting("discord_webhook_url", payload.discord_webhook_url.strip())
    if payload.ebay_verification_token is not None:
        set_setting("ebay_verification_token", payload.ebay_verification_token.strip())
    if payload.ebay_notification_endpoint is not None:
        set_setting("ebay_notification_endpoint", payload.ebay_notification_endpoint.strip())
    return read_settings()


@app.post("/api/settings/ebay-token")
def new_ebay_token() -> dict[str, Any]:
    token = ebay_notifications.generate_token()
    set_setting("ebay_verification_token", token)
    return {"ebay_verification_token": token}


@app.post("/api/settings/test-discord")
def test_discord() -> dict[str, Any]:
    if not notifier.webhook_url():
        raise HTTPException(status_code=400, detail="no Discord webhook configured")
    return {"sent": notifier.send_test_message()}


@app.get("/ebay/notifications")
def ebay_notification_challenge(challenge_code: str) -> JSONResponse:
    if not ebay_notifications.configured():
        raise HTTPException(
            status_code=503,
            detail="set the eBay verification token and notification endpoint in Settings",
        )
    return JSONResponse(
        {
            "challengeResponse": ebay_notifications.challenge_response(
                challenge_code,
                ebay_notifications.verification_token(),
                ebay_notifications.endpoint_url(),
            )
        }
    )


@app.post("/ebay/notifications", status_code=204)
async def ebay_notification(request: Request) -> Response:
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    log.info("eBay notification: %s", payload)
    notifier.send_notice(ebay_notifications.summarize(payload))
    return Response(status_code=204)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

__all__ = ["app", "get_setting"]
