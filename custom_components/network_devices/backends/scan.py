"""Generic network scan: works with any router. Probes the subnet, then reads the ARP table.

Gives Name (reverse DNS), MAC, Vendor and IP. WiFi, channel and activity need a router API.
Home Assistant must be on the same LAN (HA OS, or Docker with network_mode: host).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Any

from ..const import CONF_SUBNETS
from ..models import make_row
from .base import Backend, ConnectError

SWEEP_SECONDS = 300
MAX_HOSTS = 4096


def parse_subnets(text: str) -> list[ipaddress.IPv4Network]:
    nets = []
    for part in text.replace(";", ",").split(","):
        if part.strip():
            nets.append(ipaddress.IPv4Network(part.strip(), strict=False))
    if not nets:
        raise ValueError("no subnet")
    if sum(n.num_addresses for n in nets) > MAX_HOSTS:
        raise ValueError("too many addresses")
    return nets


def _probe(nets: list[ipaddress.IPv4Network]) -> None:
    """Send one tiny UDP packet to every address so the kernel ARPs for it."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setblocking(False)
        for net in nets:
            for ip in net.hosts():
                try:
                    s.sendto(b"", (str(ip), 9))
                except OSError:
                    pass


def read_arp(path: str = "/proc/net/arp") -> dict[str, tuple[str, str]]:
    """mac -> (ip, device) for complete entries."""
    out: dict[str, tuple[str, str]] = {}
    try:
        with open(path, encoding="ascii") as f:
            lines = f.read().splitlines()[1:]
    except OSError as err:
        raise ConnectError(f"Cannot read ARP table: {err}") from err
    for line in lines:
        p = line.split()
        if len(p) >= 6 and p[2] != "0x0" and p[3] != "00:00:00:00:00:00":
            out[p[3].lower()] = (p[0], p[5])
    return out


class ScanBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._nets = parse_subnets(self.data[CONF_SUBNETS])
        self._last_sweep = 0.0
        self._names: dict[str, str] = {}
        self._seen: dict[str, dict[str, Any]] = {}

    async def _resolve(self, ip: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            host, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), 3)
            self._names[ip] = host.split(".")[0] if not host.replace(".", "").isdigit() else ""
        except (OSError, TimeoutError, socket.gaierror):
            self._names[ip] = ""

    async def async_fetch(self) -> list[dict[str, Any]]:
        now = time.time()
        if now - self._last_sweep > SWEEP_SECONDS:
            self._last_sweep = now
            await self.hass.async_add_executor_job(_probe, self._nets)
            await asyncio.sleep(2)
        arp = await self.hass.async_add_executor_job(read_arp)
        arp = {m: v for m, v in arp.items() if any(ipaddress.IPv4Address(v[0]) in n for n in self._nets)}
        sem = asyncio.Semaphore(32)

        async def resolve(ip: str) -> None:
            async with sem:
                await self._resolve(ip)

        await asyncio.gather(*(resolve(ip) for ip, _ in arp.values() if ip not in self._names))
        for mac, (ip, dev) in arp.items():
            self._seen[mac] = {"ip": ip, "dev": dev, "last_seen": now}
        return [
            make_row(
                mac,
                hostname=self._names.get(s["ip"]),
                online=mac in arp,
                connection=f"Network scan · {s['dev']}" if mac in arp else "",
                ip=s["ip"],
                last_seen=s["last_seen"],
            )
            for mac, s in self._seen.items()
        ]
