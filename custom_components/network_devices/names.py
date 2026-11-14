"""Find hostnames for devices without relying on the router.

Sources, best first:
1. Home Assistant's DHCP watcher: the name a device sent when it joined the network.
2. Reverse DNS through Home Assistant's resolver and directly to the default gateway.
3. Home Assistant's mDNS (zeroconf) cache: names of smart-home devices it has discovered.
4. mDNS reverse lookup sent to the devices themselves (Apple, Linux, printers, many IoT devices).
5. NetBIOS name query (Windows PCs, NAS boxes, Samba).
"""
from __future__ import annotations

import asyncio
import logging
import random
import socket
import struct
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MDNS_ADDR = ("224.0.0.251", 5353)
TYPE_A, TYPE_PTR = 1, 12
NBSTAT_NAME = b"\x20" + b"CK" + b"A" * 30 + b"\x00"  # NetBIOS-encoded "*"


# --- tiny DNS helpers -------------------------------------------------------------

def ptr_name(ip: str) -> str:
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def build_query(txid: int, name: str, qtype: int) -> bytes:
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".") if p) + b"\x00"
    return struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0) + q + struct.pack(">HH", qtype, 1)


def read_name(buf: bytes, off: int) -> tuple[str, int]:
    labels: list[str] = []
    end = None
    for _ in range(128):
        length = buf[off]
        if length == 0:
            off += 1
            break
        if length & 0xC0 == 0xC0:  # compression pointer
            if end is None:
                end = off + 2
            off = ((length & 0x3F) << 8) | buf[off + 1]
            continue
        labels.append(buf[off + 1 : off + 1 + length].decode(errors="replace"))
        off += 1 + length
    return ".".join(labels), (end if end is not None else off)


def parse_ptr(packet: bytes) -> tuple[int, str] | None:
    """(txid, first PTR target) from a DNS/mDNS response, or None."""
    try:
        txid, flags, qd, an = struct.unpack(">HHHH", packet[:8])
        if not flags & 0x8000 or an == 0:
            return None
        off = 12
        for _ in range(qd):
            _, off = read_name(packet, off)
            off += 4
        for _ in range(an):
            _, off = read_name(packet, off)
            rtype, _, _, rdlen = struct.unpack(">HHIH", packet[off : off + 10])
            off += 10
            if rtype == TYPE_PTR:
                return txid, read_name(packet, off)[0]
            off += rdlen
    except (IndexError, struct.error):
        return None
    return None


def short_name(name: str) -> str:
    """'Living-Room-TV.local' / 'tv.localdomain' -> 'Living-Room-TV'."""
    name = name.strip().rstrip(".")
    if not name or name.replace(".", "").isdigit():
        return ""
    return name.split(".")[0]


def parse_nbstat(packet: bytes) -> str:
    """First unique workstation name from a NetBIOS node-status response."""
    try:
        _, off = read_name(packet, 12)
        off += 10  # type, class, ttl, rdlength
        count = packet[off]
        off += 1
        for i in range(count):
            entry = packet[off + i * 18 : off + i * 18 + 18]
            name, suffix, flags = entry[:15], entry[15], struct.unpack(">H", entry[16:18])[0]
            if suffix == 0x00 and not flags & 0x8000:
                return name.decode(errors="replace").strip()
    except (IndexError, struct.error):
        pass
    return ""


# --- UDP fan-out ----------------------------------------------------------------

class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.replies: list[tuple[bytes, str]] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.replies.append((data, addr[0]))

    def error_received(self, exc: Exception) -> None:  # ICMP unreachable etc.
        pass


async def udp_fanout(packets: list[tuple[bytes, tuple[str, int]]], wait: float) -> list[tuple[bytes, str]]:
    """Send every packet from one socket, then collect replies for `wait` seconds."""
    if not packets:
        return []
    loop = asyncio.get_running_loop()
    try:
        transport, proto = await loop.create_datagram_endpoint(_Collector, local_addr=("0.0.0.0", 0))
    except OSError as err:
        _LOGGER.debug("Cannot open UDP socket: %s", err)
        return []
    try:
        for data, addr in packets:
            try:
                transport.sendto(data, addr)
            except OSError:
                pass
            await asyncio.sleep(0.002)  # don't flood the network
        await asyncio.sleep(wait)
        return list(proto.replies)
    finally:
        transport.close()


