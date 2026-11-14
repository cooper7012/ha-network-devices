"""Constants for the Network Devices integration."""

DOMAIN = "network_devices"
VERSION = "3.1.0"

CONF_SUBNETS = "subnets"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60  # seconds
MAX_ADDRESSES = 4096
HOSTNAME_REFRESH = 1800  # re-check known hostnames every 30 minutes
HOSTNAME_RETRY = 600  # retry devices without a name every 10 minutes

PANEL_URL_PATH = "network-devices"
PANEL_COMPONENT = "network-devices-panel"
PANEL_TITLE = "Network Devices"
PANEL_ICON = "mdi:lan"
STATIC_URL = "/network_devices_static"

DATA_PANEL_REGISTERED = f"{DOMAIN}_panel_registered"
DATA_STATIC_REGISTERED = f"{DOMAIN}_static_registered"
