import asyncio
import random
import time
from typing import List, Dict, Optional, Set, Tuple
from collections import deque, defaultdict
from dataclasses import dataclass, field

from app.core.models import ProxyItem
from app.core.logger import logger

# ========== CONFIGURATION v9.1 ==========
MAX_PROXY_CONCURRENT = 10000
PROXY_BACKOFF_TIME = 5
PROXY_MAX_ERRORS = 10
PROXY_STAT_WINDOW = 60
PROXY_REFRESH_INTERVAL = 30


@dataclass
class ProxyStats:
    """Enhanced proxy statistics with health tracking"""
    proxy: ProxyItem
    last_used: float = 0.0
    success_count: int = 0
    error_count: int = 0
    total_requests: int = 0
    average_latency: float = 0.0
    last_errors: deque = field(default_factory=lambda: deque(maxlen=10))
    backoff_until: float = 0.0
    active_connections: int = 0
    consecutive_fails: int = 0
    is_alive: bool = True

    def is_healthy(self, current_time: float) -> bool:
        """Check if proxy is healthy and not in backoff"""
        if not self.is_alive:
            return False
        if current_time < self.backoff_until:
            return False
        if self.consecutive_fails >= PROXY_MAX_ERRORS:
            self.is_alive = False
            return False
        return True

    def record_success(self, latency: float, current_time: float):
        """Record successful request"""
        self.success_count += 1
        self.total_requests += 1
        self.last_used = current_time
        self.consecutive_fails = 0
        if self.average_latency == 0:
            self.average_latency = latency
        else:
            self.average_latency = self.average_latency * 0.7 + latency * 0.3

    def record_error(self, current_time: float):
        """Record failed request"""
        self.error_count += 1
        self.total_requests += 1
        self.last_errors.append(current_time)
        self.consecutive_fails += 1

        # Exponential backoff
        if self.consecutive_fails >= 3:
            backoff = min(PROXY_BACKOFF_TIME * (2 ** (self.consecutive_fails - 3)), 300)
            self.backoff_until = current_time + backoff
            logger.debug(f"Proxy {self.proxy.url} backoff for {backoff}s ({self.consecutive_fails} fails)")

    def acquire(self) -> bool:
        """Acquire connection slot"""
        if self.active_connections >= MAX_PROXY_CONCURRENT:
            return False
        self.active_connections += 1
        return True

    def release(self):
        """Release connection slot"""
        if self.active_connections > 0:
            self.active_connections -= 1

    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        total = self.success_count + self.error_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def health_score(self) -> float:
        """Calculate health score (0-100)"""
        score = self.success_rate * 100
        # Penalize high latency
        if self.average_latency > 5.0:
            score *= 0.5
        elif self.average_latency > 2.0:
            score *= 0.8
        # Penalize high active connections
        if self.active_connections > 100:
            score *= 0.7
        return max(0, score)


