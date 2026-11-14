"""MikroTik RouterOS v7 (REST API; enable the www or www-ssl service)."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

from ..models import RateTracker, band_for_freq, channel_for_freq, make_row
from .base import Backend, BackendError

UNITS = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}


def parse_rate(value: Any) -> float | None:
    """'12.5Mbps' / '1200' (bits/s) -> bytes/s."""
    if value in (None, ""):
        return None
    m = re.match(r"^\s*([\d.]+)\s*([kmg]?)", str(value).lower())
    return float(m.group(1)) * UNITS[m.group(2)] / 8 if m else None


def first_int(value: Any) -> int | None:
    m = re.match(r"^\s*(-?\d+)", str(value or ""))
    return int(m.group(1)) if m else None


class MikroTikBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        scheme = "https" if self.data.get(CONF_SSL) else "http"
        self._base = f"{scheme}://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}/rest/"
        self._auth = aiohttp.BasicAuth(self.data[CONF_USERNAME], self.data[CONF_PASSWORD])
        self._rates = RateTracker()

    async def _get(self, path: str, optional: bool = True) -> list[dict[str, Any]]:
        try:
            res = await self._request("GET", self._base + path, auth=self._auth)
        except BackendError:
            if optional:
                return []
            raise
        return res if isinstance(res, list) else []

    async def async_fetch(self) -> list[dict[str, Any]]:
        identity = await self._request("GET", self._base + "system/identity", auth=self._auth)
        router = (identity or {}).get("name") or self.data[CONF_HOST]
        leases, arp, bridge, wifi_reg, wl_reg, wifi_if, wl_if, kid = await asyncio.gather(
            self._get("ip/dhcp-server/lease"),
            self._get("ip/arp"),
            self._get("interface/bridge/host"),
            self._get("interface/wifi/registration-table"),
            self._get("interface/wireless/registration-table"),
            self._get("interface/wifi"),
            self._get("interface/wireless"),
            self._get("ip/kid-control/device"),
        )
        ifaces = {i.get("name"): i for i in wifi_if + wl_if}
        port_of = {
            b["mac-address"].lower(): b.get("on-interface")
            for b in bridge
            if b.get("mac-address") and b.get("local") not in ("true", True)
        }
        arp_map = {
            a["mac-address"].lower(): a
            for a in arp
            if a.get("mac-address") and a.get("status") not in ("failed", "incomplete")
            and a.get("disabled") not in ("true", True)
        }
        lease_map = {l["mac-address"].lower(): l for l in leases if l.get("mac-address")}
        kid_map = {k["mac-address"].lower(): k for k in kid if k.get("mac-address")}
        reg_map = {r["mac-address"].lower(): r for r in wifi_reg + wl_reg if r.get("mac-address")}

        rows = []
        for mac in lease_map.keys() | arp_map.keys() | reg_map.keys():
            lease, a, reg, k = lease_map.get(mac, {}), arp_map.get(mac, {}), reg_map.get(mac), kid_map.get(mac, {})
            online = bool(reg) or bool(a) or lease.get("status") == "bound"
            ip = a.get("address") or lease.get("address") or lease.get("active-address")
            down, up = parse_rate(k.get("rate-down")), parse_rate(k.get("rate-up"))
            if reg:
                iface = ifaces.get(reg.get("interface"), {})
                freq = first_int(reg.get("channel")) or first_int(iface.get("frequency"))
                ch = channel_for_freq(freq)
                sent_recv = str(reg.get("bytes") or "").split(",")  # router sent, router received
                if down is None and len(sent_recv) == 2:
                    down = self._rates.rate(f"{mac}:tx", sent_recv[0])
                    up = self._rates.rate(f"{mac}:rx", sent_recv[1])
                rows.append(make_row(
                    mac, name=lease.get("comment"), hostname=lease.get("host-name"), online=True, wired=False,
                    connection=f"{router} · {reg.get('interface')}", network=lease.get("server"),
                    wifi=reg.get("ssid") or iface.get("configuration.ssid") or iface.get("ssid"),
                    channel=ch, band=band_for_freq(freq),
                    signal=first_int(reg.get("signal") or reg.get("signal-strength")), ip=ip,
                    download=down if down is not None else 0.0, upload=up if up is not None else 0.0,
                ))
            else:
                port = port_of.get(mac)
                wired = True if port and not port.startswith(("wlan", "wifi", "cap")) else None
                rows.append(make_row(
                    mac, name=lease.get("comment"), hostname=lease.get("host-name"), online=online,
                    wired=wired if online else None,
                    connection=f"{router} · {port or a.get('interface') or ''}".rstrip(" ·") if online else "",
                    network=lease.get("server") or a.get("interface"), ip=ip, download=down, upload=up,
                ))
        return rows
