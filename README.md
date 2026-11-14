# Network Devices for Home Assistant

Adds a **Network Devices** page to the Home Assistant sidebar that lists every device on your network:

| Name | MAC Address | Vendor | Connection | Network | WiFi | Channel | IP Address | Activity |
|------|-------------|--------|------------|---------|------|---------|------------|----------|

- **IP addresses are links**: click one to open `http://<ip>` in a new tab.
- Live upload and download per device, where your router reports it.
- Search, sort by any column, filter WiFi or wired, and show or hide offline devices.
- Combine several routers, controllers or sites into one list.
- Adds a `Connected clients` sensor, with wired/wireless counts and total throughput as attributes.

## Supported routers and controllers

| | WiFi / Channel | Live activity | Connection | Vendor |
|---|:-:|:-:|---|:-:|
| **Ubiquiti UniFi** (UDM, UDR, UCG, UX, Cloud Key, self-hosted) | ✅ | ✅ | AP / switch port | ✅ |
| **TP-Link Omada** (OC200/300/400, Software Controller) | ✅ | ✅ | AP / switch port | ✅ |
| **ASUS** (ASUSWRT, Asuswrt-Merlin, AiMesh) | ✅ | — | Router / AiMesh node | ✅ |
| **AVM FRITZ!Box** | ✅ | — | WiFi / LAN port | ✅ |
| **OpenWrt** | ✅ | ✅ WiFi clients | Interface | ✅ |
| **MikroTik RouterOS 7** | ✅ | ✅ WiFi clients (all clients with Kid Control) | Bridge port | ✅ |
| **OPNsense** | — | ✅ | Interface | ✅ |
| **Network scan**: any other router (Netgear, eero, Google/Nest, ISP routers…) | — | — | — | ✅ |

Vendor names come from the router where it provides them, otherwise from a built-in MAC address database.
Devices using a private (randomized) MAC address are labelled as such.

## Install

**HACS (recommended)**

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cooper7012&repository=ha-network-devices&category=integration)

Or add it by hand:
1. In HACS, open the ⋮ menu and choose **Custom repositories**.
2. Add `https://github.com/cooper7012/ha-network-devices` with type **Integration**.
3. Search for **Network Devices**, download it, then restart Home Assistant.

**Manually**
1. Copy `custom_components/network_devices` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

## Set up
Go to *Settings → Devices & Services → Add Integration → Network Devices* and pick what runs your network.
You can add the integration more than once, for example a router plus a separate WiFi controller.

| Router | What you need |
|---|---|
| UniFi | A **local** UniFi account (View Only is enough). Port 443 for consoles, 8443 for self-hosted. Site ID is usually `default`. |
| Omada | A local controller account (Viewer is enough). Port 443 for OC200/300/400, 8043 for the Software Controller. |
| ASUS | The router's admin login. Port 80, or turn on HTTPS (often port 8443) if the web UI is HTTPS-only. |
| FRITZ!Box | Turn on *Home Network → Network → Network Settings → Allow access for applications*. Port 49000. Leave the username empty if you log in with a password only. |
| OpenWrt | `root` and its password. LuCI must be installed (it is by default). |
| MikroTik | RouterOS 7.1+ with *IP → Services → www* (80) or *www-ssl* (443) enabled. A read-only user works. |
| OPNsense | An API key and secret (*System → Access → Users → edit user → API keys*). Enter the key as the username and the secret as the password. |
| Network scan | Just the subnet(s), which are pre-filled. Home Assistant must be on the same network (Home Assistant OS, or Docker with `network_mode: host`). |

## Options
*Settings → Devices & Services → Network Devices → Configure*:
- **Refresh interval**: how often to poll (default 15 s, or 60 s for a network scan).
- **Swap upload/download**: if the arrows look reversed for your router, turn this on.

## Development
Tests use fake versions of every router API:
```bash
pip install pytest-homeassistant-custom-component
pytest
```
