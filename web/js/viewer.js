// Roomwalk viewer: walk through a scanned room in first person.
import * as THREE from "three";
import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";

const $ = (id) => document.getElementById(id);
const sceneId = new URLSearchParams(location.search).get("id") || "";
const base = `/scenes/${encodeURIComponent(sceneId)}/`;
const isTouch = matchMedia("(pointer: coarse)").matches;
const BODY_RADIUS = 0.2; // metres: how close you can get to a wall

// ---- small persisted preferences (per browser) -----------------------------
let prefs = {};
try { prefs = JSON.parse(localStorage.getItem("roomwalk") || "{}"); } catch { prefs = {}; }
const savePrefs = () => { try { localStorage.setItem("roomwalk", JSON.stringify(prefs)); } catch {} };

// ---- state ------------------------------------------------------------------
let info = null;            // scene.json
let splat = null;           // current SplatMesh
let grid = null;            // collision grid
const mapImages = { plan: null, photo: null };
let mapMode = prefs.mapMode === "photo" ? "photo" : "plan";
let mapView = { s: 1, ox: 0, oy: 0 };
let mapBig = false;
const player = { x: 0, z: 0, lift: 0, yaw: 0, pitch: 0, vx: 0, vz: 0, vy: 0 };
const keys = {};
const stick = { x: 0, y: 0, id: null, ox: 0, oy: 0 };
let look = { id: null, x: 0, y: 0, moved: 0 };
let eyeHeight = 1.6, walkSpeed = prefs.speed || 1.4, wallsOn = true, flying = false;
let tour = null;
let dirty = true;
let autoRatio = Math.min(window.devicePixelRatio || 1, 1.25);
let slowFrames = 0, lastRenderAt = 0;

// ---- three.js setup ---------------------------------------------------------
const canvas = $("view");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
} catch (err) {
  fail("This browser can't show 3D graphics (WebGL 2 is switched off or not supported). Try the latest Chrome or Edge.");
  throw err;
}
renderer.setClearColor(0x070f1f, 1);
const scene3 = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(prefs.fov || 62, 1, 0.05, 300);
camera.rotation.order = "YXZ";
const spark = new SparkRenderer({ renderer, onDirty: () => { dirty = true; } });
scene3.add(spark);
const world = new THREE.Group();
scene3.add(world);

function fail(message) {
  $("loading").hidden = false;
  $("loadingName").textContent = "Can't open this room";
  $("loadingText").innerHTML = "";
  $("loadingText").append(message + " ");
  const a = document.createElement("a");
  a.href = "/"; a.textContent = "Back to your scans";
  $("loadingText").append(a);
  document.querySelector(".meter").hidden = true;
}

function setLoading(fraction, text) {
  if (fraction != null) $("loadingBar").style.width = Math.round(fraction * 100) + "%";
  if (text) $("loadingText").textContent = text;
}

async function fetchInfo() {
  const r = await fetch(base + "scene.json?t=" + Date.now(), { cache: "no-store" });
  if (!r.ok) throw new Error("not-ready");
  return r.json();
}

async function loadSplat(inf, showProgress) {
  const url = base + inf.splat.file + "?v=" + inf.splat.version;
  const mesh = new SplatMesh({
    url,
    onProgress: (e) => {
      if (showProgress && e && e.total) setLoading(0.1 + 0.8 * (e.loaded / e.total),
        `Loading the scan (${Math.round(e.loaded / 1048576)} of ${Math.round(e.total / 1048576)} MB)`);
    },
  });
  new THREE.Matrix4().fromArray(inf.matrix).decompose(mesh.position, mesh.quaternion, mesh.scale);
  await mesh.initialized;
  return mesh;
}

function loadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

async function loadMaps(inf) {
  const v = "?v=" + inf.splat.version + "-" + Math.round(inf.updated || 0);
  const [plan, photo, coll] = await Promise.all([
    loadImage(base + inf.map.plan + v), loadImage(base + inf.map.photo + v),
    loadImage(base + inf.map.collision + v),
  ]);
  mapImages.plan = plan; mapImages.photo = photo;
  grid = null;
  if (coll) {
    const c = document.createElement("canvas");
    c.width = inf.map.width; c.height = inf.map.height;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.drawImage(coll, 0, 0, c.width, c.height);
    const px = g.getImageData(0, 0, c.width, c.height).data;
    const data = new Uint8Array(c.width * c.height);
    for (let i = 0; i < data.length; i++) data[i] = px[i * 4] > 127 ? 1 : 0;
    grid = { w: c.width, h: c.height, cell: inf.map.cell, x0: inf.map.x0, z0: inf.map.z0, data };
  }
}

