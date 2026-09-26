// FlagZero race-control dashboard (Person C).
// Connects to /ws/dash on the page's own host and renders server.py's 10 Hz
// "state" message (flagzero/docs/state_message.md). Track shape from
// GET /api/track. Open /dashboard?mock=1 to run on built-in fake data.

const PHONE_CARS = [17, 21];   // cars with a real phone attached
const MOCK = new URLSearchParams(location.search).has("mock");
const TRIGGER_G = 2.0, IMPACT_G = 3.0; // same thresholds as car.js
const TRACE_S = 10;                    // g-trace window

const LEVEL_NAMES = ["NORMAL", "CAUTION", "YELLOW", "DBL YELLOW", "SLOW ZONE", "RED"];
const LEVEL_COLORS = ["#cfd6de", "#b08a00", "#ffd400", "#ffd400", "#ff9800", "#e0262b"];
const SEVERITY = [
  { name: "NORMAL", bg: "#1b2a20", fg: "#6fcf8f" },
  { name: "MINOR", bg: "#b08a00", fg: "#fff" },
  { name: "SIGNIFICANT", bg: "#ffd400", fg: "#111" },
  { name: "MAJOR", bg: "#ff9800", fg: "#111" },
  { name: "CRITICAL", bg: "#e0262b", fg: "#fff" },
];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const byCar = (obj, car) => (obj ? obj[car] ?? obj[String(car)] : undefined);
// incident sources arrive as {src, kind, conf} objects (older servers: plain strings)
const srcNames = (inc) => (inc && inc.sources ? inc.sources.map((x) => (typeof x === "string" ? x : x.src)) : []);
const cornerLabel = (name) => (track.corners.find((c) => c.name === name) || {}).label || "";
const isRedOut = (s) => !!(s.red_confirmed ?? s.red_active);

let latest = null;           // last state message
const tel = {};              // car -> [{t, g}]    (t = performance.now() on arrival)
const events = {};           // car -> [{t, cls, peak}]
const trials = [];           // detect -> warn history (ms)
let lastTrialVal = null;

// ---- track ------------------------------------------------------------------
// Uses GET /track.json from the server when available, otherwise a built-in 4,000 m layout.
let track = builtInTrack();

function builtInTrack() {
  const n = 480, raw = [];
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const r = 1 + 0.22 * Math.cos(2 * a) + 0.12 * Math.sin(3 * a + 0.6);
    raw.push({ x: Math.cos(a) * r * 1.5, y: Math.sin(a) * r });
  }
  return withDistances(raw, 4000, {
    corners: [["T1", 280], ["T2", 760], ["T3", 1130], ["T4", 1423], ["T5", 1900], ["T6", 2420], ["T7", 3020], ["T8", 3580]]
      .map(([name, m]) => ({ name, m, label: "" })),
    source: "built-in layout",
  });
}

function withDistances(pts, lap, extra) {
  let total = 0;
  const d = [0];
  for (let i = 1; i < pts.length; i++) { total += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y); d.push(total); }
  const closing = Math.hypot(pts[0].x - pts[pts.length - 1].x, pts[0].y - pts[pts.length - 1].y);
  const scale = lap / (total + closing);
  return { lap, pts: pts.map((p, i) => ({ x: p.x, y: p.y, d: d[i] * scale })), ...extra };
}

function parseTrack(j) {
  const lap = j.lap_length_m ?? j.length_m ?? j.total_length_m ?? j.lap_m ?? 4000;
  let pts = (j.points ?? j.polyline ?? j.centerline ?? []).map((p) => Array.isArray(p)
    ? { x: p[0], y: p[1], d: p[2] }
    : { x: p.x, y: p.y, d: p.d ?? p.dist_m ?? p.lap_m ?? p.track_m ?? p.s });
  if (pts.length < 3) throw new Error("track.json has no polyline");
  const corners = (j.corners ?? []).map((c) => ({
    name: c.name ?? c.id, m: c.s ?? c.pos_m ?? c.lap_m ?? c.track_m ?? c.position_m ?? c.m,
    sightline: c.sightline_m ?? c.sightline, label: c.label ?? c.note ?? "",
  }));
  const extra = { corners, name: j.name, source: "track.json" };
  if (pts.some((p) => p.d == null)) return withDistances(pts, lap, extra);
  pts.sort((a, b) => a.d - b.d);
  return { lap, pts, ...extra };
}

