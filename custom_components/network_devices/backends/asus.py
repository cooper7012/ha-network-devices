"""ASUS routers (stock ASUSWRT and Asuswrt-Merlin, including AiMesh)."""
from __future__ import annotations

import base64
import json
import re
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_SSL, CONF_USERNAME

from ..models import band_for_channel, make_row
from .base import AuthError, Backend, ConnectError

USER_AGENT = "asusrouter-Android-DUTUtil-1.0.0.245"
WL_BAND = {"1": "2.4 GHz", "2": "5 GHz", "3": "5 GHz", "4": "6 GHz"}
HOOKS = "get_clientlist();wl_control_channel();" + "".join(f"nvram_get(wl{i}_ssid);" for i in range(4))


def loads_lenient(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", text))


class AsusBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        scheme = "https" if self.data.get(CONF_SSL) else "http"
        self._base = f"{scheme}://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}"
        self._headers = {"User-Agent": USER_AGENT}

    async def _login(self) -> None:
        cred = base64.b64encode(f"{self.data[CONF_USERNAME]}:{self.data[CONF_PASSWORD]}".encode()).decode()
        text = await self._request(
            "POST", f"{self._base}/login.cgi", expect_json=False, data=f"login_authorization={cred}",
            headers={**self._headers, "Content-Type": "application/x-www-form-urlencoded", "Referer": f"{self._base}/"},
        )
        try:
            token = loads_lenient(text).get("asus_token")
        except ValueError:
            token = None
        if not token:
            raise AuthError("Login rejected (wrong credentials, or locked out after too many attempts)")
        self._headers = {"User-Agent": USER_AGENT, "Cookie": f"asus_token={token}"}

    async def _hooks(self) -> dict[str, Any]:
        for attempt in range(2):
            if "Cookie" not in self._headers:
                await self._login()
            text = await self._request("GET", f"{self._base}/appGet.cgi?hook={HOOKS}", expect_json=False,
                                       headers=self._headers)
            try:
                data = loads_lenient(text)
            except ValueError:
                data = {}
            if "get_clientlist" in data:
                return data
            self._headers.pop("Cookie", None)  # session expired -> login again
        raise ConnectError("Router did not return a client list")

    async def async_fetch(self) -> list[dict[str, Any]]:
        data = await self._hooks()
        clients = data.get("get_clientlist") or {}
        channels = data.get("wl_control_channel") or []
        rows = []
        for mac in clients.get("maclist", []):
            c = clients.get(mac) or {}
            wl = str(c.get("isWL", "0"))
            wireless = wl != "0"
            idx = int(wl) - 1 if wl.isdigit() else -1
            channel = channels[idx] if wireless and 0 <= idx < len(channels) else None
            node = c.get("amesh_papMac")
            conn = f"AiMesh node {node}" if c.get("amesh_isRe") in ("1", 1) and node else "ASUS router"
            rows.append(make_row(
                mac,
                name=c.get("nickName"),
                hostname=c.get("name"),
                vendor=c.get("vendor"),
                online=str(c.get("isOnline", "1")) == "1",
                wired=not wireless,
                connection=conn,
                network="Guest" if str(c.get("isGN", "")) not in ("", "0") else "",
                wifi=data.get(f"wl{idx}_ssid") if wireless else None,
                channel=channel,
                band=(WL_BAND.get(wl) or band_for_channel(channel)) if wireless else None,
                signal=int(c["rssi"]) if wireless and str(c.get("rssi", "")).lstrip("-").isdigit() else None,
                ip=c.get("ip"),
            ))
        return rows