// ---- collision --------------------------------------------------------------
function blockedAt(x, z) {
  if (!grid) return false;
  const cx = Math.floor((x - grid.x0) / grid.cell), cz = Math.floor((z - grid.z0) / grid.cell);
  if (cx < 0 || cz < 0 || cx >= grid.w || cz >= grid.h) return true; // edge of the scanned area
  return grid.data[cz * grid.w + cx] === 1;
}
function canStand(x, z) {
  if (blockedAt(x, z)) return false;
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4;
    if (blockedAt(x + Math.cos(a) * BODY_RADIUS, z + Math.sin(a) * BODY_RADIUS)) return false;
  }
  return true;
}
function tryMove(dx, dz) {
  if (!wallsOn || flying) { player.x += dx; player.z += dz; return; }
  const n = Math.max(1, Math.ceil(Math.hypot(dx, dz) / 0.02));
  const sx = dx / n, sz = dz / n;
  for (let i = 0; i < n; i++) {
    const stuck = !canStand(player.x, player.z); // started inside something: let them out
    if (stuck || canStand(player.x + sx, player.z + sz)) { player.x += sx; player.z += sz; }
    else if (canStand(player.x + sx, player.z)) { player.x += sx; }   // slide along the wall
    else if (canStand(player.x, player.z + sz)) { player.z += sz; }
    else break;
  }
}
function teleport(x, z) {
  if (wallsOn && !flying && !canStand(x, z)) {
    let found = null;
    for (let r = 0.1; r <= 1.5 && !found; r += 0.1) {
      for (let i = 0; i < 16; i++) {
        const a = i * Math.PI / 8, tx = x + Math.cos(a) * r, tz = z + Math.sin(a) * r;
        if (canStand(tx, tz)) { found = [tx, tz]; break; }
      }
    }
    if (!found) { notify("That spot is inside a wall or furniture. Tap open floor instead.", 3500); return; }
    [x, z] = found;
  }
  stopTour();
  player.x = x; player.z = z; player.vx = player.vz = 0;
  dirty = true;
}

// ---- notices ----------------------------------------------------------------
let noticeTimer = 0;
function notify(text, ms) {
  const el = $("notice");
  el.textContent = text; el.hidden = !text;
  clearTimeout(noticeTimer);
  if (ms) noticeTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// ---- input ------------------------------------------------------------------
const anyInput = () => { hideHintSoon(); stopTour(); };
addEventListener("keydown", (e) => {
  if (e.target.matches?.("input, select, textarea")) return;
  keys[e.code] = true;
  if (e.code === "KeyM") toggleMapSize();
  else if (e.code === "KeyT") { tour ? stopTour() : startTour(); }
  else if (e.code === "KeyF") { $("fly").checked = !$("fly").checked; $("fly").dispatchEvent(new Event("change")); }
  else if (/^(Key[WASDQE]|Arrow)/.test(e.code)) anyInput();
  if (/^Arrow|Space/.test(e.code)) e.preventDefault();
});
addEventListener("keyup", (e) => { keys[e.code] = false; });
addEventListener("blur", () => { for (const k in keys) keys[k] = false; });

function turn(dx, dy, sensitivity) {
  player.yaw -= dx * sensitivity;
  player.pitch = Math.max(-1.35, Math.min(1.35, player.pitch - dy * sensitivity));
  dirty = true;
}

canvas.addEventListener("pointerdown", (e) => {
  canvas.focus();
  if (e.pointerType === "touch") {
    const leftZone = e.clientX < innerWidth * 0.42 && e.clientY > innerHeight * 0.3;
    if (leftZone && stick.id === null) {
      stick.id = e.pointerId; stick.ox = e.clientX; stick.oy = e.clientY;
      const el = $("stick");
      el.hidden = false; el.style.left = e.clientX + "px"; el.style.top = e.clientY + "px";
      el.firstElementChild.style.transform = "translate(-50%, -50%)";
      anyInput();
    } else if (look.id === null) {
      look = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: 0 };
    }
  } else if (document.pointerLockElement !== canvas) {
    look = { id: e.pointerId, x: e.clientX, y: e.clientY, moved: 0 };
  }
  canvas.setPointerCapture?.(e.pointerId);
});
canvas.addEventListener("pointermove", (e) => {
  if (document.pointerLockElement === canvas) {
    turn(e.movementX, e.movementY, 0.0022);
    stopTour();
    return;
  }
  if (e.pointerId === stick.id) {
    const max = 56;
    let dx = e.clientX - stick.ox, dy = e.clientY - stick.oy;
    const len = Math.hypot(dx, dy);
    if (len > max) { dx *= max / len; dy *= max / len; }
    stick.x = dx / max; stick.y = -dy / max;
    $("stick").firstElementChild.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;
  } else if (e.pointerId === look.id) {
    const dx = e.clientX - look.x, dy = e.clientY - look.y;
    look.x = e.clientX; look.y = e.clientY; look.moved += Math.abs(dx) + Math.abs(dy);
    // dragging moves the picture with your finger, like panning a photo
    turn(-dx, -dy, e.pointerType === "touch" ? 0.0045 : 0.004);
    stopTour();
  }
});
function pointerEnd(e) {
  if (e.pointerId === stick.id) {
    stick.id = null; stick.x = stick.y = 0; $("stick").hidden = true;
  } else if (e.pointerId === look.id) {
    // a plain click (no drag) with a mouse captures it for free looking
    if (e.type === "pointerup" && e.pointerType === "mouse" && look.moved < 4) {
      canvas.requestPointerLock?.();
      hideHintSoon();
    }
    look.id = null;
  }
}
canvas.addEventListener("pointerup", pointerEnd);
canvas.addEventListener("pointercancel", pointerEnd);
canvas.addEventListener("contextmenu", (e) => e.preventDefault());

