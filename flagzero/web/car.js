// FlagZero phone car node (Person C).
// Crash detection from the phone's motion sensors, full-screen flag warnings,
// and the "I'm OK" driver check. Protocol: v2 build prompts (docs/protocol.md)
// plus the additions in docs/phone_changes.md.
// Open as  https://<tunnel>/car?car=17  -- the socket is /ws/car?car=N on the same host.

// ---- tunables (move to config via the server later if needed) --------------
const CFG = {
  TRIGGER_G: 2.0,        // dynamic g that starts an event capture
  TRIGGER_DPS: 250,      // rotation rate (deg/s) that starts an event capture
  CAPTURE_MS: 300,       // how long to record an event before classifying
  POST_MS: 1500,         // watch window after an event for "car stopped"
  STILL_G: 0.15,
  STILL_DPS: 20,
  STILL_NEEDED_MS: 1000, // stillness needed inside POST_MS to send imu_update still:true
  CRASH_G: 3.0,          // peak g for IMPACT (below = KERB/SPIN)
  SEVERE_G: 6.0,         // peak g for SEVERE
  SPIN_DPS: 300,
  SPIN_ROT_DEG: 120,
  FLIP_DOT: -0.5,        // gravity direction dot product below this = ROLLOVER (>120 deg)
  COOLDOWN_MS: 2500,
  COUNTDOWN_S: 20,       // local fallback; the server's countdown.secs wins
  LOCAL_COUNTDOWN_WAIT_MS: 500,
  TEL_MS: 100,           // tel at 10 Hz
  LINK_TIMEOUT_MS: 3000,
};

const params = new URLSearchParams(location.search);
const CAR_ID = parseInt(params.get("car") || "17", 10);
const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/car?car=" + CAR_ID;
const G = 9.81;

// Warning levels 0-5 (v2): NORMAL, CAUTION, YELLOW, DBL YELLOW, SLOW ZONE, RED
const LEVELS = [
  { bg: "#0b7a2a", fg: "#fff", text: "TRACK CLEAR" },
  { bg: "#8a6d00", fg: "#fff", text: "CAUTION", beeps: 1 },
  { bg: "#ffd400", fg: "#111", text: "YELLOW", beeps: 2 },
  { bg: "#ffd400", fg: "#111", text: "DOUBLE YELLOW", beeps: 3, flash: true },
  { bg: "#ff9800", fg: "#111", text: "SLOW ZONE", beeps: 3 },
  { bg: "#d50000", fg: "#fff", text: "RED FLAG", beeps: 6, sub: "STOP" },
];

const $ = (id) => document.getElementById(id);

// ---- sound, vibration, wake lock --------------------------------------------
let audioCtx = null;
function beep(freq = 880, ms = 120, delayMs = 0) {
  if (!audioCtx) return;
  const t = audioCtx.currentTime + delayMs / 1000;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.frequency.value = freq;
  osc.type = "square";
  gain.gain.setValueAtTime(0.25, t);
  gain.gain.setValueAtTime(0, t + ms / 1000);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start(t);
  osc.stop(t + ms / 1000 + 0.02);
}
function beepPattern(n, freq = 1000) {
  for (let i = 0; i < n; i++) beep(freq, 110, i * 220);
  if (navigator.vibrate) navigator.vibrate(Array(n).fill([150, 80]).flat()); // Android only
}

async function keepAwake() {
  try { if ("wakeLock" in navigator) await navigator.wakeLock.request("screen"); } catch (e) { /* not fatal */ }
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });

// ---- connection -------------------------------------------------------------
// Important messages are queued while offline and flushed on reconnect; tel is not.
let ws = null, lastMsgAt = 0, sawPing = false, backoffMs = 1000, eventCount = 0;
const queue = [];

function send(msg, { queueIfOffline = false } = {}) {
  const payload = JSON.stringify({ car: CAR_ID, ...msg });
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(payload);
  else if (queueIfOffline) queue.push(payload);
}
const sendImportant = (msg) => send(msg, { queueIfOffline: true });

function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    backoffMs = 1000;
    lastMsgAt = performance.now();
    send({ type: "hello", platform: /iPhone|iPad/.test(navigator.userAgent) ? "ios" : "android" });
    while (queue.length) ws.send(queue.shift());
  };
  ws.onclose = () => { setTimeout(connect, backoffMs); backoffMs = Math.min(backoffMs * 2, 8000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    lastMsgAt = performance.now();
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleMessage(msg);
  };
}

function linkOk() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  // Before the server has pinged us at all, an open socket counts as linked.
  return !sawPing || performance.now() - lastMsgAt < CFG.LINK_TIMEOUT_MS;
}

// ---- incoming messages ------------------------------------------------------
let medicalStatus = null;

