"""OpenWrt (LuCI / rpcd ubus JSON-RPC). Log in as root or a user with read access."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

from ..models import RateTracker, band_for_channel, make_row
from .base import AuthError, Backend, BackendError, ConnectError

NULL_SESSION = "0" * 32


class OpenWrtBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        scheme = "https" if self.data.get(CONF_SSL) else "http"
        self._url = f"{scheme}://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}/ubus"
        self._sid: str | None = None
        self._rates = RateTracker()
        self._id = 0

    async def _rpc(self, sid: str, obj: str, method: str, args: dict | None = None) -> Any:
        self._id += 1
        body = await self._request(
            "POST",
            self._url,
            json={"jsonrpc": "2.0", "id": self._id, "method": "call", "params": [sid, obj, method, args or {}]},
        )
        if "error" in body:
            if body["error"].get("code") == -32002:  # access denied / session expired
                raise AuthError(body["error"].get("message", "Access denied"))
            raise ConnectError(body["error"].get("message", "ubus error"))
        result = body.get("result") or [1]
        if result[0] == 6:  # UBUS_STATUS_PERMISSION_DENIED
            raise AuthError(f"Permission denied for {obj}.{method}")
        if result[0] != 0:
            raise ConnectError(f"{obj}.{method} failed with status {result[0]}")
        return result[1] if len(result) > 1 else {}

    async def _login(self) -> None:
        try:
            res = await self._rpc(
                NULL_SESSION,
                "session",
                "login",
                {"username": self.data[CONF_USERNAME], "password": self.data[CONF_PASSWORD]},
            )
        except (AuthError, ConnectError) as err:
            raise AuthError(f"Login failed: {err}") from err
        self._sid = res["ubus_rpc_session"]

    async def _call(self, obj: str, method: str, args: dict | None = None, optional: bool = False) -> Any:
        if self._sid is None:
            await self._login()
        try:
            try:
                return await self._rpc(self._sid, obj, method, args)
            except AuthError:
                await self._login()
                return await self._rpc(self._sid, obj, method, args)
        except BackendError:
            if optional:
                return {}
            raise

    async def async_fetch(self) -> list[dict[str, Any]]:
        hints, leases, wireless, ifaces, arp = await asyncio.gather(
            self._call("luci-rpc", "getHostHints", optional=True),
            self._call("luci-rpc", "getDHCPLeases", optional=True),
            self._call("network.wireless", "status", optional=True),
            self._call("network.interface", "dump", optional=True),
            self._call("file", "read", {"path": "/proc/net/arp"}, optional=True),
        )
        dev_to_net = {
            i.get("l3_device") or i.get("device"): i.get("interface")
            for i in (ifaces or {}).get("interface", [])
            if i.get("interface") not in ("loopback",)
        }

        # Wireless clients via hostapd (bytes for activity) or iwinfo fallback.
        wifi: dict[str, dict[str, Any]] = {}
        for radio in (wireless or {}).values():
            if not isinstance(radio, dict) or not radio.get("up"):
                continue
            rcfg = radio.get("config", {})
            for iface in radio.get("interfaces", []):
                ifname = iface.get("ifname")
                icfg = iface.get("config", {})
                if not ifname or icfg.get("mode", "ap") != "ap":
                    continue
                info = await self._call("iwinfo", "info", {"device": ifname}, optional=True)
                channel = info.get("channel") or rcfg.get("channel")
                band = {"2g": "2.4 GHz", "5g": "5 GHz", "6g": "6 GHz", "60g": "60 GHz"}.get(
                    rcfg.get("band"), ""
                ) or band_for_channel(channel)
                net = (icfg.get("network") or [""])[0]
                base = {"ssid": icfg.get("ssid") or info.get("ssid"), "channel": channel, "band": band,
                        "ifname": ifname, "network": net}
                clients = (await self._call(f"hostapd.{ifname}", "get_clients", optional=True)).get("clients")
                if clients:
                    for mac, c in clients.items():
                        b = c.get("bytes") or {}
                        wifi[mac.lower()] = {**base, "signal": c.get("signal"), "rx": b.get("rx"), "tx": b.get("tx")}
                else:
                    assoc = await self._call("iwinfo", "assoclist", {"device": ifname}, optional=True)
                    for c in assoc.get("results", []):
                        wifi[c["mac"].lower()] = {**base, "signal": c.get("signal")}

        neigh: dict[str, tuple[str, str]] = {}
        for line in (arp or {}).get("data", "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 6 and parts[2] != "0x0" and parts[3] != "00:00:00:00:00:00":
                neigh[parts[3].lower()] = (parts[0], parts[5])

        lease_map = {
            l["macaddr"].lower(): l for l in (leases or {}).get("dhcp_leases", []) if l.get("macaddr")
        }
        hint_map = {m.lower(): h for m, h in (hints or {}).items()}

        rows = []
        for mac in wifi.keys() | neigh.keys() | lease_map.keys() | hint_map.keys():
            h, lease, w = hint_map.get(mac, {}), lease_map.get(mac, {}), wifi.get(mac)
            ip = (neigh.get(mac) or (None, None))[0] or lease.get("ipaddr") or (h.get("ipaddrs") or [""])[0]
            online = w is not None or mac in neigh
            if w:
                rows.append(make_row(
                    mac, hostname=lease.get("hostname") or h.get("name"), online=True, wired=False,
                    connection=f"{self.data[CONF_HOST]} · {w['ifname']}", network=w["network"], wifi=w["ssid"],
                    channel=w["channel"], band=w["band"], signal=w.get("signal"), ip=ip,
                    download=self._rates.rate(f"{mac}:tx", w.get("tx")),
                    upload=self._rates.rate(f"{mac}:rx", w.get("rx")),
                ))
            else:
                dev = (neigh.get(mac) or (None, ""))[1]
                # Not on this router's WiFi: wired, or on another access point.
                rows.append(make_row(
                    mac, hostname=lease.get("hostname") or h.get("name"), online=online,
                    connection=f"{self.data[CONF_HOST]} · {dev}" if online else "",
                    network=dev_to_net.get(dev, ""), ip=ip,
                ))
        return rows

