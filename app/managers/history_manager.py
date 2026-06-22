import asyncio
import time
import sqlite3
import aiosqlite
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from app.core.config import settings
from app.core.logger import logger
from app.core.models import HistoryEntry


class HistoryManager:
    """
    Manages persistent attack history using SQLite.
    Features:
    - Automatic database initialization (table creation)
    - Async CRUD operations using aiosqlite
    - Cleanup old entries (optional)
    - Integration with attack_manager for automatic logging
    - Provides history for /api/history endpoint
    """

    def __init__(self):
        self.db_path = self._parse_db_path(settings.HISTORY_DB)
        self._initialized = False

    def _parse_db_path(self, db_url: str) -> str:
        """
        Extract file path from SQLite URL.
        Example: "sqlite:///./data/history.db" -> "./data/history.db"
        """
        if db_url.startswith("sqlite:///"):
            return db_url.replace("sqlite:///", "")
        return db_url

    async def initialize(self):
        """
        Create database tables if they don't exist.
        Called during server startup.
        """
        if self._initialized:
            return

        # Ensure directory exists
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Create table
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        domain TEXT NOT NULL,
                        method TEXT NOT NULL,
                        avg_rps INTEGER DEFAULT 0,
                        total_requests INTEGER DEFAULT 0,
                        duration INTEGER DEFAULT 0,
                        timestamp INTEGER NOT NULL
                    )
                """)
                # Create index for fast ordering by timestamp
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_history_timestamp 
                    ON history(timestamp DESC)
                """)
                # Create index for domain lookups (if needed)
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_history_domain 
                    ON history(domain)
                """)
                await db.commit()
                self._initialized = True
                logger.info(f"History database initialized: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize history database: {e}")
            raise

    async def add_entry(
        self,
        domain: str,
        method: str,
        avg_rps: int,
        total_requests: int,
        duration: int,
        timestamp: Optional[int] = None
    ) -> bool:
        """
        Add a new entry to the history.
        Called automatically when an attack completes.
        """
        if not self._initialized:
            await self.initialize()

        if timestamp is None:
            timestamp = int(time.time())

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO history (domain, method, avg_rps, total_requests, duration, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (domain, method, avg_rps, total_requests, duration, timestamp)
                )
                await db.commit()
                logger.debug(f"Added history entry: {domain} | {method} | {total_requests} req")
                return True
        except Exception as e:
            logger.error(f"Failed to add history entry: {e}")
            return False

    async def get_history(self, limit: int = 10) -> List[HistoryEntry]:
        """
        Get the most recent history entries.
        Default limit is 10, but dashboard typically asks for 5.
        """
        if not self._initialized:
            await self.initialize()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, domain, method, avg_rps, total_requests, duration, timestamp
                    FROM history
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    entries = []
                    for row in rows:
                        # Convert to HistoryEntry model (Pydantic compatible)
                        entries.append(HistoryEntry(
                            id=row["id"],
                            domain=row["domain"],
                            method=row["method"],
                            avg_rps=row["avg_rps"],
                            total_requests=row["total_requests"],
                            duration=row["duration"],
                            timestamp=row["timestamp"]
                        ))
                    return entries
        except Exception as e:
            logger.error(f"Failed to get history: {e}")
            return []

    async def get_history_by_domain(self, domain: str, limit: int = 10) -> List[HistoryEntry]:
        """
        Get history entries filtered by domain.
        Useful for checking specific targets.
        """
        if not self._initialized:
            await self.initialize()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT id, domain, method, avg_rps, total_requests, duration, timestamp
                    FROM history
                    WHERE domain LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (f"%{domain}%", limit)
                ) as cursor:
                    rows = await cursor.fetchall()
                    entries = []
                    for row in rows:
                        entries.append(HistoryEntry(
                            id=row["id"],
                            domain=row["domain"],
                            method=row["method"],
                            avg_rps=row["avg_rps"],
                            total_requests=row["total_requests"],
                            duration=row["duration"],
                            timestamp=row["timestamp"]
                        ))
                    return entries
        except Exception as e:
            logger.error(f"Failed to get history by domain: {e}")
            return []

    async def get_stats(self) -> dict:
        """
        Get overall statistics from history.
        Returns: total_entries, total_requests_all_time, avg_rps_overall, domains_count
        """
        if not self._initialized:
            await self.initialize()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                stats = {}
                # Total entries
                async with db.execute("SELECT COUNT(*) as count FROM history") as cursor:
                    row = await cursor.fetchone()
                    stats["total_entries"] = row[0] if row else 0

                # Total requests
                async with db.execute("SELECT SUM(total_requests) as total FROM history") as cursor:
                    row = await cursor.fetchone()
                    stats["total_requests_all_time"] = row[0] if row and row[0] else 0

                # Unique domains
                async with db.execute("SELECT COUNT(DISTINCT domain) as count FROM history") as cursor:
                    row = await cursor.fetchone()
                    stats["unique_domains"] = row[0] if row else 0

                # Overall avg RPS
                async with db.execute("SELECT AVG(avg_rps) as avg FROM history WHERE avg_rps > 0") as cursor:
                    row = await cursor.fetchone()
                    stats["overall_avg_rps"] = int(row[0]) if row and row[0] else 0

                return stats
        except Exception as e:
            logger.error(f"Failed to get history stats: {e}")
            return {
                "total_entries": 0,
                "total_requests_all_time": 0,
                "unique_domains": 0,
                "overall_avg_rps": 0
            }

    async def delete_old_entries(self, days: int = 30) -> int:
        """
        Delete history entries older than N days.
        Returns the number of deleted rows.
        """
        if not self._initialized:
            await self.initialize()

        cutoff = int(time.time()) - (days * 86400)
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM history WHERE timestamp < ?",
                    (cutoff,)
                )
                await db.commit()
                deleted = cursor.rowcount
                logger.info(f"Deleted {deleted} history entries older than {days} days")
                return deleted
        except Exception as e:
            logger.error(f"Failed to delete old history: {e}")
            return 0

    async def clear_all(self) -> int:
        """
        Delete ALL history entries.
        Returns the number of deleted rows.
        Use with caution!
        """
        if not self._initialized:
            await self.initialize()

        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("DELETE FROM history")
                await db.commit()
                deleted = cursor.rowcount
                logger.warning(f"Deleted ALL history entries ({deleted} rows)")
                return deleted
        except Exception as e:
            logger.error(f"Failed to clear history: {e}")
            return 0

    async def get_latest(self) -> Optional[HistoryEntry]:
        """
        Get the most recent entry.
        """
        entries = await self.get_history(limit=1)
        return entries[0] if entries else None


# ========== SINGLETON INSTANCE ==========
history_manager = HistoryManager()