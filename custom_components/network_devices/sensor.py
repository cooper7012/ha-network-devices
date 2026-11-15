"""Summary sensor: number of devices currently online."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetworkDevicesCoordinator


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([OnlineDevicesSensor(entry.runtime_data)])


class OnlineDevicesSensor(CoordinatorEntity[NetworkDevicesCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "online_devices"
    _attr_icon = "mdi:devices"
    _attr_native_unit_of_measurement = "devices"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NetworkDevicesCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_online_devices"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Network Devices",
            "manufacturer": "Network Devices",
            "model": "Network scan",
        }

    @property
    def available(self) -> bool:
        # In "only while panel is open" mode the count isn't kept up to date.
        return super().available and not self.coordinator.paused

    @property
    def native_value(self) -> int:
        return sum(d["online"] for d in self.coordinator.data or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or []
        return {
            "known_devices": len(data),
            "subnets": self.coordinator.config_entry.data.get("subnets"),
            "scan_mode": self.coordinator.mode,
            "scan_interval_minutes": self.coordinator.interval // 60,
        }
