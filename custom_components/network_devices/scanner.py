"""Find devices on the local network: probe every address, then read the kernel ARP table.

Works with any router. Only finds devices on networks (VLANs) Home Assistant is directly
connected to, because MAC addresses don't cross routers.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, HOSTNAME_REFRESH, MAX_ADDRESSES
from .models import vendor_for

ARP_PATH = "/proc/net/arp"


class ScanError(Exception):
    """Scanning is not possible."""


def parse_subnets(text: str) -> list[ipaddress.IPv4Network]:
    """'192.168.1.0/24, 10.0.0.0/24' -> networks. Raises ValueError."""
    nets = [ipaddress.IPv4Network(p.strip(), strict=False) for p in text.replace(";", ",").split(",") if p.strip()]
    if not nets:
        raise ValueError("no subnet")
    if sum(n.num_addresses for n in nets) > MAX_ADDRESSES:
        raise ValueError("too many addresses")
    return nets


async def async_local_networks(hass: HomeAssistant) -> list[ipaddress.IPv4Network]:
    """Networks Home Assistant has an address on (from Settings > System > Network)."""
    from homeassistant.components import network  # noqa: PLC0415

    nets: list[ipaddress.IPv4Network] = []
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ip in adapter["ipv4"]:
            iface = ipaddress.IPv4Interface(f"{ip['address']}/{ip['network_prefix']}")
            if iface.is_loopback or iface.is_link_local:
                continue
            net = iface.network if iface.network.prefixlen >= 20 else ipaddress.IPv4Network(f"{iface.ip}/24", strict=False)
            if net not in nets:
                nets.append(net)
    return nets


def probe(nets: list[ipaddress.IPv4Network]) -> None:
    """Send an empty UDP packet to every address so the kernel ARPs for each one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        for net in nets:
            for ip in net.hosts():
                try:
                    sock.sendto(b"", (str(ip), 9))
                except OSError:
                    pass


def read_arp(path: str = ARP_PATH) -> dict[str, str]:
    """mac -> ip for every resolved neighbour."""
    try:
        with open(path, encoding="ascii") as f:
            lines = f.read().splitlines()[1:]
    except OSError as err:
        raise ScanError(f"Cannot read the ARP table ({err})") from err
    found: dict[str, str] = {}
    for line in lines:
        p = line.split()
        if len(p) >= 4 and p[2] != "0x0" and p[3] != "00:00:00:00:00:00":
            found[p[3].lower()] = p[0]
    return found


async def resolve_hostname(ip: str) -> str:
    """Reverse DNS. Most routers (including UniFi) answer with the device's DHCP hostname."""
    loop = asyncio.get_running_loop()
    try:
        host, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), socket.NI_NAMEREQD), 3)
    except (OSError, TimeoutError):
        return ""
    if host.replace(".", "").isdigit():
        return ""
    return host.split(".")[0]


class NetworkScanner:
    """Keeps a list of every device seen, persisted across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str, subnets: str) -> None:
        self.hass = hass
        self.nets = parse_subnets(subnets)
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}")
        self._devices: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def async_scan(self) -> list[dict[str, Any]]:
        if not self._loaded:
            self._devices = (await self._store.async_load() or {}).get("devices", {})
            self._loaded = True

        await self.hass.async_add_executor_job(probe, self.nets)
        await asyncio.sleep(2)  # give devices time to answer ARP
        arp = await self.hass.async_add_executor_job(read_arp)
        now = time.time()
        online = {
            mac: ip for mac, ip in arp.items() if any(ipaddress.IPv4Address(ip) in n for n in self.nets)
        }

        sem = asyncio.Semaphore(32)

        async def update(mac: str, ip: str) -> None:
            dev = self._devices.setdefault(mac, {"first_seen": now})
            stale = now - dev.get("resolved", 0) > HOSTNAME_REFRESH
            if dev.get("ip") != ip or stale:
                async with sem:
                    dev["hostname"] = await resolve_hostname(ip) or dev.get("hostname", "")
                dev["resolved"] = now
            dev["ip"] = ip
            dev["last_seen"] = now

        await asyncio.gather(*(update(mac, ip) for mac, ip in online.items()))
        self._store.async_delay_save(lambda: {"devices": self._devices}, 10)

        rows = [
            {
                "mac": mac,
                "hostname": d.get("hostname", ""),
                "vendor": vendor_for(mac),
                "ip": d.get("ip", ""),
                "online": mac in online,
                "last_seen": d.get("last_seen"),
                "seconds_since_seen": None if mac in online else now - d.get("last_seen", now),
            }
            for mac, d in self._devices.items()
        ]
        rows.sort(key=lambda r: tuple(int(x) for x in r["ip"].split(".")) if r["ip"] else (999,))
        return rows

    async def async_remove_store(self) -> None:
        await self._store.async_remove()