function handleMessage(m) {
  switch (m.type) {
    case "ping":
      sawPing = true;
      send({ type: "ack", id: m.id });
      break;
    case "warning":
      if (m.id != null) send({ type: "ack", id: m.id });
      showLevel(m.level ?? 0, m.corner, m.dist_m, m.eta_s);
      break;
    case "countdown":
      startCountdown(m.secs || CFG.COUNTDOWN_S, m.peak_g, false);
      break;
    case "medical":
      medicalStatus = m.status;
      if (overlayMode && overlayMode !== "countdown") $("cdResult").textContent = medicalLine();
      break;
    case "reset":
      resetLocal();
      break;
  }
}

function medicalLine(fallback) {
  return `MEDICAL: ${medicalStatus || fallback || "—"}`;
}

// ---- flag display -----------------------------------------------------------
let currentLevel = 0;
function showLevel(level, corner, distM, etaS) {
  level = Math.max(0, Math.min(5, level | 0));
  const f = LEVELS[level];
  const dash = $("dash");
  dash.style.background = f.bg;
  dash.style.color = f.fg;
  dash.classList.toggle("flash", !!f.flash);
  $("flag").textContent = f.text;
  $("where").textContent = level ? (corner || "") : "";
  $("dist").textContent = level && distM != null ? `${Math.max(0, Math.round(distM))} m` : "";
  const bits = [];
  if (f.sub) bits.push(f.sub);
  if (level && etaS != null) bits.push(`ETA ${Number(etaS).toFixed(1)} s`);
  $("sub").textContent = bits.join("  ·  ");

  if (level !== currentLevel) {
    if (level > currentLevel && f.beeps && overlayMode !== "countdown") beepPattern(f.beeps, level === 5 ? 600 : 1000);
    currentLevel = level;
  }
  refreshButtons();
}

// ---- "I'm OK" countdown and driver actions ----------------------------------
// Drivers can escalate (I'm OK, recommend red) and inform (report a false alarm), but they can
// never return a flag to green: only race control's dashboard Reset does that (a "reset" message).
// Overlay modes: null | "countdown" | "ok" | "reported" (false alarm reported) | "red" (driver asked for red) | "timeout"
let cdTimer = null, cdLeft = 0, overlayMode = null;

function setOverlay(mode) {
  overlayMode = mode;
  const cd = $("cd");
  cd.classList.toggle("hidden", mode === null);
  cd.classList.toggle("flash", mode === "countdown");
  cd.style.background = mode === "ok" ? "#0b7a2a" : "";
  $("cdNum").classList.toggle("hidden", mode !== "countdown");
  refreshButtons();
}

function refreshButtons() {
  const m = overlayMode;
  const underRed = currentLevel === 5;
  $("okBtn").classList.toggle("hidden", m !== "countdown");
  $("falseBtn").classList.toggle("hidden", !(m === "ok" && !underRed));
  $("redBtn").classList.toggle("hidden", !(m === "countdown" || ((m === "ok" || m === "reported") && !underRed)));
  // after asking for red, or with no response, the screen stays up until race control resets
  $("continueBtn").classList.toggle("hidden", !(m === "ok" || m === "reported"));
}

function startCountdown(seconds, peakG, local) {
  if (overlayMode === "countdown") return;
  clearInterval(cdTimer);
  cdLeft = seconds;
  medicalStatus = null;
  $("cdTitle").textContent = "IMPACT DETECTED";
  $("cdInfo").textContent = (peakG ? `${Number(peakG).toFixed(1)} g  ·  ` : "") + (local ? "local check" : "race control check");
  $("cdResult").textContent = "";
  $("cdNum").textContent = cdLeft;
  setOverlay("countdown");
  beep(1400, 150);
  cdTimer = setInterval(() => {
    cdLeft -= 1;
    $("cdNum").textContent = Math.max(0, cdLeft);
    if (cdLeft > 0) { beep(1400, 150); if (navigator.vibrate) navigator.vibrate(200); }
    else driverTimeout();
  }, 1000);
}

function driverOk() {
  clearInterval(cdTimer);
  sendImportant({ type: "ok_pressed" });
  sendImportant({ type: "countdown_result", result: "OK" });
  $("cdTitle").textContent = "DRIVER OK";
  $("cdInfo").textContent = "";
  $("cdResult").textContent = medicalLine("MONITOR") + " · yellow flags stay out";
  setOverlay("ok");
  beep(700, 100);
}

function driverTimeout() {
  clearInterval(cdTimer);
  sendImportant({ type: "countdown_result", result: "TIMEOUT" });
  $("cdTitle").textContent = "NO RESPONSE";
  $("cdInfo").textContent = "";
  $("cdResult").textContent = medicalLine("URGENT") + " · race control notified";
  setOverlay("timeout");
  beepPattern(5, 500);
}

