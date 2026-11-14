// Network Devices sidebar panel for Home Assistant (no build step required).

const COLUMNS = [
  { key: "name", label: "Name" },
  { key: "mac", label: "MAC Address" },
  { key: "vendor", label: "Vendor" },
  { key: "ip", label: "IP Address" },
  { key: "tags", label: "Tags" },
  { key: "seen", label: "Last Seen" },
  { key: "edit", label: "", noSort: true },
];

const POLL_MS = 10000;
const LEGACY_STORE_KEY = "network-devices-panel";
const DEFAULT_FILTER = { query: "", tags: [], match: "any", untagged: false, show_offline: false, sort: "ip", dir: 1 };

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fold = (s) => String(s ?? "").toLocaleLowerCase();
const hasTag = (list, tag) => list.some((t) => fold(t) === fold(tag));

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
const displayName = (d) => d.name || d.hostname || "";

const SORTERS = {
  name: (d) => fold(displayName(d)) || "￿",
  ip: (d) => ipValue(d.ip),
  tags: (d) => (d.tags.length ? fold([...d.tags].sort().join(",")) : "￿"),
  seen: (d) => (d.online ? -1 : d.seconds_since_seen ?? Infinity),
};

const sameFilter = (a, b) => {
  const norm = (f) => JSON.stringify({ ...DEFAULT_FILTER, ...f, tags: [...(f.tags || [])].map(fold).sort(), name: undefined, filter_id: undefined, saved_id: undefined });
  return norm(a) === norm(b);
};

class NetworkDevicesPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._errors = [];
    this._tags = [];
    this._filters = [];
    this._f = { ...DEFAULT_FILTER };
    this._savedId = null;
    try { // carry over sort / show-offline from older versions until the server has a saved state
      const old = JSON.parse(localStorage.getItem(LEGACY_STORE_KEY) || "{}");
      if (old.sort && COLUMNS.some((c) => c.key === old.sort)) this._f.sort = old.sort;
      if (old.dir) this._f.dir = old.dir;
      if (typeof old.showOffline === "boolean") this._f.show_offline = old.showOffline;
    } catch (e) { /* ignore */ }
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

  // ---------------------------------------------------------------- data
  async _fetch(refresh = false) {
    if (!this._hass) return;
    if (refresh) this._setBusy(true);
    try {
      const res = await this._hass.callWS({ type: "network_devices/list", refresh });
      this._devices = res.devices || [];
      this._errors = res.errors || [];
      this._tags = res.tags || [];
      this._filters = res.filters || [];
      if (!this._restored) {
        this._restored = true;
        if (res.last_filter) {
          const { saved_id, ...flt } = res.last_filter;
          this._f = { ...DEFAULT_FILTER, ...flt };
          this._savedId = saved_id && this._filters.some((f) => f.filter_id === saved_id) ? saved_id : null;
        }
        this._syncControls();
      }
      this._error = null;
      this._updated = new Date();
    } catch (err) {
      this._error = err.message || String(err);
    }
    if (refresh) this._setBusy(false);
    this._render();
  }

  async _ws(msg) {
    try {
      return await this._hass.callWS(msg);
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
      throw err;
    }
  }

  // Remember the current filter for this Home Assistant user (debounced).
  _filterChanged() {
    this._render();
    clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => {
      this._hass?.callWS({ type: "network_devices/set_last_filter", filter: this._f, saved_id: this._savedId }).catch(() => {});
    }, 600);
  }

  // ---------------------------------------------------------------- UI shell
  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; background:var(--primary-background-color); color:var(--primary-text-color);
                font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); --chip:var(--secondary-background-color, #eee); }
        header { display:flex; align-items:center; gap:8px; height:56px; padding:0 12px; box-sizing:border-box;
                 background:var(--app-header-background-color, var(--primary-color)); color:var(--app-header-text-color, #fff);
                 border-bottom:var(--app-header-border-bottom, none); }
        header h1 { font-size:20px; font-weight:400; margin:0 0 0 4px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        button { font:inherit; color:inherit; cursor:pointer; }
        .icon-btn { background:none; border:0; padding:8px; border-radius:50%; display:inline-flex; color:inherit; }
        .icon-btn:hover { background:rgba(127,127,127,.15); }
        .icon-btn:disabled { opacity:.4; cursor:default; }
        header .icon-btn.spin ha-icon { animation:spin 1s linear infinite; }
        @keyframes spin { to { transform:rotate(360deg); } }
        main { padding:16px; box-sizing:border-box; max-width:1300px; margin:0 auto; }
        .card { background:var(--card-background-color, #fff); border-radius:var(--ha-card-border-radius, 12px);
                box-shadow:var(--ha-card-box-shadow, none); border:1px solid var(--divider-color); }
        .toolbar { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; padding:12px 16px; border-bottom:1px solid var(--divider-color); }
        input[type=search], input[type=text], select { padding:8px 12px; font:inherit; color:inherit; border-radius:8px; min-width:0;
                border:1px solid var(--divider-color); background:var(--primary-background-color); box-sizing:border-box; }
        input[type=search] { flex:1 1 220px; }
        label.check { display:flex; align-items:center; gap:6px; font-size:14px; cursor:pointer; white-space:nowrap; }
        .btn { display:inline-flex; align-items:center; gap:6px; padding:7px 12px; border-radius:8px; font-size:14px;
               border:1px solid var(--divider-color); background:var(--primary-background-color); white-space:nowrap; }
        .btn:hover { border-color:var(--primary-color); }
        .btn.primary { background:var(--primary-color); color:var(--text-primary-color, #fff); border-color:var(--primary-color); }
        .btn.danger { color:var(--error-color, #db4437); }
        .btn .count { background:var(--primary-color); color:var(--text-primary-color, #fff); border-radius:10px; padding:0 7px; font-size:12px; }
        .btn ha-icon, .icon-btn ha-icon { --mdc-icon-size:20px; }
        .saved { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
        .saved select { max-width:220px; }
        .modified { font-size:12px; color:var(--secondary-text-color); font-style:italic; }
        .active { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:0 16px 10px; }
        .active:empty { display:none; }
        .chip { display:inline-flex; align-items:center; gap:4px; padding:2px 10px; border-radius:12px; font-size:12px; line-height:20px;
                background:var(--chip); border:1px solid var(--divider-color); white-space:nowrap; }
        button.chip { cursor:pointer; }
        button.chip:hover { border-color:var(--primary-color); }
        .chip.on { background:var(--primary-color); color:var(--text-primary-color, #fff); border-color:var(--primary-color); }
        .chip .x { cursor:pointer; opacity:.7; font-size:14px; line-height:1; background:none; border:0; padding:0 0 0 2px; }
        .chip .x:hover { opacity:1; }
        .pop-wrap { position:relative; }
        .pop { position:absolute; z-index:5; top:calc(100% + 6px); left:0; width:300px; max-width:calc(100vw - 40px);
               background:var(--card-background-color, #fff); border:1px solid var(--divider-color); border-radius:12px;
               box-shadow:0 8px 24px rgba(0,0,0,.25); padding:12px; }
        .pop[hidden] { display:none; }
        .pop h3 { margin:0 0 8px; font-size:14px; font-weight:500; }
        .seg { display:inline-flex; border:1px solid var(--divider-color); border-radius:8px; overflow:hidden; }
        .seg button { border:0; background:none; padding:4px 12px; font-size:13px; }
        .seg button.on { background:var(--primary-color); color:var(--text-primary-color, #fff); }
        .taglist { max-height:280px; overflow:auto; margin:8px -4px 0; }
        .tagrow { display:flex; align-items:center; gap:4px; padding:2px 4px; border-radius:6px; }
        .tagrow:hover { background:var(--secondary-background-color); }
        .tagrow label { flex:1; display:flex; align-items:center; gap:8px; padding:4px 0; cursor:pointer; font-size:14px; min-width:0; }
        .tagrow label span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .tagrow .n { color:var(--secondary-text-color); font-size:12px; }
        .tagrow .icon-btn { padding:4px; } .tagrow .icon-btn ha-icon { --mdc-icon-size:16px; }
        .pop .hint { font-size:12px; color:var(--secondary-text-color); margin-top:8px; }
        .stats { display:flex; flex-wrap:wrap; gap:4px 16px; padding:8px 16px; font-size:13px; color:var(--secondary-text-color);
                 border-top:1px solid var(--divider-color); border-bottom:1px solid var(--divider-color); }
        .stats b { color:var(--primary-text-color); font-weight:500; }
        .err { padding:10px 16px; background:var(--error-color, #db4437); color:#fff; font-size:14px; }
        .scroll { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        th { text-align:left; font-weight:500; font-size:13px; color:var(--secondary-text-color); padding:10px 12px; white-space:nowrap;
             cursor:pointer; user-select:none; border-bottom:1px solid var(--divider-color); }
        th.nosort { cursor:default; }
        th:hover:not(.nosort) { color:var(--primary-text-color); }
        th .arrow { display:inline-block; width:12px; color:var(--primary-color); }
        td { padding:7px 12px; border-bottom:1px solid var(--divider-color); white-space:nowrap; vertical-align:middle; }
        tr:last-child td { border-bottom:0; }
        tbody tr:hover { background:var(--secondary-background-color); }
        tr.offline td { color:var(--disabled-text-color, #999); }
        .namecell { display:flex; align-items:center; gap:8px; }
        .namecell .main { font-weight:500; }
        .sub { font-size:12px; color:var(--secondary-text-color); }
        .dot { width:8px; height:8px; border-radius:50%; background:var(--success-color, #43a047); flex:none; }
        tr.offline .dot { background:var(--disabled-text-color, #999); }
        .none { color:var(--secondary-text-color); font-weight:400; font-style:italic; }
        .mono { font-family:var(--code-font-family, ui-monospace, monospace); font-size:13px; }
        a.ip { color:var(--primary-color); text-decoration:none; }
        a.ip:hover { text-decoration:underline; }
        td.tags { white-space:normal; min-width:120px; }
        td.tags .chip { margin:1px 2px 1px 0; }
        .empty { padding:32px; text-align:center; color:var(--secondary-text-color); }
        dialog { border:0; border-radius:16px; padding:0; width:min(440px, calc(100vw - 32px)); color:var(--primary-text-color);
                 background:var(--card-background-color, #fff); box-shadow:0 12px 40px rgba(0,0,0,.4); }
        dialog::backdrop { background:rgba(0,0,0,.45); }
        .dlg { padding:20px; display:flex; flex-direction:column; gap:14px; }
        .dlg h2 { margin:0; font-size:20px; font-weight:400; }
        .dlg .meta { font-size:13px; color:var(--secondary-text-color); margin-top:-8px; }
        .dlg label.field { display:flex; flex-direction:column; gap:6px; font-size:13px; color:var(--secondary-text-color); }
        .dlg input[type=text] { width:100%; color:var(--primary-text-color); }
        .tag-editor { display:flex; flex-wrap:wrap; gap:6px; align-items:center; padding:6px; border:1px solid var(--divider-color);
                      border-radius:8px; background:var(--primary-background-color); }
        .tag-editor input { flex:1 1 120px; border:0 !important; background:none !important; padding:4px !important; outline:none; }
        .suggest { display:flex; flex-wrap:wrap; gap:6px; }
        .suggest:empty { display:none; }
        .dlg .actions { display:flex; justify-content:flex-end; gap:8px; margin-top:4px; }
        @media (max-width:600px) {
          main { padding:8px; } .toolbar { padding:10px; position:relative; } .saved select { max-width:150px; }
          .pop-wrap { position:static; } .pop { left:10px; right:10px; width:auto; max-width:none; }
        }
      </style>
      <header>
        <span id="menu"></span>
        <h1>Network Devices</h1>
        <button class="icon-btn" id="refresh" title="Scan now"><ha-icon icon="mdi:refresh"></ha-icon></button>
      </header>
      <main>
        <div class="card">
          <div class="toolbar">
            <input type="search" id="q" placeholder="Search name, MAC, vendor, IP or tag…">
            <div class="pop-wrap">
              <button class="btn" id="tagsBtn"><ha-icon icon="mdi:tag-multiple-outline"></ha-icon>Tags <span class="count" id="tagCount" hidden></span></button>
              <div class="pop" id="tagPop" hidden>
                <h3>Show devices with</h3>
                <div class="seg" id="match"><button data-v="any">Any selected tag</button><button data-v="all">All selected tags</button></div>
                <div class="taglist" id="tagList"></div>
                <div class="hint">Add tags to a device with its ✎ button.</div>
              </div>
            </div>
            <label class="check"><input type="checkbox" id="offline"> Show offline</label>
            <div class="saved">
              <select id="savedSel" title="Saved filters"></select>
              <span class="modified" id="modified" hidden>modified</span>
              <button class="btn" id="saveBtn" title="Save the current filter"><ha-icon icon="mdi:content-save-outline"></ha-icon>Save</button>
              <button class="icon-btn" id="delSaved" title="Delete this saved filter"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
              <button class="btn" id="clearBtn" title="Clear search and tag filters">Clear</button>
            </div>
          </div>
          <div class="active" id="active"></div>
          <div class="stats" id="stats"></div>
          <div id="errors"></div>
          <div class="scroll"><table>
            <thead><tr>${COLUMNS.map((c) => `<th data-key="${c.key}" class="${c.noSort ? "nosort" : ""}">${c.label}<span class="arrow"></span></th>`).join("")}</tr></thead>
            <tbody id="body"><tr><td colspan="${COLUMNS.length}" class="empty">Loading…</td></tr></tbody>
          </table></div>
        </div>
      </main>

      <dialog id="editDlg"><form method="dialog" class="dlg" id="editForm">
        <h2>Edit device</h2>
        <div class="meta" id="editMeta"></div>
        <label class="field">Custom name
          <input type="text" id="editName" maxlength="64" autocomplete="off">
        </label>
        <label class="field">Tags
          <div class="tag-editor" id="tagEditor"><input type="text" id="tagInput" maxlength="32" placeholder="Type a tag, press Enter" autocomplete="off"></div>
        </label>
        <div class="suggest" id="suggest"></div>
        <div class="actions">
          <button type="button" class="btn" id="editCancel">Cancel</button>
          <button type="submit" class="btn primary" id="editSave">Save</button>
        </div>
      </form></dialog>

      <dialog id="askDlg"><form method="dialog" class="dlg" id="askForm">
        <h2 id="askTitle"></h2>
        <div class="meta" id="askText"></div>
        <label class="field" id="askField"><span id="askLabel"></span><input type="text" id="askInput" maxlength="64" autocomplete="off"></label>
        <div class="actions">
          <button type="button" class="btn" id="askCancel">Cancel</button>
          <button type="submit" class="btn primary" id="askOk">OK</button>
        </div>
      </form></dialog>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    this.$ = $;
    this._menuBtn = document.createElement("ha-menu-button");
    this._menuBtn.hass = this._hass;
    this._menuBtn.narrow = this._narrow;
    $("menu").appendChild(this._menuBtn);

    $("q").addEventListener("input", (e) => { this._f.query = e.target.value; this._filterChanged(); });
    $("offline").addEventListener("change", (e) => { this._f.show_offline = e.target.checked; this._filterChanged(); });
    $("refresh").addEventListener("click", () => this._fetch(true));
    $("clearBtn").addEventListener("click", () => {
      Object.assign(this._f, { query: "", tags: [], untagged: false });
      this._savedId = null;
      this._syncControls();
      this._filterChanged();
    });

    // Tag filter popover
    $("tagsBtn").addEventListener("click", (e) => { e.stopPropagation(); $("tagPop").hidden = !$("tagPop").hidden; });
    $("tagPop").addEventListener("click", (e) => e.stopPropagation());
    this.shadowRoot.addEventListener("click", () => { $("tagPop").hidden = true; });
    $("match").addEventListener("click", (e) => {
      const v = e.target.closest("button")?.dataset.v;
      if (v) { this._f.match = v; this._filterChanged(); }
    });
    $("tagList").addEventListener("change", (e) => {
      const cb = e.target;
      if (cb.dataset.untagged !== undefined) this._f.untagged = cb.checked;
      else if (cb.dataset.tag !== undefined) this._toggleTag(cb.dataset.tag, cb.checked);
      this._filterChanged();
    });
    $("tagList").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-act]");
      if (!btn) return;
      if (btn.dataset.act === "rename") this._renameTag(btn.dataset.tag);
      if (btn.dataset.act === "delete") this._deleteTag(btn.dataset.tag);
    });
    $("active").addEventListener("click", (e) => {
      const x = e.target.closest("[data-remove]");
      if (!x) return;
      if (x.dataset.remove === "__untagged") this._f.untagged = false;
      else this._toggleTag(x.dataset.remove, false);
      this._filterChanged();
    });

    // Saved filters
    $("savedSel").addEventListener("change", (e) => {
      const f = this._filters.find((x) => x.filter_id === e.target.value);
      this._savedId = f ? f.filter_id : null;
      if (f) {
        const { name, filter_id, ...flt } = f;
        this._f = { ...DEFAULT_FILTER, ...flt };
        this._syncControls();
      }
      this._filterChanged();
    });
    $("saveBtn").addEventListener("click", () => this._saveFilter());
    $("delSaved").addEventListener("click", () => this._deleteFilter());

    // Table
    this.shadowRoot.querySelector("thead").addEventListener("click", (e) => {
      const th = e.target.closest("th");
      if (!th || th.classList.contains("nosort")) return;
      this._f.dir = this._f.sort === th.dataset.key ? -this._f.dir : 1;
      this._f.sort = th.dataset.key;
      this._filterChanged();
    });
    $("body").addEventListener("click", (e) => {
      const edit = e.target.closest("[data-edit]");
      if (edit) return this._openEditor(edit.dataset.edit);
      const chip = e.target.closest("button[data-filter-tag]");
      if (chip) {
        this._toggleTag(chip.dataset.filterTag, !hasTag(this._f.tags, chip.dataset.filterTag));
        this._filterChanged();
      }
    });

    this._setupEditor();
    this._setupAsk();
    this._syncControls();
    this._fetch();
  }

  _setBusy(busy) {
    const btn = this.$("refresh");
    btn.disabled = busy;
    btn.classList.toggle("spin", busy);
  }

  _toggleTag(tag, on) {
    this._f.tags = this._f.tags.filter((t) => fold(t) !== fold(tag));
    if (on) this._f.tags.push(tag);
  }

  _syncControls() {
    if (!this._built) return;
    this.$("q").value = this._f.query;
    this.$("offline").checked = this._f.show_offline;
  }

  // ---------------------------------------------------------------- filtering
  _matchesTags(d) {
    const sel = this._f.tags;
    if (!sel.length && !this._f.untagged) return true;
    if (this._f.untagged && !d.tags.length) return true;
    if (!sel.length) return false;
    return this._f.match === "all" ? sel.every((t) => hasTag(d.tags, t)) : sel.some((t) => hasTag(d.tags, t));
  }

  _rows() {
    const f = this._f;
    const q = fold(f.query.trim());
    const rows = this._devices.filter((d) =>
      (f.show_offline || d.online) &&
      this._matchesTags(d) &&
      (!q || [d.name, d.hostname, d.mac, d.ip, d.vendor, ...d.tags].some((v) => fold(v).includes(q)))
    );
    const get = SORTERS[f.sort] || ((d) => fold(d[f.sort]) || "￿");
    return rows.sort((a, b) => {
      const x = get(a), y = get(b);
      return (x < y ? -1 : x > y ? 1 : 0) * f.dir || ipValue(a.ip) - ipValue(b.ip);
    });
  }

  // ---------------------------------------------------------------- rendering
  _render() {
    if (!this._built) return;
    const $ = this.$;
    const f = this._f;

    this.shadowRoot.querySelectorAll("th").forEach((th) => {
      th.querySelector(".arrow").textContent = th.dataset.key === f.sort ? (f.dir === 1 ? "▲" : "▼") : "";
    });

    // tag popover
    const counts = {};
    this._devices.forEach((d) => d.tags.forEach((t) => { counts[fold(t)] = (counts[fold(t)] || 0) + 1; }));
    const untaggedCount = this._devices.filter((d) => !d.tags.length).length;
    $("match").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === f.match));
    const tagRows = this._tags.map((t) => `
      <div class="tagrow">
        <label><input type="checkbox" data-tag="${esc(t)}" ${hasTag(f.tags, t) ? "checked" : ""}><span>${esc(t)}</span><span class="n">${counts[fold(t)] || 0}</span></label>
        <button class="icon-btn" data-act="rename" data-tag="${esc(t)}" title="Rename tag"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
        <button class="icon-btn" data-act="delete" data-tag="${esc(t)}" title="Delete tag from all devices"><ha-icon icon="mdi:delete-outline"></ha-icon></button>
      </div>`).join("");
    const html = (this._tags.length ? tagRows : `<div class="hint">No tags yet.</div>`) + `
      <div class="tagrow"><label><input type="checkbox" data-untagged ${f.untagged ? "checked" : ""}><span><i>No tags</i></span><span class="n">${untaggedCount}</span></label></div>`;
    if (html !== this._lastTagHtml) { $("tagList").innerHTML = html; this._lastTagHtml = html; }
    const nSel = f.tags.length + (f.untagged ? 1 : 0);
    $("tagCount").hidden = !nSel;
    $("tagCount").textContent = nSel;

    // active filter chips
    const chips = f.tags.map((t) => `<span class="chip on">${esc(t)}<button class="x" data-remove="${esc(t)}" title="Remove">×</button></span>`);
    if (f.untagged) chips.push(`<span class="chip on"><i>No tags</i><button class="x" data-remove="__untagged" title="Remove">×</button></span>`);
    if (f.tags.length > 1) chips.unshift(`<span class="sub">Matching ${f.match === "all" ? "all" : "any"} of:</span>`);
    $("active").innerHTML = chips.join("");

    // saved filters
    const opts = [`<option value="">Saved filters…</option>`,
      ...this._filters.map((x) => `<option value="${esc(x.filter_id)}">${esc(x.name)}</option>`)].join("");
    if (opts !== this._lastOpts) { $("savedSel").innerHTML = opts; this._lastOpts = opts; }
    $("savedSel").value = this._savedId || "";
    const saved = this._filters.find((x) => x.filter_id === this._savedId);
    $("modified").hidden = !saved || sameFilter(saved, f);
    $("delSaved").disabled = !saved;

    // stats
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

  _row(d) {
    let name;
    if (d.name) {
      name = `<div><div class="main">${esc(d.name)}</div>${d.hostname ? `<div class="sub" title="Name found via ${esc(d.hostname_source || "lookup")}">${esc(d.hostname)}</div>` : ""}</div>`;
    } else if (d.hostname) {
      name = `<span class="main" title="Name found via ${esc(d.hostname_source || "lookup")}">${esc(d.hostname)}</span>`;
    } else {
      name = `<span class="none" title="No name found. Click ✎ to give it one.">Unknown</span>`;
    }
    const ip = d.ip
      ? `<a class="ip mono" href="http://${esc(d.ip)}" target="_blank" rel="noopener noreferrer">${esc(d.ip)}</a>`
      : "";
    const tags = d.tags.map((t) =>
      `<button class="chip ${hasTag(this._f.tags, t) ? "on" : ""}" data-filter-tag="${esc(t)}" title="Filter by this tag">${esc(t)}</button>`).join("");
    return `<tr class="${d.online ? "" : "offline"}">
      <td><div class="namecell"><span class="dot"></span>${name}</div></td>
      <td class="mono">${esc(d.mac)}</td>
      <td>${esc(d.vendor)}</td>
      <td>${ip}</td>
      <td class="tags">${tags}</td>
      <td>${d.online ? "Online" : esc(fmtAgo(d.seconds_since_seen))}</td>
      <td><button class="icon-btn" data-edit="${esc(d.mac)}" title="Edit name and tags"><ha-icon icon="mdi:pencil"></ha-icon></button></td>
    </tr>`;
  }

  // ---------------------------------------------------------------- edit dialog
  _setupEditor() {
    const $ = this.$;
    const addTag = (raw) => {
      const tag = raw.trim().slice(0, 32);
      if (tag && !hasTag(this._editTags, tag)) this._editTags.push(tag);
      $("tagInput").value = "";
      this._renderEditorTags();
    };
    $("tagInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === ",") {
        e.preventDefault();
        addTag($("tagInput").value);
      } else if (e.key === "Backspace" && !$("tagInput").value && this._editTags.length) {
        this._editTags.pop();
        this._renderEditorTags();
      }
    });
    $("tagInput").addEventListener("input", () => this._renderEditorTags());
    $("tagEditor").addEventListener("click", (e) => {
      const x = e.target.closest("[data-untag]");
      if (x) {
        this._editTags = this._editTags.filter((t) => fold(t) !== fold(x.dataset.untag));
        this._renderEditorTags();
      } else $("tagInput").focus();
    });
    $("suggest").addEventListener("click", (e) => {
      const s = e.target.closest("[data-add]");
      if (s) { addTag(s.dataset.add); $("tagInput").focus(); }
    });
    $("editCancel").addEventListener("click", () => $("editDlg").close());
    $("editForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      if ($("tagInput").value.trim()) addTag($("tagInput").value);
      $("editSave").disabled = true;
      try {
        await this._ws({ type: "network_devices/update_device", mac: this._editMac, name: $("editName").value.trim(), tags: this._editTags });
        $("editDlg").close();
        await this._fetch();
      } catch (err) { /* shown in the error bar */ }
      $("editSave").disabled = false;
    });
  }

  _openEditor(mac) {
    const d = this._devices.find((x) => x.mac === mac);
    if (!d) return;
    const $ = this.$;
    this._editMac = mac;
    this._editTags = [...d.tags];
    $("editMeta").innerHTML = `${esc(d.mac)} · ${esc(d.ip)}${d.vendor ? ` · ${esc(d.vendor)}` : ""}`;
    $("editName").value = d.name || "";
    $("editName").placeholder = d.hostname || "Unknown device";
    $("tagInput").value = "";
    this._renderEditorTags();
    $("editDlg").showModal();
    $("editName").focus();
  }

  _renderEditorTags() {
    const $ = this.$;
    $("tagEditor").querySelectorAll(".chip").forEach((c) => c.remove());
    const input = $("tagInput");
    this._editTags.forEach((t) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${esc(t)}<button type="button" class="x" data-untag="${esc(t)}" title="Remove">×</button>`;
      $("tagEditor").insertBefore(chip, input);
    });
    const typed = fold(input.value.trim());
    const suggestions = this._tags.filter((t) => !hasTag(this._editTags, t) && (!typed || fold(t).includes(typed)));
    $("suggest").innerHTML = suggestions.slice(0, 20)
      .map((t) => `<button type="button" class="chip" data-add="${esc(t)}">+ ${esc(t)}</button>`).join("");
  }

  // ---------------------------------------------------------------- small prompt dialog
  _setupAsk() {
    const $ = this.$;
    $("askCancel").addEventListener("click", () => { $("askDlg").close(); this._askResolve?.(null); });
    $("askDlg").addEventListener("cancel", () => this._askResolve?.(null));
    $("askForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const value = this._askHasInput ? $("askInput").value.trim() : true;
      if (this._askHasInput && !value) return;
      $("askDlg").close();
      this._askResolve?.(value);
    });
  }

  _ask({ title, text = "", label = "", value = "", ok = "OK", danger = false, input = true }) {
    const $ = this.$;
    $("askTitle").textContent = title;
    $("askText").textContent = text;
    $("askText").hidden = !text;
    $("askField").hidden = !input;
    $("askLabel").textContent = label;
    $("askInput").value = value;
    $("askOk").textContent = ok;
    $("askOk").classList.toggle("danger", danger);
    $("askOk").classList.toggle("primary", !danger);
    this._askHasInput = input;
    $("askDlg").showModal();
    if (input) { $("askInput").focus(); $("askInput").select(); }
    return new Promise((resolve) => { this._askResolve = (v) => { this._askResolve = null; resolve(v); }; });
  }

  // ---------------------------------------------------------------- saved filters & tag management
  async _saveFilter() {
    const current = this._filters.find((x) => x.filter_id === this._savedId);
    const name = await this._ask({
      title: "Save filter",
      text: "Saves the search, tag selection, Show offline and sort order. Use the same name to update an existing filter.",
      label: "Name",
      value: current ? current.name : "",
      ok: "Save",
    });
    if (!name) return;
    const existing = this._filters.find((x) => fold(x.name) === fold(name));
    const res = await this._ws({ type: "network_devices/save_filter", name, filter: this._f, ...(existing ? { filter_id: existing.filter_id } : {}) });
    this._savedId = res.filter_id;
    await this._fetch();
    this._filterChanged();
  }

  async _deleteFilter() {
    const current = this._filters.find((x) => x.filter_id === this._savedId);
    if (!current) return;
    const ok = await this._ask({ title: "Delete saved filter?", text: `"${current.name}" will be removed for everyone.`, ok: "Delete", danger: true, input: false });
    if (!ok) return;
    await this._ws({ type: "network_devices/delete_filter", filter_id: current.filter_id });
    this._savedId = null;
    await this._fetch();
    this._filterChanged();
  }

  async _renameTag(tag) {
    this.$("tagPop").hidden = true;
    const name = await this._ask({ title: "Rename tag", text: "Renames it on every device and saved filter.", label: "New name", value: tag, ok: "Rename" });
    if (!name || name === tag) return;
    await this._ws({ type: "network_devices/rename_tag", old: tag, new: name.slice(0, 32) });
    if (hasTag(this._f.tags, tag)) { this._toggleTag(tag, false); this._toggleTag(name.slice(0, 32), true); }
    await this._fetch();
    this._filterChanged();
  }

  async _deleteTag(tag) {
    this.$("tagPop").hidden = true;
    const ok = await this._ask({ title: `Delete tag "${tag}"?`, text: "It will be removed from every device and saved filter.", ok: "Delete", danger: true, input: false });
    if (!ok) return;
    await this._ws({ type: "network_devices/delete_tag", tag });
    this._toggleTag(tag, false);
    await this._fetch();
    this._filterChanged();
  }
}

customElements.define("network-devices-panel", NetworkDevicesPanel);
