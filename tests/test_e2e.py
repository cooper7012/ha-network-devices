"""Tests for the network-scan version of Network Devices."""
import asyncio
import ipaddress
import struct

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
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
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"subnets": "192.168.99.0/25", "scan_mode": "light", "scan_minutes": 2, "full_sweep_minutes": 20})
    assert r["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["subnets"] == "192.168.99.0/25" and entry.options == {"scan_mode": "light", "scan_interval": 120, "full_sweep_interval": 1200}
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


# --- custom names, tags and filters -----------------------------------------------

async def test_names_tags_and_filters(hass, enable_custom_integrations, fake_net, hass_ws_client,
                                      hass_admin_user, hass_read_only_user, hass_storage, hass_read_only_access_token):
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain="network_devices", version=2, data={"subnets": "192.168.99.0/24"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ws = await hass_ws_client(hass)
    n = iter(range(1, 1000))

    async def call(**msg):
        await ws.send_json({"id": next(n), **msg})
        res = await ws.receive_json()
        assert res["success"], res
        return res["result"]

    tv = "00:17:88:aa:bb:cc"
    await call(type="network_devices/update_device", mac=tv, name="  Living Room TV ", tags=["Media", "media", "Upstairs"])
    await call(type="network_devices/update_device", mac="da:a1:19:00:00:01", tags=["Phones"])
    res = await call(type="network_devices/list")
    dev = {d["mac"]: d for d in res["devices"]}
    assert dev[tv]["name"] == "Living Room TV" and dev[tv]["tags"] == ["Media", "Upstairs"]  # trimmed, de-duplicated
    assert dev[tv]["hostname"] == "living-room-tv"  # discovered name is kept alongside
    assert res["tags"] == ["Media", "Phones", "Upstairs"]

    # Bad input is rejected
    await ws.send_json({"id": next(n), "type": "network_devices/update_device", "mac": "not-a-mac", "name": "x"})
    assert not (await ws.receive_json())["success"]

    # Saved filters
    flt = {"query": "tv", "tags": ["Media"], "match": "all", "untagged": False, "show_offline": True, "sort": "name", "dir": -1}
    fid = (await call(type="network_devices/save_filter", name="Media stuff", filter=flt))["filter_id"]
    same = (await call(type="network_devices/save_filter", filter_id=fid, name="Media", filter={**flt, "query": ""}))["filter_id"]
    assert same == fid
    res = await call(type="network_devices/list")
    assert res["filters"] == [{"filter_id": fid, "name": "Media", **flt, "query": ""}]

    # Last filter is remembered per user
    await call(type="network_devices/set_last_filter", filter=flt, saved_id=fid)
    assert (await call(type="network_devices/list"))["last_filter"] == {**flt, "saved_id": fid}
    ws2 = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    await ws2.send_json({"id": 1, "type": "network_devices/list"})
    assert (await ws2.receive_json())["result"]["last_filter"] is None

    # Rename / delete tags updates devices and saved filters
    await call(type="network_devices/rename_tag", old="media", new="TV & Audio")
    res = await call(type="network_devices/list")
    assert {d["mac"]: d for d in res["devices"]}[tv]["tags"] == ["TV & Audio", "Upstairs"]
    assert res["filters"][0]["tags"] == ["TV & Audio"] and res["last_filter"]["tags"] == ["TV & Audio"]
    await call(type="network_devices/delete_tag", tag="Phones")
    res = await call(type="network_devices/list")
    assert {d["mac"]: d for d in res["devices"]}["da:a1:19:00:00:01"]["tags"] == []

    # Clearing name and tags removes the stored entry; deleting a saved filter
    await call(type="network_devices/update_device", mac=tv, name="", tags=[])
    await call(type="network_devices/delete_filter", filter_id=fid)
    res = await call(type="network_devices/list")
    assert res["filters"] == [] and res["tags"] == []

    # Everything is persisted in Home Assistant's storage
    await call(type="network_devices/update_device", mac=tv, name="TV")
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)  # what a restart does: flush pending saves
    await hass.async_block_till_done()
    stored = hass_storage["network_devices.customizations"]["data"]
    assert stored["devices"] == {tv: {"name": "TV"}}
    assert stored["last"]  # last-used filter survives restarts


# --- scan modes -------------------------------------------------------------------

@pytest.fixture
def probes(monkeypatch, fake_net):
    """Record which addresses each scan sends packets to."""
    sent = []
    monkeypatch.setattr(scanner, "probe", lambda targets: sent.append(list(targets)))
    return sent


async def _setup(hass, options=None):
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain="network_devices", version=2, data={"subnets": "192.168.99.0/24"}, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_light_scan_only_probes_known_devices(hass, enable_custom_integrations, probes, monkeypatch):
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    assert len(probes[0]) == 254  # first scan: full sweep to find devices
    await coordinator.async_refresh()
    assert probes[1] == ["192.168.99.10", "192.168.99.20"]  # then only known devices
    # after 15 minutes, another full sweep looks for new devices
    monkeypatch.setattr(coordinator.scanner, "last_full_sweep", coordinator.scanner.last_full_sweep - 901)
    await coordinator.async_refresh()
    assert len(probes[2]) == 254
    assert coordinator.status()["mode"] == "light" and coordinator.status()["devices_probed"] == 254


async def test_panel_mode_sends_nothing_until_panel_opens(hass, enable_custom_integrations, probes, hass_ws_client):
    entry = await _setup(hass, {"scan_mode": "panel", "scan_interval": 60})
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    assert probes == []  # nobody looking: no packets at all
    assert hass.states.get("sensor.network_devices_online_devices").state == "unavailable"

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "network_devices/list"})  # opening the panel
    res = (await ws.receive_json())["result"]
    assert res["scan"]["starting"] is True  # opening the panel kicks off a scan
    await hass.async_block_till_done()
    assert len(probes) == 1 and len(probes[0]) == 254  # scanned right away
    await ws.send_json({"id": 2, "type": "network_devices/list"})
    res = (await ws.receive_json())["result"]
    assert res["scan"]["paused"] is False and res["scan"]["starting"] is False
    assert [d["hostname"] for d in res["devices"] if d["online"]] == ["living-room-tv", ""]
    assert hass.states.get("sensor.network_devices_online_devices").state == "2"

    # panel closed for longer than the heartbeat window: back to silence
    coordinator.panel_seen -= 60
    await coordinator.async_refresh()
    assert len(probes) == 1


