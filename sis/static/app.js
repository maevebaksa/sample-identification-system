"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const selected = new Set();
let filters = { q: "", result: "" };
let info = {};

async function api(path, opts = {}) {
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch("/api" + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg, ms = 3500) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = "none", ms);
}
const fmtDate = t => t ? new Date(t * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "";
const fmtDur = s => { if (!s) return ""; const m = Math.round(s / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`; };
const badge = r => `<span class="badge b-${r}">${r === "success" ? "✓ success" : r === "fail" ? "✗ failed" : "unrated"}</span>`;

// ------------------------------------------------- settings grouping
// First matching rule wins, so order matters. Keys are matched without their "meta."/"cura." prefix.
const GROUPS = [
  ["Print estimates", /^(estimated|object_height|layer_count|filament_total|filament_weight|size$|first_layer_(extr|bed)_temp|gcode_start|gcode_end|print_start|chamber_temp$)/, true],
  ["Slicer & profile", /^(slicer|generated|.*settings_id$|.*_preset$|inherits|compatible_|printer_model|print_host|notes$|.*_vendor$)/],
  ["G-code scripts", /(gcode|^start_|^end_|^before_|^toolchange|template|^machine_(start|end)|post_process|^between_objects)/],
  ["Temperatures", /(temperature|_temp$|^temp|bed_temp|chamber|^standby)/],
  ["Cooling & fan", /(fan|cool|slow_down|min_layer_time|^full_fan)/],
  ["Speeds & acceleration", /(speed|accel|jerk|velocity|volumetric|^travel)/],
  ["Retraction & pressure advance", /(retract|wipe|z_hop|lift|pressure_advance|^extra_length|^deretract)/],
  ["Support, brim & skirt", /(support|brim|skirt|raft|adhesion|draft_shield|interface|^tree_)/],
  ["Infill", /(fill|infill)/],
  ["Walls & shells", /(perimeter|wall|shell|^top_|^bottom_|solid_|^thick_|overhang|^ensure_vertical|^only_one_perimeter)/],
  ["Layers & quality", /(layer_height|^first_layer|layer_|resolution|^slice|elefant|xy_size|^xy_|seam|ironing|fuzzy|^arc|precise|^z_offset|^wall_gen|^gap_)/],
  ["Filament & extrusion", /(filament|extrusion|flow|nozzle|material|density|diameter|^cost|extruder|width|^pellet|^spiral|^temperature_vitrification|^shrink)/],
  ["Machine & bed", /(bed_|printable|machine|printer|kinematic|^max_|^min_|^gcode_flavor|^use_|thumbnail|host|^silent|^z_|^extra_|^single_extruder|^nozzle_|^cura\.)/],
];
const OTHER = "Other";
function groupOf(key) {
  const meta = key.startsWith("meta.");
  const k = key.replace(/^(meta|cura)\./, "").toLowerCase();
  for (const [name, re, metaOnly] of GROUPS) if ((!metaOnly || meta) && re.test(k)) return name;
  return OTHER;
}
// [[groupName, [keys...]], ...] in display order, empty groups dropped.
function groupedKeys(keys) {
  const m = new Map([...GROUPS.map(g => g[0]), OTHER].map(n => [n, []]));
  for (const k of keys) m.get(groupOf(k)).push(k);
  return [...m].filter(([, v]) => v.length);
}

// ------------------------------------------------------------------ list
const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { } },
};

async function listView(archived = false) {
  const all = await api("/samples");
  const hideUnrated = !archived && store.get("hideUnrated", "0") === "1";
  const nUnrated = all.filter(s => !s.archived && s.result === "unrated").length;
  const rows = all.filter(s =>
    !!s.archived === archived &&
    !(hideUnrated && s.result === "unrated") &&
    (!filters.result || s.result === filters.result) &&
    (!filters.q || [s.id, s.title, s.filename, s.notes, s.tags].join(" ").toLowerCase().includes(filters.q.toLowerCase())));
  view.innerHTML = `
    <h1>${archived ? "Archive" : "Samples"}</h1>
    ${archived ? `<p class="muted">Archived samples are hidden from the main list and from default exports. Their gcode and photos are kept.</p>` : ""}
    <div class="bar">
      <input type="search" id="q" placeholder="Search number, name, notes, tags…" value="${esc(filters.q)}">
      <select id="rf"><option value="">All results</option>
        ${["unrated", "success", "fail"].map(r => `<option ${filters.result === r ? "selected" : ""}>${r}</option>`).join("")}</select>
      ${archived ? "" : `<label class="muted"><input type="checkbox" id="hu" ${hideUnrated ? "checked" : ""}> Hide unrated</label>`}
      <span class="grow"></span>
      <button id="cmp" class="primary" disabled>Compare (<span id="n">0</span>)</button>
      <button id="lbl" disabled>Labels</button>
      <button id="arc" disabled>${archived ? "Restore" : "Archive"} selected</button>
      ${archived || !nUnrated ? "" : `<button id="arcu">Archive all unrated (${nUnrated})</button>`}
      <button id="exp">Export CSV</button>
    </div>
    <div class="card flush tablewrap"><table>
      <thead><tr><th></th><th>#</th><th></th><th>Print</th><th>Result</th><th class="hide-m">Printed</th><th class="hide-m">Time</th></tr></thead>
      <tbody>${rows.map(s => `
        <tr class="click" data-id="${s.id}">
          <td><input type="checkbox" class="sel" data-id="${s.id}" ${selected.has(s.id) ? "checked" : ""} style="width:auto"></td>
          <td class="num">${s.id}</td>
          <td>${s.thumb ? `<img class="thumb" loading="lazy" src="/photos/${esc(s.thumb)}">` : ""}</td>
          <td>${esc(s.title || s.filename.split("/").pop())}
            <div class="muted">${esc(s.tags)}${s.reprint_of ? ` · reprint of #${s.reprint_of}` : ""}${s.gcode_path ? "" : ' · <span class="b-warn">gcode not archived</span>'}</div></td>
          <td>${badge(s.result)}${s.moonraker_status && s.moonraker_status !== "completed" ? `<div class="muted">${esc(s.moonraker_status)}</div>` : ""}</td>
          <td class="hide-m muted">${fmtDate(s.start_time)}</td>
          <td class="hide-m muted">${fmtDur(s.print_duration)}</td>
        </tr>`).join("") || `<tr><td colspan="7" class="muted">No samples yet. Finished prints appear here automatically.</td></tr>`}
      </tbody></table></div>`;
  const upd = () => { $("#n").textContent = selected.size; $("#cmp").disabled = selected.size < 2; $("#lbl").disabled = !selected.size; $("#arc").disabled = !selected.size; };
  upd();
  $("#q").oninput = e => { filters.q = e.target.value; const p = e.target.selectionStart; listView(archived).then(() => { const q = $("#q"); q.focus(); q.setSelectionRange(p, p); }); };
  $("#rf").onchange = e => { filters.result = e.target.value; listView(archived); };
  if ($("#hu")) $("#hu").onchange = e => { store.set("hideUnrated", e.target.checked ? "1" : "0"); listView(archived); };
  $("#arc").onclick = async () => {
    const ids = [...selected];
    await api("/samples/archive", { method: "POST", body: { ids, archived: !archived } });
    selected.clear(); toast(`${ids.length} sample(s) ${archived ? "restored" : "archived"}`); listView(archived);
  };
  if ($("#arcu")) $("#arcu").onclick = async () => {
    if (!confirm(`Move all ${nUnrated} unrated samples to the Archive? You can restore them later.`)) return;
    await api("/samples/archive", { method: "POST", body: { unrated: true, archived: true } });
    selected.clear(); toast("Unrated samples archived"); listView(archived);
  };
  view.querySelectorAll("tr.click").forEach(tr => tr.onclick = e => {
    if (e.target.classList.contains("sel")) {
      const id = +e.target.dataset.id; e.target.checked ? selected.add(id) : selected.delete(id); upd(); return;
    }
    location.hash = "#/sample/" + tr.dataset.id;
  });
  $("#cmp").onclick = () => location.hash = "#/compare/" + [...selected].sort((a, b) => a - b).join(",");
  $("#lbl").onclick = () => window.open("/labels?ids=" + [...selected].join(","));
  $("#exp").onclick = () => exportDialog();
}

function exportDialog() {
  const ids = [...selected].join(",");
  view.insertAdjacentHTML("afterbegin", `<div class="card" id="expcard"><h2>Export ${selected.size ? selected.size + " selected" : "all"} samples</h2>
    <div class="row"><select id="emode">
      <option value="key">CSV – results + key settings</option>
      <option value="all">CSV – results + every setting (wide)</option>
      <option value="none">CSV – results only</option></select>
      <button id="dl" class="primary">Download CSV</button>
      <button id="dlj">Download JSON (everything)</button>
      <button id="cx" class="ghost">Cancel</button></div></div>`);
  $("#dl").onclick = () => location.href = `/api/export.csv?ids=${ids}&settings=${$("#emode").value}`;
  $("#dlj").onclick = () => location.href = `/api/export.json?ids=${ids}`;
  $("#cx").onclick = () => $("#expcard").remove();
}

// ---------------------------------------------------------------- detail
async function detailView(id) {
  const s = await api("/samples/" + id);
  const keys = Object.keys(s.settings).sort();
  view.innerHTML = `
    <div class="bar"><a href="#/" class="btn ghost">← All samples</a><span class="grow"></span>
      ${id > 1 ? `<a class="btn ghost" href="#/sample/${id - 1}">‹ #${id - 1}</a>` : ""}<a class="btn ghost" href="#/sample/${id + 1}">#${id + 1} ›</a></div>
    <h1>Sample #${s.id} ${badge(s.result)} ${s.archived ? '<span class="badge b-warn">archived</span>' : ""}</h1>
    <div class="muted">${esc(s.filename)} · ${fmtDate(s.start_time)} · ${fmtDur(s.print_duration)}
      ${s.moonraker_status && s.moonraker_status !== "completed" ? ` · Moonraker: ${esc(s.moonraker_status)}` : ""}
      ${s.reprint_of ? ` · same gcode as <a href="#/sample/${s.reprint_of}">#${s.reprint_of}</a>` : ""}</div>
    <div class="grid2" style="margin-top:14px">
      <div>
        <div class="card"><h2>Result</h2>
          <div class="resultbtns">
            <button class="s ${s.result === "success" ? "sel" : ""}" data-r="success">✓ Success</button>
            <button class="f ${s.result === "fail" ? "sel" : ""}" data-r="fail">✗ Failed</button></div>
          <p><input id="title" placeholder="Title / what was being tested" value="${esc(s.title)}"></p>
          <p><input id="tags" placeholder="Tags (comma separated)" value="${esc(s.tags)}"></p>
          <p><textarea id="notes" rows="4" placeholder="Notes: what you saw, what to change next…">${esc(s.notes)}</textarea></p>
          <button id="save" class="primary">Save notes</button></div>
        <div class="card"><h2>Photos</h2>
          <div class="row">
            <label class="btn primary" style="text-align:center">📷 Take photo<input type="file" id="cam" accept="image/*" capture="environment" hidden></label>
            <label class="btn" style="text-align:center">🖼 Choose from gallery<input type="file" id="gal" accept="image/*" multiple hidden></label></div>
          <div class="photos">${s.photos.map(p => `<div><a href="/photos/${esc(p.filename)}" target="_blank"><img src="/photos/${esc(p.filename)}"></a>
            <button class="x" data-p="${p.id}">×</button></div>`).join("")}</div></div>
      </div>
      <div>
        <div class="card"><h2>Reprint / files</h2>
          ${s.gcode_path ? `<div class="row"><button id="reprint" class="primary">▶ Reprint this gcode</button>
            <a class="btn" href="/api/samples/${id}/gcode">⬇ Download gcode</a></div>
            <p class="muted">Archived copy: ${esc(s.gcode_path)}. If the file was deleted from Moonraker it is re-uploaded first.</p>`
            : `<p class="b-warn">The gcode could not be archived: ${esc(s.archive_error || "unknown")}</p>`}
          <div class="qr row" style="align-items:center"><span style="flex:none" id="qr"></span>
            <span class="muted">Scan on your phone to open this sample and add photos. <a href="/labels?ids=${id}" target="_blank">Print label</a></span></div></div>
        <div class="card"><h2>Print stats</h2><table class="kv">
          <tr><td>Print time</td><td>${fmtDur(s.print_duration)}</td></tr>
          <tr><td>Filament used</td><td>${s.filament_used ? (s.filament_used / 1000).toFixed(2) + " m" : ""}</td></tr></table></div>
      </div>
    </div>
    <div class="card"><h2>Settings (${keys.length})</h2>
      <input type="search" id="sf" placeholder="Filter settings…" style="max-width:300px;margin-bottom:8px">
      <div id="stab">${groupedKeys(keys).map(([g, ks], i) => `
        <details class="grp" ${i < 5 ? "open" : ""}><summary>${esc(g)} <span class="muted">(${ks.length})</span></summary>
          <div class="tablewrap"><table class="kv">${ks.map(k => `<tr><td>${esc(k.replace(/^meta\./, ""))}${k.startsWith("meta.") ? ' <span class="muted">· moonraker</span>' : ""}</td><td class="val">${esc(s.settings[k])}</td></tr>`).join("")}</table></div>
        </details>`).join("")}</div>
      <p><button id="archv">${s.archived ? "Restore from archive" : "Move to archive"}</button>
      <button id="del" class="ghost bad">Delete sample record</button>
      <span class="muted">(archived gcode stays on disk)</span></p></div>`;
  fetch(`/api/samples/${id}/qr.svg`).then(r => r.text()).then(t => $("#qr").innerHTML = `<span class="qr">${t}</span>`);
  view.querySelectorAll("[data-r]").forEach(b => b.onclick = async () => {
    await api("/samples/" + id, { method: "PATCH", body: { result: s.result === b.dataset.r ? "unrated" : b.dataset.r } });
    detailView(id);
  });
  $("#save").onclick = async () => {
    await api("/samples/" + id, { method: "PATCH", body: { title: $("#title").value, tags: $("#tags").value, notes: $("#notes").value } });
    toast("Saved");
  };
  const reprint = $("#reprint");
  if (reprint) reprint.onclick = async () => {
    if (!confirm(`Start printing sample #${id} (${s.filename}) now?`)) return;
    reprint.disabled = true;
    try { const r = await api(`/samples/${id}/reprint`, { method: "POST" }); toast(`Started: ${r.started}${r.uploaded ? " (restored from archive)" : ""}`, 6000); }
    catch (e) { toast("Reprint failed: " + e.message, 7000); }
    reprint.disabled = false;
  };
  const up = async ev => {
    const files = [...ev.target.files]; if (!files.length) return;
    toast(`Uploading ${files.length} photo(s)…`, 20000);
    const fd = new FormData();
    for (const f of files) fd.append("photo", await shrink(f), f.name.replace(/\.\w+$/, "") + ".jpg");
    try { await api(`/samples/${id}/photos`, { method: "POST", body: fd }); toast("Photo added"); detailView(id); }
    catch (e) { toast("Upload failed: " + e.message, 6000); }
  };
  $("#cam").onchange = up; $("#gal").onchange = up;
  view.querySelectorAll(".photos .x").forEach(b => b.onclick = async () => {
    if (confirm("Delete this photo?")) { await api("/photos/" + b.dataset.p, { method: "DELETE" }); detailView(id); }
  });
  $("#sf").oninput = e => {
    const q = e.target.value.toLowerCase();
    $("#stab").querySelectorAll("details").forEach(d => {
      let shown = 0;
      d.querySelectorAll("tr").forEach(tr => { const on = tr.textContent.toLowerCase().includes(q); tr.style.display = on ? "" : "none"; shown += on; });
      d.style.display = shown ? "" : "none";
      if (q && shown) d.open = true;
    });
  };
  $("#archv").onclick = async () => {
    await api("/samples/" + id, { method: "PATCH", body: { archived: !s.archived } });
    toast(s.archived ? "Restored" : "Moved to archive"); detailView(id);
  };
  $("#del").onclick = async () => { if (confirm(`Delete record for sample #${id}? Photos are removed; the archived gcode is kept.`)) { await api("/samples/" + id, { method: "DELETE" }); location.hash = "#/"; } };
}

// Downscale big phone photos before upload (falls back to the original file).
function shrink(file, max = 2000) {
  return new Promise(res => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = img.width * k; c.height = img.height * k;
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(b => { URL.revokeObjectURL(url); res(b || file); }, "image/jpeg", 0.88);
    };
    img.onerror = () => { URL.revokeObjectURL(url); res(file); };
    img.src = url;
  });
}

// --------------------------------------------------------------- compare
async function compareView(ids) {
  const d = await api("/compare?ids=" + ids);
  let onlyDiff = true;
  const draw = () => {
    const rows = d.rows.filter(r => !onlyDiff || r.differs);
    view.innerHTML = `
      <div class="bar"><a href="#/" class="btn ghost">← All samples</a><span class="grow"></span>
        <label><input type="checkbox" id="od" style="width:auto" ${onlyDiff ? "checked" : ""}> Only differences (${d.rows.filter(r => r.differs).length} of ${d.rows.length})</label>
        <button id="ex">Export these CSV</button></div>
      <h1>Compare ${d.samples.map(s => "#" + s.id).join(" vs ")}</h1>
      <div class="card tablewrap"><table>
        <thead><tr><th>Setting</th>${d.samples.map(s => `<th><a href="#/sample/${s.id}">#${s.id}</a> ${badge(s.result)}</th>`).join("")}</tr></thead>
        <tbody>
          <tr><td class="muted">Photo</td>${d.samples.map(s => `<td>${s.photos.length ? `<a href="#/sample/${s.id}"><img class="thumb" style="width:90px;height:90px" src="/photos/${esc(s.photos[s.photos.length - 1].filename)}"></a>` : ""}</td>`).join("")}</tr>
          <tr><td class="muted">Notes</td>${d.samples.map(s => `<td>${esc(s.notes)}</td>`).join("")}</tr>
          ${(() => {
            const byKey = new Map(rows.map(r => [r.key, r]));
            return groupedKeys([...byKey.keys()]).map(([g, ks]) => `
              <tr class="grphead"><td colspan="${d.samples.length + 1}">${esc(g)} <span class="muted">· ${ks.filter(k => byKey.get(k).differs).length} differ / ${ks.length}</span></td></tr>
              ${ks.map(k => { const r = byKey.get(k); return `<tr class="${r.differs ? "diff" : ""}"><td class="muted" style="word-break:break-all">${esc(k.replace(/^meta\./, ""))}</td>
                ${r.values.map(v => `<td class="val">${v === null ? '<span class="muted">—</span>' : esc(v)}</td>`).join("")}</tr>`; }).join("")}`).join("");
          })()}
        </tbody></table></div>`;
    $("#od").onchange = e => { onlyDiff = e.target.checked; draw(); };
    $("#ex").onclick = () => location.href = `/api/export.csv?ids=${ids}&settings=all`;
  };
  draw();
}

// ---------------------------------------------------------------- router
async function route() {
  const [, kind, arg] = location.hash.split("/");
  window.scrollTo(0, 0);
  document.body.classList.remove("menu-open");
  document.querySelectorAll(".nav[data-nav]").forEach(a =>
    a.classList.toggle("active", a.dataset.nav === (kind === "archive" ? "archive" : "samples")));
  try {
    if (kind === "sample") await detailView(+arg);
    else if (kind === "compare") await compareView(arg);
    else if (kind === "archive") await listView(true);
    else await listView();
  } catch (e) { view.innerHTML = `<div class="card b-fail">Error: ${esc(e.message)}</div>`; }
}
async function refreshInfo() {
  try {
    info = await api("/info");
    const ls = info.last_sync;
    $("#syncinfo").textContent = ls.error ? "⚠ " + ls.error : ls.time ? "Synced " + new Date(ls.time * 1000).toLocaleTimeString() : "";
    $("#mainsail").href = info.moonraker_url.replace(/:\d+$/, "").replace("localhost", location.hostname);
  } catch { }
}
$("#syncbtn").onclick = async e => {
  e.preventDefault();
  const r = await api("/sync", { method: "POST" });
  toast(r.error ? "Sync error: " + r.error : r.new ? `${r.new} new sample(s)` : "Up to date");
  refreshInfo(); route();
};
$("#menu").onclick = () => document.body.classList.toggle("menu-open");
$("#scrim").onclick = () => document.body.classList.remove("menu-open");
window.addEventListener("hashchange", route);
refreshInfo(); route(); setInterval(refreshInfo, 30000);
