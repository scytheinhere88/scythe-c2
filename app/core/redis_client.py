import redis.asyncio as redis
from typing import Optional
import logging
from app.core.config import settings

logger = logging.getLogger("scythe_c2.redis")

# ========== GLOBAL VARIABLE ==========
_redis_client: Optional[redis.Redis] = None

# ========== KEY PREFIX ==========
class RedisKeys:
    """Helper untuk naming convention key di Redis"""
    PREFIX = "scythe:"

    @staticmethod
    def attack(attack_id: str) -> str:
        return f"{RedisKeys.PREFIX}attack:{attack_id}"

    @staticmethod
    def active_attacks() -> str:
        return f"{RedisKeys.PREFIX}active_attacks"

    @staticmethod
    def proxy_pool() -> str:
        return f"{RedisKeys.PREFIX}proxy_pool"

    @staticmethod
    def proxy_alive() -> str:
        return f"{RedisKeys.PREFIX}proxy_alive"

    @staticmethod
    def bot(bot_id: str) -> str:
        return f"{RedisKeys.PREFIX}bot:{bot_id}"

    @staticmethod
    def active_bots() -> str:
        return f"{RedisKeys.PREFIX}active_bots"

    @staticmethod
    def config(key: str) -> str:
        return f"{RedisKeys.PREFIX}config:{key}"

    @staticmethod
    def last_scrap() -> str:
        return f"{RedisKeys.PREFIX}proxy_last_scrap"


# ========== CONNECTION ==========
async def get_redis() -> redis.Redis:
    """
    Mendapatkan koneksi Redis (singleton).
    Otomatis membuat koneksi jika belum ada.
    """
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                settings.REDIS_URL,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,          # Auto-decode string
                max_connections=20,              # Connection pool
                socket_timeout=5,                # Timeout operasi
                socket_connect_timeout=5,        # Timeout koneksi
                retry_on_timeout=True,
                health_check_interval=30,
            )
            # Test koneksi
            await _redis_client.ping()
            logger.info(f"Redis connected: {settings.REDIS_URL}")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise
    return _redis_client


async def close_redis():
    """Tutup koneksi Redis dengan aman."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed.")


# ========== HELPER FUNCTIONS ==========
async def redis_set(key: str, value, ex: Optional[int] = None):
    """Set key-value dengan optional expiration (detik)."""
    r = await get_redis()
    return await r.set(key, value, ex=ex)


async def redis_get(key: str):
    """Get value by key."""
    r = await get_redis()
    return await r.get(key)


async def redis_delete(key: str):
    """Hapus key."""
    r = await get_redis()
    return await r.delete(key)


async def redis_hset(key: str, mapping: dict):
    """Set hash fields."""
    r = await get_redis()
    return await r.hset(key, mapping=mapping)


async def redis_hget(key: str, field: str):
    """Get hash field."""
    r = await get_redis()
    return await r.hget(key, field)


async def redis_hgetall(key: str) -> dict:
    """Get all fields dari hash."""
    r = await get_redis()
    return await r.hgetall(key)


async def redis_sadd(key: str, *members):
    """Tambah member ke set."""
    r = await get_redis()
    return await r.sadd(key, *members)


async def redis_srem(key: str, *members):
    """Hapus member dari set."""
    r = await get_redis()
    return await r.srem(key, *members)


async def redis_smembers(key: str):
    """Get all members dari set."""
    r = await get_redis()
    return await r.smembers(key)


async def redis_scard(key: str) -> int:
    """Count members di set."""
    r = await get_redis()
    return await r.scard(key)


async def redis_keys(pattern: str):
    """Cari keys dengan pattern."""
    r = await get_redis()
    return await r.keys(pattern)


async def redis_expire(key: str, seconds: int):
    """Set TTL untuk key."""
    r = await get_redis()
    return await r.expire(key, seconds)


async def redis_publish(channel: str, message: str):
    """Publish message ke channel (untuk event)."""
    r = await get_redis()
    return await r.publish(channel, message)


async def redis_subscribe(channel: str):
    """Subscribe ke channel (untuk pub/sub)."""
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(channel)
    return pubsub


# ========== INIT ==========
async def init_redis():
    """Inisialisasi Redis (bisa dipanggil di startup)."""
    try:
        r = await get_redis()
        # Opsional: bersihkan keys basi atau set default config
        logger.info("Redis initialized.")
    except Exception as e:
        logger.error(f"Redis init failed: {e}")
        raise


# ========== EXCEPTION ==========
class RedisError(Exception):
    pass