// ---- tour (replays the filmed path) -----------------------------------------
const shortest = (a) => Math.atan2(Math.sin(a), Math.cos(a));
function startTour() {
  const p = info.path || [];
  if (p.length >= 2) {
    const times = [0];
    for (let i = 1; i < p.length; i++) {
      const d = Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1]);
      const dy = Math.abs(shortest(p[i][2] - p[i - 1][2]));
      times.push(times[i - 1] + Math.max(0.12, d / 0.8 + dy / 1.1));
    }
    tour = { t: 0, times, i: 0 };
    player.x = p[0][0]; player.z = p[0][1]; player.yaw = p[0][2];
  } else {
    tour = { t: 0, spin: player.yaw };
  }
  player.vx = player.vz = 0;
  $("tourBtn").textContent = "Stop tour";
  dirty = true;
}
function stopTour() {
  if (!tour) return;
  tour = null;
  $("tourBtn").textContent = info.path && info.path.length >= 2 ? "Play tour" : "Look around";
}
function stepTour(dt) {
  tour.t += dt;
  const ease = 1 - Math.exp(-dt * 3.5);
  let tx = player.x, tz = player.z, tyaw;
  if (tour.times) {
    const { times } = tour, p = info.path;
    while (tour.i < times.length - 2 && times[tour.i + 1] < tour.t) tour.i++;
    const i = tour.i;
    const a = Math.min(1, Math.max(0, (tour.t - times[i]) / (times[i + 1] - times[i])));
    tx = p[i][0] + (p[i + 1][0] - p[i][0]) * a;
    tz = p[i][1] + (p[i + 1][1] - p[i][1]) * a;
    tyaw = p[i][2] + shortest(p[i + 1][2] - p[i][2]) * a;
    if (tour.t >= times[times.length - 1]) { stopTour(); return; }
  } else {
    tyaw = tour.spin + tour.t * 0.35;
    if (tour.t * 0.35 > Math.PI * 2) { stopTour(); return; }
  }
  player.x += (tx - player.x) * ease;
  player.z += (tz - player.z) * ease;
  player.yaw += shortest(tyaw - player.yaw) * ease;
  player.pitch += (0 - player.pitch) * ease;
}

