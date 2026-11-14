// Network Devices sidebar panel for Home Assistant (no build step required).

const COLUMNS = [
  { key: "name", label: "Name" },
  { key: "mac", label: "MAC Address" },
  { key: "vendor", label: "Vendor" },
  { key: "connection", label: "Connection" },
  { key: "network", label: "Network" },
  { key: "wifi", label: "WiFi" },
  { key: "channel", label: "Channel" },
  { key: "ip", label: "IP Address" },
  { key: "activity", label: "Activity" },
];

const POLL_MS = 5000;
const STORE_KEY = "network-devices-panel";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function fmtRate(bytesPerSec) {
  const bits = (bytesPerSec || 0) * 8;
  if (bits < 1000) return `${Math.round(bits)} bps`;
  const units = ["Kbps", "Mbps", "Gbps"];
  let v = bits / 1000, i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

function fmtAgo(seconds) {
  if (seconds == null) return "";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

const ipValue = (ip) => {
  const p = String(ip || "").split(".").map(Number);
  return p.length === 4 && p.every((n) => !isNaN(n)) ? ((p[0] * 256 + p[1]) * 256 + p[2]) * 256 + p[3] : Infinity;
};

const SORTERS = {
  ip: (d) => ipValue(d.ip),
  channel: (d) => (d.channel == null ? Infinity : d.channel),
  activity: (d) => -((d.download || 0) + (d.upload || 0)),
};

class NetworkDevicesPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._sources = [];
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { /* ignore */ }
    this._state = { sort: "activity", dir: 1, query: "", showOffline: false, type: "all", ...saved, query: "" };
  }

  set hass(hass) {
    this._hass = hass;
    if (this._menuBtn) this._menuBtn.hass = hass;
    if (!this._built) this._build();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menuBtn) this._menuBtn.narrow = narrow;
  }

  connectedCallback() {
    this._timer = setInterval(() => { if (!document.hidden) this._fetch(); }, POLL_MS);
    this._onVis = () => { if (!document.hidden) this._fetch(); };
    document.addEventListener("visibilitychange", this._onVis);
    if (this._built) this._fetch();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._onVis);
  }

  _save() {
    const { sort, dir, showOffline, type } = this._state;
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ sort, dir, showOffline, type })); } catch (e) { /* ignore */ }
  }

  async _fetch(refresh = false) {
    if (!this._hass) return;
    try {
      const res = await this._hass.callWS({ type: "network_devices/list", refresh });
      this._devices = res.devices || [];
      this._sources = res.sources || [];
      this._error = null;
      this._updated = new Date();
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._renderBody();
  }

  _build() {
    this._built = true;
    const s = this._state;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; background:var(--primary-background-color); color:var(--primary-text-color);
                font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        header { display:flex; align-items:center; gap:8px; height:56px; padding:0 12px; box-sizing:border-box;
                 background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff);
                 border-bottom:var(--app-header-border-bottom, none); }
        header h1 { font-size:20px; font-weight:400; margin:0 0 0 4px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        header button { background:none; border:0; color:inherit; cursor:pointer; padding:8px; border-radius:50%; display:flex; }
        header button:hover { background:rgba(255,255,255,.12); }
        main { padding:16px; box-sizing:border-box; max-width:100%; }
        .card { background:var(--card-background-color, #fff); border-radius:var(--ha-card-border-radius, 12px);
                box-shadow:var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); overflow:hidden; }
        .toolbar { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--divider-color); }
        .toolbar input[type=search] { flex:1 1 220px; min-width:0; padding:8px 12px; font:inherit; color:inherit; border-radius:8px;
                border:1px solid var(--divider-color); background:var(--primary-background-color); }
        .toolbar select { padding:7px 8px; font:inherit; color:inherit; border-radius:8px; border:1px solid var(--divider-color);
                background:var(--primary-background-color); }
        .toolbar label { display:flex; align-items:center; gap:6px; font-size:14px; cursor:pointer; white-space:nowrap; }
        .stats { display:flex; flex-wrap:wrap; gap:4px 16px; padding:8px 16px; font-size:13px; color:var(--secondary-text-color);
                 border-bottom:1px solid var(--divider-color); }
        .stats b { color:var(--primary-text-color); font-weight:500; }
        .err { padding:10px 16px; background:var(--error-color, #db4437); color:#fff; font-size:14px; }
        .scroll { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        th { text-align:left; font-weight:500; font-size:13px; color:var(--secondary-text-color); padding:10px 12px; white-space:nowrap;
             cursor:pointer; user-select:none; border-bottom:1px solid var(--divider-color); position:sticky; top:0;
             background:var(--card-background-color, #fff); }
        th:hover { color:var(--primary-text-color); }
        th .arrow { display:inline-block; width:12px; color:var(--primary-color); }
        td { padding:8px 12px; border-bottom:1px solid var(--divider-color); white-space:nowrap; vertical-align:middle; }
        tr:last-child td { border-bottom:0; }
        tbody tr:hover { background:var(--secondary-background-color); }
        tr.offline td { color:var(--disabled-text-color, #999); }
        .name { display:flex; align-items:center; gap:8px; font-weight:500; }
        .dot { width:8px; height:8px; border-radius:50%; background:var(--success-color, #43a047); flex:none; }
        tr.offline .dot { background:var(--disabled-text-color, #999); }
        .sub { font-size:12px; color:var(--secondary-text-color); font-weight:400; }
        .mono { font-family:var(--code-font-family, ui-monospace, monospace); font-size:13px; }
        a.ip { color:var(--primary-color); text-decoration:none; }
        a.ip:hover { text-decoration:underline; }
        .act { display:grid; grid-template-columns:auto auto; gap:0 6px; font-size:13px; font-variant-numeric:tabular-nums; }
        .act span:nth-child(odd) { color:var(--secondary-text-color); }
        .empty { padding:32px; text-align:center; color:var(--secondary-text-color); }
        ha-icon { --mdc-icon-size:18px; color:var(--secondary-text-color); }
        @media (max-width:600px) { main { padding:8px; } }
      </style>
      <header>
        <span id="menu"></span>
        <h1>Network Devices</h1>
        <button id="refresh" title="Refresh now"><ha-icon icon="mdi:refresh" style="color:inherit"></ha-icon></button>
      </header>
      <main>
        <div class="card">
          <div class="toolbar">
            <input type="search" id="q" placeholder="Search name, MAC, IP, vendor…">
            <select id="type">
              <option value="all">All connections</option>
              <option value="wireless">WiFi only</option>
              <option value="wired">Wired only</option>
            </select>
            <label><input type="checkbox" id="offline"> Show offline</label>
          </div>
          <div class="stats" id="stats"></div>
          <div id="errors"></div>
          <div class="scroll"><table>
            <thead><tr>${COLUMNS.map((c) => `<th data-key="${c.key}">${c.label}<span class="arrow"></span></th>`).join("")}</tr></thead>
            <tbody id="body"><tr><td colspan="${COLUMNS.length}" class="empty">Loading…</td></tr></tbody>
          </table></div>
        </div>
      </main>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    this._menuBtn = document.createElement("ha-menu-button");
    this._menuBtn.hass = this._hass;
    this._menuBtn.narrow = this._narrow;
    $("menu").appendChild(this._menuBtn);

    $("q").addEventListener("input", (e) => { s.query = e.target.value; this._renderBody(); });
    $("type").value = s.type;
    $("type").addEventListener("change", (e) => { s.type = e.target.value; this._save(); this._renderBody(); });
    $("offline").checked = s.showOffline;
    $("offline").addEventListener("change", (e) => { s.showOffline = e.target.checked; this._save(); this._renderBody(); });
    $("refresh").addEventListener("click", () => this._fetch(true));
    this.shadowRoot.querySelectorAll("th").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.key;
        s.dir = s.sort === key ? -s.dir : 1;
        s.sort = key;
        this._save();
        this._renderBody();
      })
    );
    this._fetch();
  }

  _rows() {
    const s = this._state;
    const q = s.query.trim().toLowerCase();
    let rows = this._devices.filter((d) =>
      (s.showOffline || d.online) &&
      (s.type === "all" || d.connection_type === s.type) &&
      (!q || [d.name, d.hostname, d.mac, d.ip, d.vendor, d.connection, d.network, d.wifi]
        .some((v) => String(v || "").toLowerCase().includes(q)))
    );
    const get = SORTERS[s.sort] || ((d) => String(d[s.sort] ?? "").toLowerCase() || "￿");
    return rows.sort((a, b) => {
      const x = get(a), y = get(b);
      return (x < y ? -1 : x > y ? 1 : 0) * s.dir || a.name.localeCompare(b.name);
    });
  }

  _row(d) {
    const ip = d.ip
      ? `<a class="ip mono" href="http://${esc(d.ip)}" target="_blank" rel="noopener noreferrer">${esc(d.ip)}</a>`
      : "";
    const connIcon = d.connection_type === "wired" ? "mdi:ethernet" : "mdi:wifi";
    const channel = d.channel != null ? `${esc(d.channel)}<div class="sub">${esc(d.band)}</div>` : "";
    const activity = d.online
      ? `<div class="act"><span>↓</span><span>${fmtRate(d.download)}</span><span>↑</span><span>${fmtRate(d.upload)}</span></div>`
      : `<span class="sub">${esc(fmtAgo(d.seconds_since_seen))}</span>`;
    const signal = d.signal != null ? `<div class="sub">${esc(d.signal)} dBm</div>` : "";
    const sub = d.hostname && d.hostname !== d.name ? `<div class="sub">${esc(d.hostname)}</div>` : "";
    return `<tr class="${d.online ? "" : "offline"}">
      <td><div class="name"><span class="dot"></span><div>${esc(d.name)}${sub}</div></div></td>
      <td class="mono">${esc(d.mac)}</td>
      <td>${esc(d.vendor)}</td>
      <td><div class="name" style="font-weight:400"><ha-icon icon="${connIcon}"></ha-icon><div>${esc(d.connection)}${signal}</div></div></td>
      <td>${esc(d.network)}</td>
      <td>${esc(d.wifi)}</td>
      <td>${channel}</td>
      <td>${ip}</td>
      <td>${activity}</td>
    </tr>`;
  }

  _renderBody() {
    if (!this._built) return;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const s = this._state;

    this.shadowRoot.querySelectorAll("th").forEach((th) => {
      th.querySelector(".arrow").textContent = th.dataset.key === s.sort ? (s.dir === 1 ? "▲" : "▼") : "";
    });

    const online = this._devices.filter((d) => d.online);
    const down = online.reduce((t, d) => t + (d.download || 0), 0);
    const up = online.reduce((t, d) => t + (d.upload || 0), 0);
    const rows = this._rows();
    $("stats").innerHTML = `
      <span><b>${online.length}</b> online</span>
      <span><b>${online.filter((d) => d.connection_type === "wireless").length}</b> WiFi</span>
      <span><b>${online.filter((d) => d.connection_type === "wired").length}</b> wired</span>
      <span><b>${this._devices.length - online.length}</b> offline</span>
      <span>↓ <b>${fmtRate(down)}</b> ↑ <b>${fmtRate(up)}</b></span>
      <span>Showing <b>${rows.length}</b></span>
      ${this._updated ? `<span>Updated ${this._updated.toLocaleTimeString()}</span>` : ""}`;

    const errs = [];
    if (this._error) errs.push(this._error);
    this._sources.filter((x) => !x.ok).forEach((x) => errs.push(`${x.title}: ${x.error || "update failed"}`));
    $("errors").innerHTML = errs.map((e) => `<div class="err">${esc(e)}</div>`).join("");

    $("body").innerHTML = rows.length
      ? rows.map((d) => this._row(d)).join("")
      : `<tr><td colspan="${COLUMNS.length}" class="empty">${
          this._devices.length ? "No devices match your filters." : this._error ? "Could not load devices." : "No devices yet."
        }</td></tr>`;
  }
}

customElements.define("network-devices-panel", NetworkDevicesPanel);
