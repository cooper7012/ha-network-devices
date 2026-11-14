"""Constants for the Network Devices integration."""

DOMAIN = "network_devices"
VERSION = "2.0.0"

CONF_ROUTER_TYPE = "router_type"
CONF_SITE = "site"
CONF_SUBNETS = "subnets"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SWAP_DIRECTION = "swap_direction"

DEFAULT_SCAN_INTERVAL = 15  # seconds
DEFAULT_SCAN_INTERVAL_NETWORK_SCAN = 60

PANEL_URL_PATH = "network-devices"
PANEL_COMPONENT = "network-devices-panel"
PANEL_TITLE = "Network Devices"
PANEL_ICON = "mdi:lan"
STATIC_URL = "/network_devices_static"

DATA_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
DATA_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
