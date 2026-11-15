"""Polling coordinator: decides when to scan, and whether a scan is light or full."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SCAN_MODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FULL_SWEEP_SECONDS,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MODE_LIGHT,
    MODE_PANEL,
    PANEL_ACTIVE_SECONDS,
    SCAN_MODES,
)
from .scanner import NetworkScanner, ScanError

_LOGGER = logging.getLogger(__name__)


def settings_from_options(options: dict[str, Any]) -> tuple[str, int]:
    mode = options.get(CONF_SCAN_MODE, MODE_LIGHT)
    interval = int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    return (mode if mode in SCAN_MODES else MODE_LIGHT,
            min(max(interval, MIN_SCAN_INTERVAL), MAX_SCAN_INTERVAL))


class NetworkDevicesCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, scanner: NetworkScanner) -> None:
        self.mode, interval = settings_from_options(entry.options)
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(seconds=interval)
        )
        self.scanner = scanner
        self.panel_seen = 0.0
        self.paused = False
        self.last_scan = 0.0
        self.last_scan_full = False

    @property
    def interval(self) -> int:
        return int(self.update_interval.total_seconds()) if self.update_interval else DEFAULT_SCAN_INTERVAL

    @callback
    def apply_settings(self, mode: str, interval: int) -> None:
        """Change mode / interval on the fly (no reload)."""
        changed = interval != self.interval
        self.mode = mode
        self.update_interval = timedelta(seconds=interval)
        if changed or (mode == MODE_LIGHT and self.paused):
            self.hass.async_create_task(self.async_request_refresh())  # restart the timer

    def panel_active(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.panel_seen < PANEL_ACTIVE_SECONDS

    @callback
    def panel_heartbeat(self) -> bool:
        """The panel is open. Returns True if a scan was started because results were stale."""
        now = time.time()
        self.panel_seen = now
        if self.mode == MODE_PANEL and now - self.last_scan >= self.interval:
            self.hass.async_create_task(self.async_request_refresh())
            return True
        return False

    def status(self) -> dict[str, Any]:
        now = time.time()
        next_full = max(0.0, self.scanner.last_full_sweep + FULL_SWEEP_SECONDS - now)
        return {
            "mode": self.mode,
            "interval": self.interval,
            "paused": self.paused,
            "last_scan": self.last_scan or None,
            "last_scan_full": self.last_scan_full,
            "devices_probed": self.scanner.last_probe_count,
            "next_full_sweep_in": round(next_full) if self.scanner.last_full_sweep else 0,
        }

    async def _async_update_data(self) -> list[dict[str, Any]]:
        now = time.time()
        if self.mode == MODE_PANEL and not self.panel_active(now):
            self.paused = True  # nobody is looking: send nothing
            return self.data if self.data is not None else await self.scanner.async_known_rows()
        self.paused = False
        full = now - self.scanner.last_full_sweep >= FULL_SWEEP_SECONDS
        try:
            rows = await self.scanner.async_scan(full=full)
        except ScanError as err:
            raise UpdateFailed(str(err)) from err
        self.last_scan = time.time()
        self.last_scan_full = full
        return rows