// ---- per-frame update ---------------------------------------------------------
function update(dt) {
  if (tour) { stepTour(dt); return; }
  let f = (keys.KeyW || keys.ArrowUp ? 1 : 0) - (keys.KeyS || keys.ArrowDown ? 1 : 0) + stick.y;
  let s = (keys.KeyD ? 1 : 0) - (keys.KeyA ? 1 : 0) + stick.x;
  const spin = (keys.ArrowLeft ? 1 : 0) - (keys.ArrowRight ? 1 : 0);
  if (spin) player.yaw += spin * 1.9 * dt;
  const len = Math.hypot(f, s);
  if (len > 1) { f /= len; s /= len; }
  const speed = walkSpeed * (keys.ShiftLeft || keys.ShiftRight ? 2.2 : 1);
  const sin = Math.sin(player.yaw), cos = Math.cos(player.yaw);
  const tx = (-sin * f + cos * s) * speed, tz = (-cos * f - sin * s) * speed;
  const k = 1 - Math.exp(-dt * 12);
  player.vx += (tx - player.vx) * k;
  player.vz += (tz - player.vz) * k;
  if (Math.abs(player.vx) + Math.abs(player.vz) > 0.002) tryMove(player.vx * dt, player.vz * dt);
  const ty = flying ? ((keys.KeyE || keys.Space ? 1 : 0) - (keys.KeyQ || keys.KeyC ? 1 : 0)) * speed : 0;
  player.vy += (ty - player.vy) * k;
  if (flying) player.lift += player.vy * dt;
  else player.lift += (0 - player.lift) * (1 - Math.exp(-dt * 6));
}

const lastPose = { x: NaN, y: 0, z: 0, yaw: 0, pitch: 0 };
function frame(now) {
  const dt = Math.min(0.25, (now - (frame.last || now)) / 1000);
  frame.last = now;
  if (!info) return;
  update(dt);
  const y = eyeHeight + player.lift;
  const moved = Math.abs(player.x - lastPose.x) + Math.abs(player.z - lastPose.z) + Math.abs(y - lastPose.y) > 1e-4
    || Math.abs(player.yaw - lastPose.yaw) + Math.abs(player.pitch - lastPose.pitch) > 1e-5;
  if (!moved && !dirty) return;
  dirty = false;
  Object.assign(lastPose, { x: player.x, y, z: player.z, yaw: player.yaw, pitch: player.pitch });
  camera.position.set(player.x, y, player.z);
  camera.rotation.set(player.pitch, player.yaw, 0);
  renderer.render(scene3, camera);
  drawMap();
  // automatic sharpness: if walking is choppy, render fewer pixels
  if (moved && (prefs.quality || "auto") === "auto") {
    const gap = now - lastRenderAt;
    slowFrames = gap > 45 && gap < 500 ? slowFrames + 1 : Math.max(0, slowFrames - 2);
    if (slowFrames > 40 && autoRatio > 0.5) {
      autoRatio = Math.max(0.5, autoRatio * 0.8); slowFrames = 0; resize();
    }
  }
  lastRenderAt = now;
}

function resize() {
  const q = prefs.quality || "auto";
  const dpr = window.devicePixelRatio || 1;
  const ratio = q === "auto" ? autoRatio : q === "2" ? Math.min(dpr, 2) : Math.min(Number(q), Math.max(dpr, 1));
  renderer.setPixelRatio(ratio);
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  dirty = true;
}
addEventListener("resize", resize);

