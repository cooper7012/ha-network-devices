"""Config flow for Network Devices."""
from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback

from .const import CONF_SCAN_INTERVAL, CONF_SUBNETS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import async_load_vendor_db
from .scanner import NetworkScanner, ScanError, async_local_networks, parse_subnets


async def async_default_subnets(hass: HomeAssistant) -> str:
    try:
        return ", ".join(str(n) for n in await async_local_networks(hass)) or "192.168.1.0/24"
    except Exception:  # noqa: BLE001
        return "192.168.1.0/24"


async def async_validate(hass: HomeAssistant, subnets: str) -> tuple[str | None, dict[str, str]]:
    """Return (error key, placeholders)."""
    try:
        nets = parse_subnets(subnets)
    except ValueError:
        return "invalid_subnet", {}
    local = await async_local_networks(hass)
    remote = [str(n) for n in nets if local and not any(n.overlaps(l) for l in local)]
    if remote:
        return "not_local", {"remote": ", ".join(remote), "local": ", ".join(map(str, local))}
    await async_load_vendor_db()
    try:
        found = await NetworkScanner(hass, "validate", subnets).async_scan(resolve_names=False)
    except ScanError as err:
        return "scan_failed", {"detail": str(err)}
    if not found:
        return "no_devices", {}
    return None, {}


class NetworkDevicesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"remote": "", "local": "", "detail": ""}
        if user_input is not None:
            error, extra = await async_validate(self.hass, user_input[CONF_SUBNETS])
            if error is None:
                return self.async_create_entry(title="Network Devices", data=user_input)
            errors["base"] = error
            placeholders.update(extra)
        default = (user_input or {}).get(CONF_SUBNETS) or await async_default_subnets(self.hass)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_SUBNETS, default=default): str}),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NetworkDevicesOptionsFlow()


class NetworkDevicesOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"remote": "", "local": "", "detail": ""}
        entry = self.config_entry
        if user_input is not None:
            error, extra = await async_validate(self.hass, user_input[CONF_SUBNETS])
            if error is None:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_SUBNETS: user_input[CONF_SUBNETS]}
                )
                return self.async_create_entry(data={CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL]})
            errors["base"] = error
            placeholders.update(extra)
        cur = user_input or {}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SUBNETS, default=cur.get(CONF_SUBNETS, entry.data[CONF_SUBNETS])): str,
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=cur.get(CONF_SCAN_INTERVAL, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
                    ): vol.All(int, vol.Range(min=30, max=3600)),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )
