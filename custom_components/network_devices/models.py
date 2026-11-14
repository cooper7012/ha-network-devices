"""MAC address helpers."""
from __future__ import annotations

try:  # ships with Home Assistant (used by the nmap integration)
    import aiooui
except ImportError:  # pragma: no cover
    aiooui = None


async def async_load_vendor_db() -> None:
    if aiooui is not None and not aiooui.is_loaded():
        try:
            await aiooui.async_load()
        except Exception:  # noqa: BLE001 - vendor names are a nice-to-have
            pass


def is_random_mac(mac: str) -> bool:
    """Locally administered bit set: a private/randomized MAC (phones, tablets)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


def vendor_for(mac: str) -> str:
    if is_random_mac(mac):
        return "Private (random MAC)"
    if aiooui is not None and aiooui.is_loaded():
        return aiooui.get_vendor(mac) or ""
    return ""
