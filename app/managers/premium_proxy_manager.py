import asyncio
import time
import json
import random
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

from app.core.config import settings
from app.core.redis_client import get_redis, RedisKeys
from app.core.logger import logger

logger = logging.getLogger("scythe_c2.proxy.premium")

# ================================================================
# IPROYAL RESIDENTIAL PROXY CONFIGURATION
# ================================================================
class IPRoyalConfig:
    """Konfigurasi IPRoyal Residential Proxy - Efisien & Tidak Boros"""

    # Endpoint utama - 1 endpoint = unlimited IP rotation
    ENDPOINTS = [
        {
            "host": "geo.iproyal.com",
            "port": 12321,
            "username": "p9mcCJTFN3vjuAHD",
            "password": "Admin88_country-id",
            "country": "id",  # Indonesia residential
            "priority": 10,
            "max_sessions": 50,  # Max concurrent session per endpoint
            "session_duration": 600,  # 10 menit per session
            "cooldown": 30,  # 30 detik cooldown antar session
        },
        # Tambah endpoint lain kalau punya
        # {
        #     "host": "geo.iproyal.com",
        #     "port": 12321,
        #     "username": "user2",
        #     "password": "pass2_country-us",
        #     "country": "us",
        #     "priority": 9,
        #     "max_sessions": 50,
        #     "session_duration": 600,
        #     "cooldown": 30,
        # },
    ]

    # Usage limits - JANGAN BOROS!
    MAX_CONCURRENT_SESSIONS = 50  # Total session aktif
    SESSION_ROTATE_INTERVAL = 300  # Rotate session setiap 5 menit
    BANDWIDTH_CHECK_INTERVAL = 60  # Cek bandwidth tiap 1 menit

    # Mix ratio: IPRoyal vs Scraped
    PREMIUM_RATIO = 0.6  # 60% traffic lewat IPRoyal
    BULK_RATIO = 0.4     # 40% traffic lewat scraped proxy


@dataclass
class IPRoyalSession:
    """Session management untuk IPRoyal - efisien & rotate"""
    endpoint_id: str
    session_id: str
    proxy_url: str
    created_at: float
    last_used: float
    request_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    is_active: bool = True
    country: str = "id"

    @property
    def age(self) -> float:
        return time.time() - self.created_at

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 1.0
        return self.success_count / total

    def should_rotate(self) -> bool:
        """Check if session should be rotated"""
        if self.age > IPRoyalConfig.SESSION_ROTATE_INTERVAL:
            return True
        if self.fail_count >= 5:
            return True
        if self.success_rate < 0.5 and self.request_count > 10:
            return True
        return False


class ProxyTier(Enum):
    """Tier system untuk proxy selection"""
    PREMIUM = "premium"      # IPRoyal residential
    FAST = "fast"           # Scraped < 1s response
    MEDIUM = "medium"       # Scraped 1-3s response
    SLOW = "slow"          # Scraped > 3s response
    DEAD = "dead"          # Failed proxy


