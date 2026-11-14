"""AVM FRITZ!Box (TR-064 on port 49000; enable 'Allow access for applications')."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from xml.etree import ElementTree as ET

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from ..models import band_for_channel, make_row
from .base import TIMEOUT, AuthError, Backend, BackendError, ConnectError

import aiohttp


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _items(xml_text: str) -> list[dict[str, str]]:
    root = ET.fromstring(xml_text)
    return [{_strip_ns(c.tag): (c.text or "") for c in item} for item in root.iter() if _strip_ns(item.tag) == "Item"]


class FritzBoxBackend(Backend):
    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._base = f"http://{self.data[CONF_HOST]}:{self.data[CONF_PORT]}"
        self._nc = 0

    def _digest(self, header: str, uri: str) -> str:
        params = dict(re.findall(r'(\w+)="?([^",]*)"?', header))
        user, pw = self.data.get(CONF_USERNAME) or "", self.data[CONF_PASSWORD]
        realm, nonce, qop = params.get("realm", ""), params.get("nonce", ""), params.get("qop", "auth")
        self._nc += 1
        nc, cnonce = f"{self._nc:08x}", os.urandom(8).hex()
        ha1 = hashlib.md5(f"{user}:{realm}:{pw}".encode()).hexdigest()
        ha2 = hashlib.md5(f"POST:{uri}".encode()).hexdigest()
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop.split(',')[0]}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                f'qop={qop.split(",")[0]}, nc={nc}, cnonce="{cnonce}", response="{resp}", algorithm=MD5')

    async def _soap(self, control: str, service: str, action: str) -> dict[str, str]:
        body = (
            '<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}"/></s:Body></s:Envelope>'
        )
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service}#{action}"'}
        url = self._base + control
        try:
            for attempt in range(2):
                async with self.session.post(url, data=body, headers=headers, timeout=TIMEOUT) as r:
                    text = await r.text()
                    if r.status == 401 and attempt == 0 and "WWW-Authenticate" in r.headers:
                        headers["Authorization"] = self._digest(r.headers["WWW-Authenticate"], control)
                        continue
                    if r.status == 401:
                        raise AuthError("FRITZ!Box rejected the username/password")
                    if r.status >= 400:
                        raise ConnectError(f"{action}: HTTP {r.status}")
                    break
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConnectError(str(err)) from err
        root = ET.fromstring(text)
        resp = next((e for e in root.iter() if _strip_ns(e.tag) == f"{action}Response"), None)
        return {_strip_ns(c.tag): (c.text or "") for c in (resp if resp is not None else [])}

    async def _xml_list(self, path: str) -> list[dict[str, str]]:
        url = path if path.startswith("http") else self._base + path
        return _items(await self._request("GET", url, expect_json=False))

    async def async_fetch(self) -> list[dict[str, Any]]:
        res = await self._soap("/upnp/control/hosts", "urn:dslforum-org:service:Hosts:1", "X_AVM-DE_GetHostListPath")
        hosts = await self._xml_list(res["NewX_AVM-DE_HostListPath"])

        wifi: dict[str, dict[str, Any]] = {}
        for i in range(1, 5):
            svc = f"urn:dslforum-org:service:WLANConfiguration:{i}"
            try:
                info = await self._soap(f"/upnp/control/wlanconfig{i}", svc, "GetInfo")
                if info.get("NewEnable") != "1":
                    continue
                path = (await self._soap(f"/upnp/control/wlanconfig{i}", svc, "X_AVM-DE_GetWLANDeviceListPath"))[
                    "NewX_AVM-DE_WLANDeviceListPath"]
                for d in await self._xml_list(path):
                    mac = d.get("AssociatedDeviceMACAddress", "").lower()
                    if mac:
                        wifi[mac] = {"ssid": info.get("NewSSID"), "channel": info.get("NewChannel"),
                                     "signal": d.get("X_AVM-DE_SignalStrength")}
            except (BackendError, KeyError, ET.ParseError):
                continue

        rows = []
        for h in hosts:
            mac = h.get("MACAddress", "").lower()
            if not mac:
                continue
            w = wifi.get(mac)
            itype = h.get("InterfaceType", "")
            wireless = w is not None or itype == "802.11"
            online = h.get("Active") == "1"
            port = h.get("X_AVM-DE_Port", "")
            conn = "FRITZ!Box"
            if itype == "Ethernet" and port not in ("", "0"):
                conn += f" · LAN {port}"
            elif itype and itype != "802.11":
                conn += f" · {itype}"
            channel = (w or {}).get("channel")
            rows.append(make_row(
                mac, hostname=h.get("HostName"), online=online, wired=None if not itype and not w else not wireless,
                connection=conn if online else "", network="Guest" if h.get("X_AVM-DE_Guest") == "1" else "",
                wifi=(w or {}).get("ssid"), channel=channel, band=band_for_channel(channel),
                signal=None, ip=h.get("IPAddress"),
            ))
        return rows
