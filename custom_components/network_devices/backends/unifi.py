"""Ubiquiti UniFi Network (UniFi OS consoles and self-hosted controllers)."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from ..const import CONF_SITE
from ..models import BANDS, make_row
from .base import TIMEOUT, AuthError, Backend, ConnectError, describe

import aiohttp


class UniFiBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        d = self.data
        self._base = f"https://{d[CONF_HOST]}:{d[CONF_PORT]}"
        self._site = d.get(CONF_SITE) or "default"
        self._is_unifi_os: bool | None = None
        self._headers: dict[str, str] = {}

    async def _login(self) -> None:
        if self._is_unifi_os is None:
            try:
                async with self.session.get(f"{self._base}/", allow_redirects=False, timeout=TIMEOUT) as r:
                    self._is_unifi_os = r.status == 200
            except (aiohttp.ClientError, TimeoutError) as err:
                raise ConnectError(describe(err)) from err
        path = "/api/auth/login" if self._is_unifi_os else "/api/login"
        payload = {"username": self.data[CONF_USERNAME], "password": self.data[CONF_PASSWORD], "remember": True}
        try:
            async with self.session.post(f"{self._base}{path}", json=payload, timeout=TIMEOUT) as r:
                if r.status in (400, 401, 403):
                    raise AuthError("Invalid username or password")
                if r.status == 499:
                    raise AuthError("This account requires two-factor login. Create a UniFi admin with "
                                    "'Restrict to Local Access Only' and use that instead")
                if r.status == 429:
                    raise ConnectError("Too many login attempts. Wait about 5 minutes and try again")
                if r.status >= 400:
                    raise ConnectError(f"Login failed: HTTP {r.status}")
                if token := r.headers.get("x-csrf-token"):
                    self._headers["x-csrf-token"] = token
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConnectError(describe(err)) from err

    async def _get(self, endpoint: str) -> list[dict[str, Any]]:
        prefix = "/proxy/network" if self._is_unifi_os else ""
        url = f"{self._base}{prefix}/api/s/{self._site}/{endpoint}"
        for attempt in range(2):
            try:
                body = await self._request("GET", url, headers=self._headers)
            except AuthError:
                if attempt:
                    raise
                await self._login()
                continue
            return body.get("data", []) if isinstance(body, dict) else []
        return []

    async def async_fetch(self) -> list[dict[str, Any]]:
        if self._is_unifi_os is None:
            await self._login()
        active, known, devices, networks = await asyncio.gather(
            self._get("stat/sta"), self._get("rest/user"), self._get("stat/device"), self._get("rest/networkconf")
        )
        infra = {d.get("mac"): d for d in devices if d.get("mac")}
        nets = {n.get("_id"): n.get("name") for n in networks}
        known_map = {u.get("mac"): u for u in known if u.get("mac")}
        active_map = {c.get("mac"): c for c in active if c.get("mac")}

        rows = []
        for mac in active_map.keys() | known_map.keys():
            c = {**known_map.get(mac, {}), **active_map.get(mac, {})}
            online = mac in active_map
            wired = bool(c.get("is_wired"))
            if wired:
                sw = infra.get(c.get("sw_mac"), {})
                conn = sw.get("name") or sw.get("model") or c.get("sw_mac") or "Wired"
                if c.get("sw_port"):
                    conn = f"{conn} · Port {c['sw_port']}"
            else:
                ap = infra.get(c.get("ap_mac"), {})
                conn = ap.get("name") or ap.get("model") or c.get("ap_mac") or ""
            # UniFi counters are from the AP/switch view: tx = sent to the client = download.
            down = c.get("tx_bytes-r", c.get("wired-tx_bytes-r"))
            up = c.get("rx_bytes-r", c.get("wired-rx_bytes-r"))
            rows.append(
                make_row(
                    mac,
                    name=known_map.get(mac, {}).get("name") or c.get("name"),
                    hostname=c.get("hostname"),
                    vendor=c.get("oui"),
                    online=online,
                    wired=wired,
                    connection=conn,
                    network=c.get("network") or nets.get(c.get("network_id")),
                    wifi=None if wired else c.get("essid"),
                    channel=None if wired else c.get("channel"),
                    band=None if wired else BANDS.get(c.get("radio"), c.get("radio")),
                    signal=None if wired else c.get("signal"),
                    ip=c.get("ip") or (None if online else c.get("last_ip")),
                    download=float(down) if down is not None else (0.0 if online else None),
                    upload=float(up) if up is not None else (0.0 if online else None),
                    uptime=c.get("uptime"),
                    last_seen=c.get("last_seen"),
                )
            )
        return rows
