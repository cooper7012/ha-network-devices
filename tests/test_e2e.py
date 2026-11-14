"""End-to-end tests against fake routers/controllers (one mock per supported API)."""
import base64
import json
import pathlib
import ssl

import pytest
from aiohttp import web

from homeassistant.config_entries import ConfigEntryState
from homeassistant.setup import async_setup_component

from custom_components.network_devices import make_backend
from custom_components.network_devices.models import finalize

HTTPS, HTTP = 18443, 18080

SOAP_RESP = (
    '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
    '<u:{a}Response xmlns:u="{svc}">{body}</u:{a}Response></s:Body></s:Envelope>'
)


def build_app() -> web.Application:
    app = web.Application()
    r = app.router
    j = web.json_response

    # ---- UniFi (legacy controller) ----
    async def root(req): raise web.HTTPFound("/manage")
    async def unifi_login(req):
        b = await req.json()
        if b["password"] == "mfa": return j({"code": "MFA_AUTH_REQUIRED"}, status=499)
        if b["password"] != "pw": return j({}, status=400)
        resp = j({"data": []}); resp.set_cookie("unifises", "x"); return resp
    unifi = {
        "stat/sta": [{"mac": "3c:22:fb:00:11:22", "hostname": "Robs-iPhone", "oui": "Apple", "ap_mac": "aa", "essid": "HomeNet",
                      "channel": 36, "radio": "na", "ip": "192.168.1.45", "tx_bytes-r": 1000, "rx_bytes-r": 10}],
        "rest/user": [{"mac": "00:11:32:aa:bb:cc", "name": "NAS", "last_ip": "192.168.1.20", "last_seen": 1}],
        "stat/device": [{"mac": "aa", "name": "Living Room AP"}], "rest/networkconf": []}
    r.add_get("/", root)
    r.add_post("/api/login", unifi_login)
    for k, v in unifi.items():
        async def h(req, v=v):
            return j({"data": v}) if "unifises" in req.cookies else j({}, status=401)
        r.add_get(f"/api/s/default/{k}", h)

    # ---- Omada ----
    async def om_info(req): return j({"errorCode": 0, "result": {"omadacId": "cid"}})
    async def om_login(req):
        b = await req.json()
        return j({"errorCode": 0, "result": {"token": "tok"}} if b["password"] == "pw" else {"errorCode": -30109, "msg": "bad"})
    def om(result):
        async def h(req):
            if req.headers.get("Csrf-Token") != "tok": return j({"errorCode": -1200})
            return j({"errorCode": 0, "result": result})
        return h
    r.add_get("/api/info", om_info)
    r.add_post("/cid/api/v2/login", om_login)
    r.add_get("/cid/api/v2/sites", om({"data": [{"id": "s1", "name": "Default"}]}))
    r.add_get("/cid/api/v2/sites/s1/clients", om({"data": [
        {"mac": "AA-BB-CC-00-00-01", "name": "AA-BB-CC-00-00-01", "hostName": "laptop", "ip": "10.0.0.5", "wireless": True,
         "ssid": "Office", "apName": "EAP650", "channel": 149, "radioId": 1, "rssi": -55, "activity": 5000, "uploadActivity": 300},
        {"mac": "AA-BB-CC-00-00-02", "name": "Printer", "ip": "10.0.0.9", "wireless": False, "switchName": "SG2008",
         "port": 3, "networkName": "LAN", "activity": 0, "uploadActivity": 0}]}))
    r.add_get("/cid/api/v2/sites/s1/insight/clients", om({"data": [
        {"mac": "AA-BB-CC-00-00-03", "name": "Old Phone", "lastSeen": 1700000000000, "wireless": True}]}))

    # ---- OPNsense ----
    def opn(result):
        async def h(req):
            ok = req.headers.get("Authorization") == "Basic " + base64.b64encode(b"key:secret").decode()
            return j(result) if ok else j({}, status=401)
        return h
    r.add_get("/api/diagnostics/interface/search_arp", opn({"rows": [
        {"mac": "00:11:22:33:44:55", "ip": "192.168.10.7", "intf": "igb1", "intf_description": "LAN",
         "manufacturer": "Sonos", "hostname": "sonos-kitchen"}]}))
    r.add_get("/api/dnsmasq/leases/search", opn({"rows": [
        {"hwaddr": "00:11:22:33:44:55", "address": "192.168.10.7", "hostname": "sonos-kitchen"},
        {"hwaddr": "00:11:22:33:44:66", "address": "192.168.10.8", "hostname": "tv", "if_descr": "LAN"}]}))
    r.add_get("/api/diagnostics/traffic/interface", opn({"interfaces": {"lan": {}, "wan": {}}}))
    r.add_get("/api/diagnostics/traffic/top/lan", opn({"lan": {"records": [
        {"address": "192.168.10.7", "rate_bits_in": 8000, "rate_bits_out": 80000}]}}))

    # ---- OpenWrt ubus ----
    async def ubus(req):
        b = await req.json()
        sid, obj, method, args = b["params"]
        if obj == "session":
            if args["password"] != "pw": return j({"jsonrpc": "2.0", "id": b["id"], "result": [6]})
            return j({"jsonrpc": "2.0", "id": b["id"], "result": [0, {"ubus_rpc_session": "S"}]})
        if sid != "S": return j({"jsonrpc": "2.0", "id": b["id"], "error": {"code": -32002, "message": "Access denied"}})
        data = {
            ("luci-rpc", "getHostHints"): {"11:22:33:44:55:66": {"name": "desktop", "ipaddrs": ["192.168.1.10"]}},
            ("luci-rpc", "getDHCPLeases"): {"dhcp_leases": [{"macaddr": "66:55:44:33:22:11", "hostname": "pixel", "ipaddr": "192.168.1.30"}]},
            ("network.wireless", "status"): {"radio0": {"up": True, "config": {"band": "5g", "channel": "auto"},
                                              "interfaces": [{"ifname": "phy0-ap0", "config": {"ssid": "OpenNet", "mode": "ap", "network": ["lan"]}}]}},
            ("network.interface", "dump"): {"interface": [{"interface": "lan", "l3_device": "br-lan"}]},
            ("file", "read"): {"data": "IP address HW type Flags HW address Mask Device\n"
                                       "192.168.1.10 0x1 0x2 11:22:33:44:55:66 * br-lan\n"
                                       "192.168.1.30 0x1 0x2 66:55:44:33:22:11 * br-lan\n"},
            ("iwinfo", "info"): {"channel": 44, "ssid": "OpenNet"},
            ("hostapd.phy0-ap0", "get_clients"): {"clients": {"66:55:44:33:22:11": {"signal": -60, "bytes": {"rx": 100, "tx": 9000}}}},
        }.get((obj, method))
        if data is None: return j({"jsonrpc": "2.0", "id": b["id"], "result": [4]})
        return j({"jsonrpc": "2.0", "id": b["id"], "result": [0, data]})
    r.add_post("/ubus", ubus)

    # ---- MikroTik REST ----
    def mt(result):
        async def h(req):
            ok = req.headers.get("Authorization") == "Basic " + base64.b64encode(b"admin:pw").decode()
            return j(result) if ok else j({"error": 401}, status=401)
        return h
    r.add_get("/rest/system/identity", mt({"name": "hAP-ax3"}))
    r.add_get("/rest/ip/dhcp-server/lease", mt([
        {"address": "192.168.88.20", "mac-address": "DE:AD:BE:EF:00:01", "host-name": "tablet", "status": "bound", "server": "defconf"},
        {"address": "192.168.88.21", "mac-address": "DE:AD:BE:EF:00:02", "host-name": "nas", "comment": "Basement NAS", "status": "bound", "server": "defconf"}]))
    r.add_get("/rest/ip/arp", mt([
        {"address": "192.168.88.20", "mac-address": "DE:AD:BE:EF:00:01", "interface": "bridge", "status": "reachable"},
        {"address": "192.168.88.21", "mac-address": "DE:AD:BE:EF:00:02", "interface": "bridge", "status": "stale"}]))
    r.add_get("/rest/interface/bridge/host", mt([{"mac-address": "DE:AD:BE:EF:00:02", "on-interface": "ether3", "local": "false"}]))
    r.add_get("/rest/interface/wifi/registration-table", mt([
        {"mac-address": "DE:AD:BE:EF:00:01", "interface": "wifi1", "ssid": "MikroNet", "channel": "5180/ax/Ceee", "signal": "-58", "bytes": "50000,2000"}]))
    r.add_get("/rest/interface/wifi", mt([{"name": "wifi1", "configuration.ssid": "MikroNet"}]))

    # ---- ASUS ----
    async def asus_login(req):
        form = await req.text()
        ok = form == "login_authorization=" + base64.b64encode(b"admin:pw").decode()
        return web.Response(text='{"asus_token":"T"}' if ok else '{"error_status":"3"}')
    async def asus_get(req):
        if req.headers.get("Cookie") != "asus_token=T": return web.Response(text="<html>login</html>")
        return web.Response(text=json.dumps({
            "get_clientlist": {"maclist": ["F0:2F:74:00:00:01", "F0:2F:74:00:00:02"],
                "F0:2F:74:00:00:01": {"name": "Galaxy-S24", "nickName": "", "ip": "192.168.50.12", "vendor": "Samsung", "isOnline": "1", "isWL": "2", "rssi": "-48"},
                "F0:2F:74:00:00:02": {"name": "XBOX", "nickName": "Living room Xbox", "ip": "192.168.50.13", "vendor": "Microsoft", "isOnline": "1", "isWL": "0"}},
            "wl_control_channel": ["6", "100"], "wl0_ssid": "Asus24", "wl1_ssid": "Asus5"})[:-1] + ",}")  # trailing comma like real firmware
    r.add_post("/login.cgi", asus_login)
    r.add_get("/appGet.cgi", asus_get)

    # ---- FRITZ!Box TR-064 ----
    async def fritz(req):
        if not req.headers.get("Authorization", "").startswith("Digest "):
            return web.Response(status=401, headers={"WWW-Authenticate": 'Digest realm="F!Box SOAP-Auth", nonce="N1", algorithm=MD5, qop="auth"'})
        action = req.headers["SOAPAction"].strip('"').split("#")[1]
        svc = req.headers["SOAPAction"].strip('"').split("#")[0]
        n = req.path[-1]
        body = {"X_AVM-DE_GetHostListPath": "<NewX_AVM-DE_HostListPath>/devicehostlist.lua?sid=1</NewX_AVM-DE_HostListPath>",
                "GetInfo": "<NewEnable>1</NewEnable><NewSSID>FRITZ!Box 7590</NewSSID><NewChannel>{}</NewChannel>".format({"1": 11, "2": 36}.get(n, 1)),
                "X_AVM-DE_GetWLANDeviceListPath": f"<NewX_AVM-DE_WLANDeviceListPath>/wlandevicelist.lua?n={n}</NewX_AVM-DE_WLANDeviceListPath>"}[action]
        if action != "X_AVM-DE_GetHostListPath" and n not in "12": body = body.replace("<NewEnable>1", "<NewEnable>0")
        return web.Response(text=SOAP_RESP.format(a=action, svc=svc, body=body), content_type="text/xml")
    async def fritz_hosts(req):
        return web.Response(content_type="text/xml", text="""<List>
<Item><MACAddress>C8:0E:14:00:00:01</MACAddress><IPAddress>192.168.178.20</IPAddress><HostName>iPad</HostName><Active>1</Active><InterfaceType>802.11</InterfaceType></Item>
<Item><MACAddress>C8:0E:14:00:00:02</MACAddress><IPAddress>192.168.178.21</IPAddress><HostName>PC</HostName><Active>1</Active><InterfaceType>Ethernet</InterfaceType><X_AVM-DE_Port>2</X_AVM-DE_Port></Item>
<Item><MACAddress>C8:0E:14:00:00:03</MACAddress><IPAddress>192.168.178.22</IPAddress><HostName>Old</HostName><Active>0</Active><InterfaceType></InterfaceType></Item>
</List>""")
    async def fritz_wlan(req):
        items = "<Item><AssociatedDeviceMACAddress>C8:0E:14:00:00:01</AssociatedDeviceMACAddress></Item>" if req.query["n"] == "2" else ""
        return web.Response(content_type="text/xml", text=f"<List>{items}</List>")
    r.add_post("/upnp/control/hosts", fritz)
    for i in range(1, 5):
        r.add_post(f"/upnp/control/wlanconfig{i}", fritz)
    r.add_get("/devicehostlist.lua", fritz_hosts)
    r.add_get("/wlandevicelist.lua", fritz_wlan)
    return app


