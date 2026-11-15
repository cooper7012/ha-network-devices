"""Constants for the Network Devices integration."""

DOMAIN = "network_devices"
VERSION = "3.4.0"

CONF_SUBNETS = "subnets"
CONF_SCAN_INTERVAL = "scan_interval"  # seconds
CONF_SCAN_MODE = "scan_mode"
CONF_FULL_SWEEP = "full_sweep_interval"  # seconds

MODE_LIGHT = "light"  # check known devices every interval, full sweep every CONF_FULL_SWEEP
MODE_PANEL = "panel"  # only scan while someone has the panel open
SCAN_MODES = [MODE_LIGHT, MODE_PANEL]

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 60  # 1 minute
MAX_SCAN_INTERVAL = 1800  # 30 minutes
DEFAULT_FULL_SWEEP = 900  # search the whole subnet for new devices every 15 minutes
MIN_FULL_SWEEP = 60  # 1 minute
MAX_FULL_SWEEP = 3600  # 60 minutes
RECENT_DEVICE_SECONDS = 86400  # light scans check devices seen in the last 24 hours
PANEL_ACTIVE_SECONDS = 35  # the panel checks in every 10 s while open
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
