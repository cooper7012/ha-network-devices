"""Network Devices: list every client on a UniFi network in a sidebar panel."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import UniFiClient
from .const import (
    CONF_SITE,
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

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type NetworkDevicesConfigEntry = ConfigEntry[NetworkDevicesCoordinator]


def make_client(hass: HomeAssistant, data: dict[str, Any]) -> UniFiClient:
    session = async_create_clientsession(
        hass,
        verify_ssl=data.get(CONF_VERIFY_SSL, False),
        cookie_jar=aiohttp.CookieJar(unsafe=True),  # needed for cookies on bare IPs
    )
    return UniFiClient(
        session,
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        data[CONF_SITE],
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket_api.async_register_command(hass, ws_list_devices)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NetworkDevicesConfigEntry) -> bool:
    coordinator = NetworkDevicesCoordinator(hass, entry, make_client(hass, dict(entry.data)))
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
    """Return all devices from every configured controller/site."""
    devices: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator: NetworkDevicesCoordinator = entry.runtime_data
        if msg["refresh"]:
            await coordinator.async_refresh()
        for row in coordinator.data or []:
            devices.append({**row, "source": entry.title})
        sources.append(
            {
                "title": entry.title,
                "ok": coordinator.last_update_success,
                "error": str(coordinator.last_exception)
                if not coordinator.last_update_success and coordinator.last_exception
                else None,
            }
        )
    connection.send_result(msg["id"], {"devices": devices, "sources": sources})
