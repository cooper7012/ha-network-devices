// Network Devices sidebar panel for Home Assistant (no build step required).

const COLUMNS = [
  { key: "hostname", label: "Hostname" },
  { key: "mac", label: "MAC Address" },
  { key: "vendor", label: "Vendor" },
  { key: "ip", label: "IP Address" },
  { key: "seen", label: "Last Seen" },
];

const POLL_MS = 10000;
const STORE_KEY = "network-devices-panel";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

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
  seen: (d) => (d.online ? -1 : d.seconds_since_seen ?? Infinity),
};

class NetworkDevicesPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._errors = [];
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); } catch (e) { /* ignore */ }
    this._state = { sort: "ip", dir: 1, showOffline: false, ...saved, query: "" };
    if (!COLUMNS.some((c) => c.key === this._state.sort)) this._state.sort = "ip";
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
    const { sort, dir, showOffline } = this._state;
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ sort, dir, showOffline })); } catch (e) { /* ignore */ }
  }

  async _fetch(refresh = false) {
    if (!this._hass) return;
    if (refresh) this._setBusy(true);
    try {
      const res = await this._hass.callWS({ type: "network_devices/list", refresh });
      this._devices = res.devices || [];
      this._errors = res.errors || [];
      this._error = null;
      this._updated = new Date();
    } catch (err) {
      this._error = err.message || String(err);
    }
    if (refresh) this._setBusy(false);
    this._renderBody();
  }

  _setBusy(busy) {
    const btn = this.shadowRoot.getElementById("refresh");
    if (btn) { btn.disabled = busy; btn.classList.toggle("spin", busy); }
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
        header button.spin ha-icon { animation:spin 1s linear infinite; }
        @keyframes spin { to { transform:rotate(360deg); } }
        main { padding:16px; box-sizing:border-box; max-width:1200px; margin:0 auto; }
        .card { background:var(--card-background-color, #fff); border-radius:var(--ha-card-border-radius, 12px);
                box-shadow:var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); overflow:hidden; }
        .toolbar { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--divider-color); }
        .toolbar input[type=search] { flex:1 1 220px; min-width:0; padding:8px 12px; font:inherit; color:inherit; border-radius:8px;
                border:1px solid var(--divider-color); background:var(--primary-background-color); }
        .toolbar label { display:flex; align-items:center; gap:6px; font-size:14px; cursor:pointer; white-space:nowrap; }
        .stats { display:flex; flex-wrap:wrap; gap:4px 16px; padding:8px 16px; font-size:13px; color:var(--secondary-text-color);
                 border-bottom:1px solid var(--divider-color); }
        .stats b { color:var(--primary-text-color); font-weight:500; }
        .err { padding:10px 16px; background:var(--error-color, #db4437); color:#fff; font-size:14px; }
        .scroll { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        th { text-align:left; font-weight:500; font-size:13px; color:var(--secondary-text-color); padding:10px 12px; white-space:nowrap;
             cursor:pointer; user-select:none; border-bottom:1px solid var(--divider-color); }
        th:hover { color:var(--primary-text-color); }
        th .arrow { display:inline-block; width:12px; color:var(--primary-color); }
        td { padding:9px 12px; border-bottom:1px solid var(--divider-color); white-space:nowrap; vertical-align:middle; }
        tr:last-child td { border-bottom:0; }
        tbody tr:hover { background:var(--secondary-background-color); }
        tr.offline td { color:var(--disabled-text-color, #999); }
        .host { display:flex; align-items:center; gap:8px; font-weight:500; }
        .dot { width:8px; height:8px; border-radius:50%; background:var(--success-color, #43a047); flex:none; }
        tr.offline .dot { background:var(--disabled-text-color, #999); }
        .none { color:var(--secondary-text-color); font-weight:400; font-style:italic; }
        .mono { font-family:var(--code-font-family, ui-monospace, monospace); font-size:13px; }
        a.ip { color:var(--primary-color); text-decoration:none; }
        a.ip:hover { text-decoration:underline; }
        .empty { padding:32px; text-align:center; color:var(--secondary-text-color); }
        @media (max-width:600px) { main { padding:8px; } }
      </style>
      <header>
        <span id="menu"></span>
        <h1>Network Devices</h1>
        <button id="refresh" title="Scan now"><ha-icon icon="mdi:refresh"></ha-icon></button>
      </header>
      <main>
        <div class="card">
          <div class="toolbar">
            <input type="search" id="q" placeholder="Search hostname, MAC, vendor or IP…">
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
    const rows = this._devices.filter((d) =>
      (s.showOffline || d.online) &&
      (!q || [d.hostname, d.mac, d.ip, d.vendor].some((v) => String(v || "").toLowerCase().includes(q)))
    );
    const get = SORTERS[s.sort] || ((d) => String(d[s.sort] ?? "").toLowerCase() || "￿");
    return rows.sort((a, b) => {
      const x = get(a), y = get(b);
      return (x < y ? -1 : x > y ? 1 : 0) * s.dir || ipValue(a.ip) - ipValue(b.ip);
    });
  }

  _row(d) {
    const host = d.hostname ? esc(d.hostname) : `<span class="none">Unknown</span>`;
    const ip = d.ip
      ? `<a class="ip mono" href="http://${esc(d.ip)}" target="_blank" rel="noopener noreferrer">${esc(d.ip)}</a>`
      : "";
    const seen = d.online ? "Online" : esc(fmtAgo(d.seconds_since_seen));
    return `<tr class="${d.online ? "" : "offline"}">
      <td><div class="host"><span class="dot"></span>${host}</div></td>
      <td class="mono">${esc(d.mac)}</td>
      <td>${esc(d.vendor)}</td>
      <td>${ip}</td>
      <td>${seen}</td>
    </tr>`;
  }

  _renderBody() {
    if (!this._built) return;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const s = this._state;

    this.shadowRoot.querySelectorAll("th").forEach((th) => {
      th.querySelector(".arrow").textContent = th.dataset.key === s.sort ? (s.dir === 1 ? "▲" : "▼") : "";
    });

    const online = this._devices.filter((d) => d.online).length;
    const rows = this._rows();
    $("stats").innerHTML = `
      <span><b>${online}</b> online</span>
      <span><b>${this._devices.length - online}</b> offline</span>
      <span>Showing <b>${rows.length}</b></span>
      ${this._updated ? `<span>Updated ${this._updated.toLocaleTimeString()}</span>` : ""}`;

    const errs = [...(this._error ? [this._error] : []), ...this._errors];
    $("errors").innerHTML = errs.map((e) => `<div class="err">${esc(e)}</div>`).join("");

    $("body").innerHTML = rows.length
      ? rows.map((d) => this._row(d)).join("")
      : `<tr><td colspan="${COLUMNS.length}" class="empty">${
          this._devices.length ? "No devices match your filters." : this._error ? "Could not load devices." : "No devices found yet."
        }</td></tr>`;
  }
}

customElements.define("network-devices-panel", NetworkDevicesPanel);
