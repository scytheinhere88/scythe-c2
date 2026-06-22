import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

# Base directory project (cari folder 'app' atau root)
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # sampai ke root project


class Settings(BaseSettings):
    """
    Konfigurasi utama SCYTHE C2.
    Semua nilai punya default yang masuk akal, jadi user tinggal run.
    Bisa di-override via .env atau environment variables.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- SERVER ----
    API_PORT: int = Field(default=1837, ge=1, le=65535)
    C2_PORT: int = Field(default=4444, ge=1, le=65535)
    HOST: str = Field(default="0.0.0.0")
    DEBUG: bool = Field(default=False)

    # ---- REDIS ----
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    REDIS_PASSWORD: Optional[str] = Field(default=None)

    # ---- DATABASE ----
    HISTORY_DB: str = Field(
        default=f"sqlite:///{BASE_DIR}/data/history.db"
    )

    # ---- CONCURRENT & ATTACK ----
    MAX_CONCURRENT: int = Field(default=5, ge=1, le=100)
    DEFAULT_DURATION: int = Field(default=60, ge=1)
    MAX_HOLD_TIME: int = Field(default=86400, ge=0)  # 24 jam
    RPS_LIMIT: int = 100000  # default 100k RPS

    # ---- PROXY ----
    PROXY_REFRESH_INTERVAL: int = Field(default=60, ge=10)
    PROXY_HEALTH_TIMEOUT: int = Field(default=5, ge=1)
    PROXY_SCRAP_TIMEOUT: int = Field(default=10, ge=1)
    PROXY_POOL_SIZE_LIMIT: int = Field(default=10000, ge=100)

    # ---- BOTNET ----
    HEARTBEAT_INTERVAL: int = Field(default=10, ge=3)
    BOT_RECONNECT_DELAY: int = Field(default=5, ge=1)

    # ---- SECURITY (CORS) ----
    ALLOWED_ORIGINS: List[str] = Field(
        default=["http://localhost:1837", "http://127.0.0.1:1837", "*"]
    )

    # ---- LOGGING ----
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FILE: str = Field(default=str(BASE_DIR / "logs" / "app.log"))

    # ---- AUTO-CREATE DIRECTORIES ----
    @field_validator("LOG_FILE", "HISTORY_DB", mode="before")
    @classmethod
    def ensure_directories(cls, v: str) -> str:
        """Buat direktori untuk log dan database jika belum ada"""
        if v:
            path = Path(v)
            # Jika berupa URL sqlite, ambil path setelah sqlite:///
            if v.startswith("sqlite:///"):
                path = Path(v.replace("sqlite:///", ""))
            if path.suffix in [".db", ".sqlite", ".log"]:
                parent = path.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("REDIS_URL")
    @classmethod
    def validate_redis(cls, v):
        if v and not v.startswith("redis://") and not v.startswith("rediss://"):
            raise ValueError("REDIS_URL harus mulai dengan redis:// atau rediss://")
        return v


# Singleton instance
settings = Settings()

# Pastikan folder data dan logs ada (panggil manual karena validator jalan di awal)
(Path(settings.LOG_FILE).parent).mkdir(parents=True, exist_ok=True)
if settings.HISTORY_DB.startswith("sqlite:///"):
    db_path = Path(settings.HISTORY_DB.replace("sqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)

# Helper untuk print konfigurasi saat startup
def print_config():
    print("=== SCYTHE C2 CONFIGURATION ===")
    print(f"API Port     : {settings.API_PORT}")
    print(f"C2 Port      : {settings.C2_PORT}")
    print(f"Redis        : {settings.REDIS_URL}")
    print(f"History DB   : {settings.HISTORY_DB}")
    print(f"Max Concurrent: {settings.MAX_CONCURRENT}")
    print(f"Proxy Refresh: {settings.PROXY_REFRESH_INTERVAL}s")
    print(f"Log Level    : {settings.LOG_LEVEL}")
    print("================================")