class PremiumProxyManager:
    """
    Manager untuk IPRoyal + Scraped Proxy Integration
    - Efisien: 1 IPRoyal endpoint = 1000+ IP residential
    - Tidak boros: Smart session rotation, bandwidth monitoring
    - Optimal: 60/40 mix premium/bulk
    """

    def __init__(self):
        self.sessions: Dict[str, IPRoyalSession] = {}
        self.session_index = 0
        self._lock = asyncio.Lock()
        self._total_requests = 0
        self._premium_requests = 0
        self._bulk_requests = 0
        self._last_rotate = 0
        self._session_counter = 0

    def _generate_session_id(self) -> str:
        """Generate unique session ID"""
        self._session_counter += 1
        return f"sess_{int(time.time())}_{self._session_counter}_{random.randint(1000,9999)}"

    def _build_proxy_url(self, endpoint: dict, session_id: str) -> str:
        """Build proxy URL dengan session ID untuk sticky session"""
        # Format: http://user_session-sessionId:pass@host:port
        # Session ID memastikan IP stick selama session_duration
        username = endpoint["username"]
        password = endpoint["password"]
        host = endpoint["host"]
        port = endpoint["port"]

        # Tambah session ID ke username untuk sticky session
        # IPRoyal format: username_session-XXXX:password
        sticky_user = f"{username}_session-{session_id}"

        return f"http://{sticky_user}:{password}@{host}:{port}"

    async def initialize_sessions(self, count: int = 10) -> List[str]:
        """
        Initialize IPRoyal sessions - efisien, tidak boros
        count: jumlah session (1 session = 1 sticky IP)
        """
        async with self._lock:
            active_sessions = []

            for endpoint in IPRoyalConfig.ENDPOINTS:
                for i in range(min(count, endpoint["max_sessions"])):
                    session_id = self._generate_session_id()
                    proxy_url = self._build_proxy_url(endpoint, session_id)

                    session = IPRoyalSession(
                        endpoint_id=f"{endpoint['host']}:{endpoint['port']}",
                        session_id=session_id,
                        proxy_url=proxy_url,
                        created_at=time.time(),
                        last_used=time.time(),
                        country=endpoint.get("country", "id"),
                    )

                    self.sessions[session_id] = session
                    active_sessions.append(proxy_url)

            logger.info(f"[IPROYAL] Initialized {len(active_sessions)} premium sessions")
            logger.info(f"[IPROYAL] 1 endpoint = ~1000 residential IPs via rotation")
            return active_sessions

    async def get_premium_proxies(self, count: int = 50, rotate: bool = True) -> List[str]:
        """
        Get premium proxies dengan smart rotation
        - rotate=True: rotate session yang stale
        - count: jumlah proxy URL yang di-return (bisa duplicate untuk load balance)
        """
        async with self._lock:
            now = time.time()

            # Rotate sessions yang sudah stale
            if rotate and (now - self._last_rotate) > 60:
                rotated = 0
                for session_id, session in list(self.sessions.items()):
                    if session.should_rotate():
                        # Mark old session inactive, create new one
                        session.is_active = False

                        # Find endpoint config
                        for endpoint in IPRoyalConfig.ENDPOINTS:
                            ep_id = f"{endpoint['host']}:{endpoint['port']}"
                            if ep_id == session.endpoint_id:
                                new_session_id = self._generate_session_id()
                                new_proxy = self._build_proxy_url(endpoint, new_session_id)

                                new_session = IPRoyalSession(
                                    endpoint_id=ep_id,
                                    session_id=new_session_id,
                                    proxy_url=new_proxy,
                                    created_at=now,
                                    last_used=now,
                                    country=endpoint.get("country", "id"),
                                )
                                self.sessions[new_session_id] = new_session
                                rotated += 1
                                break

                if rotated > 0:
                    logger.info(f"[IPROYAL] Rotated {rotated} stale sessions")

                # Cleanup inactive sessions
                inactive = [sid for sid, s in self.sessions.items() if not s.is_active]
                for sid in inactive:
                    del self.sessions[sid]

                self._last_rotate = now

            # Get active sessions
            active = [s for s in self.sessions.values() if s.is_active]

            if not active:
                # Re-initialize if no active sessions
                logger.warning("[IPROYAL] No active sessions, re-initializing...")
                return await self.initialize_sessions(count=10)

            # Return proxies - duplicate untuk load balancing
            # Kalau count=50 tapi cuma 10 session, duplicate 5x each
            result = []
            if len(active) >= count:
                # Random selection
                selected = random.sample(active, count)
                result = [s.proxy_url for s in selected]
            else:
                # Duplicate untuk reach count
                repeats = (count // len(active)) + 1
                for s in active:
                    for _ in range(repeats):
                        result.append(s.proxy_url)
                        if len(result) >= count:
                            break
                    if len(result) >= count:
                        break

            # Update stats
            for s in active:
                s.last_used = now

            logger.info(f"[IPROYAL] Returning {len(result)} premium proxies from {len(active)} sessions")
            return result[:count]

    async def get_mixed_proxies(self, bulk_proxies: List[str], total_count: int = 2000) -> Tuple[List[str], Dict]:
        """
        Get mixed proxies: 60% premium (IPRoyal) + 40% bulk (scraped)
        Returns: (proxy_list, stats_dict)
        """
        premium_count = int(total_count * IPRoyalConfig.PREMIUM_RATIO)
        bulk_count = total_count - premium_count

        # Get premium
        premium = await self.get_premium_proxies(count=premium_count)

        # Get bulk (scraped) - limit untuk hemat
        bulk = bulk_proxies[:bulk_count] if len(bulk_proxies) > bulk_count else bulk_proxies

        # Mix: interleave premium dan bulk
        mixed = []
        p_idx, b_idx = 0, 0
        for i in range(total_count):
            if i % 5 < 3 and p_idx < len(premium):  # 3/5 = 60% premium
                mixed.append(premium[p_idx])
                p_idx = (p_idx + 1) % len(premium) if premium else 0
            elif b_idx < len(bulk):
                mixed.append(bulk[b_idx])
                b_idx += 1
            elif p_idx < len(premium):
                mixed.append(premium[p_idx])
                p_idx = (p_idx + 1) % len(premium)

        stats = {
            "premium_count": len(premium),
            "bulk_count": len(bulk),
            "total_mixed": len(mixed),
            "premium_ratio": len(premium) / len(mixed) if mixed else 0,
            "active_sessions": len([s for s in self.sessions.values() if s.is_active]),
        }

        logger.info(f"[MIXED] Premium: {len(premium)} | Bulk: {len(bulk)} | Total: {len(mixed)} | Ratio: {stats['premium_ratio']:.1%}")
        return mixed, stats

    async def report_proxy_result(self, proxy_url: str, success: bool):
        """Report proxy success/fail untuk tracking"""
        async with self._lock:
            # Find session by proxy_url
            for session in self.sessions.values():
                if session.proxy_url == proxy_url:
                    session.request_count += 1
                    if success:
                        session.success_count += 1
                        self._premium_requests += 1
                    else:
                        session.fail_count += 1
                    break

            self._total_requests += 1

    async def get_stats(self) -> Dict:
        """Get premium proxy stats"""
        async with self._lock:
            active = [s for s in self.sessions.values() if s.is_active]
            total_req = self._total_requests
            premium_req = self._premium_requests

            return {
                "active_sessions": len(active),
                "total_sessions_created": self._session_counter,
                "total_requests": total_req,
                "premium_requests": premium_req,
                "bulk_requests": self._bulk_requests,
                "premium_ratio": premium_req / total_req if total_req > 0 else 0,
                "avg_session_age": sum(s.age for s in active) / len(active) if active else 0,
                "avg_success_rate": sum(s.success_rate for s in active) / len(active) if active else 0,
            }

    async def health_check_premium(self) -> Dict:
        """Health check untuk premium proxies"""
        import aiohttp

        check_url = "http://httpbin.org/ip"
        results = {"ok": 0, "fail": 0, "details": []}

        async with aiohttp.ClientSession() as session:
            for proxy_url in await self.get_premium_proxies(count=5, rotate=False):
                try:
                    async with session.get(
                        check_url,
                        proxy=proxy_url,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            ip = data.get("origin", "unknown")
                            results["ok"] += 1
                            results["details"].append({"proxy": proxy_url[:50], "ip": ip, "status": "ok"})
                            logger.info(f"[IPROYAL-HEALTH] OK - IP: {ip}")
                        else:
                            results["fail"] += 1
                            results["details"].append({"proxy": proxy_url[:50], "status": f"http_{resp.status}"})
                except Exception as e:
                    results["fail"] += 1
                    results["details"].append({"proxy": proxy_url[:50], "status": str(e)})

        logger.info(f"[IPROYAL-HEALTH] OK: {results['ok']}, Fail: {results['fail']}")
        return results


# Singleton
premium_proxy_manager = PremiumProxyManager()