def self_signed_cert(folder: pathlib.Path) -> tuple[str, str]:
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(folder / "cert.pem"), str(folder / "key.pem")


@pytest.fixture
async def fake_routers(socket_enabled, tmp_path):
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*self_signed_cert(tmp_path))
    await web.TCPSite(runner, "127.0.0.1", HTTPS, ssl_context=ctx).start()
    await web.TCPSite(runner, "127.0.0.1", HTTP).start()
    yield
    await runner.cleanup()


H = "127.0.0.1"
CASES = {
    "unifi": ({"host": H, "port": HTTPS, "username": "u", "password": "pw", "site": "default"},
              {"Robs-iPhone": ("wireless", "HomeNet", 36, "5 GHz", True), "NAS": ("wireless", "", None, "", False)}),
    "omada": ({"host": H, "port": HTTPS, "username": "u", "password": "pw", "site": "Default"},
              {"laptop": ("wireless", "Office", 149, "5 GHz", True), "Printer": ("wired", "", None, "", True),
               "Old Phone": ("wireless", "", None, "", False)}),
    "opnsense": ({"host": H, "port": HTTPS, "username": "key", "password": "secret"},
                 {"sonos-kitchen": ("unknown", "", None, "", True), "tv": ("unknown", "", None, "", False)}),
    "openwrt": ({"host": H, "port": HTTP, "username": "root", "password": "pw"},
                {"pixel": ("wireless", "OpenNet", 44, "5 GHz", True), "desktop": ("unknown", "", None, "", True)}),
    "mikrotik": ({"host": H, "port": HTTP, "username": "admin", "password": "pw"},
                 {"tablet": ("wireless", "MikroNet", 36, "5 GHz", True), "Basement NAS": ("wired", "", None, "", True)}),
    "asus": ({"host": H, "port": HTTP, "username": "admin", "password": "pw"},
             {"Galaxy-S24": ("wireless", "Asus5", "100", "5 GHz", True), "Living room Xbox": ("wired", "", None, "", True)}),
    "fritzbox": ({"host": H, "port": HTTP, "username": "", "password": "pw"},
                 {"iPad": ("wireless", "FRITZ!Box 7590", "36", "5 GHz", True), "PC": ("wired", "", None, "", True),
                  "Old": ("unknown", "", None, "", False)}),
}


