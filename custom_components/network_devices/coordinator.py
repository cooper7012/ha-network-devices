"""Polling coordinator."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UniFiAuthError, UniFiClient, UniFiError
from .const import CONF_SCAN_INTERVAL, CONF_SWAP_DIRECTION, DEFAULT_SCAN_INTERVAL, DOMAIN
from .parser import build_rows

_LOGGER = logging.getLogger(__name__)


class NetworkDevicesCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: UniFiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            raw = await self.client.fetch()
        except UniFiAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UniFiError as err:
            raise UpdateFailed(str(err)) from err
        return build_rows(raw, self.config_entry.options.get(CONF_SWAP_DIRECTION, False))
