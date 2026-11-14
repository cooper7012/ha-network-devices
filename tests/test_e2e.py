import ssl, pathlib
from aiohttp import web
import pytest
from homeassistant.setup import async_setup_component
from homeassistant.config_entries import ConfigEntryState

HERE = pathlib.Path(__file__).parent.parent

@pytest.fixture
async def unifi(socket_enabled):
    app = web.Application()
    async def root(r): raise web.HTTPFound("/manage")
    app.router.add_get("/", root)
    async def login(r):
        b = await r.json()
        if b["password"] != "pw": return web.json_response({}, status=400)
        resp = web.json_response({"data": []}); resp.set_cookie("unifises", "x"); return resp
    app.router.add_post("/api/login", login)
    raw = {"stat/sta": [{"mac":"3c:22:fb:00:11:22","hostname":"Robs-iPhone","oui":"Apple","ap_mac":"aa","essid":"HomeNet","channel":36,"radio":"na","ip":"192.168.1.45","tx_bytes-r":1000,"rx_bytes-r":10}],
           "rest/user": [{"mac":"00:11:32:aa:bb:cc","name":"NAS","last_ip":"192.168.1.20"}], "stat/device": [{"mac":"aa","name":"Living Room AP"}], "rest/networkconf": []}
    for k, v in raw.items():
        async def h(r, v=v):
            if "unifises" not in r.cookies: return web.json_response({}, status=401)
            return web.json_response({"data": v})
        app.router.add_get(f"/api/s/default/{k}", h)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH); ctx.load_cert_chain(HERE/"cert.pem", HERE/"key.pem")
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18443, ssl_context=ctx).start()
    yield
    await runner.cleanup()

async def test_full(hass, enable_custom_integrations, unifi, hass_ws_client):
    assert await async_setup_component(hass, "http", {})
    r = await hass.config_entries.flow.async_init("network_devices", context={"source": "user"})
    base = dict(host="127.0.0.1", port=18443, username="u", site="default", verify_ssl=False)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "password": "bad"})
    assert r["errors"] == {"base": "invalid_auth"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {**base, "password": "pw"})
    assert r["type"] == "create_entry", r
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("network_devices")[0]
    assert entry.state is ConfigEntryState.LOADED
    assert "network-devices" in hass.data["frontend_panels"]
    st = [s for s in hass.states.async_all("sensor")]
    print([(s.entity_id, s.state, dict(s.attributes)) for s in st])
    assert st[0].state == "1"
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "network_devices/list"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    print(msg["result"])
    assert {d["name"] for d in msg["result"]["devices"]} == {"Robs-iPhone", "NAS"}
    # options flow
    r = await hass.config_entries.options.async_init(entry.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_interval": 30, "swap_direction": True})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert "network-devices" not in hass.data["frontend_panels"]