const getJson = (url) => fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(url))));
getJson("/api/track").catch(() => getJson("/track.json")).then((j) => {
  track = parseTrack(j);
  $("trackSrc").textContent = track.name || "track.json";
}).catch(() => { /* keep the built-in layout */ });

const wrap = (m) => ((m % track.lap) + track.lap) % track.lap;
const upstream = (fromM, toM) => wrap(toM - fromM); // distance a car at fromM travels to reach toM

function pointAt(m) {
  const pts = track.pts, n = pts.length;
  m = wrap(m);
  let lo = 0, hi = n - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (pts[mid].d <= m) lo = mid; else hi = mid - 1; }
  const a = pts[lo], b = lo + 1 < n ? pts[lo + 1] : { ...pts[0], d: track.lap };
  const f = b.d > a.d ? (m - a.d) / (b.d - a.d) : 0;
  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}

// ---- connection -------------------------------------------------------------
let ws = null, lastStateAt = 0;

function send(msg) {
  if (MOCK) return mockHandle(msg);
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function connect() {
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/dash";
  ws = new WebSocket(url);
  ws.onclose = () => setTimeout(connect, 2000);
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    onMessage(m);
  };
}

function onMessage(m) {
  if (m.type === "state") { lastStateAt = performance.now(); onState(m); }
  else if (m.type === "tel") onTel(m.car, m.g);
  else if (m.type === "imu_event") onImuEvent(m.car, m.cls, m.peak_g);
}

function onTel(car, g) {
  const buf = (tel[car] ??= []);
  const now = performance.now();
  buf.push({ t: now, g: Number(g) || 0 });
  while (buf.length && now - buf[0].t > TRACE_S * 1000) buf.shift();
}

function onImuEvent(car, cls, peak) {
  const buf = (events[car] ??= []);
  const now = performance.now();
  buf.push({ t: now, cls, peak });
  while (buf.length && now - buf[0].t > TRACE_S * 1000) buf.shift();
}

// ---- state -> panels --------------------------------------------------------
function topIncident(s) {
  const incidents = (s.incidents || []).filter((i) => (i.severity ?? 0) > 0);
  return { inc: incidents.sort((a, b) => (b.severity - a.severity) || (b.updated_ms - a.updated_ms))[0], count: incidents.length };
}

function focusCar(s) {
  const { inc } = topIncident(s || {});
  return inc && inc.car != null ? inc.car : 17;
}

const lastTelMs = {};
function onState(s) {
  latest = s;
  const telNow = (s.latency && s.latency.tel) || {};
  for (const [car, v] of Object.entries(telNow)) {
    if (v && v.ms != null && v.ms !== lastTelMs[car]) { lastTelMs[car] = v.ms; onTel(Number(car), v.g); }
  }
  const v = s.latency && s.latency.last_detect_to_warn_ms;
  if (v != null && v !== lastTrialVal) { lastTrialVal = v; trials.push(v); }

  const { inc, count } = topIncident(s);
  renderBanners(s, inc);
  renderIncident(s, inc, count);
  $("approachBody").innerHTML = approachingTable(s, inc);
  renderDriver(s, inc);
  renderLatency(s);
  $("traceTitle").textContent = `Live g-force · car #${focusCar(s)}`;
}

function renderBanners(s, inc) {
  const why = inc ? `${inc.corner || ""} · car #${inc.car ?? "?"} · ${srcNames(inc).join(" + ")}` : "";
  $("redBanner").classList.toggle("show", !!s.red_pending);
  $("redWhy").textContent = s.red_pending ? why : "";
  $("redActive").classList.toggle("show", isRedOut(s));
  const auto = s.red_auto ?? srcNames(inc).includes("NO_RESPONSE");
  $("redActiveWhy").textContent = isRedOut(s) ? (auto ? "AUTOMATIC · driver unresponsive · " : "confirmed by race control · ") + why : "";
}

