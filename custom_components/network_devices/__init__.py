"""Network Devices: list every device on your network in a sidebar panel."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .config_flow import async_default_subnets
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SCAN_MODE,
    CONF_SUBNETS,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SCAN_MODES,
    DATA_PANEL_REGISTERED,
    DATA_STATIC_REGISTERED,
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    STATIC_URL,
    VERSION,
)
from .coordinator import NetworkDevicesCoordinator, settings_from_options
from .customizations import FILTER_SCHEMA, NAME, TAG, TAGS, async_get_customizations
from .models import async_load_vendor_db
from .scanner import NetworkScanner

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NetworkDevicesConfigEntry = ConfigEntry[NetworkDevicesCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await async_get_customizations(hass)
    for command in (ws_list_devices, ws_set_scan_settings, ws_update_device, ws_rename_tag, ws_delete_tag,
                    ws_save_filter, ws_delete_filter, ws_set_last_filter):
        websocket_api.async_register_command(hass, command)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1/v2.x entries talked to a router; v3 scans the local network instead."""
    if entry.version == 1:
        subnets = entry.data.get(CONF_SUBNETS) or await async_default_subnets(hass)
        hass.config_entries.async_update_entry(
            entry, data={CONF_SUBNETS: subnets}, options={}, title="Network Devices", unique_id=None, version=2
        )
        _LOGGER.info("Network Devices now scans %s directly; router settings were removed", subnets)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NetworkDevicesConfigEntry) -> bool:
    await async_load_vendor_db()
    coordinator = NetworkDevicesCoordinator(hass, entry, NetworkScanner(hass, entry.entry_id, entry.data[CONF_SUBNETS]))
    coordinator.subnets = entry.data[CONF_SUBNETS]
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await _async_register_panel(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: NetworkDevicesConfigEntry) -> None:
    """New subnets need a reload; scan mode and interval apply on the fly."""
    coordinator = entry.runtime_data
    if entry.data.get(CONF_SUBNETS) != coordinator.subnets:
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        coordinator.apply_settings(*settings_from_options(entry.options))


async def async_unload_entry(hass: HomeAssistant, entry: NetworkDevicesConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    others = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id and e.state is ConfigEntryState.LOADED
    ]
    if unloaded and not others and hass.data.pop(DATA_PANEL_REGISTERED, False):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the saved device list when the integration is deleted."""
    await NetworkScanner(hass, entry.entry_id, entry.data.get(CONF_SUBNETS) or "127.0.0.0/30").async_remove_store()


async def _async_register_panel(hass: HomeAssistant) -> None:
    if not hass.data.get(DATA_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)]
        )
        hass.data[DATA_STATIC_REGISTERED] = True
    if hass.data.get(DATA_PANEL_REGISTERED):
        return
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{STATIC_URL}/network-devices-panel.js?v={VERSION}",
        require_admin=False,
        config={},
    )
    hass.data[DATA_PANEL_REGISTERED] = True


@websocket_api.websocket_command(
    {vol.Required("type"): "network_devices/list", vol.Optional("refresh", default=False): bool}
)
@websocket_api.async_response
async def ws_list_devices(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Every device found (with custom names/tags), saved filters, and this user's last filter."""
    custom = await async_get_customizations(hass)
    devices: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    scan: dict[str, Any] | None = None
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator: NetworkDevicesCoordinator = entry.runtime_data
        if msg["refresh"]:
            coordinator.panel_seen = time.time()
            await coordinator.async_refresh()
            starting = False
        else:
            starting = coordinator.panel_heartbeat()  # the panel is open
        scan = scan or {**coordinator.status(), "starting": starting}
        for row in coordinator.data or []:
            extra = custom.device(row["mac"])
            devices.setdefault(row["mac"], {**row, "name": extra.get("name", ""), "tags": extra.get("tags", [])})
        if not coordinator.last_update_success and coordinator.last_exception:
            errors.append(str(coordinator.last_exception))
    connection.send_result(
        msg["id"],
        {
            "devices": list(devices.values()),
            "errors": errors,
            "tags": custom.all_tags(),
            "filters": [{"filter_id": fid, **f} for fid, f in custom.filters.items()],
            "last_filter": custom.last.get(connection.user.id),
            "scan": scan,
            "is_admin": connection.user.is_admin,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "network_devices/set_scan_settings",
        vol.Optional("mode"): vol.In(SCAN_MODES),
        vol.Optional("interval"): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_scan_settings(hass, connection, msg) -> None:
    """Change scan mode / interval from the panel. Saved in the entry's options."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        options = {**entry.options}
        if "mode" in msg:
            options[CONF_SCAN_MODE] = msg["mode"]
        if "interval" in msg:
            options[CONF_SCAN_INTERVAL] = msg["interval"]
        hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "network_devices/update_device",
        vol.Required("mac"): vol.All(str, vol.Match(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")),
        vol.Optional("name"): NAME,
        vol.Optional("tags"): TAGS,
    }
)
@websocket_api.async_response
async def ws_update_device(hass, connection, msg) -> None:
    custom = await async_get_customizations(hass)
    connection.send_result(msg["id"], custom.update_device(msg["mac"], msg.get("name"), msg.get("tags")))


@websocket_api.websocket_command(
    {vol.Required("type"): "network_devices/rename_tag", vol.Required("old"): TAG, vol.Required("new"): TAG}
)
@websocket_api.async_response
async def ws_rename_tag(hass, connection, msg) -> None:
    (await async_get_customizations(hass)).rename_tag(msg["old"], msg["new"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command({vol.Required("type"): "network_devices/delete_tag", vol.Required("tag"): TAG})
@websocket_api.async_response
async def ws_delete_tag(hass, connection, msg) -> None:
    (await async_get_customizations(hass)).delete_tag(msg["tag"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "network_devices/save_filter",
        vol.Optional("filter_id"): str,
        vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
        vol.Required("filter"): FILTER_SCHEMA,
    }
)
@websocket_api.async_response
async def ws_save_filter(hass, connection, msg) -> None:
    custom = await async_get_customizations(hass)
    connection.send_result(
        msg["id"], {"filter_id": custom.save_filter(msg["name"], msg["filter"], msg.get("filter_id"))}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "network_devices/delete_filter", vol.Required("filter_id"): str}
)
@websocket_api.async_response
async def ws_delete_filter(hass, connection, msg) -> None:
    (await async_get_customizations(hass)).delete_filter(msg["filter_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "network_devices/set_last_filter",
        vol.Required("filter"): FILTER_SCHEMA,
        vol.Optional("saved_id"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_set_last_filter(hass, connection, msg) -> None:
    """Remember what this user had open, so it comes back next time on any browser."""
    (await async_get_customizations(hass)).set_last(connection.user.id, msg["filter"], msg.get("saved_id"))
    connection.send_result(msg["id"])