async def ptr_lookup(ips: list[str], server: tuple[str, int], wait: float = 2.0) -> dict[str, str]:
    """Reverse-lookup many IPs at once against one DNS (or mDNS) server."""
    ids: dict[int, str] = {}
    packets = []
    for ip in ips:
        txid = random.randrange(1, 0xFFFF)
        while txid in ids:
            txid = random.randrange(1, 0xFFFF)
        ids[txid] = ip
        packets.append((build_query(txid, ptr_name(ip), TYPE_PTR), server))
    names: dict[str, str] = {}
    for data, _ in await udp_fanout(packets, wait):
        if (parsed := parse_ptr(data)) and parsed[0] in ids and (name := short_name(parsed[1])):
            names.setdefault(ids[parsed[0]], name)
    return names


async def netbios_lookup(ips: list[str], wait: float = 1.5) -> dict[str, str]:
    packets = []
    for ip in ips:
        txid = random.randrange(1, 0xFFFF)
        packets.append((struct.pack(">HHHHHH", txid, 0, 1, 0, 0, 0) + NBSTAT_NAME + b"\x00\x21\x00\x01", (ip, 137)))
    names: dict[str, str] = {}
    for data, src in await udp_fanout(packets, wait):
        if name := parse_nbstat(data):
            names.setdefault(src, name)
    return names


async def system_reverse_dns(ips: list[str]) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(32)
    names: dict[str, str] = {}

    async def one(ip: str) -> None:
        async with sem:
            try:
                host, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), 3)
            except (OSError, TimeoutError):
                return
            if name := short_name(host):
                names[ip] = name

    await asyncio.gather(*(one(ip) for ip in ips))
    return names


def default_gateways(path: str = "/proc/net/route") -> list[str]:
    gws: list[str] = []
    try:
        with open(path, encoding="ascii") as f:
            for line in f.read().splitlines()[1:]:
                p = line.split()
                if len(p) > 3 and p[1] == "00000000" and int(p[3], 16) & 0x2:
                    gw = socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
                    if gw not in gws:
                        gws.append(gw)
    except (OSError, ValueError):
        pass
    return gws


# --- Home Assistant's own knowledge ---------------------------------------------------

def dhcp_names(hass: HomeAssistant) -> dict[str, str]:
    """mac (aa:bb:..) -> hostname, from HA's DHCP watcher."""
    try:
        from homeassistant.components.dhcp.helpers import async_get_address_data_internal  # noqa: PLC0415

        data = async_get_address_data_internal(hass)
    except Exception:  # noqa: BLE001 - dhcp not loaded / internal API changed
        return {}
    out = {}
    for mac, info in data.items():
        if (name := short_name(info.get("hostname") or "")) and len(mac) == 12:
            out[":".join(mac[i : i + 2] for i in range(0, 12, 2)).lower()] = name
    return out


async def zeroconf_names(hass: HomeAssistant) -> dict[str, str]:
    """ip -> hostname, from A records HA's mDNS discovery has already seen."""
    if "zeroconf" not in hass.config.components:
        return {}
    try:
        from homeassistant.components import zeroconf  # noqa: PLC0415

        aiozc = await zeroconf.async_get_async_instance(hass)
        out: dict[str, str] = {}
        for store in list(aiozc.zeroconf.cache.cache.values()):
            for rec in list(store):
                address = getattr(rec, "address", None)
                if rec.type == TYPE_A and isinstance(address, bytes) and len(address) == 4:
                    if name := short_name(rec.name):
                        out.setdefault(socket.inet_ntoa(address), name)
        return out
    except Exception:  # noqa: BLE001 - internal structures may change
        return {}


async def async_find_names(hass: HomeAssistant, devices: dict[str, str]) -> dict[str, tuple[str, str]]:
    """devices: mac -> ip. Returns mac -> (hostname, source) for every name found."""
    found: dict[str, tuple[str, str]] = {}
    by_dhcp = dhcp_names(hass)
    for mac in devices:
        if name := by_dhcp.get(mac):
            found[mac] = (name, "DHCP")

    async def run(source: str, lookup) -> None:
        missing = {ip: mac for mac, ip in devices.items() if mac not in found}
        if not missing:
            return
        try:
            names = await lookup(list(missing))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s name lookup failed: %s", source, err)
            return
        for ip, name in names.items():
            if ip in missing and name:
                found.setdefault(missing[ip], (name, source))

    await run("DNS", system_reverse_dns)
    for gw in default_gateways():
        await run("DNS", lambda ips, gw=gw: ptr_lookup(ips, (gw, 53)))
    zc = await zeroconf_names(hass)
    await run("mDNS", lambda ips: _const(zc))
    await run("mDNS", lambda ips: ptr_lookup(ips, MDNS_ADDR))
    await run("NetBIOS", netbios_lookup)
    return found


async def _const(value: dict[str, str]) -> dict[str, str]:
    return value
