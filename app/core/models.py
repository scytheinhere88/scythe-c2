from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Any, Dict
from datetime import datetime
import re

# ========== ATTACK MODELS ==========
class AttackRequest(BaseModel):
    """Request untuk launch attack dari dashboard/admin."""
    method: str = Field(..., description="Metode serangan (spectre, vortex, titan, dll)")
    target: str = Field(..., description="Target (domain atau IP)")
    port: int = Field(..., ge=1, le=65535, description="Port target")
    duration: int = Field(..., gt=0, description="Durasi aktif serangan (detik)")
    hold_time: Optional[int] = Field(None, ge=0, description="Waktu hold setelah durasi (detik)")
    rps_limit: Optional[int] = Field(0, ge=0, description="Batas RPS per attack (0 = unlimited, mengikuti global)")

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: str) -> str:
        """Validasi basic target (domain/IP)."""
        v = v.strip()
        if not v:
            raise ValueError("Target tidak boleh kosong")
        # Sederhana: cek apakah ada http:// atau https://, kalau enggak tambahkan
        if not v.startswith("http://") and not v.startswith("https://") and not re.match(r"^\d+\.\d+\.\d+\.\d+$", v):
            # Bisa jadi domain tanpa protocol
            pass
        return v

    @field_validator("rps_limit")
    @classmethod
    def validate_rps_limit(cls, v: Optional[int]) -> Optional[int]:
        """Validasi rps_limit minimal 0."""
        if v is None:
            return 0
        if v < 0:
            raise ValueError("rps_limit harus >= 0")
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "method": "titan",
            "target": "https://example.com",
            "port": 443,
            "duration": 60,
            "hold_time": 3600,
            "rps_limit": 50000
        }
    })


class AttackStatus(BaseModel):
    """Status serangan yang sedang berjalan (disimpan di Redis)."""
    id: str = Field(..., description="Attack ID unik")
    method: str = Field(..., description="Metode serangan")
    target: str = Field(..., description="Target")
    port: int = Field(..., description="Port target")
    start_time: int = Field(..., description="Timestamp mulai (epoch)")
    duration: int = Field(..., description="Durasi (detik)")
    hold_time: Optional[int] = Field(0, description="Hold time (detik)")
    rps: int = Field(0, description="Requests per second saat ini")
    total_requests: int = Field(0, description="Total request yang sudah dikirim")
    proxy_count_current: int = Field(0, description="Jumlah proxy yang digunakan saat ini")

    @property
    def elapsed(self) -> int:
        """Waktu yang sudah berjalan (detik)."""
        import time
        return int(time.time() - self.start_time)

    @property
    def remaining(self) -> int:
        """Sisa waktu attack (detik)."""
        return max(0, self.duration - self.elapsed)

    @property
    def hold_remaining(self) -> int:
        """Sisa waktu hold (detik)."""
        total = self.duration + (self.hold_time or 0)
        return max(0, total - self.elapsed)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "id": "abc-123",
            "method": "titan",
            "target": "https://example.com",
            "port": 443,
            "start_time": 1712345678,
            "duration": 60,
            "hold_time": 3600,
            "rps": 15000,
            "total_requests": 900000,
            "proxy_count_current": 50
        }
    })


class AttackResult(BaseModel):
    """Laporan hasil serangan dari bot."""
    type: str = "attack_result"
    id: str = Field(..., description="Bot ID")
    attack_id: Optional[str] = None
    method: str
    target: str
    port: int
    duration: int
    hold_time: Optional[int] = 0
    status: str  # success / error
    output: Optional[str] = None
    error: Optional[str] = None


class AttackResponse(BaseModel):
    """Response setelah launch attack."""
    success: bool
    attack_id: Optional[str] = None
    message: Optional[str] = None


# ========== PROXY MODELS ==========
class ProxyItem(BaseModel):
    """Single proxy entry."""
    ip: str = Field(..., description="IP address")
    port: int = Field(..., ge=1, le=65535, description="Port")

    def __str__(self):
        return f"{self.ip}:{self.port}"

    @field_validator("ip")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        # Validasi IP sederhana
        parts = v.split(".")
        if len(parts) != 4:
            raise ValueError("Invalid IP format")
        for p in parts:
            if not p.isdigit() or not (0 <= int(p) <= 255):
                raise ValueError("Invalid IP format")
        return v


class ProxyStats(BaseModel):
    """Statistik proxy pool."""
    total: int = 0
    alive: int = 0
    dead: int = 0
    last_scrap: Optional[str] = None  # "Never" atau timestamp


class ProxyScrapRequest(BaseModel):
    """Request untuk scrap proxy dari URL."""
    urls: List[str] = Field(..., description="Daftar URL proxy list")

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("Minimal 1 URL required")
        return [url.strip() for url in v if url.strip()]


# ========== BOTNET MODELS ==========
class BotRegister(BaseModel):
    """Data registrasi bot."""
    type: str = "register"
    id: str = Field(..., description="Bot ID")
    ip: Optional[str] = None  # Akan diisi dari koneksi
    last_heartbeat: Optional[int] = None


class BotHeartbeat(BaseModel):
    """Heartbeat dari bot."""
    type: str = "heartbeat"
    id: str
    time: int


class BotStats(BaseModel):
    """Statistik botnet."""
    active: int = 0          # Bot online (heartbeat dalam 30 detik)
    total: int = 0           # Total bot yang pernah register
    avg_rpm: int = 0         # Rata-rata request per menit dari semua bot
    total_requests: int = 0  # Total request dari semua bot (all time)


# ========== HISTORY MODELS ==========
class HistoryEntry(BaseModel):
    """Entry history serangan (disimpan di SQLite)."""
    id: Optional[int] = None
    domain: str = Field(..., description="Target domain/IP")
    method: str = Field(..., description="Metode serangan")
    avg_rps: int = Field(0, description="Rata-rata RPS")
    total_requests: int = Field(0, description="Total request")
    duration: int = Field(0, description="Durasi (detik)")
    timestamp: Optional[int] = Field(None, description="Timestamp selesai (epoch)")

    model_config = ConfigDict(from_attributes=True)


# ========== SYSTEM STATUS MODELS ==========
class SystemStatus(BaseModel):
    """Status lengkap sistem untuk dashboard (/api/status & SSE)."""
    active_attacks: List[AttackStatus] = []
    total_rps: int = 0
    total_requests: int = 0
    proxy_pool: int = 0          # Jumlah proxy alive
    proxy_refreshing: bool = False
    max_concurrent: int = 5
    history: Optional[List[HistoryEntry]] = None


# ========== CONFIG MODELS ==========
class ConcurrentConfig(BaseModel):
    """Konfigurasi max concurrent."""
    max_concurrent: int = Field(5, ge=1, le=100, description="Max concurrent attacks")


class ServiceActionResponse(BaseModel):
    """Response untuk service action (restart, clear logs)."""
    success: bool
    message: Optional[str] = None


# ========== GENERIC MODELS ==========
class MessageResponse(BaseModel):
    """Generic message response."""
    success: bool
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Generic error response."""
    detail: str
    status_code: int = 400