@pytest.mark.parametrize("rtype", list(CASES))
async def test_backend(hass, fake_routers, rtype):
    data, expected = CASES[rtype]
    backend = make_backend(hass, {"router_type": rtype, "verify_ssl": False, **data})
    rows = finalize(await backend.async_fetch())
    rows = finalize(await backend.async_fetch())  # second poll: rates from counters, session reuse
    got = {r["name"]: (r["connection_type"], r["wifi"], r["channel"], r["band"], r["online"]) for r in rows}
    print(rtype, json.dumps(rows, indent=1, default=str))
    assert got == expected
    for r in rows:
        assert r["mac"] == r["mac"].lower() and ":" in r["mac"]


async def test_specific_values(hass, fake_routers):
    om = finalize(await make_backend(hass, {"router_type": "omada", **CASES["omada"][0]}).async_fetch())
    laptop = next(r for r in om if r["name"] == "laptop")
    assert (laptop["download"], laptop["upload"], laptop["connection"]) == (5000, 300, "EAP650")
    assert next(r for r in om if r["name"] == "Printer")["connection"] == "SG2008 · Port 3"
    opn = finalize(await make_backend(hass, {"router_type": "opnsense", **CASES["opnsense"][0]}).async_fetch())
    sonos = next(r for r in opn if r["name"] == "sonos-kitchen")
    assert (sonos["download"], sonos["upload"], sonos["vendor"]) == (10000, 1000, "Sonos")
    asus = finalize(await make_backend(hass, {"router_type": "asus", **CASES["asus"][0]}).async_fetch())
    assert next(r for r in asus if r["name"] == "Galaxy-S24")["download"] is None
    mt = finalize(await make_backend(hass, {"router_type": "mikrotik", **CASES["mikrotik"][0]}).async_fetch())
    assert next(r for r in mt if r["name"] == "Basement NAS")["connection"] == "hAP-ax3 · ether3"


