# Network Devices for Home Assistant

Adds a **Network Devices** page to the Home Assistant sidebar listing every device on your network.
It works with **any router**: no router login, API key or controller needed.

| Name | MAC Address | Vendor | IP Address | Tags | Last Seen |
|------|-------------|--------|------------|------|-----------|

- **IP addresses are links**: click one to open `http://<ip>` in a new tab.
- **Custom names and tags**: click ✎ on any device to name it and tag it (for example *Camera*, *Kitchen*, *IoT*).
- **Filter by tags**: tick tags in the **Tags** menu and show devices matching *any* or *all* of them, or devices with no tags.
  Click a tag in the table to filter by it. Tags can be renamed or deleted everywhere at once.
- **Saved filters**: save the current search, tags, sort and *Show offline* setting under a name and reuse it from the drop-down.
- **Picks up where you left off**: your last filter is remembered for your Home Assistant user, on any browser or device.
- Search, sort by any column, and show or hide devices that have gone offline.
- Remembers devices across restarts, so you can see when something was last online.
- Adds an `Online devices` sensor you can use in dashboards and automations.

## How it works
Home Assistant sends tiny packets to addresses on your network, then reads which devices answered from its own
ARP table. Choose how it scans with the ⚙ button on the Network Devices page (administrators only):

| Mode | What it sends | Online devices sensor |
|---|---|---|
| **Light scan** (default) | Each check, one packet straight to each known device, with no broadcasts. Every so often, a sweep of the whole subnet to find new devices. | Kept up to date |
| **Only while this page is open** | Nothing at all unless someone has the page open; scans as soon as it's opened. | Unavailable while nobody is looking |

Two sliders on the same screen control the timing, and changes apply immediately:
- **Check known devices every** 1–30 minutes (default 1).
- **Search for new devices every** 1–60 minutes (default 15). The full search runs with the first check after
  that time. At or below the check interval, every scan is a full search.

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

*Configure* lets you change the subnets (comma-separated, for example `192.168.1.0/24, 192.168.20.0/24`),
the scan mode and both intervals. They can also be changed from the ⚙ button on the page.

Names, tags and saved filters are stored in Home Assistant (`.storage/network_devices.customizations`),
so they're included in Home Assistant backups.

## Upgrading from 2.x
Router-based setups (UniFi, Omada, ASUS…) are converted automatically to a scan of Home Assistant's own network,
and their stored router credentials are deleted.

## Development
```bash
pip install pytest-homeassistant-custom-component
pytest
```
