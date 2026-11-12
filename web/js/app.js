// Roomwalk home page: start scans, watch their progress, open them.
const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
};
const HEAD = { "X-Roomwalk": "1" };
let state = null;
let chosen = null;
let lastRendered = "";

// ---- talking to the app -------------------------------------------------------
async function api(method, path) {
  const r = await fetch(path, { method, headers: HEAD });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "Something went wrong");
  return data;
}
async function refresh() {
  try {
    const r = await fetch("/api/state", { cache: "no-store" });
    state = await r.json();
    renderScans();
    $("engineNote").hidden = state.engine_installed;
    $("version").textContent = "Version " + state.version + ".";
    if (!$("presetChoices").childElementCount) renderPresets();
  } catch {
    $("scans").replaceChildren(el("div", { class: "empty", text:
      "The app is not answering. Check that the Roomwalk window is still open, then reload this page." }));
    lastRendered = "";
  }
  const busy = state && state.scenes.some((s) => ["running", "queued"].includes(s.status.state));
  setTimeout(refresh, busy ? 1500 : 6000);
}

// ---- new scan form --------------------------------------------------------------
function renderPresets() {
  const box = $("presetChoices");
  for (const p of state.presets) {
    box.append(el("label", { class: "choice" },
      el("input", { type: "radio", name: "preset", value: p.id, checked: p.id === state.default_preset }),
      el("b", { text: p.label }),
      el("small", { text: p.blurb })));
  }
}
function choose(file) {
  $("formError").hidden = true;
  if (!file) return;
  const isVideo = file.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|avi|webm|3gp|mts)$/i.test(file.name);
  if (!isVideo) {
    showFormError(`"${file.name}" is not a video. Choose an .mp4 or .mov file.`);
    return;
  }
  chosen = file;
  $("fileName").textContent = `${file.name} (${(file.size / 1048576).toFixed(0)} MB)`;
  $("scanName").value = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 80);
  $("scanOptions").hidden = false;
  $("dropHelp").hidden = true;
}
function resetForm() {
  chosen = null;
  $("videoFile").value = "";
  $("fileName").textContent = "";
  $("scanOptions").hidden = true;
  $("uploadState").hidden = true;
  $("dropHelp").hidden = false;
  $("startBtn").disabled = false;
}
function showFormError(text) {
  $("formError").textContent = text;
  $("formError").hidden = false;
}
function upload(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Roomwalk", "1");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      xhr.status < 300 ? resolve(data) : reject(new Error(data.error || "Upload failed"));
    };
    xhr.onerror = () => reject(new Error("The app stopped answering during the upload."));
    xhr.send(file);
  });
}
$("videoFile").addEventListener("change", (e) => choose(e.target.files[0]));
$("clearBtn").addEventListener("click", resetForm);
$("scanForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!chosen) return;
  const preset = document.querySelector("input[name=preset]:checked")?.value || state.default_preset;
  const q = new URLSearchParams({
    name: $("scanName").value.trim(), preset, filename: chosen.name, camera_height: $("camHeight").value,
  });
  $("startBtn").disabled = true;
  $("uploadState").hidden = false;
  $("formError").hidden = true;
  try {
    await upload("/api/scenes?" + q, chosen, (f) => {
      $("uploadBar").style.width = Math.round(f * 100) + "%";
      $("uploadText").textContent = f < 1 ? `Copying the video into the app (${Math.round(f * 100)}%)` : "Starting the scan";
    });
    resetForm();
    lastRendered = "";
    await refreshNow();
  } catch (err) {
    showFormError(err.message);
    $("startBtn").disabled = false;
    $("uploadState").hidden = true;
  }
});
async function refreshNow() {
  const r = await fetch("/api/state", { cache: "no-store" });
  state = await r.json();
  renderScans();
}
const zone = $("dropzone");
["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
zone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file && /\.ply$/i.test(file.name)) importPly(file); else choose(file);
});
addEventListener("dragover", (e) => e.preventDefault());
addEventListener("drop", (e) => e.preventDefault());

async function importPly(file) {
  if (!file) return;
  $("formError").hidden = true;
  $("uploadState").hidden = false;
  try {
    const q = new URLSearchParams({ name: file.name.replace(/\.[^.]+$/, ""), filename: file.name });
    await upload("/api/import?" + q, file, (f) => {
      $("uploadBar").style.width = Math.round(f * 100) + "%";
      $("uploadText").textContent = f < 1 ? "Copying the file into the app" : "Finding the floor and drawing the map";
    });
    lastRendered = "";
    await refreshNow();
  } catch (err) {
    showFormError(err.message);
  }
  $("uploadState").hidden = true;
  $("plyFile").value = "";
}
$("plyFile").addEventListener("change", (e) => importPly(e.target.files[0]));
$("importLink").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("plyFile").click(); } });

