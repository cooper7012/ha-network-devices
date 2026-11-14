"""Backend base class and errors."""
from __future__ import annotations

import errno
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant

TIMEOUT = aiohttp.ClientTimeout(total=20)


class BackendError(Exception):
    """Generic backend failure."""


class AuthError(BackendError):
    """Credentials rejected (status 401) or not allowed (403)."""

    def __init__(self, msg: str = "", status: int = 401) -> None:
        super().__init__(msg)
        self.status = status


class ConnectError(BackendError):
    """Device unreachable or unexpected response."""


def describe(err: Exception) -> str:
    """Human-readable reason for a network failure."""
    if isinstance(err, TimeoutError) or isinstance(err, aiohttp.ServerTimeoutError):
        return "Timed out. The device didn't answer; check the IP, port and any firewall/VLAN rules"
    if isinstance(err, aiohttp.ClientConnectorCertificateError):
        return "SSL certificate rejected; turn off 'Verify SSL certificate'"
    if isinstance(err, aiohttp.ClientConnectorError):
        code = getattr(err.os_error, "errno", None)
        if code == errno.ECONNREFUSED:
            return f"Connection refused on port {err.port}; check the port number"
        if code in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT):
            return (f"Cannot reach {err.host}:{err.port}. Check the IP, and that your firewall/VLAN "
                    "rules let Home Assistant reach it")
        return f"Cannot reach {err.host}:{err.port} ({err.os_error or err})"
    return f"{type(err).__name__}: {err}"


class Backend:
    """A source of network devices (router, controller, scanner)."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession, data: dict[str, Any]) -> None:
        self.hass = hass
        self.session = session
        self.data = data

    async def async_fetch(self) -> list[dict[str, Any]]:
        """Return rows built with models.make_row (finalize is applied by the caller)."""
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    async def _request(self, method: str, url: str, *, expect_json: bool = True, **kwargs: Any) -> Any:
        try:
            async with self.session.request(method, url, timeout=TIMEOUT, **kwargs) as resp:
                if resp.status in (401, 403):
                    raise AuthError(f"HTTP {resp.status} from {url.split('?')[0]}", resp.status)
                if resp.status >= 400:
                    raise ConnectError(f"HTTP {resp.status} from {url.split('?')[0]}")
                if expect_json:
                    return await resp.json(content_type=None)
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ConnectError(describe(err)) from err
        except ValueError as err:
            raise ConnectError(f"Unexpected response from {url.split('?')[0]}") from err
