"""Minimal async client for the UniFi Network controller API.

Works with UniFi OS consoles (UDM / UDR / UCG / Cloud Key Gen2+, port 443)
and legacy self-hosted controllers (usually port 8443).
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)


class UniFiError(Exception):
    """Base error."""


class UniFiAuthError(UniFiError):
    """Bad credentials."""


class UniFiConnectionError(UniFiError):
    """Controller unreachable or returned garbage."""


class UniFiClient:
    """Talks to a UniFi Network controller."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        site: str,
    ) -> None:
        self._session = session
        self._base = f"https://{host}:{port}"
        self._username = username
        self._password = password
        self._site = site
        self._is_unifi_os: bool | None = None
        self._headers: dict[str, str] = {}

    async def _detect(self) -> None:
        """UniFi OS answers 200 on '/', legacy controllers redirect."""
        try:
            async with self._session.get(
                f"{self._base}/", allow_redirects=False, timeout=TIMEOUT
            ) as resp:
                self._is_unifi_os = resp.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UniFiConnectionError(str(err)) from err

    async def login(self) -> None:
        if self._is_unifi_os is None:
            await self._detect()
        path = "/api/auth/login" if self._is_unifi_os else "/api/login"
        payload = {
            "username": self._username,
            "password": self._password,
            "remember": True,
        }
        try:
            async with self._session.post(
                f"{self._base}{path}", json=payload, timeout=TIMEOUT
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise UniFiAuthError("Invalid username or password")
                if resp.status >= 400:
                    raise UniFiConnectionError(f"Login failed: HTTP {resp.status}")
                if token := resp.headers.get("x-csrf-token"):
                    self._headers["x-csrf-token"] = token
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UniFiConnectionError(str(err)) from err

    async def _get(self, endpoint: str) -> list[dict[str, Any]]:
        prefix = "/proxy/network" if self._is_unifi_os else ""
        url = f"{self._base}{prefix}/api/s/{self._site}/{endpoint}"
        for attempt in range(2):
            try:
                async with self._session.get(
                    url, headers=self._headers, timeout=TIMEOUT
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        await self.login()
                        continue
                    if resp.status == 401:
                        raise UniFiAuthError("Session rejected by controller")
                    if resp.status == 404:
                        raise UniFiConnectionError(
                            f"Not found: {endpoint} (check the site name)"
                        )
                    if resp.status >= 400:
                        raise UniFiConnectionError(f"HTTP {resp.status} for {endpoint}")
                    body = await resp.json(content_type=None)
                    return body.get("data", []) if isinstance(body, dict) else []
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise UniFiConnectionError(str(err)) from err
        return []

    async def fetch(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch everything needed to build the device table."""
        if self._is_unifi_os is None:
            await self.login()
        active, known, devices, networks = await asyncio.gather(
            self._get("stat/sta"),
            self._get("rest/user"),
            self._get("stat/device"),
            self._get("rest/networkconf"),
        )
        return {
            "active": active,
            "known": known,
            "devices": devices,
            "networks": networks,
        }
