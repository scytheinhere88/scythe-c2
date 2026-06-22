"""
Smart proxy scraper utilities for SCYTHE C2.
Features:
- Auto-detects format (text, JSON, HTML)
- Parses proxy lists from various sources
- Validates IP addresses and ports
- Deduplication
- Supports plain text, JSON, and HTML table formats
- Extensible for custom parsers
"""

import re
import json
import logging
from typing import List, Tuple, Optional, Dict, Any, Union

from app.core.logger import logger
from app.utils.http_client import fetch_text, fetch_json

# ========== IP VALIDATION ==========

def is_valid_ip(ip: str) -> bool:
    """Validate IPv4 address."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not (0 <= int(p) <= 255):
            return False
    return True


def is_valid_port(port: int) -> bool:
    """Validate port number (1-65535)."""
    return 1 <= port <= 65535


# ========== PARSERS ==========

def parse_proxy_line(line: str) -> Optional[Tuple[str, int]]:
    """
    Parse a single line of proxy text.
    Supports formats:
    - ip:port
    - ip port
    - ip:port:user:pass (ignore auth)
    - http://ip:port (strip protocol)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Remove protocol prefix if present
    if "://" in line:
        line = line.split("://")[-1]

    # Try ip:port:user:pass
    parts = line.split(":")
    if len(parts) >= 2:
        ip = parts[0].strip()
        port_str = parts[1].strip()
        if is_valid_ip(ip) and port_str.isdigit():
            port = int(port_str)
            if is_valid_port(port):
                return (ip, port)

    # Try ip port (space separated)
    parts = line.split()
    if len(parts) >= 2:
        ip = parts[0].strip()
        port_str = parts[1].strip()
        if is_valid_ip(ip) and port_str.isdigit():
            port = int(port_str)
            if is_valid_port(port):
                return (ip, port)

    return None


def parse_text_proxies(content: str) -> List[Tuple[str, int]]:
    """
    Parse plain text proxy list.
    Each line should contain one proxy.
    """
    proxies = []
    for line in content.splitlines():
        result = parse_proxy_line(line)
        if result:
            proxies.append(result)
    return proxies


def parse_json_proxies(content: Union[str, dict, list]) -> List[Tuple[str, int]]:
    """
    Parse JSON proxy list.
    Supports various formats:
    - Array of objects with 'ip'/'port' keys
    - Object with 'proxies' array
    - Array of strings "ip:port"
    """
    if isinstance(content, str):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            return []
    else:
        data = content

    proxies = []

    # Format: {"proxies": [{"ip": "...", "port": 8080}, ...]}
    if isinstance(data, dict) and "proxies" in data:
        items = data.get("proxies", [])
        for item in items:
            if isinstance(item, dict):
                ip = item.get("ip") or item.get("host") or item.get("address")
                port = item.get("port")
                if ip and port and is_valid_ip(ip) and is_valid_port(int(port)):
                    proxies.append((ip, int(port)))
            elif isinstance(item, str):
                result = parse_proxy_line(item)
                if result:
                    proxies.append(result)
        return proxies

    # Format: [{"ip": "...", "port": 8080}, ...]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ip = item.get("ip") or item.get("host") or item.get("address")
                port = item.get("port")
                if ip and port and is_valid_ip(ip) and is_valid_port(int(port)):
                    proxies.append((ip, int(port)))
            elif isinstance(item, str):
                result = parse_proxy_line(item)
                if result:
                    proxies.append(result)
        return proxies

    # Format: ["ip:port", "ip:port", ...]
    if isinstance(data, list) and all(isinstance(x, str) for x in data):
        for line in data:
            result = parse_proxy_line(line)
            if result:
                proxies.append(result)
        return proxies

    logger.warning(f"Unsupported JSON format: {type(data)}")
    return []


