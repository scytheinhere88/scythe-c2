import asyncio
import random
import time
import logging
from typing import List, Dict, Optional, Set, Tuple
from collections import deque, defaultdict
from dataclasses import dataclass, field

from app.core.models import ProxyItem
from app.core.logger import logger

# ========== CONFIGURATION ==========
# Dinaikkan ke angka gede biar gak ngeblok banjir traffic
MAX_PROXY_CONCURRENT = 999999
PROXY_BACKOFF_TIME = 0         # Matikan backoff
PROXY_MAX_ERRORS = 999999      # Abaikan error
PROXY_STAT_WINDOW = 60
PROXY_REFRESH_INTERVAL = 30


@dataclass
class ProxyStats:
    """Statistik penggunaan proxy."""
    proxy: ProxyItem
    last_used: float = 0.0
    success_count: int = 0
    error_count: int = 0
    total_requests: int = 0
    average_latency: float = 0.0
    last_errors: deque = field(default_factory=lambda: deque(maxlen=5))
    backoff_until: float = 0.0
    active_connections: int = 0

    def is_healthy(self, current_time: float) -> bool:
        # Selalu sehat (biar semua proxy kepakai)
        return True

    def record_success(self, latency: float, current_time: float):
        self.success_count += 1
        self.total_requests += 1
        self.last_used = current_time
        if self.average_latency == 0:
            self.average_latency = latency
        else:
            self.average_latency = self.average_latency * 0.7 + latency * 0.3

    def record_error(self, current_time: float):
        self.error_count += 1
        self.last_errors.append(current_time)

    def acquire(self) -> bool:
        # Selalu bisa acquire
        self.active_connections += 1
        return True

    def release(self):
        if self.active_connections > 0:
            self.active_connections -= 1


class ProxyRotator:
    """
    Proxy rotator MODE DEWA – semua proxy dipakai tanpa batas.
    """

    def __init__(self):
        self._stats: Dict[str, ProxyStats] = {}
        self._lock = asyncio.Lock()
        self._use_explicit_proxies = False
        self._last_refresh = 0.0
        self._round_robin_index = 0

    async def update_proxies(self, proxies: List[ProxyItem]):
        """Update daftar proxy (panggil dari layer7)."""
        async with self._lock:
            self._stats.clear()
            for p in proxies:
                key = str(p)
                self._stats[key] = ProxyStats(proxy=p)
            self._use_explicit_proxies = True
            self._last_refresh = time.time()
            self._round_robin_index = 0
            logger.info(f"ProxyRotator updated with {len(proxies)} proxies (MODE DEWA)")

    async def _refresh_proxies(self):
        # Tidak dipakai (kita pakai explicit)
        pass

    async def get_proxy(self, force_refresh: bool = False) -> Optional[ProxyItem]:
        """
        Dapatkan proxy secara round‑robin (semua proxy, tanpa health check).
        Ini yang bikin traffic banjir!
        """
        async with self._lock:
            if not self._stats:
                return None

            # Ambil semua key
            keys = list(self._stats.keys())
            if not keys:
                return None

            # Round‑robin
            if self._round_robin_index >= len(keys):
                self._round_robin_index = 0

            key = keys[self._round_robin_index]
            self._round_robin_index += 1

            stats = self._stats[key]
            # Paksa acquire (biar semua proxy kepakai)
            stats.acquire()
            return stats.proxy

    def release_proxy(self, proxy: ProxyItem, success: bool, latency: float = 0.0):
        """
        Release proxy setelah dipakai – untuk statistik (tidak memblokir).
        """
        key = str(proxy)
        current_time = time.time()

        async def _release():
            async with self._lock:
                stats = self._stats.get(key)
                if not stats:
                    return
                stats.release()
                if success:
                    stats.record_success(latency, current_time)
                else:
                    stats.record_error(current_time)

        asyncio.create_task(_release())

    async def get_proxy_with_auto_release(self, force_refresh: bool = False):
        """Generator untuk auto-release (jika dipakai di layer7)."""
        proxy = await self.get_proxy(force_refresh)
        success = False
        latency = 0.0
        try:
            yield proxy
            success = True
        except Exception:
            success = False
            raise
        finally:
            if proxy:
                self.release_proxy(proxy, success, latency)

    # ========== Method Tambahan ==========
    async def mark_dead(self, proxy: ProxyItem):
        """Hapus proxy dari rotasi (kalau benar-benar mati)."""
        key = str(proxy)
        async with self._lock:
            if key in self._stats:
                del self._stats[key]
                logger.debug(f"Proxy {key} removed from pool")

    async def get_alive_count(self) -> int:
        """Jumlah proxy yang tersedia."""
        return len(self._stats)

    def get_stats(self) -> Dict:
        """Statistik singkat (untuk debug)."""
        total = len(self._stats)
        return {
            "total_proxies": total,
            "last_refresh": self._last_refresh,
            "use_explicit": self._use_explicit_proxies,
        }