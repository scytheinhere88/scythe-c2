"""
Async HTTP client utilities for SCYTHE C2.
Features:
- Singleton aiohttp session with connection pooling
- Automatic retries with exponential backoff
- User-Agent rotation
- Proxy support (optional)
- Timeout configuration
- Logging and error handling
"""

import asyncio
import random
import logging
from typing import Optional, Dict, Any, List, Union
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import ClientTimeout, ClientSession, TCPConnector, ClientError

from app.core.config import settings

logger = logging.getLogger("scythe_c2.http_client")

# ========== CONFIGURATION ==========
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 0.5  # initial delay in seconds
MAX_CONNECTIONS = 100
MAX_CONNECTIONS_PER_HOST = 30

# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0",
]


class HttpClient:
    """
    Singleton HTTP client with connection pooling and retry logic.
    """

    _instance = None
    _session: Optional[ClientSession] = None
    _connector: Optional[TCPConnector] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_session(self) -> ClientSession:
        """Get or create the global aiohttp session."""
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=MAX_CONNECTIONS,
                limit_per_host=MAX_CONNECTIONS_PER_HOST,
                ttl_dns_cache=300,
                use_dns_cache=True,
                enable_cleanup_closed=True,
            )
            timeout = ClientTimeout(
                total=DEFAULT_TIMEOUT,
                connect=10,
                sock_read=10,
            )
            self._session = ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,
            )
            logger.debug("HTTP session created")
        return self._session

    async def close(self):
        """Close the HTTP session gracefully."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("HTTP session closed")

    @staticmethod
    def _random_user_agent() -> str:
        return random.choice(USER_AGENTS)

    @staticmethod
    def _default_headers() -> Dict[str, str]:
        return {
            "User-Agent": HttpClient._random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Any = None,
        json: Any = None,
        proxy: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: int = MAX_RETRIES,
        retry_delay: float = RETRY_BACKOFF,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """
        Perform an HTTP request with retries and error handling.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            headers: Additional headers (merged with defaults)
            params: Query parameters
            data: Form data or bytes
            json: JSON payload
            proxy: Proxy URL (optional)
            timeout: Request timeout in seconds
            retries: Number of retries on failure
            retry_delay: Initial backoff delay (exponential)
            **kwargs: Additional arguments passed to session.request

        Returns:
            aiohttp.ClientResponse: The response object (caller must read)
        """
        session = await self.get_session()
        _headers = self._default_headers()
        if headers:
            _headers.update(headers)

        # Use provided timeout or default
        timeout_obj = ClientTimeout(total=timeout or DEFAULT_TIMEOUT)

        attempt = 0
        while attempt <= retries:
            try:
                async with session.request(
                    method=method,
                    url=url,
                    headers=_headers,
                    params=params,
                    data=data,
                    json=json,
                    proxy=proxy,
                    timeout=timeout_obj,
                    **kwargs
                ) as response:
                    # Return the response; caller is responsible for reading and closing
                    return response
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                attempt += 1
                if attempt > retries:
                    logger.error(f"Request failed after {retries} retries: {url} - {e}")
                    raise
                delay = retry_delay * (2 ** (attempt - 1))  # exponential backoff
                logger.warning(f"Request failed (attempt {attempt}/{retries}), retrying in {delay:.2f}s: {url} - {e}")
                await asyncio.sleep(delay)
            except Exception as e:
                # Non-retryable exception
                logger.error(f"Request error (non-retryable): {url} - {e}")
                raise

    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        proxy: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: int = MAX_RETRIES,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """Perform a GET request."""
        return await self.request(
            method="GET",
            url=url,
            headers=headers,
            params=params,
            proxy=proxy,
            timeout=timeout,
            retries=retries,
            **kwargs
        )

    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Any = None,
        json: Any = None,
        proxy: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: int = MAX_RETRIES,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """Perform a POST request."""
        return await self.request(
            method="POST",
            url=url,
            headers=headers,
            data=data,
            json=json,
            proxy=proxy,
            timeout=timeout,
            retries=retries,
            **kwargs
        )

    async def head(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        proxy: Optional[str] = None,
        timeout: Optional[float] = None,
        retries: int = MAX_RETRIES,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """Perform a HEAD request."""
        return await self.request(
            method="HEAD",
            url=url,
            headers=headers,
            proxy=proxy,
            timeout=timeout,
            retries=retries,
            **kwargs
        )

    @asynccontextmanager
    async def get_response(self, *args, **kwargs):
        """
        Context manager for getting a response and automatically handling cleanup.
        Usage:
            async with http_client.get_response("GET", "https://...") as resp:
                data = await resp.text()
        """
        response = await self.request(*args, **kwargs)
        try:
            yield response
        finally:
            response.close()


# ========== SINGLETON INSTANCE ==========
http_client = HttpClient()


# ========== HELPER FUNCTIONS ==========

async def fetch_text(url: str, timeout: float = 30, proxy: Optional[str] = None) -> str:
    """
    Fetch plain text content from a URL with retries.
    """
    async with http_client.get_response(
        method="GET",
        url=url,
        timeout=timeout,
        proxy=proxy,
    ) as resp:
        if resp.status != 200:
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"HTTP {resp.status}",
                headers=resp.headers,
            )
        return await resp.text()


async def fetch_json(url: str, timeout: float = 30, proxy: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch JSON content from a URL.
    """
    text = await fetch_text(url, timeout, proxy)
    import json
    return json.loads(text)


async def fetch_bytes(url: str, timeout: float = 30, proxy: Optional[str] = None) -> bytes:
    """
    Fetch binary content from a URL.
    """
    async with http_client.get_response(
        method="GET",
        url=url,
        timeout=timeout,
        proxy=proxy,
    ) as resp:
        if resp.status != 200:
            raise aiohttp.ClientResponseError(
                resp.request_info,
                resp.history,
                status=resp.status,
                message=f"HTTP {resp.status}",
                headers=resp.headers,
            )
        return await resp.read()