def parse_html_proxies(content: str) -> List[Tuple[str, int]]:
    """
    Parse HTML table from free-proxy-list.net style.
    """
    proxies = []
    # Pattern for <td>ip</td><td>port</td>
    pattern = r'<td>(\d+\.\d+\.\d+\.\d+)<\/td>\s*<td>(\d+)<\/td>'
    matches = re.findall(pattern, content, re.IGNORECASE)
    for ip, port in matches:
        if is_valid_ip(ip) and is_valid_port(int(port)):
            proxies.append((ip, int(port)))

    # Alternative pattern: td with class
    pattern2 = r'<td[^>]*>(\d+\.\d+\.\d+\.\d+)<\/td>[^<]*<td[^>]*>(\d+)<\/td>'
    matches = re.findall(pattern2, content, re.IGNORECASE)
    for ip, port in matches:
        if is_valid_ip(ip) and is_valid_port(int(port)):
            proxies.append((ip, int(port)))

    return proxies


def parse_auto(content: str) -> List[Tuple[str, int]]:
    """
    Auto-detect format and parse proxy list.
    """
    # Check if it's JSON
    try:
        data = json.loads(content)
        return parse_json_proxies(data)
    except json.JSONDecodeError:
        pass

    # Check if it's HTML (contains table tags)
    if "<td" in content.lower() and ("ip" in content.lower() or "port" in content.lower()):
        return parse_html_proxies(content)

    # Default: plain text
    return parse_text_proxies(content)


# ========== SCRAPER FUNCTIONS ==========

async def scrape_from_url(url: str, timeout: int = 10) -> List[Tuple[str, int]]:
    """
    Scrape proxy list from a URL.
    Auto-detects format.
    """
    try:
        content = await fetch_text(url, timeout=timeout)
        if not content:
            logger.warning(f"Empty response from {url}")
            return []
        proxies = parse_auto(content)
        logger.debug(f"Scraped {len(proxies)} proxies from {url}")
        return proxies
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return []


async def scrape_from_urls(urls: List[str], timeout: int = 10) -> List[Tuple[str, int]]:
    """
    Scrape proxy lists from multiple URLs.
    Returns deduplicated list.
    """
    all_proxies = []
    seen = set()
    for url in urls:
        proxies = await scrape_from_url(url, timeout)
        for ip, port in proxies:
            key = f"{ip}:{port}"
            if key not in seen:
                seen.add(key)
                all_proxies.append((ip, port))
    logger.info(f"Scraped {len(all_proxies)} unique proxies from {len(urls)} URLs")
    return all_proxies


# ========== DEDUPLICATION ==========

def deduplicate_proxies(proxies: List[Tuple[str, int]]) -> List[Tuple[str, int]]:
    """Remove duplicate proxies."""
    seen = set()
    result = []
    for ip, port in proxies:
        key = f"{ip}:{port}"
        if key not in seen:
            seen.add(key)
            result.append((ip, port))
    return result


# ========== FILTER ==========

def filter_country(proxies: List[Tuple[str, int]], country_codes: List[str]) -> List[Tuple[str, int]]:
    """
    Filter proxies by country code.
    Note: Requires geoip lookup, not implemented here.
    """
    # Placeholder – can be extended with geoip database
    return proxies


def filter_protocol(proxies: List[Tuple[str, int]], protocols: List[str]) -> List[Tuple[str, int]]:
    """
    Filter proxies by protocol.
    Note: Requires checking each proxy, not implemented here.
    """
    return proxies


# ========== SMART SCRAPER ==========

class SmartProxyScraper:
    """
    Smart scraper with caching and source management.
    """

    def __init__(self):
        self.cache = {}  # url -> list of proxies (with timestamp)

    async def scrape_with_cache(self, url: str, max_age: int = 60) -> List[Tuple[str, int]]:
        """
        Scrape with cache; returns cached version if still fresh.
        max_age in seconds.
        """
        import time
        if url in self.cache:
            cache_time, proxies = self.cache[url]
            if time.time() - cache_time < max_age:
                logger.debug(f"Using cached proxies from {url}")
                return proxies
        proxies = await scrape_from_url(url)
        self.cache[url] = (time.time(), proxies)
        return proxies

    def clear_cache(self):
        """Clear the cache."""
        self.cache.clear()
        logger.debug("Scraper cache cleared")


# ========== SINGLETON ==========
smart_scraper = SmartProxyScraper()