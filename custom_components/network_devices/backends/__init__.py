"""Registry of supported routers/controllers."""
from __future__ import annotations

from dataclasses import dataclass

from .asus import AsusBackend
from .base import AuthError, Backend, BackendError, ConnectError
from .fritzbox import FritzBoxBackend
from .mikrotik import MikroTikBackend
from .omada import OmadaBackend
from .openwrt import OpenWrtBackend
from .opnsense import OPNsenseBackend
from .scan import ScanBackend
from .unifi import UniFiBackend


@dataclass(frozen=True)
class RouterType:
    label: str
    backend: type[Backend]
    port: int = 0
    username: str | None = ""  # default username; None = no username field
    site: str | None = None  # default site; None = no site field
    ssl_toggle: bool = False  # offer an "use HTTPS" switch
    verify_ssl: bool = False  # offer a "verify certificate" switch
    password_required: bool = True


ROUTER_TYPES: dict[str, RouterType] = {
    "unifi": RouterType("Ubiquiti UniFi", UniFiBackend, 443, site="default", verify_ssl=True),
    "omada": RouterType("TP-Link Omada", OmadaBackend, 443, site="Default", verify_ssl=True),
    "asus": RouterType("ASUS", AsusBackend, 80, "admin", ssl_toggle=True, verify_ssl=True),
    "fritzbox": RouterType("AVM FRITZ!Box", FritzBoxBackend, 49000),
    "openwrt": RouterType("OpenWrt", OpenWrtBackend, 80, "root", ssl_toggle=True, verify_ssl=True),
    "mikrotik": RouterType("MikroTik", MikroTikBackend, 80, "admin", ssl_toggle=True, verify_ssl=True),
    "opnsense": RouterType("OPNsense", OPNsenseBackend, 443, verify_ssl=True),
    "scan": RouterType("Network scan", ScanBackend, username=None, password_required=False),
}

__all__ = ["ROUTER_TYPES", "AuthError", "BackendError", "ConnectError", "RouterType"]
