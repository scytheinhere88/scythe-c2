import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logger import logger
from app.core.redis_client import get_redis, RedisKeys


class ConcurrentManager:
    """
    Manages the maximum concurrent attacks limit.
    - Stores value in Redis for persistence across restarts.
    - Caches value in memory for fast access.
    - Syncs with dashboard/admin panel via GET/POST /api/config/concurrent.
    - Validates input range (1-100).
    - Provides default from settings.MAX_CONCURRENT if not set in Redis.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._max: Optional[int] = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _get_redis(self):
        """Get Redis client (either passed in or from global get_redis)."""
        if self._redis:
            return self._redis
        return await get_redis()

    async def initialize(self):
        """
        Load max concurrent from Redis on startup.
        If not set, use default from settings and save it.
        """
        if self._initialized:
            return

        redis = await self._get_redis()
        key = RedisKeys.config("max_concurrent")

        try:
            value = await redis.get(key)
            if value is not None:
                self._max = int(value)
                logger.info(f"Loaded max_concurrent from Redis: {self._max}")
            else:
                # Use default from settings
                self._max = settings.MAX_CONCURRENT
                await redis.set(key, str(self._max))
                logger.info(f"Set default max_concurrent in Redis: {self._max}")
        except Exception as e:
            logger.error(f"Failed to load max_concurrent from Redis: {e}")
            self._max = settings.MAX_CONCURRENT

        self._initialized = True

    async def get_max(self) -> int:
        """
        Get the current max concurrent attacks limit.
        """
        if not self._initialized:
            await self.initialize()
        return self._max

    async def set_max(self, value: int) -> bool:
        """
        Set a new max concurrent attacks limit.
        Validates that value is between 1 and 100.
        Returns True if successful, False otherwise.
        """
        # Validation
        if not isinstance(value, int):
            logger.error(f"Invalid max_concurrent type: {type(value)}")
            return False

        if value < 1:
            logger.warning(f"max_concurrent {value} is less than 1, setting to 1")
            value = 1
        elif value > 100:
            logger.warning(f"max_concurrent {value} exceeds 100, setting to 100")
            value = 100

        # Check against current active attacks
        # We need to ensure we don't lower it below current active count.
        # We'll import attack_manager dynamically to avoid circular import.
        from app.managers.attack_manager import attack_manager

        active_count = len(attack_manager.active_attacks)
        if value < active_count:
            logger.warning(
                f"Cannot set max_concurrent to {value} because there are {active_count} active attacks."
                f" Must be >= {active_count}."
            )
            return False

        # Store in Redis
        redis = await self._get_redis()
        key = RedisKeys.config("max_concurrent")

        try:
            await redis.set(key, str(value))
            self._max = value
            logger.info(f"Updated max_concurrent to {value} (active attacks: {active_count})")
            return True
        except Exception as e:
            logger.error(f"Failed to save max_concurrent to Redis: {e}")
            return False

    async def get_max_safe(self) -> int:
        """
        Get max concurrent, ensuring it's at least the number of active attacks.
        Used when starting new attacks to prevent weird states.
        """
        max_val = await self.get_max()
        from app.managers.attack_manager import attack_manager

        active_count = len(attack_manager.active_attacks)
        if max_val < active_count:
            # Auto-correct to avoid blocking
            logger.warning(
                f"max_concurrent ({max_val}) is less than active attacks ({active_count}). "
                f"Auto-correcting to {active_count + 1}"
            )
            await self.set_max(active_count + 1)
            max_val = active_count + 1
        return max_val

    async def reset_to_default(self) -> bool:
        """
        Reset max concurrent to the default value from settings.
        """
        default_val = settings.MAX_CONCURRENT
        # Check if default is less than active attacks
        from app.managers.attack_manager import attack_manager

        active_count = len(attack_manager.active_attacks)
        if default_val < active_count:
            logger.warning(
                f"Cannot reset to default {default_val} because {active_count} attacks are active. "
                f"Setting to {active_count + 1}"
            )
            default_val = active_count + 1

        return await self.set_max(default_val)


# ========== SINGLETON INSTANCE ==========
concurrent_manager = ConcurrentManager()