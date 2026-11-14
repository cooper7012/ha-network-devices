"""Common device-row model shared by every backend."""
from __future__ import annotations

import time
from typing import Any

try:  # optional, ships with Home Assistant's nmap integration
    import aiooui
except ImportError:  # pragma: no cover
    aiooui = None

BANDS = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz", "ad": "60 GHz"}


def norm_mac(mac: str | None) -> str:
    mac = (mac or "").strip().lower().replace("-", ":")
    if len(mac) == 12 and ":" not in mac:
        mac = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    return mac


def is_random_mac(mac: str) -> bool:
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def band_for_channel(channel: Any) -> str:
    try:
        ch = int(channel)
    except (TypeError, ValueError):
        return ""
    if 1 <= ch <= 14:
        return "2.4 GHz"
    if 32 <= ch <= 177:
        return "5 GHz"
    return ""


def band_for_freq(mhz: Any) -> str:
    try:
        f = float(mhz)
    except (TypeError, ValueError):
        return ""
    if f < 3000:
        return "2.4 GHz"
    if f < 5925:
        return "5 GHz"
    if f < 7200:
        return "6 GHz"
    return ""


def channel_for_freq(mhz: Any) -> int | None:
    try:
        f = int(float(mhz))
    except (TypeError, ValueError):
        return None
    if f == 2484:
        return 14
    if 2412 <= f < 2484:
        return (f - 2407) // 5
    if 5000 <= f < 5925:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    return None


def make_row(
    mac: str,
    *,
    name: str | None = None,
    hostname: str | None = None,
    vendor: str | None = None,
    online: bool = True,
    wired: bool | None = None,
    connection: str | None = "",
    network: str | None = "",
    wifi: str | None = "",
    channel: Any = None,
    band: str | None = "",
    signal: Any = None,
    ip: str | None = "",
    download: float | None = None,
    upload: float | None = None,
    uptime: Any = None,
    last_seen: float | None = None,
) -> dict[str, Any]:
    """Build one row. download/upload are bytes per second (None = not available)."""
    return {
        "mac": norm_mac(mac),
        "name": name or "",
        "hostname": hostname or "",
        "vendor": vendor or "",
        "online": online,
        "connection_type": "unknown" if wired is None else ("wired" if wired else "wireless"),
        "connection": connection or "",
        "network": network or "",
        "wifi": wifi or "",
        "channel": channel if channel not in ("", 0, "0") else None,
        "band": band or "",
        "signal": signal,
        "ip": ip or "",
        "download": download,
        "upload": upload,
        "uptime": uptime,
        "last_seen": last_seen,
    }


def finalize(rows: list[dict[str, Any]], swap: bool = False) -> list[dict[str, Any]]:
    """Fill vendor/name fallbacks, apply options, sort."""
    now = time.time()
    lookup = aiooui.get_vendor if aiooui is not None and aiooui.is_loaded() else None
    for r in rows:
        if not r["vendor"]:
            if is_random_mac(r["mac"]):
                r["vendor"] = "Private (random MAC)"
            elif lookup:
                r["vendor"] = lookup(r["mac"]) or ""
        if not r["name"]:
            real_vendor = r["vendor"] if not r["vendor"].startswith("Private") else ""
            r["name"] = r["hostname"] or real_vendor or r["mac"]
        if swap:
            r["download"], r["upload"] = r["upload"], r["download"]
        if not r["online"]:
            r["download"] = r["upload"] = None
            r["channel"] = r["signal"] = r["uptime"] = None
            r["band"] = ""
        r["seconds_since_seen"] = (now - r["last_seen"]) if r["last_seen"] else None
    rows.sort(key=lambda r: (not r["online"], r["name"].lower()))
    return rows


class RateTracker:
    """Turns cumulative byte counters into bytes/second."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, float]] = {}

    def rate(self, key: str, total: Any) -> float | None:
        try:
            total = float(total)
        except (TypeError, ValueError):
            return None
        now = time.monotonic()
        prev = self._prev.get(key)
        self._prev[key] = (now, total)
        if prev is None or total < prev[1] or now <= prev[0]:
            return 0.0
        return (total - prev[1]) / (now - prev[0])