// ---- scan cards -------------------------------------------------------------------
const clock = (sec) => {
  sec = Math.round(sec || 0);
  if (sec < 60) return sec + " s";
  if (sec < 3600) return Math.floor(sec / 60) + " min";
  return (sec / 3600).toFixed(1) + " h";
};
function stateLine(s) {
  const st = s.status.state;
  if (s.kind === "demo") return ["Try the controls here before scanning your own room.", ""];
  if (st === "running") return ["Scanning now. You can keep using the computer, but it will be slower.", ""];
  if (st === "queued") return ["Waiting for the scan in front of it to finish.", ""];
  if (st === "failed") return ["This scan stopped with a problem.", "bad"];
  if (st === "cancelled") return ["You stopped this scan. It can carry on from where it stopped.", ""];
  if (st === "interrupted") return ["The app was closed during this scan. It can carry on from where it stopped.", ""];
  if (s.kind === "import") return [`Imported file, ${s.splats.toLocaleString()} splats.`, "ok"];
  return [`Ready. ${s.splats.toLocaleString()} splats, ${presetLabel(s.preset)} quality.`, "ok"];
}
const presetLabel = (id) => (state.presets.find((p) => p.id === id) || { label: "Standard" }).label;

function act(fn) {
  return async (e) => {
    e.currentTarget.disabled = true;
    try { await fn(); } catch (err) { alert(err.message); }
    lastRendered = "";
    await refreshNow().catch(() => {});
  };
}
function card(s) {
  const st = s.status.state;
  const active = st === "running" || st === "queued";
  const id = encodeURIComponent(s.id);
  const thumb = el("div", { class: "thumb" + (s.thumb ? "" : " plan") });
  if (s.thumb) thumb.style.backgroundImage = `url(/scenes/${id}/thumb.jpg)`;
  else if (s.viewable) thumb.style.backgroundImage = `url(/scenes/${id}/map_plan.png)`;

  const [line, tone] = stateLine(s);
  const body = el("div", {}, el("h3", { text: s.name }), el("p", { class: "state " + tone, text: line }));

  if ((active || st === "failed" || st === "cancelled" || st === "interrupted") && s.status.stages) {
    const list = el("ol", { class: "stages" });
    for (const stage of state.stages) {
      const info = s.status.stages[stage.id] || { state: "pending" };
      const li = el("li", { class: info.state },
        el("span", { text: stage.label }),
        el("time", { text: info.state === "pending" || !info.seconds ? "" : clock(info.seconds) }));
      if (info.state === "running") {
        const bar = el("i");
        bar.style.width = Math.round((info.progress || 0) * 100) + "%";
        li.append(el("div", { class: "meter" }, bar));
      }
      list.append(li);
    }
    body.append(list);
    if (st === "running" && s.status.message) body.append(el("p", { class: "message", text: s.status.message }));
  }
  if (s.status.notes && s.status.notes.length) {
    body.append(el("ul", { class: "notes" }, ...s.status.notes.map((n) => el("li", { text: n }))));
  }
  if (st === "failed" && s.status.error) body.append(el("p", { class: "error", text: s.status.error }));

  const buttons = el("div", { class: "buttons" });
  if (s.viewable) {
    const label = st === "done" ? "Walk inside" : s.quality === "final" ? "Walk inside the earlier version"
      : s.quality === "preview" ? "Peek at the dotted preview" : "Peek at progress";
    buttons.append(el("a", { class: "btn" + (st === "done" ? " primary" : ""), href: `/view?id=${id}`, text: label }));
  }
  if (active) {
    buttons.append(el("button", { class: "btn quiet", type: "button", text: "Stop",
      onclick: act(() => api("POST", `/api/scenes/${id}/cancel`)) }));
  } else {
    if (st === "failed") buttons.append(el("button", { class: "btn", type: "button", text: "Retry",
      onclick: act(() => api("POST", `/api/scenes/${id}/retry`)) }));
    if (st === "cancelled" || st === "interrupted") buttons.append(el("button", { class: "btn", type: "button",
      text: "Carry on", onclick: act(() => api("POST", `/api/scenes/${id}/retry`)) }));
    if (st === "done" && s.kind === "scan") {
      for (const p of state.presets) {
        if (p.id === s.preset || p.steps <= (state.presets.find((x) => x.id === s.preset) || {}).steps) continue;
        buttons.append(el("button", { class: "btn quiet", type: "button", text: `Rebuild at ${p.label} quality`,
          title: "Keeps the camera tracking and trains the 3D scan again for longer",
          onclick: act(() => api("POST", `/api/scenes/${id}/retry?preset=${p.id}`)) }));
      }
    }
    if (s.kind !== "demo") buttons.append(el("button", { class: "btn quiet", type: "button", text: "Delete",
      onclick: act(async () => {
        if (confirm(`Delete "${s.name}"? This removes the scan and its copy of the video from this app.`)) {
          await api("DELETE", `/api/scenes/${id}`);
        }
      }) }));
  }
  body.append(buttons);
  return el("article", { class: "scan" }, thumb, body);
}
function renderScans() {
  const key = JSON.stringify(state.scenes);
  if (key === lastRendered) return;
  lastRendered = key;
  const box = $("scans");
  if (!state.scenes.length) {
    box.replaceChildren(el("div", { class: "empty", text: "No scans yet. Choose a video on the left to make your first one." }));
    return;
  }
  box.replaceChildren(...state.scenes.map(card));
}

refresh();
