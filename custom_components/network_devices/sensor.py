"""Summary sensor: number of connected clients."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .backends import ROUTER_TYPES
from .const import CONF_ROUTER_TYPE
from .coordinator import NetworkDevicesCoordinator


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ConnectedClientsSensor(entry.runtime_data)])


class ConnectedClientsSensor(CoordinatorEntity[NetworkDevicesCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "connected_clients"
    _attr_icon = "mdi:devices"
    _attr_native_unit_of_measurement = "clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NetworkDevicesCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_connected_clients"
        self._attr_device_info = {
            "identifiers": {("network_devices", entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Network Devices",
            "model": ROUTER_TYPES[entry.data.get(CONF_ROUTER_TYPE, "unifi")].label,
        }

    @property
    def _online(self) -> list[dict[str, Any]]:
        return [d for d in self.coordinator.data or [] if d["online"]]

    @property
    def native_value(self) -> int:
        return len(self._online)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        online = self._online
        return {
            "wired": sum(d["connection_type"] == "wired" for d in online),
            "wireless": sum(d["connection_type"] == "wireless" for d in online),
            "known_offline": len(self.coordinator.data or []) - len(online),
            "total_download_bps": round(sum(d["download"] for d in online) * 8),
            "total_upload_bps": round(sum(d["upload"] for d in online) * 8),
        }
