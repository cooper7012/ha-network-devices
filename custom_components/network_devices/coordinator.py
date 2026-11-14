"""Polling coordinator."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .scanner import NetworkScanner, ScanError

_LOGGER = logging.getLogger(__name__)


class NetworkDevicesCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, scanner: NetworkScanner) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.scanner = scanner

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.scanner.async_scan()
        except ScanError as err:
            raise UpdateFailed(str(err)) from err