function renderIncident(s, inc, count) {
  if (!inc) {
    $("incidentBody").innerHTML = `<div class="none">No active incident · green flag racing</div>`;
    return;
  }
  const sev = SEVERITY[Math.max(0, Math.min(4, inc.severity))];
  const auto = s.red_auto ?? srcNames(inc).includes("NO_RESPONSE");
  const sevLabel = isRedOut(s) ? (auto ? "RED FLAG · AUTO (NO RESPONSE)" : "RED FLAG OUT")
    : inc.severity >= 4 && s.red_pending ? "RED FLAG RECOMMENDED" : (inc.label || sev.name).replace(/_/g, " ");
  const chips = (inc.sources || []).map((x) => {
    const name = typeof x === "string" ? x : x.src;
    const detail = typeof x === "string" ? "" : [x.kind && x.kind !== name ? x.kind : "", x.conf != null ? Math.round(x.conf * 100) + "%" : ""].filter(Boolean).join(" ");
    return `<span class="chip ${esc(name)}">${esc(String(name).replace("_", "-"))}${detail ? " · " + esc(detail) : ""}</span>`;
  }).join("");
  const label = inc.corner_label || cornerLabel(inc.corner);
  const med = inc.medical ? `<span class="med-${esc(inc.medical)}">${esc(inc.medical)}</span>` : "—";
  const age = inc.created_ms ? `${Math.max(0, (Date.now() - inc.created_ms) / 1000).toFixed(0)} s ago` : "";
  $("incidentBody").innerHTML = `
    <span class="sev" style="background:${sev.bg};color:${sev.fg}">SEV ${inc.severity} · ${esc(sevLabel)}</span>
    <div class="kind">${esc(String(inc.kind || "INCIDENT").replace(/_/g, " "))}</div>
    <div class="where">${esc(inc.corner || "?")}${label ? " · " + esc(label) : ""} · ${inc.track_m != null ? Math.round(inc.track_m) + " m" : "position unknown"}</div>
    <div class="kv">
      <span class="k">Fused confidence</span><span class="conf">${inc.fused_conf != null ? Math.round(inc.fused_conf * 100) + "%" : "—"}</span>
      <span class="k">Sources</span><span>${chips || "—"}</span>
      <span class="k">Vehicle</span><span>${inc.car != null ? "#" + esc(inc.car) : "—"}</span>
      <span class="k">Medical</span><span>${med}</span>
      <span class="k">Detected</span><span>${age}</span>
    </div>
    ${count > 1 ? `<div class="others">+ ${count - 1} more active incident(s)</div>` : ""}`;
}

function approachingTable(s, inc) {
  // Prefer the server's own list on the incident; otherwise derive it from car positions.
  let rows;
  if (inc && Array.isArray(inc.approaching) && inc.approaching.length) {
    rows = inc.approaching.map((a) => ({
      car: a.car ?? a.vehicle_id, dist: a.dist_m ?? a.distance_to_hazard, speed: a.speed_kmh,
      eta: a.eta_s ?? a.ETA, level: a.warning ?? a.warning_level ?? 0,
    }));
  } else {
    rows = (s.cars || []).map((c) => {
      const dist = inc && inc.track_m != null ? upstream(c.track_m, inc.track_m) : null;
      const v = (c.speed_kmh || 0) / 3.6;
      return { car: c.car, dist, speed: c.speed_kmh, eta: dist != null && v > 0 ? dist / v : null, level: c.warning ?? 0 };
    });
    if (inc) rows = rows.filter((r) => r.car !== inc.car && r.dist != null && r.dist <= 2000);
  }
  // each car's actual flag (includes a confirmed/automatic RED) comes from state.cars
  const carWarning = Object.fromEntries((s.cars || []).map((c) => [c.car, c.warning]));
  rows.forEach((r) => { if (carWarning[r.car] != null) r.level = carWarning[r.car]; });
  if (!rows.length) return `<div class="none">${inc ? "No cars within 2,000 m of the hazard" : "No active hazard"}</div>`;
  rows.sort((a, b) => (a.eta ?? 1e9) - (b.eta ?? 1e9));
  return `<table><thead><tr><th>Car</th><th>Distance</th><th>Speed</th><th>ETA</th><th>Warning</th></tr></thead><tbody>${
    rows.map((r) => `<tr class="${PHONE_CARS.includes(r.car) ? "phone" : ""}">
      <td class="car">#${esc(r.car)}</td>
      <td>${r.dist != null ? Math.round(r.dist) + " m" : "—"}</td>
      <td>${r.speed != null ? Math.round(r.speed) + " km/h" : "—"}</td>
      <td>${r.eta != null ? Number(r.eta).toFixed(1) + " s" : "—"}</td>
      <td><span class="lvl l${r.level}">${LEVEL_NAMES[r.level] || r.level}</span></td></tr>`).join("")
  }</tbody></table>`;
}

