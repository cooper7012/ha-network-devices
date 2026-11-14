"""Polling coordinator."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .backends import AuthError, BackendError
from .backends.base import Backend
from .const import (
    CONF_ROUTER_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_SWAP_DIRECTION,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_NETWORK_SCAN,
    DOMAIN,
)
from .models import finalize

_LOGGER = logging.getLogger(__name__)


def default_interval(router_type: str) -> int:
    return DEFAULT_SCAN_INTERVAL_NETWORK_SCAN if router_type == "scan" else DEFAULT_SCAN_INTERVAL


class NetworkDevicesCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, backend: Backend) -> None:
        rtype = entry.data.get(CONF_ROUTER_TYPE, "unifi")
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, default_interval(rtype))),
        )
        self.backend = backend

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            rows = await self.backend.async_fetch()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BackendError as err:
            raise UpdateFailed(str(err)) from err
        return finalize(rows, self.config_entry.options.get(CONF_SWAP_DIRECTION, False))
