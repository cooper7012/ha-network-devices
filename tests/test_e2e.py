"""Tests for the network-scan version of Network Devices."""
import ipaddress

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.network_devices import config_flow, scanner
from custom_components.network_devices.models import async_load_vendor_db

ARP_HEADER = "IP address       HW type     Flags       HW address            Mask     Device\n"
LAN = [ipaddress.IPv4Network("192.168.99.0/24")]


@pytest.fixture
def fake_net(monkeypatch, tmp_path):
    """Fake ARP table + DNS; no packets are sent."""
    arp = tmp_path / "arp"
    names = {"192.168.99.10": "living-room-tv.localdomain", "192.168.99.20": ""}

    def set_arp(*entries):
        arp.write_text(ARP_HEADER + "".join(f"{ip} 0x1 {flags} {mac} * eth0\n" for ip, mac, flags in entries))

    set_arp(("192.168.99.10", "00:17:88:aa:bb:cc", "0x2"),   # Philips Hue
            ("192.168.99.20", "da:a1:19:00:00:01", "0x2"),   # random MAC phone
            ("192.168.99.30", "00:00:00:00:00:00", "0x0"),   # no answer
            ("10.0.0.5", "00:11:32:00:00:01", "0x2"))        # other network: ignored
    real_read = scanner.read_arp
    monkeypatch.setattr(scanner, "read_arp", lambda: real_read(str(arp)))
    monkeypatch.setattr(scanner, "probe", lambda nets: None)

    async def no_sleep(_):
        return None

    async def resolve(ip):
        return (names.get(ip) or "").split(".")[0]

    monkeypatch.setattr(scanner.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(scanner, "resolve_hostname", resolve)

    async def local(hass):
        return LAN

    monkeypatch.setattr(scanner, "async_local_networks", local)
    monkeypatch.setattr(config_flow, "async_local_networks", local)
    return set_arp


def test_read_arp_parses_real_format(tmp_path):
    f = tmp_path / "arp"
    f.write_text(ARP_HEADER + "192.168.1.1      0x1         0x2         74:ac:b9:11:22:33     *        eth0\n"
                              "192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        eth0\n")
    assert scanner.read_arp(str(f)) == {"74:ac:b9:11:22:33": "192.168.1.1"}


def test_parse_subnets():
    assert [str(n) for n in scanner.parse_subnets("192.168.1.5/24, 10.0.0.0/28")] == ["192.168.1.0/24", "10.0.0.0/28"]
    with pytest.raises(ValueError):
        scanner.parse_subnets("10.0.0.0/16")  # too big
    with pytest.raises(ValueError):
        scanner.parse_subnets("nonsense")


async def test_scanner_rows_and_persistence(hass, fake_net, hass_storage):
    await async_load_vendor_db()
    rows = await scanner.NetworkScanner(hass, "e1", "192.168.99.0/24").async_scan()
    assert [(r["ip"], r["hostname"], r["online"]) for r in rows] == [
        ("192.168.99.10", "living-room-tv", True), ("192.168.99.20", "", True)]
    assert rows[0]["vendor"]  # 00:17:88 = Philips, from the bundled vendor database
    assert rows[1]["vendor"] == "Private (random MAC)"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15))  # delayed save runs
    await hass.async_block_till_done()
    assert "da:a1:19:00:00:01" in hass_storage["network_devices.e1"]["data"]["devices"]

    # Phone leaves; a fresh scanner (e.g. after a restart) still remembers it as offline.
    fake_net(("192.168.99.10", "00:17:88:aa:bb:cc", "0x2"))
    s2 = scanner.NetworkScanner(hass, "e1", "192.168.99.0/24")
    rows = await s2.async_scan()
    phone = next(r for r in rows if r["mac"] == "da:a1:19:00:00:01")
    assert phone["online"] is False and phone["ip"] == "192.168.99.20"
    assert phone["seconds_since_seen"] is not None


async def test_setup_flow(hass, enable_custom_integrations, fake_net, hass_ws_client):
    assert await async_setup_component(hass, "http", {})
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    assert r["step_id"] == "user"
    assert r["data_schema"]({})["subnets"] == "192.168.99.0/24"  # pre-filled

    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"subnets": "192.168.10.0/24"})
    assert r["errors"] == {"base": "not_local"}
    assert r["description_placeholders"]["remote"] == "192.168.10.0/24"

    fake_net()  # nothing answers
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"subnets": "192.168.99.0/24"})
    assert r["errors"] == {"base": "no_devices"}

    fake_net(("192.168.99.10", "00:17:88:aa:bb:cc", "0x2"))
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"subnets": "192.168.99.0/24"})
    assert r["type"] == "create_entry", r
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("network_devices")[0]
    assert entry.state is ConfigEntryState.LOADED
    assert "network-devices" in hass.data["frontend_panels"]
    assert hass.states.get("sensor.network_devices_online_devices").state == "1"

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "network_devices/list", "refresh": True})
    msg = await ws.receive_json()
    assert msg["success"]
    assert [d["hostname"] for d in msg["result"]["devices"]] == ["living-room-tv"]

    # Only one instance allowed
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    assert r["type"] == "abort"

    # Options: change interval and subnets
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"subnets": "192.168.99.0/25", "scan_interval": 120})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["subnets"] == "192.168.99.0/25" and entry.options["scan_interval"] == 120
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "network-devices" not in hass.data["frontend_panels"]


async def test_old_unifi_entry_is_migrated(hass, enable_custom_integrations, fake_net):
    assert await async_setup_component(hass, "http", {})
    old = MockConfigEntry(
        domain="network_devices", version=1, title="Ubiquiti UniFi (192.168.99.1)", unique_id="unifi_x",
        data={"router_type": "unifi", "host": "192.168.99.1", "port": 443, "username": "u", "password": "p", "site": "default"},
        options={"scan_interval": 15, "swap_direction": False},
    )
    old.add_to_hass(hass)
    assert await hass.config_entries.async_setup(old.entry_id)
    await hass.async_block_till_done()
    assert old.state is ConfigEntryState.LOADED
    assert old.version == 2 and old.data == {"subnets": "192.168.99.0/24"} and old.options == {}
    assert "password" not in old.data
