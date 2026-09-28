from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_path: Path = ROOT / "data" / "poke_watcher.db"

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = "EBAY_US"
    ebay_search_limit: int = 50

    # Marketplace account deletion notifications (required for production keys).
    ebay_verification_token: str = ""
    ebay_notification_endpoint: str = ""

    discord_webhook_url: str = ""

    poll_interval_seconds: int = 300
    price_refresh_hours: int = 12
    # Hours between automatic card index rebuilds; 0 disables automatic builds.
    index_refresh_hours: int = 24
    request_timeout_seconds: float = 30.0

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
