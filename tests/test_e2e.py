"""Tests for the network-scan version of Network Devices."""
import asyncio
import ipaddress
import struct

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

    async def find_names(hass, devices):
        return {mac: (names[ip].split(".")[0], "DNS") for mac, ip in devices.items() if names.get(ip)}

    monkeypatch.setattr(scanner.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(scanner, "async_find_names", find_names)
    monkeypatch.setattr(scanner, "dhcp_names", lambda hass: {})

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


# --- hostname lookups -----------------------------------------------------------

from custom_components.network_devices import names  # noqa: E402


def dns_reply(query: bytes, target: str) -> bytes:
    """Answer a PTR query the way a real DNS/mDNS server does (with name compression)."""
    txid = query[:2]
    question = query[12:]
    rdata = b"".join(bytes([len(p)]) + p.encode() for p in target.split(".")) + b"\x00"
    answer = b"\xc0\x0c" + struct.pack(">HHIH", 12, 1, 120, len(rdata)) + rdata
    return txid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + question + answer


def nbstat_reply(name: str) -> bytes:
    entries = (b"WORKGROUP".ljust(15) + b"\x00" + struct.pack(">H", 0x8400)  # group name: skip
               + name.encode().ljust(15) + b"\x00" + struct.pack(">H", 0x0400)
               + name.encode().ljust(15) + b"\x20" + struct.pack(">H", 0x0400))
    rdata = bytes([3]) + entries + b"\x00" * 6
    return (struct.pack(">HHHHHH", 1, 0x8400, 0, 1, 0, 0) + names.NBSTAT_NAME
            + struct.pack(">HHIH", 0x21, 1, 0, len(rdata)) + rdata)


def test_packet_parsing():
    q = names.build_query(0x1234, names.ptr_name("192.168.99.10"), names.TYPE_PTR)
    assert b"\x0210\x0299\x03168\x03192\x07in-addr\x04arpa\x00" in q
    assert names.parse_ptr(dns_reply(q, "Kitchen-Speaker.local")) == (0x1234, "Kitchen-Speaker.local")
    assert names.parse_nbstat(nbstat_reply("GAMING-PC")) == "GAMING-PC"
    assert names.short_name("tv.localdomain.") == "tv" and names.short_name("10.0.0.1") == ""


async def test_ptr_lookup_against_fake_dns(socket_enabled):
    """Many queries go out on one socket; replies are matched back by transaction id."""
    table = {names.ptr_name("10.1.1.5"): "nas.lan", names.ptr_name("10.1.1.6"): "printer.lan"}

    class FakeDNS(asyncio.DatagramProtocol):
        def connection_made(self, t):
            self.t = t

        def datagram_received(self, data, addr):
            qname, _ = names.read_name(data, 12)
            if qname in table:
                self.t.sendto(dns_reply(data, table[qname]), addr)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(FakeDNS, local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("sockname")[1]
    try:
        got = await names.ptr_lookup(["10.1.1.5", "10.1.1.6", "10.1.1.7"], ("127.0.0.1", port), wait=0.3)
    finally:
        transport.close()
    assert got == {"10.1.1.5": "nas", "10.1.1.6": "printer"}


async def test_name_sources_priority(hass, monkeypatch):
    devices = {"aa:00:00:00:00:01": "10.0.0.1", "aa:00:00:00:00:02": "10.0.0.2",
               "aa:00:00:00:00:03": "10.0.0.3", "aa:00:00:00:00:04": "10.0.0.4"}
    monkeypatch.setattr(names, "dhcp_names", lambda hass: {"aa:00:00:00:00:01": "from-dhcp"})

    async def sysdns(ips):
        return {"10.0.0.1": "should-not-win", "10.0.0.2": "from-dns"}

    async def zc(hass):
        return {"10.0.0.3": "esp-kitchen"}

    async def nb(ips):
        return {"10.0.0.4": "GAMING-PC"}

    async def nothing(ips, server, wait=2.0):
        return {}

    monkeypatch.setattr(names, "system_reverse_dns", sysdns)
    monkeypatch.setattr(names, "zeroconf_names", zc)
    monkeypatch.setattr(names, "netbios_lookup", nb)
    monkeypatch.setattr(names, "ptr_lookup", nothing)
    monkeypatch.setattr(names, "default_gateways", lambda: ["10.0.0.254"])
    assert await names.async_find_names(hass, devices) == {
        "aa:00:00:00:00:01": ("from-dhcp", "DHCP"), "aa:00:00:00:00:02": ("from-dns", "DNS"),
        "aa:00:00:00:00:03": ("esp-kitchen", "mDNS"), "aa:00:00:00:00:04": ("GAMING-PC", "NetBIOS")}


def test_default_gateway(tmp_path):
    f = tmp_path / "route"
    f.write_text("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
                 "eth0\t00000000\t016310AC\t0003\t0\t0\t0\t00000000\n"
                 "eth0\t006310AC\t00000000\t0001\t0\t0\t0\t00FFFFFF\n")
    assert names.default_gateways(str(f)) == ["172.16.99.1"]
