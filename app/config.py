from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Hosted Postgres (Supabase) in production; the SQLite file is the fallback.
    database_url: str = ""
    database_path: Path = ROOT / "data" / "poke_watcher.db"

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_marketplace: str = "EBAY_US"
    ebay_search_limit: int = 50

    # Marketplace account deletion notifications (required for production keys).
    ebay_verification_token: str = ""
    ebay_notification_endpoint: str = ""

    # Fanatics Collect watches; false turns that marketplace off.
    fanatics_enabled: bool = True

    discord_webhook_url: str = ""

    # Defaults for the per-marketplace sweep intervals; both can be changed in Settings.
    poll_interval_seconds: int = 300
    fanatics_poll_interval_seconds: int = 60
    # Default resale fee for profit estimates: Fanatics Buy Now's 6% seller fee.
    resale_fee_pct: float = 6.0
    # Hours between automatic card index rebuilds; 0 disables automatic builds.
    index_refresh_hours: int = 24
    request_timeout_seconds: float = 30.0

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url.strip()
        if not url:
            return f"sqlite:///{self.database_path}"
        for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
