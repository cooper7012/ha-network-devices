"""Turn raw UniFi API data into rows for the device table (no HA imports)."""
from __future__ import annotations

import time
from typing import Any

BANDS = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz", "ad": "60 GHz"}


def _is_random_mac(mac: str) -> bool:
    """Locally administered bit set -> randomized/private MAC."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def _rate(client: dict[str, Any], key: str) -> float:
    """Bytes/second; wired clients use the 'wired-' prefixed counters."""
    for k in (key, f"wired-{key}"):
        val = client.get(k)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


def build_rows(raw: dict[str, list[dict[str, Any]]], swap: bool = False) -> list[dict[str, Any]]:
    infra = {d.get("mac"): d for d in raw.get("devices", []) if d.get("mac")}
    nets = {n.get("_id"): n.get("name") for n in raw.get("networks", [])}
    known = {u.get("mac"): u for u in raw.get("known", []) if u.get("mac")}
    active = {c.get("mac"): c for c in raw.get("active", []) if c.get("mac")}
    now = time.time()

    rows: list[dict[str, Any]] = []
    for mac in active.keys() | known.keys():
        c = {**known.get(mac, {}), **active.get(mac, {})}
        online = mac in active
        alias = known.get(mac, {}).get("name") or c.get("name")
        wired = bool(c.get("is_wired"))

        vendor = c.get("oui") or ""
        if not vendor and _is_random_mac(mac):
            vendor = "Private (random MAC)"

        if wired:
            sw = infra.get(c.get("sw_mac"), {})
            sw_name = sw.get("name") or sw.get("model") or c.get("sw_mac") or "Wired"
            port = c.get("sw_port")
            connection = f"{sw_name} · Port {port}" if port else sw_name
        else:
            ap = infra.get(c.get("ap_mac"), {})
            connection = ap.get("name") or ap.get("model") or c.get("ap_mac") or ("Wi-Fi" if online else "")

        # UniFi counters are from the AP/switch point of view: tx = sent to the client.
        down, up = _rate(c, "tx_bytes-r"), _rate(c, "rx_bytes-r")
        if swap:
            down, up = up, down
        if not online:
            down = up = 0.0

        rows.append(
            {
                "mac": mac,
                "name": alias or c.get("hostname") or c.get("oui") or mac,
                "hostname": c.get("hostname") or "",
                "vendor": vendor,
                "online": online,
                "connection_type": "wired" if wired else "wireless",
                "connection": connection,
                "network": c.get("network") or nets.get(c.get("network_id"), "") or "",
                "wifi": "" if wired else (c.get("essid") or ""),
                "channel": None if wired or not online else c.get("channel"),
                "band": "" if wired or not online else BANDS.get(c.get("radio"), c.get("radio") or ""),
                "signal": None if wired or not online else c.get("signal"),
                "ip": c.get("ip") or (c.get("last_ip") if not online else "") or "",
                "fixed_ip": bool(c.get("use_fixedip")),
                "download": down,
                "upload": up,
                "uptime": c.get("uptime") if online else None,
                "last_seen": c.get("last_seen"),
                "seconds_since_seen": (now - c["last_seen"]) if c.get("last_seen") else None,
            }
        )
    rows.sort(key=lambda r: (not r["online"], r["name"].lower()))
    return rows