async def test_change_settings_from_panel(hass, enable_custom_integrations, probes, hass_ws_client,
                                          hass_read_only_access_token):
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "network_devices/set_scan_settings", "mode": "panel", "interval": 600,
                        "full_sweep": 1800})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options == {"scan_mode": "panel", "scan_interval": 600, "full_sweep_interval": 1800}
    assert entry.runtime_data is coordinator  # applied live, no reload
    assert coordinator.mode == "panel" and coordinator.interval == 600 and coordinator.full_sweep == 1800
    assert coordinator.status()["full_sweep"] == 1800
    for bad in (30, 3660):  # under 1 minute / over 60 minutes
        await ws.send_json({"id": 10 + bad, "type": "network_devices/set_scan_settings", "full_sweep": bad})
        assert not (await ws.receive_json())["success"]

    await ws.send_json({"id": 2, "type": "network_devices/set_scan_settings", "interval": 30})  # under 1 minute
    assert not (await ws.receive_json())["success"]
    await ws.send_json({"id": 3, "type": "network_devices/set_scan_settings", "interval": 1860})  # over 30 minutes
    assert not (await ws.receive_json())["success"]

    ro = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    await ro.send_json({"id": 1, "type": "network_devices/set_scan_settings", "mode": "light"})
    msg = await ro.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"
    await ro.send_json({"id": 2, "type": "network_devices/list"})
    assert (await ro.receive_json())["result"]["is_admin"] is False


async def test_full_sweep_interval_setting(hass, enable_custom_integrations, probes, monkeypatch):
    # 1-minute checks, 5-minute full search
    entry = await _setup(hass, {"scan_interval": 60, "full_sweep_interval": 300})
    coordinator = entry.runtime_data
    assert len(probes[0]) == 254
    clock = [coordinator.scanner.last_full_sweep]
    monkeypatch.setattr(scanner.time, "time", lambda: clock[0])
    import custom_components.network_devices.coordinator as coord_mod
    monkeypatch.setattr(coord_mod.time, "time", lambda: clock[0])
    sizes = []
    for _ in range(10):  # ten scans, one minute apart (a little late, like real timers)
        clock[0] += 61
        await coordinator.async_refresh()
        sizes.append(len(probes[-1]))
    assert sizes == [2, 2, 2, 2, 254, 2, 2, 2, 2, 254]  # full search on every 5th scan

    # At or below the check interval: every scan is a full search
    coordinator.apply_settings("light", 120, 60)
    await hass.async_block_till_done()
    probes.clear()
    for _ in range(3):
        clock[0] += 121
        await coordinator.async_refresh()
    assert [len(p) for p in probes][-3:] == [254, 254, 254]
