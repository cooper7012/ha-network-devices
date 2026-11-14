# Network Devices for Home Assistant

Adds a **Network Devices** page to the Home Assistant sidebar listing every device on your network.
It works with **any router**: no router login, API key or controller needed.

| Hostname | MAC Address | Vendor | IP Address | Last Seen |
|----------|-------------|--------|------------|-----------|

- **IP addresses are links**: click one to open `http://<ip>` in a new tab.
- Search, sort by any column, and show or hide devices that have gone offline.
- Remembers devices across restarts, so you can see when something was last online.
- Adds an `Online devices` sensor you can use in dashboards and automations.

## How it works
Every minute, Home Assistant sends a tiny packet to each address on your network, then reads which devices
answered from its own ARP table:
- **Hostname** is found without needing your router's help, trying in order: the name the device sent when it
  joined the network (Home Assistant's DHCP watcher), reverse DNS (Home Assistant's resolver and your gateway),
  mDNS (Apple devices, printers, ESPHome/Shelly and most smart-home gear), and NetBIOS (Windows PCs, NAS boxes).
  Hover over a name to see where it came from. Devices that answer none of these show *Unknown*.
- **Vendor** comes from the MAC address, using the vendor database that ships with Home Assistant.
  Phones and tablets that use a private (randomized) MAC address are labelled *Private (random MAC)*.

### Limitation: one network (VLAN) at a time
MAC addresses don't cross routers, so Home Assistant only sees devices on networks it is **directly connected to**.
If your devices are spread over several VLANs, either:
- give Home Assistant an interface on each VLAN (Home Assistant OS: *Settings → System → Network*, or `ha network vlan`;
  the switch port must carry those VLANs), or
- accept that only Home Assistant's own VLAN is listed.

Home Assistant in Docker needs `network_mode: host`.

## Install

**Home Assistant Marketplace (or HACS)**
1. *Settings → Marketplace → ⋮ → Custom repositories* (in HACS: *⋮ → Custom repositories*).
2. Add `https://github.com/cooper7012/ha-network-devices` as an **Integration**.
3. Download **Network Devices**, then restart Home Assistant.

**Manually**: copy `custom_components/network_devices` into `config/custom_components/` and restart.

## Set up
*Settings → Devices & Services → Add Integration → Network Devices.* Your network is pre-filled, so just click **Submit**.

*Configure* lets you change the subnets (comma-separated, for example `192.168.1.0/24, 192.168.20.0/24`)
and the scan interval (default 60 seconds).

## Upgrading from 2.x
Router-based setups (UniFi, Omada, ASUS…) are converted automatically to a scan of Home Assistant's own network,
and their stored router credentials are deleted.

## Development
```bash
pip install pytest-homeassistant-custom-component
pytest
```