// panel 3
function renderDriver(s, inc) {
  const car = focusCar(s);
  $("driverTitle").textContent = `Driver status · car #${car}`;
  const d = byCar(s.vitals ?? s.drivers, car) || {};
  const medical = d.medical ?? (inc && inc.car === car ? inc.medical : null);
  const resp = d.response && d.response !== "-" ? d.response : null;
  const cdLeft = inc && inc.car === car && inc.countdown_left_s != null ? inc.countdown_left_s : d.countdown_s;
  const respLabel = { WAITING: "WAITING FOR I'M OK", OK: "RESPONSIVE", NO_RESPONSE: "NO RESPONSE" }[resp];
  const hr = d.hr != null ? Math.round(d.hr) : "—";
  const spo2 = d.spo2 != null ? Math.round(d.spo2) : "—";
  $("driverBody").innerHTML = `
    <div class="vitals">
      <div class="vital"><span class="big"><span class="heart">❤</span> ${hr}</span><span class="unit">bpm</span></div>
      <div class="vital"><span class="big">${spo2}</span><span class="unit">% SpO₂</span></div>
    </div>
    <div class="kv">
      <span class="k">Response</span><span>${respLabel ? `<span class="resp ${esc(resp)}">${respLabel}</span>` : "—"}</span>
      <span class="k">I'm-OK countdown</span><span class="countdown">${resp === "WAITING" && cdLeft != null ? cdLeft + " s" : "—"}</span>
      <span class="k">Medical</span><span>${medical ? `<span class="med-${esc(medical)}">${esc(medical)}</span>` : "—"}</span>
    </div>
    ${s.vitals || s.drivers ? "" : `<div class="note">Server isn't sending driver data yet (state.vitals).</div>`}`;
}

// panel 7
function pct(arr, p) {
  // linear interpolation between closest ranks (p = 0.5 gives the true median)
  const a = [...arr].sort((x, y) => x - y);
  const i = p * (a.length - 1), lo = Math.floor(i), hi = Math.ceil(i);
  return a[lo] + (a[hi] - a[lo]) * (i - lo);
}

function renderLatency(s) {
  const lat = s.latency || {};
  const perCar = lat.per_car || Object.fromEntries(PHONE_CARS.filter((c) => lat[`rtt_car${c}_ms`] != null).map((c) => [c, lat[`rtt_car${c}_ms`]]));
  const rtts = PHONE_CARS.map((c) => {
    const v = byCar(perCar, c);
    return `#${c} ${v != null ? Math.round(v) + " ms" : "—"}`;
  }).join(" · ");
  const last = lat.last_detect_to_warn_ms;
  const stats = trials.length
    ? `n=${trials.length} · median ${Math.round(pct(trials, 0.5))} · p90 ${Math.round(pct(trials, 0.9))} · max ${Math.round(Math.max(...trials))} ms`
    : "no trials yet";
  $("latencyBody").innerHTML = `
    <div class="lat">
      <span class="k">Detect → warning</span><span class="hero">${last != null ? Math.round(last) + " ms" : "—"}</span>
      <span class="k">Trials</span><span class="v">${stats}</span>
      <span class="k">Tunnel RTT</span><span class="v">${Object.keys(perCar).length ? rtts : lat.tunnel_rtt_ms != null ? Math.round(lat.tunnel_rtt_ms) + " ms" : "—"}</span>
    </div>
    <div class="note">Server clock. Detect → warning = crash reaches the server → approaching phone acks its warning.</div>`;
}

// ---- canvases -----------------------------------------------------------------
function fitCanvas(c) {
  const r = c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}

