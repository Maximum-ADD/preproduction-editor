"use strict";
const $ = (s) => document.querySelector(s);
const video = $("#video");
const canvas = $("#timeline");
const scroller = $("#timeline-scroll");

const COLORS = { keep: "#3fb950", silence: "#59606e", retake: "#f85149", clap: "#e3b341",
                 preroll: "#8a5a2b", manual: "#c38bff" };
const state = {
  info: null, result: null,
  eventEdits: {},      // detected clap key -> "single" | "double" | "ignore"
  addedEvents: [],     // [{t, type}]
  overrides: [],       // [{start, end, keep}]
  selected: -1,        // index into result.segments
  zoom: 40,            // px per second
};

// ---------- helpers ----------
async function api(path, body) {
  const opts = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function fmt(t, ms = false) {
  t = Math.max(0, t);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${(ms ? s.toFixed(2) : Math.floor(s).toString()).padStart(ms ? 5 : 2, "0")}`;
}
function busy(text) {
  $("#busy").hidden = !text;
  if (text) $("#busy-text").textContent = text;
}
function debounce(fn, ms) {
  let id; return (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); };
}
function segmentAt(t) {
  const segs = state.result?.segments || [];
  let lo = 0, hi = segs.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (t < segs[mid].start) hi = mid - 1;
    else if (t >= segs[mid].end) lo = mid + 1;
    else return mid;
  }
  return -1;
}

// ---------- open ----------
$("#browse").onclick = async () => {
  try {
    const { path } = await api("/api/browse", {});
    if (path) { $("#path").value = path; openVideo(path); }
  } catch (e) { $("#open-status").textContent = e.message; }
};
$("#open").onclick = () => openVideo($("#path").value);
$("#path").addEventListener("keydown", (e) => { if (e.key === "Enter") openVideo($("#path").value); });

async function openVideo(path) {
  if (!path.trim()) return;
  busy("Reading audio… (longer recordings take a little while)");
  try {
    state.info = await api("/api/open", { path });
    state.eventEdits = {}; state.addedEvents = []; state.overrides = []; state.selected = -1;
    $("#file-name").textContent = state.info.name;
    $("#silence").value = state.info.auto_silence_db;
    $("#auto-silence").checked = true;
    video.src = "/api/video?ts=" + Date.now();
    $("#open-screen").hidden = true;
    $("#review").hidden = false;
    $("#render-status").hidden = true;
    fitZoom();
    await analyze();
  } catch (e) {
    $("#open-status").textContent = "⚠ " + e.message;
  } finally { busy(null); }
}

// ---------- settings / analysis ----------
const sliders = [
  ["silence", (v) => `${(+v).toFixed(1)} dB`],
  ["min-pause", (v) => `${(+v).toFixed(2)} s`],
  ["padding", (v) => `${(+v).toFixed(2)} s`],
  ["clap-sens", (v) => (v > 0 ? "+" : "") + v],
  ["double-gap", (v) => `${(+v).toFixed(2)} s`],
];
function updateLabels() {
  for (const [id, f] of sliders) $(`#${id}-val`).textContent = f($(`#${id}`).value);
  if ($("#auto-silence").checked) $("#silence-val").textContent += " (auto)";
}
function settings() {
  return {
    silence_db: $("#auto-silence").checked ? null : +$("#silence").value,
    min_pause: +$("#min-pause").value,
    padding: +$("#padding").value,
    clap_sensitivity: +$("#clap-sens").value,
    double_gap: +$("#double-gap").value,
  };
}
function requestBody() {
  return { settings: settings(), event_edits: state.eventEdits,
           added_events: state.addedEvents, overrides: state.overrides };
}

let analyzeSeq = 0;
async function analyze() {
  const seq = ++analyzeSeq;
  try {
    const res = await api("/api/analyze", requestBody());
    if (seq !== analyzeSeq) return;
    state.result = res;
    if ($("#auto-silence").checked) $("#silence").value = res.auto_silence_db;
    updateLabels(); renderStats(); renderClaps(); draw();
  } catch (e) { alert(e.message); }
}
const analyzeSoon = debounce(analyze, 150);

for (const [id] of sliders) {
  $(`#${id}`).addEventListener("input", () => {
    if (id === "silence") $("#auto-silence").checked = false;
    updateLabels(); analyzeSoon();
  });
}
$("#auto-silence").onchange = () => { updateLabels(); analyze(); };

function renderStats() {
  const s = state.result.stats;
  const pct = s.original ? Math.round((1 - s.edited / s.original) * 100) : 0;
  $("#stats").innerHTML = `
    <dt>Original</dt><dd>${fmt(s.original)}</dd>
    <dt>Edited</dt><dd class="big">${fmt(s.edited)}</dd>
    <dt>Cut</dt><dd>${pct}%</dd>
    <dt>Pauses cut</dt><dd>${s.pauses} · ${fmt(s.pause_cut)}</dd>
    <dt>Retakes cut</dt><dd>${fmt(s.retake_cut)}</dd>
    <dt>Before 1st clap</dt><dd>${fmt(s.preroll_cut)}</dd>
    <dt>Claps</dt><dd>${s.singles}× 👏 · ${s.doubles}× 👏👏</dd>
    ${s.no_claps ? '<dt class="warn" style="grid-column:1/-1">⚠ No claps found — keeping everything (minus pauses).</dt>' : ""}`;
}

// ---------- clap list ----------
function renderClaps() {
  const tbody = $("#claps tbody");
  const events = state.result.events;
  $("#no-claps").hidden = events.length > 0;
  tbody.innerHTML = "";
  events.forEach((ev) => {
    const tr = document.createElement("tr");
    tr.dataset.key = ev.key;
    if (ev.type === "ignore") tr.className = "ignored";
    const times = ev.t_end !== ev.t ? `${fmt(ev.t, true)} + ${fmt(ev.t_end, true)}` : fmt(ev.t, true);
    const label = ev.note ? ev.note : ev.added ? "added by you" :
      ev.type !== ev.detected_type ? `detected as ${ev.detected_type}` : "detected";
    tr.innerHTML = `
      <td class="t"><button>${times}</button></td>
      <td><select>
        <option value="single">👏 single — redo</option>
        <option value="double">👏👏 double — new bit</option>
        <option value="ignore">ignore (not a clap)</option>
      </select></td>
      <td class="tag">${label}</td>
      <td>${ev.added ? '<button class="rm">Remove</button>' : ""}</td>`;
    const sel = tr.querySelector("select");
    sel.value = ev.type;
    tr.querySelector(".t button").onclick = () => seek(ev.t - 1);
    sel.onchange = () => {
      if (ev.added) {
        const a = state.addedEvents.find((a) => a.t === ev.t);
        if (a) a.type = sel.value;
      } else if (sel.value === ev.detected_type) {
        delete state.eventEdits[ev.key];
      } else {
        state.eventEdits[ev.key] = sel.value;
      }
      analyze();
    };
    const rm = tr.querySelector(".rm");
    if (rm) rm.onclick = () => {
      state.addedEvents = state.addedEvents.filter((a) => a.t !== ev.t);
      analyze();
    };
    tbody.appendChild(tr);
  });
}
function addEvent(type) {
  state.addedEvents.push({ t: Math.round(video.currentTime * 100) / 100, type });
  analyze();
}
$("#add-single").onclick = () => addEvent("single");
$("#add-double").onclick = () => addEvent("double");
$("#reset-edits").onclick = () => {
  if (!confirm("Undo all your clap changes and segment flips?")) return;
  state.eventEdits = {}; state.addedEvents = []; state.overrides = [];
  analyze();
};

// ---------- segment flipping ----------
function toggleSegment(i) {
  const seg = state.result?.segments[i];
  if (!seg) return;
  state.overrides.push({ start: seg.start, end: seg.end, keep: !seg.keep });
  state.selected = i;
  analyze();
}

// ---------- timeline ----------
const RULER = 18, LANE = 14;
function fitZoom() {
  const w = scroller.clientWidth || 800;
  setZoom(Math.max(2, Math.min(400, w / Math.max(state.info.duration, 1))), 0);
}
function zoomToSlider(z) { return Math.log(z / 2) / Math.log(200) * 100; }
function sliderToZoom(v) { return 2 * Math.pow(200, v / 100); }
function setZoom(z, anchorT) {
  const anchorX = (anchorT ?? 0) * state.zoom - scroller.scrollLeft;
  state.zoom = Math.max(2, Math.min(400, z));
  $("#zoom").value = zoomToSlider(state.zoom);
  $("#timeline-spacer").style.width = (state.info.duration * state.zoom) + "px";
  scroller.scrollLeft = (anchorT ?? 0) * state.zoom - anchorX;
  draw();
}
$("#zoom").addEventListener("input", () => setZoom(sliderToZoom(+$("#zoom").value), video.currentTime));
scroller.addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  const t = (scroller.scrollLeft + e.offsetX) / state.zoom;
  setZoom(state.zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2), t);
}, { passive: false });
scroller.addEventListener("scroll", () => draw());
window.addEventListener("resize", () => draw());

