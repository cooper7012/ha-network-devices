"""Config flow for Network Devices."""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .backends import ROUTER_TYPES, AuthError, BackendError
from .backends.scan import parse_subnets
from .const import (
    CONF_ROUTER_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_SITE,
    CONF_SUBNETS,
    CONF_SWAP_DIRECTION,
    DOMAIN,
)
from .coordinator import default_interval

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def router_schema(rtype: str, d: Mapping[str, Any], default_subnets: str = "") -> vol.Schema:
    t = ROUTER_TYPES[rtype]
    if rtype == "scan":
        return vol.Schema({vol.Required(CONF_SUBNETS, default=d.get(CONF_SUBNETS, default_subnets)): str})
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=d.get(CONF_HOST, "")): str,
        vol.Required(CONF_PORT, default=d.get(CONF_PORT, t.port)): int,
    }
    if t.username is not None:
        fields[vol.Optional(CONF_USERNAME, default=d.get(CONF_USERNAME, t.username))] = str
    fields[vol.Required(CONF_PASSWORD) if t.password_required else vol.Optional(CONF_PASSWORD, default="")] = PASSWORD
    if t.site is not None:
        fields[vol.Required(CONF_SITE, default=d.get(CONF_SITE, t.site))] = str
    if t.ssl_toggle:
        fields[vol.Required(CONF_SSL, default=d.get(CONF_SSL, False))] = bool
    if t.verify_ssl:
        fields[vol.Required(CONF_VERIFY_SSL, default=d.get(CONF_VERIFY_SSL, False))] = bool
    return vol.Schema(fields)


class NetworkDevicesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> str | None:
        from . import async_load_vendor_db, make_backend  # noqa: PLC0415 - avoid import cycle

        if data[CONF_ROUTER_TYPE] == "scan":
            try:
                parse_subnets(data[CONF_SUBNETS])
            except ValueError:
                return "invalid_subnet"
        await async_load_vendor_db()
        try:
            await make_backend(self.hass, data).async_fetch()
        except AuthError:
            return "invalid_auth"
        except BackendError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=list(ROUTER_TYPES))

    async def _default_subnets(self) -> str:
        from homeassistant.components import network  # noqa: PLC0415

        nets: list[str] = []
        try:
            for adapter in await network.async_get_adapters(self.hass):
                if not adapter["enabled"]:
                    continue
                for ip in adapter["ipv4"]:
                    iface = ipaddress.IPv4Interface(f"{ip['address']}/{ip['network_prefix']}")
                    if iface.is_loopback or iface.is_link_local:
                        continue
                    net = iface.network if iface.network.prefixlen >= 20 else ipaddress.IPv4Network(f"{iface.ip}/24", strict=False)
                    if str(net) not in nets:
                        nets.append(str(net))
        except Exception:  # noqa: BLE001
            pass
        return ", ".join(nets) or "192.168.1.0/24"

    async def _router_step(self, rtype: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        label = ROUTER_TYPES[rtype].label
        if user_input is not None:
            data = {CONF_ROUTER_TYPE: rtype, **user_input}
            key = data.get(CONF_SUBNETS) if rtype == "scan" else f"{data[CONF_HOST]}_{data.get(CONF_SITE, '')}"
            await self.async_set_unique_id(f"{rtype}_{key}")
            self._abort_if_unique_id_configured()
            if not (error := await self._validate(data)):
                where = data[CONF_SUBNETS] if rtype == "scan" else data[CONF_HOST]
                return self.async_create_entry(title=f"{label} ({where})", data=data)
            errors["base"] = error
        defaults = user_input or {}
        subnets = await self._default_subnets() if rtype == "scan" and not defaults else ""
        return self.async_show_form(
            step_id=rtype, data_schema=router_schema(rtype, defaults, subnets), errors=errors
        )

    async def async_step_unifi(self, user_input=None):  # noqa: D102
        return await self._router_step("unifi", user_input)

    async def async_step_omada(self, user_input=None):  # noqa: D102
        return await self._router_step("omada", user_input)

    async def async_step_asus(self, user_input=None):  # noqa: D102
        return await self._router_step("asus", user_input)

    async def async_step_fritzbox(self, user_input=None):  # noqa: D102
        return await self._router_step("fritzbox", user_input)

    async def async_step_openwrt(self, user_input=None):  # noqa: D102
        return await self._router_step("openwrt", user_input)

    async def async_step_mikrotik(self, user_input=None):  # noqa: D102
        return await self._router_step("mikrotik", user_input)

    async def async_step_opnsense(self, user_input=None):  # noqa: D102
        return await self._router_step("opnsense", user_input)

    async def async_step_scan(self, user_input=None):  # noqa: D102
        return await self._router_step("scan", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        rtype = ROUTER_TYPES[entry.data.get(CONF_ROUTER_TYPE, "unifi")]
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_ROUTER_TYPE: entry.data.get(CONF_ROUTER_TYPE, "unifi"), **entry.data, **user_input}
            if not (error := await self._validate(data)):
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        fields: dict[Any, Any] = {}
        if rtype.username is not None:
            fields[vol.Optional(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, ""))] = str
        fields[vol.Required(CONF_PASSWORD)] = PASSWORD
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema(fields), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return NetworkDevicesOptionsFlow()


class NetworkDevicesOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        default = default_interval(self.config_entry.data.get(CONF_ROUTER_TYPE, "unifi"))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, default)): vol.All(
                        int, vol.Range(min=5, max=3600)
                    ),
                    vol.Required(CONF_SWAP_DIRECTION, default=opts.get(CONF_SWAP_DIRECTION, False)): bool,
                }
            ),
        )