// panel 5: track map
function drawMap() {
  const { ctx, w, h } = fitCanvas($("map"));
  ctx.clearRect(0, 0, w, h);
  if (w < 10 || h < 10) return;
  const pts = track.pts;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const p of pts) { minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
  const pad = 46;
  const sc = Math.min((w - 2 * pad) / (maxX - minX || 1), (h - 2 * pad) / (maxY - minY || 1));
  const ox = (w - sc * (maxX - minX)) / 2, oy = (h - sc * (maxY - minY)) / 2;
  const cx = w / 2, cy = h / 2;
  const P = (m) => { const p = pointAt(m); return [ox + (p.x - minX) * sc, oy + (maxY - p.y) * sc]; }; // y up in data
  const outward = (x, y, dist) => { const dx = x - cx, dy = y - cy, l = Math.hypot(dx, dy) || 1; return [x + dx / l * dist, y + dy / l * dist]; };
  const path = (from, len, step) => {
    ctx.beginPath();
    for (let s = 0; s <= len; s += step) { const [x, y] = P(from + s); s ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
    const [x, y] = P(from + len); ctx.lineTo(x, y);
  };
  ctx.lineJoin = ctx.lineCap = "round";

  // track surface + centre line
  path(0, track.lap, 8);
  ctx.strokeStyle = "#2c343e"; ctx.lineWidth = 18; ctx.stroke();
  ctx.strokeStyle = "#4a5462"; ctx.lineWidth = 1.5; ctx.stroke();

  // start / finish
  { const [x, y] = P(0); ctx.fillStyle = "#e8ecf1"; ctx.fillRect(x - 2, y - 11, 4, 22);
    const [lx, ly] = outward(x, y, 30); ctx.fillStyle = "#8a95a3"; ctx.font = "600 11px system-ui"; ctx.textAlign = "center"; ctx.fillText("S/F", lx, ly + 4); }

  // corners
  ctx.textAlign = "center";
  ctx.fillStyle = "#8a95a3"; ctx.font = "600 11px system-ui";
  for (const c of track.corners) {
    if (c.m == null) continue;
    const [x, y] = P(c.m);
    const [lx, ly] = outward(x, y, 24);
    ctx.fillText(c.name, lx, ly + 4);
  }

  const s = latest || {};
  const { inc } = topIncident(s);

  // hazard marker
  if (inc && inc.track_m != null) {
    const [x, y] = P(inc.track_m);
    const pulse = 18 + 6 * Math.sin(performance.now() / 180);
    ctx.beginPath(); ctx.arc(x, y, pulse, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(224,38,43,.25)"; ctx.fill();
    ctx.beginPath(); ctx.moveTo(x, y - 12); ctx.lineTo(x + 11, y + 8); ctx.lineTo(x - 11, y + 8); ctx.closePath();
    ctx.fillStyle = "#ff5a5f"; ctx.fill();
    ctx.fillStyle = "#111"; ctx.font = "900 12px system-ui"; ctx.fillText("!", x, y + 6);
  }

  // cars
  for (const c of s.cars || []) {
    if (c.track_m == null) continue;
    const [x, y] = P(c.track_m);
    const crashed = inc && inc.car === c.car;
    ctx.beginPath(); ctx.arc(x, y, 12, 0, Math.PI * 2);
    ctx.fillStyle = crashed ? "#e0262b" : LEVEL_COLORS[c.warning ?? 0] || LEVEL_COLORS[0];
    ctx.fill();
    const phone = c.phone ?? PHONE_CARS.includes(c.car);
    ctx.lineWidth = phone ? 3 : 1;
    ctx.strokeStyle = phone ? "#ffffff" : "#0d0f12";
    ctx.stroke();
    ctx.fillStyle = crashed || (c.warning ?? 0) === 5 || (c.warning ?? 0) === 1 ? "#fff" : "#111";
    ctx.font = "800 11px system-ui";
    ctx.fillText(String(c.car), x, y + 4);
  }
}

// panel 6: live g-force trace
function drawTrace() {
  const { ctx, w, h } = fitCanvas($("trace"));
  ctx.clearRect(0, 0, w, h);
  if (w < 10 || h < 10) return;
  const car = focusCar(latest);
  const buf = tel[car] || [];
  const evs = events[car] || [];
  const now = performance.now();
  const left = 34, right = 8, top = 8, bottom = 20;
  const pw = w - left - right, ph = h - top - bottom;
  const peak = Math.max(0, ...buf.map((p) => p.g), ...evs.map((e) => e.peak || 0));
  const maxG = Math.max(6, Math.ceil(peak + 0.5));
  const X = (t) => left + pw - ((now - t) / (TRACE_S * 1000)) * pw;
  const Y = (g) => top + ph - (Math.min(g, maxG) / maxG) * ph;

  // grid
  ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.fillStyle = "#8a95a3";
  const step = maxG > 12 ? 4 : maxG > 6 ? 2 : 1;
  for (let g = 0; g <= maxG; g += step) {
    ctx.strokeStyle = "#232a33"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(left, Y(g)); ctx.lineTo(w - right, Y(g)); ctx.stroke();
    ctx.fillText(`${g} g`, left - 4, Y(g) + 4);
  }
  ctx.textAlign = "left";
  ctx.fillText(`-${TRACE_S} s`, left, h - 5);
  ctx.textAlign = "right";
  ctx.fillText("now", w - right, h - 5);

  // thresholds
  const dashed = (g, color, label) => {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(left, Y(g)); ctx.lineTo(w - right, Y(g)); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.textAlign = "left"; ctx.fillText(label, left + 4, Y(g) - 3);
  };
  dashed(TRIGGER_G, "#b08a00", "trigger");
  dashed(IMPACT_G, "#e0262b", "impact");

  // imu_event markers
  for (const e of evs) {
    const x = X(e.t);
    if (x < left) continue;
    ctx.strokeStyle = "#ff5a5f"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + ph); ctx.stroke();
    ctx.fillStyle = "#ff5a5f"; ctx.textAlign = x > w - 90 ? "right" : "left"; ctx.font = "800 11px system-ui";
    ctx.fillText(`${e.cls}${e.peak ? " " + Number(e.peak).toFixed(1) + " g" : ""}`, x + (x > w - 90 ? -4 : 4), top + 12);
  }

  // trace
  if (buf.length < 2) {
    ctx.fillStyle = "#8a95a3"; ctx.textAlign = "center"; ctx.font = "13px system-ui";
    ctx.fillText(`Waiting for tel from car #${car}…`, left + pw / 2, top + ph / 2);
    return;
  }
  ctx.beginPath();
  buf.forEach((p, i) => { const x = X(p.t), y = Y(p.g); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
  ctx.strokeStyle = "#4aa3ff"; ctx.lineWidth = 2; ctx.stroke();
}

function frame() {
  drawMap();
  drawTrace();
  requestAnimationFrame(frame);
}

// ---- buttons + clock ----------------------------------------------------------
$("confirmRed").addEventListener("click", () => send({ type: "confirm_red" }));
$("resetBtn").addEventListener("click", () => send({ type: "reset" }));
document.querySelectorAll("[data-scene]").forEach((b) =>
  b.addEventListener("click", () => send({ type: "scene", n: Number(b.dataset.scene) })));
$("clearTrials").addEventListener("click", () => { trials.length = 0; if (latest) renderLatency(latest); });

setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString();
  const ok = MOCK || performance.now() - lastStateAt < 2000;
  $("conn").textContent = MOCK ? "MOCK DATA" : ok ? "LIVE" : "NO SERVER DATA";
  $("conn").className = ok ? "ok" : "bad";
}, 500);

// ---- ?mock=1: fake state so the dashboard works without a server ---------------
// 40 s loop: green -> car 17 crashes wherever it is (8 s) -> camera confirms (10 s) -> then either
// no response for 15 s -> AUTO RED (odd loops), or the driver asks for red -> Confirm red (even loops).
let mock = null;
function mockHandle(msg) {
  if (msg.type === "reset" || msg.type === "scene") { mock.t0 = Date.now(); mock.confirmed = false; mock.loop++; }
  if (msg.type === "confirm_red") mock.confirmed = true;
}

function mockTick() {
  let t = (Date.now() - mock.t0) / 1000;
  const HZ = wrap(1300 + 8 * 165 / 3.6);   // where car 17 happens to be when it crashes at 8 s
  if (t > 40) { mock.t0 = Date.now(); mock.confirmed = false; mock.loop++; t = 0; }
  const noResponse = mock.loop % 2 === 0;
  const crashed = t > 8;
  const cars = [[21, 1213, 180], [8, 900, 170], [44, 400, 185], [3, 2900, 175], [17, 1300, 165]].map(([car, off, kmh]) => {
    const stopped = car === 17 && crashed;
    const speed = stopped ? 0 : crashed && car === 21 ? 80 : kmh;
    const pos = stopped ? HZ : wrap(off + t * speed / 3.6 * (car === 21 && crashed ? 0.2 : 1));
    return { car, track_m: pos, speed_kmh: speed, warning: 0 };
  });
  const state = {
    type: "state", cars, incidents: [], red_pending: false, red_active: false,
    latency: { per_car: { 17: 105 + Math.random() * 25, 21: 95 + Math.random() * 25 }, last_detect_to_warn_ms: crashed ? 410 + (mock.loop % 3) * 35 : null },
    drivers: {},
  };
  const base = 74 + 3 * Math.sin(t / 3);
  let response = null, countdown = null, medical = null;
  if (crashed) {
    const cdLeft = Math.max(0, Math.ceil(23 - t));
    if (noResponse) { response = t < 23 ? "WAITING" : "NO_RESPONSE"; countdown = t < 23 ? cdLeft : null; medical = t < 23 ? null : "URGENT"; }
    else { response = t < 12 ? "WAITING" : "OK"; countdown = t < 12 ? Math.ceil(23 - t) : null; medical = t < 12 ? null : "MONITOR"; }
    const sources = ["IMU", ...(t > 10 ? ["CAMERA"] : []), ...(noResponse && t >= 23 ? ["NO_RESPONSE"] : []), ...(!noResponse && t >= 14 ? ["DRIVER"] : [])];
    const sev4 = noResponse ? t >= 23 : t >= 14;
    state.incidents.push({
      id: "mock-" + mock.loop, kind: t > 10 ? "DISABLED_VEHICLE" : "IMPACT", car: 17,
      corner: (track.corners.reduce((a, c) => (Math.abs(c.m - HZ) < Math.abs(a.m - HZ) ? c : a), track.corners[0]) || {}).name, track_m: HZ,
      severity: sev4 ? 4 : 3, fused_conf: t > 10 ? 0.9916 : 0.88, sources, medical,
      created_ms: mock.t0 + 8000, updated_ms: Date.now(),
    });
    state.red_active = sev4 && (noResponse || mock.confirmed);
    state.red_pending = sev4 && !state.red_active;
    for (const c of cars) {
      if (c.car === 17) continue;
      const eta = c.speed_kmh ? upstream(c.track_m, HZ) / (c.speed_kmh / 3.6) : null;
      const max = sev4 ? 4 : 3;
      c.warning = state.red_active ? 5 : eta == null || eta < 6 ? max : eta < 20 ? 2 : eta < 40 ? 1 : 0;
    }
  }
  state.drivers[17] = { hr: crashed ? base + (140 - base) * Math.min(1, (t - 8) / 5) : base, spo2: 97, vitals: "SIM", response, countdown_s: countdown, medical };
  state.drivers[21] = { hr: 78, spo2: 98, vitals: "SIM", response: null, countdown_s: null, medical: null };
  onState(state);

  // fake phone telemetry for car 17 (10 Hz) with a crash spike at 8 s
  const g = crashed ? (t < 8.3 ? 5.4 - (t - 8) * 8 : 0.03 + Math.random() * 0.03) : 0.15 + Math.random() * 0.25 + (Math.random() < 0.03 ? 1.2 : 0);
  onTel(17, Math.max(0, g));
  onTel(21, 0.2 + Math.random() * 0.3);
  if (crashed && !mock.firedFor[mock.loop]) { mock.firedFor[mock.loop] = true; onImuEvent(17, "IMPACT", 5.4); }
}

if (MOCK) {
  mock = { t0: Date.now(), confirmed: false, loop: 0, firedFor: {} };
  $("modeNote").textContent = "Mock mode: crash at 8 s; loops alternate AUTO red (no response) and driver-requested red (click CONFIRM RED).";
  setInterval(mockTick, 100);
} else {
  connect();
}
requestAnimationFrame(frame);
