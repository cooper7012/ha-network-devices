# Network Devices (UniFi) for Home Assistant

Adds a **Network Devices** page to the Home Assistant sidebar that lists every client on your UniFi network:

| Name | MAC Address | Vendor | Connection | Network | WiFi | Channel | IP Address | Activity |
|------|-------------|--------|------------|---------|------|---------|------------|----------|

- **IP addresses are links**: click one to open `http://<ip>` in a new tab.
- Live upload and download rates per device, refreshed every 5 seconds in the panel.
- Search, sort by any column, filter WiFi or wired, and show or hide offline (known) devices.
- Creates a `Connected clients` sensor, with wired/wireless counts and total throughput as attributes.
- Works with UniFi OS consoles (UDM, UDR, UCG, UX, Cloud Key Gen2+) and self-hosted UniFi Network controllers.

## Install

**HACS (recommended)**

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cooper7012&repository=ha-network-devices&category=integration)

Or add it by hand:
1. In HACS, open the ⋮ menu and choose **Custom repositories**.
2. Add `https://github.com/cooper7012/ha-network-devices` with type **Integration**.
3. Search for **Network Devices (UniFi)**, download it, then restart Home Assistant.

**Manually**
1. Copy `custom_components/network_devices` into your Home Assistant `config/custom_components/` folder.
   (Use the Samba share, the File editor / Studio Code Server add-on, or `scp`.)
2. Restart Home Assistant.

## Set up
1. In UniFi, create a **local** admin (*Settings → Admins & Users → Add*: "Restrict to local access only").
   The *View Only* role is enough. A ui.com cloud account with 2FA will not work.
2. In Home Assistant, go to *Settings → Devices & Services → Add Integration → Network Devices (UniFi)*.
3. Enter:
   - **Host**: your console's IP, for example `192.168.1.1`.
   - **Port**: `443` for UniFi OS consoles, `8443` for a self-hosted controller.
   - **Site ID**: `default` unless you have several sites. The ID is in the controller URL (`/manage/<site>/...`).
   - **Verify SSL**: leave it off if the console uses its built-in self-signed certificate.
4. **Network Devices** now appears in the sidebar.

## Options
*Settings → Devices & Services → Network Devices → Configure*:
- **Refresh interval**: how often to poll the controller (default 15 s).
- **Swap upload/download**: UniFi reports traffic from the access point's point of view
  (AP transmit = client download). If the arrows look reversed on your firmware, turn this on.

## Notes
- You can add several controllers or sites. The panel merges them into one list.
- Links open `http://<ip>`. Devices that only serve HTTPS usually redirect there automatically.