@pytest.mark.parametrize("rtype", list(CASES))
async def test_bad_password(hass, fake_routers, rtype):
    from custom_components.network_devices.backends import AuthError
    data = {**CASES[rtype][0], "password": "wrong", "router_type": rtype}
    if rtype == "opnsense":
        data["password"] = "nope"
    if rtype == "fritzbox":
        pytest.skip("mock FRITZ!Box accepts any digest")
    with pytest.raises(AuthError):
        await make_backend(hass, data).async_fetch()


async def test_scan_backend(hass, tmp_path, monkeypatch):
    from custom_components.network_devices.backends import scan
    arp = tmp_path / "arp"
    arp.write_text("IP address HW type Flags HW address Mask Device\n"
                   "127.0.0.2 0x1 0x2 00:17:88:aa:bb:cc * eth0\n"
                   "127.0.0.3 0x1 0x0 00:00:00:00:00:00 * eth0\n"
                   "10.9.9.9 0x1 0x2 00:17:88:dd:ee:ff * eth0\n")
    real_read = scan.read_arp
    monkeypatch.setattr(scan, "read_arp", lambda: real_read(str(arp)))
    monkeypatch.setattr(scan, "_probe", lambda nets: None)
    monkeypatch.setattr(scan.asyncio, "sleep", _nosleep)
    from custom_components.network_devices import async_load_vendor_db
    await async_load_vendor_db()
    rows = finalize(await make_backend(hass, {"router_type": "scan", "subnets": "127.0.0.0/24"}).async_fetch())
    assert [r["ip"] for r in rows] == ["127.0.0.2"]
    assert rows[0]["vendor"]  # 00:17:88 = Philips Lighting, from the bundled OUI database
    print(rows)


