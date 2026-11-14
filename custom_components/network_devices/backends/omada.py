"""TP-Link Omada (Software Controller, OC200/OC300/OC400 hardware controllers)."""
from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from ..const import CONF_SITE
from ..models import make_row
from .base import AuthError, Backend, ConnectError

RADIO_BANDS = {0: "2.4 GHz", 1: "5 GHz", 2: "5 GHz", 3: "6 GHz"}
PAGE = "currentPage=1&currentPageSize=1000"


class OmadaBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._base = f"https://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}"
        self._cid: str | None = None
        self._site_id: str | None = None
        self._headers: dict[str, str] = {}

    async def _login(self) -> None:
        info = await self._request("GET", f"{self._base}/api/info")
        self._cid = (info.get("result") or {}).get("omadacId")
        if not self._cid:
            raise ConnectError("Not an Omada controller (no omadacId)")
        body = await self._request(
            "POST",
            f"{self._base}/{self._cid}/api/v2/login",
            json={"username": self.data[CONF_USERNAME], "password": self.data[CONF_PASSWORD]},
        )
        if body.get("errorCode") != 0:
            raise AuthError(body.get("msg") or "Login failed")
        self._headers = {"Csrf-Token": body["result"]["token"]}

        sites = await self._api("sites?" + PAGE, site=False)
        wanted = (self.data.get(CONF_SITE) or "").strip().lower()
        site_list = sites.get("data", []) if isinstance(sites, dict) else []
        if not site_list:
            raise ConnectError("No sites found on the Omada controller")
        match = next(
            (s for s in site_list if wanted in (str(s.get("name", "")).lower(), str(s.get("id", "")).lower())),
            None,
        )
        if wanted and wanted != "default" and match is None:
            raise ConnectError(f"Site '{self.data[CONF_SITE]}' not found")
        self._site_id = (match or site_list[0])["id"]

    async def _api(self, path: str, site: bool = True, retry: bool = True) -> Any:
        prefix = f"{self._base}/{self._cid}/api/v2/"
        if site:
            prefix += f"sites/{self._site_id}/"
        try:
            body = await self._request("GET", prefix + path, headers=self._headers)
        except AuthError:
            body = {"errorCode": -1}
        if body.get("errorCode") != 0:
            if retry:
                await self._login()
                return await self._api(path, site, retry=False)
            raise ConnectError(body.get("msg") or f"Omada error {body.get('errorCode')}")
        return body.get("result")

    async def async_fetch(self) -> list[dict[str, Any]]:
        if self._cid is None:
            await self._login()
        active = (await self._api(f"clients?{PAGE}&filters.active=true") or {}).get("data", [])
        try:
            known = (await self._api(f"insight/clients?{PAGE}", retry=False) or {}).get("data", [])
        except ConnectError:
            known = []

        rows = []
        seen = set()
        for c in active:
            mac = c.get("mac")
            if not mac:
                continue
            seen.add(mac.lower().replace("-", ":"))
            wireless = bool(c.get("wireless"))
            if wireless:
                conn = c.get("apName") or c.get("apMac") or ""
            else:
                conn = c.get("switchName") or c.get("gatewayName") or "Wired"
                if c.get("port"):
                    conn = f"{conn} · Port {c['port']}"
            rows.append(
                make_row(
                    mac,
                    name=c.get("name") if c.get("name") != mac else None,
                    hostname=c.get("hostName"),
                    vendor=c.get("vendor") or c.get("manufacturer"),
                    online=True,
                    wired=not wireless,
                    connection=conn,
                    network=c.get("networkName") or (f"VLAN {c['vid']}" if c.get("vid") else ""),
                    wifi=c.get("ssid") if wireless else None,
                    channel=c.get("channel") if wireless else None,
                    band=RADIO_BANDS.get(c.get("radioId")) if wireless else None,
                    signal=c.get("rssi") if wireless else None,
                    ip=c.get("ip"),
                    download=float(c.get("activity") or 0),
                    upload=float(c.get("uploadActivity") or 0),
                    uptime=c.get("uptime"),
                )
            )
        for c in known:
            mac = (c.get("mac") or "").lower().replace("-", ":")
            if not mac or mac in seen:
                continue
            last = c.get("lastSeen")
            rows.append(
                make_row(
                    mac,
                    name=c.get("name") if c.get("name") != c.get("mac") else None,
                    vendor=c.get("vendor") or c.get("manufacturer"),
                    online=False,
                    wired=None if c.get("wireless") is None else not c.get("wireless"),
                    ip=c.get("ip"),
                    last_seen=last / 1000 if isinstance(last, (int, float)) and last > 1e11 else last,
                )
            )
        return rows
