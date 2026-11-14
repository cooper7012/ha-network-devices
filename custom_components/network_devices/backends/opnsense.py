"""OPNsense firewall (API key + secret)."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from ..models import make_row
from .base import AuthError, Backend, BackendError


class OPNsenseBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._base = f"https://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}/api/"
        self._auth = aiohttp.BasicAuth(self.data[CONF_USERNAME], self.data[CONF_PASSWORD])

    async def _get(self, path: str, optional: bool = True) -> Any:
        try:
            return await self._request("GET", self._base + path, auth=self._auth)
        except AuthError as err:
            if optional and err.status == 403:  # this API key lacks that privilege
                return None
            raise
        except BackendError:
            if optional:
                return None
            raise

    @staticmethod
    def _rows(res: Any) -> list[dict[str, Any]]:
        if isinstance(res, list):
            return res
        if isinstance(res, dict):
            return res.get("rows") or []
        return []

    async def async_fetch(self) -> list[dict[str, Any]]:
        arp = self._rows(await self._get("diagnostics/interface/search_arp"))
        if not arp:
            arp = self._rows(await self._get("diagnostics/interface/getArp", optional=False))
        isc, kea, dnsmasq, tif = await asyncio.gather(
            self._get("dhcpv4/leases/searchLease"),
            self._get("kea/leases4/search"),
            self._get("dnsmasq/leases/search"),
            self._get("diagnostics/traffic/interface"),
        )
        leases: dict[str, dict[str, Any]] = {}
        for l in self._rows(isc) + self._rows(kea) + self._rows(dnsmasq):
            if l.get("hwaddr"):
                leases.setdefault(l["hwaddr"].lower(), l)

        # Live per-IP throughput from the "top talkers" sampler (best effort).
        traffic: dict[str, tuple[float, float]] = {}
        keys = [k for k in ((tif or {}).get("interfaces") or {}) if k != "wan"]
        if keys:
            top = await self._get(f"diagnostics/traffic/top/{','.join(keys)}") or {}
            for iface in top.values() if isinstance(top, dict) else []:
                for rec in (iface or {}).get("records", []):
                    # 'in' is traffic entering the firewall from the host = upload.
                    traffic[rec.get("address")] = (
                        float(rec.get("rate_bits_out") or 0) / 8, float(rec.get("rate_bits_in") or 0) / 8)

        rows = []
        arp_macs = set()
        for a in arp:
            mac = (a.get("mac") or "").lower()
            if not mac or a.get("expired") in (True, "1"):
                continue
            arp_macs.add(mac)
            lease = leases.get(mac, {})
            down, up = traffic.get(a.get("ip"), (0.0, 0.0)) if traffic else (None, None)
            rows.append(make_row(
                mac, name=lease.get("descr"), hostname=a.get("hostname") or lease.get("hostname"),
                vendor=a.get("manufacturer") or lease.get("man"), online=True,
                connection=f"OPNsense · {a.get('intf_description') or a.get('intf') or ''}".rstrip(" ·"),
                network=a.get("intf_description") or lease.get("if_descr"), ip=a.get("ip"),
                download=down, upload=up,
            ))
        for mac, lease in leases.items():
            if mac not in arp_macs:
                rows.append(make_row(
                    mac, name=lease.get("descr"), hostname=lease.get("hostname"), vendor=lease.get("man"),
                    online=False, network=lease.get("if_descr"), ip=lease.get("address"),
                ))
        return rows