// ---- minimap ------------------------------------------------------------------
function drawMap() {
  const c = $("mapCanvas");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cw = c.clientWidth, ch = c.clientHeight;
  if (!cw || !ch) return;
  if (c.width !== Math.round(cw * dpr) || c.height !== Math.round(ch * dpr)) {
    c.width = Math.round(cw * dpr); c.height = Math.round(ch * dpr);
  }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, cw, ch);
  const m = info.map, mw = m.width * m.cell, mh = m.height * m.cell, pad = 8;
  const s = Math.min((cw - 2 * pad) / mw, (ch - 2 * pad) / mh);
  const ox = (cw - mw * s) / 2, oy = (ch - mh * s) / 2;
  mapView = { s, ox, oy };
  const img = mapImages[mapMode] || mapImages.plan;
  if (img) g.drawImage(img, ox, oy, mw * s, mh * s);
  const toMap = (x, z) => [ox + (x - m.x0) * s, oy + (z - m.z0) * s];

  if (info.path && info.path.length > 1) {
    g.beginPath();
    info.path.forEach((p, i) => { const [x, y] = toMap(p[0], p[1]); i ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.setLineDash([2, 4]); g.lineWidth = 1; g.strokeStyle = "rgba(222,233,255,.38)"; g.stroke();
    g.setLineDash([]);
  }
  const [px, py] = toMap(player.x, player.z);
  const heading = Math.atan2(-Math.cos(player.yaw), -Math.sin(player.yaw));
  const half = Math.atan(Math.tan(camera.fov * Math.PI / 360) * camera.aspect);
  const reach = Math.max(26, Math.min(cw, ch) * 0.24);
  const cone = g.createRadialGradient(px, py, 2, px, py, reach);
  cone.addColorStop(0, "rgba(255,180,58,.75)"); cone.addColorStop(1, "rgba(255,180,58,0)");
  g.beginPath(); g.moveTo(px, py); g.arc(px, py, reach, heading - half, heading + half); g.closePath();
  g.fillStyle = cone; g.fill();
  g.beginPath(); g.arc(px, py, 4.5, 0, Math.PI * 2);
  g.shadowColor = "rgba(255,180,58,.9)"; g.shadowBlur = 10; g.fillStyle = "#ffb43a"; g.fill();
  g.shadowBlur = 0; g.lineWidth = 1.5; g.strokeStyle = "#fff"; g.stroke();
}
$("mapCanvas").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect(), m = info.map;
  teleport((e.clientX - r.left - mapView.ox) / mapView.s + m.x0, (e.clientY - r.top - mapView.oy) / mapView.s + m.z0);
});
function toggleMapSize() {
  mapBig = !mapBig;
  $("minimap").classList.toggle("big", mapBig);
  $("mapSize").textContent = mapBig ? "Shrink" : "Enlarge";
  dirty = true;
}
$("mapSize").addEventListener("click", toggleMapSize);
function showMapMode() { $("mapMode").textContent = mapMode === "plan" ? "Photo" : "Plan"; }
$("mapMode").addEventListener("click", () => {
  mapMode = mapMode === "plan" ? "photo" : "plan";
  prefs.mapMode = mapMode; savePrefs(); showMapMode(); dirty = true;
});

// ---- hint ---------------------------------------------------------------------
let hintTimer = 0;
function showHint() {
  const el = $("hint");
  el.innerHTML = isTouch
    ? "<span>Left thumb walks</span><span>Right thumb looks</span><span>Tap the map to jump there</span>"
    : "<span><kbd>Click</kbd> then move the mouse to look</span><span><kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> walk</span><span><kbd>Shift</kbd> run</span><span><kbd>Esc</kbd> frees the mouse</span>";
  el.hidden = false;
}
function hideHintSoon() {
  if (hintTimer || $("hint").hidden) return;
  hintTimer = setTimeout(() => { $("hint").classList.add("gone"); }, 3500);
}

