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

from .const import DOMAIN, HOSTNAME_REFRESH, HOSTNAME_RETRY, MAX_ADDRESSES, RECENT_DEVICE_SECONDS
from .models import vendor_for
from .names import async_find_names, dhcp_names

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


def probe(targets: list[str]) -> None:
    """Send an empty UDP packet (discard port) to each address so the kernel ARPs for it.

    For an address the kernel already knows, the ARP check is a direct (unicast) message
    to that device; only unknown addresses cause ARP broadcasts.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        for ip in targets:
            try:
                sock.sendto(b"", (ip, 9))
            except OSError:
                pass


def all_hosts(nets: list[ipaddress.IPv4Network]) -> list[str]:
    return [str(ip) for net in nets for ip in net.hosts()]


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


class NetworkScanner:
    """Keeps a list of every device seen, persisted across restarts."""

    def __init__(self, hass: HomeAssistant, entry_id: str, subnets: str) -> None:
        self.hass = hass
        self.nets = parse_subnets(subnets)
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}")
        self._devices: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self.last_full_sweep = 0.0
        self.last_probe_count = 0

    async def _async_load(self) -> None:
        if not self._loaded:
            self._devices = (await self._store.async_load() or {}).get("devices", {})
            self._loaded = True

    async def async_known_rows(self) -> list[dict[str, Any]]:
        """Saved devices without scanning (all shown as offline until a scan runs)."""
        await self._async_load()
        return self._rows({}, time.time())

    async def async_scan(self, resolve_names: bool = True, full: bool = True) -> list[dict[str, Any]]:
        """full=True probes every address (finds new devices); False only known recent devices."""
        await self._async_load()
        start = time.time()
        if full:
            targets = all_hosts(self.nets)
            self.last_full_sweep = start
        else:
            targets = sorted({
                d["ip"] for d in self._devices.values()
                if d.get("ip") and start - d.get("last_seen", 0) < RECENT_DEVICE_SECONDS
                and any(ipaddress.IPv4Address(d["ip"]) in n for n in self.nets)
            })
        self.last_probe_count = len(targets)
        await self.hass.async_add_executor_job(probe, targets)
        await asyncio.sleep(2)  # give devices time to answer ARP
        arp = await self.hass.async_add_executor_job(read_arp)
        now = time.time()
        online = {
            mac: ip for mac, ip in arp.items() if any(ipaddress.IPv4Address(ip) in n for n in self.nets)
        }

        for mac, ip in online.items():
            dev = self._devices.setdefault(mac, {"first_seen": now})
            if dev.get("ip") != ip:
                dev["ip"] = ip
                dev["resolved"] = 0  # new address: look the name up again
            dev["last_seen"] = now

        if resolve_names:
            await self._async_update_names(online, now)
        self._store.async_delay_save(lambda: {"devices": self._devices}, 10)
        return self._rows(online, now)

    def _rows(self, online: dict[str, str], now: float) -> list[dict[str, Any]]:
        rows = [
            {
                "mac": mac,
                "hostname": d.get("hostname", ""),
                "hostname_source": d.get("hostname_source", ""),
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

    async def _async_update_names(self, online: dict[str, str], now: float) -> None:
        # Names HA's DHCP watcher heard are free to check every scan.
        for mac, name in dhcp_names(self.hass).items():
            if mac in self._devices and self._devices[mac].get("hostname") != name:
                self._devices[mac].update(hostname=name, hostname_source="DHCP", resolved=now)

        due = {
            mac: ip
            for mac, ip in online.items()
            if now - self._devices[mac].get("resolved", 0)
            > (HOSTNAME_REFRESH if self._devices[mac].get("hostname") else HOSTNAME_RETRY)
        }
        if not due:
            return
        found = await async_find_names(self.hass, due)
        for mac in due:
            dev = self._devices[mac]
            dev["resolved"] = now
            if mac in found:
                dev["hostname"], dev["hostname_source"] = found[mac]

    async def async_remove_store(self) -> None:
        await self._store.async_remove()