class ProxyRotator:
    """
    ProxyRotator v9.1 - 100% async-safe, compatible with Layer7 v9.0
    - Uses asyncio.Lock (not threading.Lock) for async compatibility
    - Smart health-based selection (not just round-robin)
    - Auto-backoff for failing proxies
    - Connection limit tracking
    """

    def __init__(self):
        self._stats: Dict[str, ProxyStats] = {}
        self._lock = asyncio.Lock()  # FIX: asyncio.Lock for async safety
        self._use_explicit_proxies = False
        self._last_refresh = 0.0
        self._round_robin_index = 0
        self._health_check_task: Optional[asyncio.Task] = None

    async def update_proxies(self, proxies: List[ProxyItem]):
        """Update proxy list - async safe"""
        async with self._lock:
            # Preserve stats for existing proxies
            old_stats = {k: v for k, v in self._stats.items()}
            self._stats.clear()

            for p in proxies:
                key = str(p)
                if key in old_stats:
                    # Keep old stats but update proxy object
                    old_stats[key].proxy = p
                    self._stats[key] = old_stats[key]
                else:
                    self._stats[key] = ProxyStats(proxy=p)

            self._use_explicit_proxies = True
            self._last_refresh = time.time()
            self._round_robin_index = 0

        logger.info(f"[ROTATOR-v9.1] Updated with {len(proxies)} proxies | Health tracking active")

    async def get_proxy(self, force_refresh: bool = False) -> Optional[ProxyItem]:
        """
        Get proxy with smart selection:
        1. Try healthy proxies first (sorted by health score)
        2. Fallback to any available proxy
        3. Return None if pool empty
        """
        async with self._lock:
            if not self._stats:
                return None

            current_time = time.time()

            # Get healthy proxies sorted by health score (descending)
            healthy = [
                (k, v) for k, v in self._stats.items()
                if v.is_healthy(current_time) and v.acquire()
            ]

            if healthy:
                # Sort by health score (best first)
                healthy.sort(key=lambda x: x[1].health_score, reverse=True)

                # Pick from top 20% (randomized for load distribution)
                top_count = max(1, len(healthy) // 5)
                selected = random.choice(healthy[:top_count])
                return selected[1].proxy

            # Fallback: try any proxy (even unhealthy)
            all_proxies = list(self._stats.items())
            if all_proxies:
                # Round-robin fallback
                self._round_robin_index = (self._round_robin_index + 1) % len(all_proxies)
                key, stats = all_proxies[self._round_robin_index]
                if stats.acquire():
                    return stats.proxy

            return None

    def release_proxy(self, proxy: ProxyItem, success: bool, latency: float = 0.0):
        """
        Release proxy - SYNC but thread-safe via internal lock
        FIX: Uses asyncio.Lock internally, safe for async workers
        """
        key = str(proxy)
        current_time = time.time()

        # Run in executor to avoid blocking async event loop
        asyncio.create_task(self._release_async(key, success, latency, current_time))

    async def _release_async(self, key: str, success: bool, latency: float, current_time: float):
        """Async release to prevent blocking"""
        async with self._lock:
            stats = self._stats.get(key)
            if not stats:
                return
            stats.release()
            if success:
                stats.record_success(latency, current_time)
            else:
                stats.record_error(current_time)

    async def mark_dead(self, proxy: ProxyItem):
        """Mark proxy as dead - async safe"""
        key = str(proxy)
        async with self._lock:
            if key in self._stats:
                self._stats[key].is_alive = False
                self._stats[key].consecutive_fails = PROXY_MAX_ERRORS
                logger.debug(f"[ROTATOR] Proxy {key} marked dead")

    async def get_alive_count(self) -> int:
        """Get count of alive proxies - async safe"""
        async with self._lock:
            current_time = time.time()
            return sum(1 for s in self._stats.values() if s.is_healthy(current_time))

    async def get_stats(self) -> Dict:
        """Get detailed statistics - async safe"""
        async with self._lock:
            current_time = time.time()
            total = len(self._stats)
            alive = sum(1 for s in self._stats.values() if s.is_healthy(current_time))
            dead = total - alive
            avg_health = sum(s.health_score for s in self._stats.values()) / max(1, total)
            avg_latency = sum(s.average_latency for s in self._stats.values() if s.average_latency > 0) / max(1, sum(1 for s in self._stats.values() if s.average_latency > 0))

        return {
            "total_proxies": total,
            "alive": alive,
            "dead": dead,
            "avg_health_score": round(avg_health, 2),
            "avg_latency": round(avg_latency, 3),
            "last_refresh": self._last_refresh,
            "use_explicit": self._use_explicit_proxies,
        }

    async def get_health_report(self) -> List[Dict]:
        """Get per-proxy health report"""
        async with self._lock:
            current_time = time.time()
            report = []
            for key, stats in self._stats.items():
                report.append({
                    "proxy": key[:50],
                    "alive": stats.is_healthy(current_time),
                    "health_score": round(stats.health_score, 2),
                    "success_rate": round(stats.success_rate, 2),
                    "avg_latency": round(stats.average_latency, 3),
                    "total_requests": stats.total_requests,
                    "active_connections": stats.active_connections,
                    "consecutive_fails": stats.consecutive_fails,
                })
            return sorted(report, key=lambda x: x["health_score"], reverse=True)

    async def cleanup_dead(self, max_age: float = 3600) -> int:
        """Remove dead proxies that haven't been used in a while"""
        async with self._lock:
            current_time = time.time()
            to_remove = [
                k for k, v in self._stats.items()
                if not v.is_alive and (current_time - v.last_used) > max_age
            ]
            for k in to_remove:
                del self._stats[k]
            return len(to_remove)
