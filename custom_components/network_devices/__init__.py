"""Network Devices: list every device on your network in a sidebar panel."""
from __future__ import annotations

import logging
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
    CONF_SUBNETS,
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
from .coordinator import NetworkDevicesCoordinator
from .models import async_load_vendor_db
from .scanner import NetworkScanner

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NetworkDevicesConfigEntry = ConfigEntry[NetworkDevicesCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket_api.async_register_command(hass, ws_list_devices)
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
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await _async_register_panel(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


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
    """Return every device found, plus any scan errors."""
    devices: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator: NetworkDevicesCoordinator = entry.runtime_data
        if msg["refresh"]:
            await coordinator.async_refresh()
        for row in coordinator.data or []:
            devices.setdefault(row["mac"], row)
        if not coordinator.last_update_success and coordinator.last_exception:
            errors.append(str(coordinator.last_exception))
    connection.send_result(msg["id"], {"devices": list(devices.values()), "errors": errors})