function draw() {
  if (!state.info) return;
  const dpr = window.devicePixelRatio || 1;
  const W = scroller.clientWidth, H = 150;
  if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const z = state.zoom, t0 = scroller.scrollLeft / z, t1 = t0 + W / z;
  const x = (t) => (t - t0) * z;
  const segs = state.result?.segments || [];
  const waveTop = RULER + LANE + 4, waveH = H - waveTop - 4, mid = waveTop + waveH / 2;

  // ruler
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find((s) => s * z >= 70) || 600;
  ctx.fillStyle = "#8b93a3"; ctx.font = "10px ui-monospace, Consolas, monospace";
  ctx.strokeStyle = "#272b35"; ctx.lineWidth = 1;
  for (let t = Math.floor(t0 / step) * step; t <= t1; t += step) {
    const px = Math.round(x(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, H); ctx.stroke();
    ctx.fillText(step < 1 ? fmt(t, true) : fmt(t), px + 3, 12);
  }

  // segment lane + cut shading
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    if (s.end < t0 || s.start > t1) continue;
    const a = x(s.start), w = Math.max(1, (s.end - s.start) * z);
    ctx.fillStyle = s.manual ? COLORS.manual : s.keep ? COLORS.keep : COLORS[s.reason];
    ctx.fillRect(a, RULER, w, LANE);
    if (!s.keep) { ctx.fillStyle = "rgba(0,0,0,0.45)"; ctx.fillRect(a, waveTop, w, waveH); }
  }

  // waveform
  const wf = state.info.waveform, bin = state.info.wave_bin;
  let si = Math.max(0, segmentAt(t0));
  const b0 = Math.max(0, Math.floor(t0 / bin)), b1 = Math.min(wf.length, Math.ceil(t1 / bin));
  const barW = Math.max(1, bin * z - (bin * z > 3 ? 1 : 0));
  for (let b = b0; b < b1; b++) {
    const t = b * bin;
    while (si < segs.length - 1 && t >= segs[si].end) si++;
    const kept = segs[si] ? segs[si].keep : true;
    ctx.fillStyle = kept ? "#9fb4ff" : "#3a3f4c";
    const h = Math.max(1, wf[b] * waveH / 2);
    ctx.fillRect(x(t), mid - h, barW, h * 2);
  }

  // claps
  for (const ev of state.result?.events || []) {
    if (ev.t_end < t0 - 1 || ev.t > t1 + 1) continue;
    const ignored = ev.type === "ignore";
    ctx.strokeStyle = ignored ? "#59606e" : COLORS.clap;
    ctx.setLineDash(ignored ? [3, 3] : []);
    for (const t of ev.type === "double" || ev.t_end !== ev.t ? [ev.t, ev.t_end] : [ev.t]) {
      const px = Math.round(x(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(px, RULER); ctx.lineTo(px, H); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.font = "13px system-ui";
    ctx.fillText(ignored ? "✕" : ev.type === "double" ? "👏👏" : "👏", x(ev.t) + 3, waveTop + 14);
  }

  // selection
  const sel = segs[state.selected];
  if (sel) {
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
    ctx.strokeRect(x(sel.start) + 1, RULER + 1, (sel.end - sel.start) * z - 2, H - RULER - 2);
    ctx.lineWidth = 1;
  }

  // playhead
  const p = Math.round(x(video.currentTime)) + 0.5;
  ctx.strokeStyle = "#fff"; ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, H); ctx.stroke();
}

function timeFromEvent(e) {
  return (scroller.scrollLeft + e.offsetX) / state.zoom;
}
// clap whose line is within a few pixels of x (either clap of a double counts)
function clapNear(t) {
  const tol = 6 / state.zoom;
  return (state.result?.events || []).find((ev) =>
    Math.abs(ev.t - t) <= tol || Math.abs(ev.t_end - t) <= tol);
}
function highlightClap(ev) {
  const row = [...document.querySelectorAll("#claps tr")].find((r) => r.dataset.key === ev.key);
  if (!row) return;
  row.scrollIntoView({ behavior: "smooth", block: "center" });
  row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
}
canvas.addEventListener("click", (e) => {
  const t = timeFromEvent(e);
  const ev = clapNear(t);
  if (ev) highlightClap(ev);
  state.selected = segmentAt(t);
  seek(t);
});
canvas.addEventListener("mousemove", (e) => {
  canvas.title = clapNear(timeFromEvent(e)) ? "Click to edit this clap in the list below" : "";
});
canvas.addEventListener("dblclick", (e) => toggleSegment(segmentAt(timeFromEvent(e))));

function seek(t) {
  video.currentTime = Math.max(0, Math.min(t, state.info.duration));
  draw();
}

// keep playhead in view & preview mode (skip cut parts)
function tick() {
  if (state.result && !video.paused) {
    if ($("#preview").checked) {
      const kept = state.result.kept, t = video.currentTime;
      if (!kept.some(([a, b]) => t >= a && t < b - 0.02)) {
        const next = kept.find(([a]) => a > t);
        if (next) video.currentTime = next[0]; else video.pause();
      }
    }
    const px = video.currentTime * state.zoom - scroller.scrollLeft;
    if (px < 0 || px > scroller.clientWidth - 40) scroller.scrollLeft = video.currentTime * state.zoom - 80;
    draw();
  }
  $("#time").textContent = state.info ? `${fmt(video.currentTime, true)} / ${fmt(state.info.duration)}` : "";
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
video.addEventListener("seeked", draw);

$("#preview").onchange = () => {
  if ($("#preview").checked && video.paused) video.play();
};

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)
    && !["range", "checkbox"].includes(e.target.type);
  if (!state.info || typing || e.target === video) return;
  if (e.key === " ") { e.preventDefault(); video.paused ? video.play() : video.pause(); }
  else if (e.key === "x" || e.key === "X") toggleSegment(state.selected);
  else if (e.key === "p" || e.key === "P") { $("#preview").checked = !$("#preview").checked; $("#preview").onchange(); }
});

// ---------- render ----------
$("#render").onclick = async () => {
  $("#render").disabled = true;
  $("#render-status").hidden = false;
  $("#reveal").hidden = true;
  $("#render-text").textContent = "Starting…";
  try {
    await api("/api/render", requestBody());
    pollRender();
  } catch (e) {
    $("#render-text").textContent = "⚠ " + e.message;
    $("#render").disabled = false;
  }
};
async function pollRender() {
  const job = await api("/api/render");
  $("#render-bar").style.width = Math.round((job.progress || 0) * 100) + "%";
  if (job.state === "running") {
    $("#render-text").textContent = `Rendering… ${Math.round(job.progress * 100)}%`;
    setTimeout(pollRender, 500);
  } else {
    $("#render").disabled = false;
    if (job.state === "done") {
      $("#render-text").textContent = "✓ Saved to " + job.output;
      $("#reveal").hidden = false;
    } else {
      $("#render-text").textContent = "⚠ " + (job.error || "Render failed");
    }
  }
}
$("#reveal").onclick = () => api("/api/reveal", {}).catch((e) => alert(e.message));

updateLabels();