function driverRequestsRed() {
  if (overlayMode === "countdown") {       // pressing this proves the driver is responsive
    clearInterval(cdTimer);
    sendImportant({ type: "ok_pressed" });
    sendImportant({ type: "countdown_result", result: "OK" });
  }
  sendImportant({ type: "driver_request", request: "RED_FLAG" });
  $("cdTitle").textContent = "RED FLAG REQUESTED";
  $("cdInfo").textContent = "";
  $("cdResult").textContent = "Race control notified · " + medicalLine("MONITOR");
  setOverlay("red");
  beepPattern(2, 600);
}

function driverReportsFalseAlarm() {
  // Only a report: race control sees it on the dashboard and decides whether to clear the flags.
  sendImportant({ type: "driver_request", request: "FALSE_ALARM" });
  $("cdTitle").textContent = "FALSE ALARM REPORTED";
  $("cdInfo").textContent = "";
  $("cdResult").textContent = "Race control decides · flags stay out until they clear them";
  setOverlay("reported");
  beep(700, 100);
}

function resetLocal() {   // only on race control's "reset"
  clearInterval(cdTimer);
  medicalStatus = null;
  setOverlay(null);
  showLevel(0);
}

$("okBtn").addEventListener("click", () => { if (overlayMode === "countdown" && cdLeft > 0) driverOk(); });
$("redBtn").addEventListener("click", driverRequestsRed);
$("falseBtn").addEventListener("click", driverReportsFalseAlarm);
$("continueBtn").addEventListener("click", () => setOverlay(null));

// ---- crash detection --------------------------------------------------------
// IDLE -> CAPTURE (300 ms, record features) -> classify + send imu_event
//      -> POST (1.5 s, check the phone stays still -> imu_update) -> IDLE
let state = "IDLE", cap = null, postStart = 0, postStillMs = 0, cooldownUntil = 0, lastCls = null;
let lastT = 0, gravLP = null, gravDir = null;
let telPeakG = 0, telPeakDps = 0, motionCount = 0, motionHz = 0, nowG = 0, nowDps = 0;

const norm = (v) => { const m = Math.hypot(v.x, v.y, v.z) || 1; return { x: v.x / m, y: v.y / m, z: v.z / m }; };
const dot = (a, b) => a.x * b.x + a.y * b.y + a.z * b.z;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function onMotion(e) {
  const now = performance.now();
  const dt = lastT ? Math.min((now - lastT) / 1000, 0.1) : 1 / 60;
  lastT = now;
  motionCount++;

  const ig = e.accelerationIncludingGravity || { x: 0, y: 0, z: G };
  const igv = { x: ig.x || 0, y: ig.y || 0, z: ig.z || 0 };

  // slow low-pass of gravity: fallback for phones without e.acceleration, and flip detection
  if (!gravLP) gravLP = { ...igv };
  const k = state === "IDLE" ? 0.05 : 0; // freeze the "before" direction during an event
  gravLP.x += k * (igv.x - gravLP.x); gravLP.y += k * (igv.y - gravLP.y); gravLP.z += k * (igv.z - gravLP.z);
  if (state === "IDLE") gravDir = norm(gravLP);

  let a;
  if (e.acceleration && e.acceleration.x != null) a = e.acceleration;
  else a = { x: igv.x - gravLP.x, y: igv.y - gravLP.y, z: igv.z - gravLP.z };
  const dynG = Math.hypot(a.x || 0, a.y || 0, a.z || 0) / G;
  const r = e.rotationRate || {};
  const dps = Math.hypot(r.alpha || 0, r.beta || 0, r.gamma || 0);

  nowG = dynG; nowDps = dps;
  telPeakG = Math.max(telPeakG, dynG);
  telPeakDps = Math.max(telPeakDps, dps);
  const still = dynG < CFG.STILL_G && dps < CFG.STILL_DPS;

  if (state === "IDLE") {
    if (now > cooldownUntil && (dynG > CFG.TRIGGER_G || dps > CFG.TRIGGER_DPS)) {
      state = "CAPTURE";
      cap = { t0: now, peakG: dynG, durMs: 0, peakDps: dps, rotDeg: 0, grav0: gravDir || norm(igv) };
    }
  }
  if (state === "CAPTURE") {
    cap.peakG = Math.max(cap.peakG, dynG);
    cap.peakDps = Math.max(cap.peakDps, dps);
    cap.rotDeg += dps * dt;
    if (dynG > CFG.TRIGGER_G) cap.durMs += dt * 1000;
    if (now - cap.t0 >= CFG.CAPTURE_MS) finishCapture(igv, now);
  } else if (state === "POST") {
    postStillMs = still ? postStillMs + dt * 1000 : 0;
    if (now - postStart >= CFG.POST_MS) finishPost(igv, now);
  }
}

const flipped = (igv) => cap && dot(norm(igv), cap.grav0) < CFG.FLIP_DOT;