// ---- settings -------------------------------------------------------------------
function post(path, body) {
  return fetch(path, { method: "POST", headers: { "X-Roomwalk": "1", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
}
let eyeSave = 0;
function wireSettings() {
  const panel = $("settings"), btn = $("settingsBtn");
  btn.addEventListener("click", () => {
    panel.hidden = !panel.hidden; btn.setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("eye").value = eyeHeight; $("eyeOut").textContent = eyeHeight.toFixed(2) + " m";
  $("eye").addEventListener("input", (e) => {
    eyeHeight = Number(e.target.value); $("eyeOut").textContent = eyeHeight.toFixed(2) + " m"; dirty = true;
    clearTimeout(eyeSave);
    eyeSave = setTimeout(() => post(`/api/scenes/${encodeURIComponent(sceneId)}/view`, { eye_height: eyeHeight }), 600);
  });
  $("speed").value = walkSpeed; $("speedOut").textContent = walkSpeed.toFixed(1) + " m/s";
  $("speed").addEventListener("input", (e) => {
    walkSpeed = Number(e.target.value); $("speedOut").textContent = walkSpeed.toFixed(1) + " m/s";
    prefs.speed = walkSpeed; savePrefs();
  });
  $("fov").value = camera.fov; $("fovOut").textContent = Math.round(camera.fov) + "°";
  $("fov").addEventListener("input", (e) => {
    camera.fov = Number(e.target.value); $("fovOut").textContent = Math.round(camera.fov) + "°";
    prefs.fov = camera.fov; savePrefs(); camera.updateProjectionMatrix(); dirty = true;
  });
  $("quality").value = prefs.quality || "auto";
  $("quality").addEventListener("change", (e) => { prefs.quality = e.target.value; savePrefs(); resize(); });
  $("walls").checked = wallsOn;
  $("walls").addEventListener("change", (e) => { wallsOn = e.target.checked; });
  $("fly").addEventListener("change", (e) => { flying = e.target.checked; dirty = true; });
  $("tourBtn").addEventListener("click", () => { tour ? stopTour() : startTour(); });
  if (!(info.path && info.path.length >= 2)) $("tourBtn").textContent = "Look around";
  const full = $("fullBtn");
  if (!document.documentElement.requestFullscreen) full.hidden = true;
  full.addEventListener("click", () => {
    document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
  });
  if (info.kind === "import") {
    $("upRow").hidden = false;
    $("upChoices").addEventListener("click", async (e) => {
      const up = e.target.dataset.up;
      if (!up) return;
      notify("Turning the room and redrawing the map");
      const r = await fetch(`/api/scenes/${encodeURIComponent(sceneId)}/reorient?up=${encodeURIComponent(up)}`,
        { method: "POST", headers: { "X-Roomwalk": "1" } });
      if (r.ok) location.reload(); else notify("Could not turn the room.", 4000);
    });
  }
  $("sceneFacts").textContent = `${info.splat.count.toLocaleString()} splats` +
    (info.frames ? `, from ${info.frames} video frames` : "") + ".";
}

// ---- live updates while a scan is still training ---------------------------------
async function describeProgress() {
  try {
    const r = await fetch(`/api/scenes/${encodeURIComponent(sceneId)}`, { cache: "no-store" });
    const s = (await r.json()).status || {};
    if (s.state === "running") return s.message || "";
    if (s.state === "failed") return "The scan stopped early. Go back to Scans to see why.";
    if (s.state === "queued") return "Waiting for another scan to finish.";
    return "";
  } catch { return ""; }
}
async function showStage() {
  if (info.quality === "final") {
    if (info.kind === "demo") notify("This example room is drawn by code. A real scan looks like your actual room.", 7000);
    return;
  }
  const lead = info.quality === "preview"
    ? "Rough dotted preview from camera tracking. The real scan replaces it as it trains."
    : "Early snapshot. It keeps sharpening while training runs.";
  const more = await describeProgress();
  notify(more ? `${lead} ${more}` : lead);
}
async function pollForUpdates() {
  if (!info || info.quality === "final") return;
  try {
    const next = await fetchInfo();
    if (next.splat.file !== info.splat.file || next.splat.version !== info.splat.version) {
      const mesh = await loadSplat(next, false);
      // keep the visitor in the same real-world spot if the alignment shifted a little
      await loadMaps(next);
      world.add(mesh);
      if (splat) { world.remove(splat); splat.dispose?.(); }
      splat = mesh; info = { ...next, eye_height: info.eye_height };
      if (info.quality !== "preview") { wallsOn = true; $("walls").checked = true; }
      dirty = true;
      if (info.quality === "final") notify("Training finished. This is the final scan.", 6000);
    }
    if (info.quality !== "final") showStage();
  } catch { /* scan folder busy; try again next time */ }
  if (info.quality !== "final") setTimeout(pollForUpdates, 12000);
}

// ---- start ----------------------------------------------------------------------
async function start() {
  if (!sceneId) return fail("No scan was chosen.");
  try {
    info = await fetchInfo();
  } catch {
    return fail("This scan isn't ready to view yet.");
  }
  document.title = info.name + " - Roomwalk";
  $("loadingName").textContent = info.name;
  setLoading(0.05, "Loading the scan");
  try {
    [splat] = await Promise.all([loadSplat(info, true), loadMaps(info)]);
  } catch (err) {
    console.error(err);
    return fail("The scan file could not be loaded.");
  }
  world.add(splat);
  eyeHeight = info.eye_height || 1.6;
  player.x = info.start.x; player.z = info.start.z; player.yaw = info.start.yaw;
  wallsOn = info.quality !== "preview";
  wireSettings();
  showMapMode();
  resize();
  $("loading").hidden = true;
  showHint();
  showStage();
  if (info.quality !== "final") setTimeout(pollForUpdates, 12000);
  renderer.setAnimationLoop(frame);
  window.__roomwalk = { player, info, canStand, teleport, startTour, get tour() { return tour; },
    renderNow: () => { dirty = true; } };
}
start();