async def _nosleep(_):
    return None


async def test_full_setup_via_ui(hass, enable_custom_integrations, fake_routers, hass_ws_client):
    assert await async_setup_component(hass, "http", {})
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    assert r["type"] == "menu" and "fritzbox" in r["menu_options"]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "mikrotik"})
    assert r["step_id"] == "mikrotik"
    bad = {"host": H, "port": HTTP, "username": "admin", "password": "x", "ssl": False, "verify_ssl": False}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], bad)
    assert r["errors"] == {"base": "invalid_auth"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**bad, "password": "pw"})
    assert r["type"] == "create_entry", r
    assert r["title"] == "MikroTik (127.0.0.1)"

    # second source: UniFi
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "unifi"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**CASES["unifi"][0], "verify_ssl": False})
    assert r["type"] == "create_entry", r
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries("network_devices")
    assert all(e.state is ConfigEntryState.LOADED for e in entries)
    assert "network-devices" in hass.data["frontend_panels"]
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "network_devices/list"})
    msg = await ws.receive_json()
    assert msg["success"]
    assert {d["source"] for d in msg["result"]["devices"]} == {"MikroTik (127.0.0.1)", "Ubiquiti UniFi (127.0.0.1)"}
    assert len(msg["result"]["sources"]) == 2

    # Existing v1 entries (no router_type) keep working as UniFi
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    old = MockConfigEntry(domain="network_devices", data={**CASES["unifi"][0], "verify_ssl": False, "site": "default"},
                          unique_id="legacy")
    old.add_to_hass(hass)
    assert await hass.config_entries.async_setup(old.entry_id)
    assert old.state is ConfigEntryState.LOADED

    for e in hass.config_entries.async_entries("network_devices"):
        assert await hass.config_entries.async_unload(e.entry_id)
    assert "network-devices" not in hass.data["frontend_panels"]


async def test_error_details_shown(hass, enable_custom_integrations, fake_routers):
    """The setup form explains *why* it failed."""
    assert await async_setup_component(hass, "http", {})
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "unifi"})
    base = {**CASES["unifi"][0], "verify_ssl": False}
    # Nothing listening on this port
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "port": 18999})
    assert r["errors"] == {"base": "cannot_connect"}
    assert "refused" in r["description_placeholders"]["error_detail"].lower()
    # Account that needs two-factor login (UniFi OS answers 499)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "password": "mfa"})
    assert r["errors"] == {"base": "invalid_auth"}
    assert "two-factor" in r["description_placeholders"]["error_detail"]