function classify(igv) {
  const c = cap;
  if (flipped(igv)) return { cls: "ROLLOVER", conf: 0.9 };
  if (c.peakG >= CFG.SEVERE_G) return { cls: "SEVERE", conf: clamp(0.8 + 0.03 * (c.peakG - CFG.SEVERE_G), 0.8, 0.97) };
  if (c.peakG >= CFG.CRASH_G) return { cls: "IMPACT", conf: clamp(0.6 + 0.08 * (c.peakG - CFG.CRASH_G), 0.6, 0.9) };
  if (c.peakDps >= CFG.SPIN_DPS || c.rotDeg >= CFG.SPIN_ROT_DEG) {
    return { cls: "SPIN", conf: clamp(0.6 + (c.peakDps - CFG.SPIN_DPS) / 1000, 0.6, 0.95) };
  }
  return { cls: "KERB", conf: 0.5 };
}

const isCrash = (cls) => cls === "IMPACT" || cls === "SEVERE" || cls === "ROLLOVER";

function finishCapture(igv, now) {
  const c = classify(igv);
  lastCls = c.cls;
  sendImuEvent(c);
  if (isCrash(c.cls)) armLocalCountdown(cap.peakG);
  state = "POST";
  postStart = now;
  postStillMs = 0;
}

function finishPost(igv, now) {
  if (lastCls !== "KERB" && postStillMs >= CFG.STILL_NEEDED_MS) {
    sendImportant({ type: "imu_update", still: true });
  }
  if (lastCls !== "ROLLOVER" && flipped(igv)) { // a flip that finished after the capture window
    lastCls = "ROLLOVER";
    sendImuEvent({ cls: "ROLLOVER", conf: 0.9 });
    armLocalCountdown(cap.peakG);
  }
  state = "IDLE";
  cap = null;
  cooldownUntil = now + CFG.COOLDOWN_MS;
}

function sendImuEvent(c, extra = {}) {
  eventCount++;
  sendImportant({
    type: "imu_event",
    cls: c.cls,
    peak_g: +cap.peakG.toFixed(2),
    dur_ms: Math.round(cap.durMs),
    gyro_peak: Math.round(cap.peakDps),
    rot_deg: Math.round(cap.rotDeg),
    conf: +c.conf.toFixed(2),
    capture_ms: CFG.CAPTURE_MS,
    ...extra,
  });
}

function armLocalCountdown(peakG) {
  // Fail-safe: if the server hasn't started the driver check shortly after a crash, start it ourselves.
  setTimeout(() => { if (overlayMode === null) startCountdown(CFG.COUNTDOWN_S, peakG, true); }, CFG.LOCAL_COUNTDOWN_WAIT_MS);
}

$("testBtn").addEventListener("click", () => {
  cap = { peakG: 5.0, durMs: 40, peakDps: 120, rotDeg: 20 };
  lastCls = "IMPACT";
  sendImuEvent({ cls: "IMPACT", conf: 0.85 }, { test: true });
  armLocalCountdown(5.0);
  cap = null;
});

// ---- telemetry + debug strip ------------------------------------------------
setInterval(() => {
  send({ type: "tel", g: +telPeakG.toFixed(2), gyro: Math.round(telPeakDps) });
  telPeakG = 0;
  telPeakDps = 0;
}, CFG.TEL_MS);

setInterval(() => {
  motionHz = motionCount;
  motionCount = 0;
  const ok = linkOk();
  const link = $("link");
  link.textContent = ok ? "LINKED" : "NO LINK – LOCAL MODE";
  link.className = ok ? "ok" : "bad";
  $("debug").textContent =
    `g ${nowG.toFixed(2)}  rot ${Math.round(nowDps)}°/s  sensor ${motionHz} Hz  state ${state}\n` +
    `events ${eventCount} (last: ${lastCls || "-"})  queued ${queue.length}` +
    (motionHz === 0 ? "\nNO MOTION DATA – check permission / use https" : "");
}, 1000);

// ---- start ------------------------------------------------------------------
$("startCar").textContent = `Car #${CAR_ID}`;
$("carTag").textContent = `#${CAR_ID}`;

$("startBtn").addEventListener("click", async () => {
  // iOS: motion permission must be requested inside the tap handler
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    try {
      const res = await DeviceMotionEvent.requestPermission();
      if (res !== "granted") { alert("Motion permission was denied. Close the tab, reopen the link and tap Allow."); return; }
    } catch (e) {
      alert("Motion permission failed. The page must be opened over https (the tunnel link)."); return;
    }
  }
  try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); beep(880, 80); } catch (e) { /* no sound */ }
  keepAwake();
  window.addEventListener("devicemotion", onMotion);
  $("start").classList.add("hidden");
  $("dash").classList.remove("hidden");
  showLevel(0);
  connect();
});
