"""User data that lives in Home Assistant (not the browser).

- Custom names and tags per device (by MAC address)
- Saved filters, shared by everyone
- The last filter each Home Assistant user had open, so it comes back on any browser
"""
from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

DATA_CUSTOM = f"{DOMAIN}_custom"
MAX_TAGS_PER_DEVICE = 25

NAME = vol.All(str, vol.Strip, vol.Length(max=64))
TAG = vol.All(str, vol.Strip, vol.Length(min=1, max=32))
TAGS = vol.All([TAG], vol.Length(max=MAX_TAGS_PER_DEVICE))
SORT_KEYS = vol.In(["name", "mac", "vendor", "ip", "tags", "seen"])

# One filter: what the panel's toolbar is set to.
FILTER_SCHEMA = vol.Schema(
    {
        vol.Optional("query", default=""): vol.All(str, vol.Length(max=200)),
        vol.Optional("tags", default=[]): vol.All([TAG], vol.Length(max=100)),
        vol.Optional("match", default="any"): vol.In(["any", "all"]),
        vol.Optional("untagged", default=False): bool,
        vol.Optional("show_offline", default=False): bool,
        vol.Optional("sort", default="ip"): SORT_KEYS,
        vol.Optional("dir", default=1): vol.In([1, -1]),
    },
    extra=vol.REMOVE_EXTRA,
)


def _dedupe(tags: list[str]) -> list[str]:
    """Keep the first spelling of each tag, ignoring case."""
    seen: dict[str, str] = {}
    for tag in tags:
        seen.setdefault(tag.casefold(), tag)
    return list(seen.values())


class Customizations:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.customizations")
        self.devices: dict[str, dict[str, Any]] = {}
        self.filters: dict[str, dict[str, Any]] = {}
        self.last: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.devices = data.get("devices", {})
        self.filters = data.get("filters", {})
        self.last = data.get("last", {})

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {"devices": self.devices, "filters": self.filters, "last": self.last}, 2
        )

    # devices -------------------------------------------------------------
    def device(self, mac: str) -> dict[str, Any]:
        return self.devices.get(mac.lower(), {})

    def update_device(self, mac: str, name: str | None, tags: list[str] | None) -> dict[str, Any]:
        mac = mac.lower()
        dev = dict(self.devices.get(mac, {}))
        if name is not None:
            dev["name"] = name
        if tags is not None:
            dev["tags"] = _dedupe(tags)
        dev = {k: v for k, v in dev.items() if v}  # drop empty name / empty tag list
        if dev:
            self.devices[mac] = dev
        else:
            self.devices.pop(mac, None)
        self._save()
        return dev

    def all_tags(self) -> list[str]:
        return sorted(_dedupe([t for d in self.devices.values() for t in d.get("tags", [])]), key=str.casefold)

    def rename_tag(self, old: str, new: str) -> None:
        for dev in self.devices.values():
            tags = dev.get("tags", [])
            if any(t.casefold() == old.casefold() for t in tags):
                dev["tags"] = _dedupe([new if t.casefold() == old.casefold() else t for t in tags])
        self._rewrite_filter_tags(old, new)
        self._save()

    def delete_tag(self, tag: str) -> None:
        for mac, dev in list(self.devices.items()):
            dev["tags"] = [t for t in dev.get("tags", []) if t.casefold() != tag.casefold()]
            if not dev["tags"]:
                del dev["tags"]
            if not dev:
                del self.devices[mac]
        self._rewrite_filter_tags(tag, None)
        self._save()

    def _rewrite_filter_tags(self, old: str, new: str | None) -> None:
        for f in [*self.filters.values(), *self.last.values()]:
            updated = []
            for t in f.get("tags", []):
                if t.casefold() != old.casefold():
                    updated.append(t)
                elif new:
                    updated.append(new)
            f["tags"] = _dedupe(updated)

    # filters -------------------------------------------------------------
    def save_filter(self, name: str, flt: dict[str, Any], filter_id: str | None = None) -> str:
        filter_id = filter_id if filter_id in self.filters else secrets.token_hex(4)
        self.filters[filter_id] = {**FILTER_SCHEMA(flt), "name": name}
        self._save()
        return filter_id

    def delete_filter(self, filter_id: str) -> None:
        self.filters.pop(filter_id, None)
        self._save()

    def set_last(self, user_id: str, flt: dict[str, Any], saved_id: str | None) -> None:
        self.last[user_id] = {**FILTER_SCHEMA(flt), "saved_id": saved_id}
        self._save()


async def async_get_customizations(hass: HomeAssistant) -> Customizations:
    if (custom := hass.data.get(DATA_CUSTOM)) is None:
        custom = hass.data[DATA_CUSTOM] = Customizations(hass)
        await custom.async_load()
    return custom
