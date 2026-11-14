"""Constants for the Network Devices integration."""

DOMAIN = "network_devices"
VERSION = "1.0.0"

CONF_SITE = "site"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SWAP_DIRECTION = "swap_direction"

DEFAULT_PORT = 443
DEFAULT_SITE = "default"
DEFAULT_SCAN_INTERVAL = 15  # seconds

PANEL_URL_PATH = "network-devices"
PANEL_COMPONENT = "network-devices-panel"
PANEL_TITLE = "Network Devices"
PANEL_ICON = "mdi:lan"
STATIC_URL = "/network_devices_static"

DATA_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
DATA_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
