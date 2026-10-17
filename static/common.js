// Shared helpers for all Warmer pages.

const $ = (sel, el = document) => el.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Proximity in [0, 1]: the top-1000 neighbourhood occupies the upper half,
// everything else is scaled by similarity relative to the 1000th neighbour.
function proximity(g, ref) {
  if (g.rank === 0) return 1;
  if (g.rank) return rankProximity(g.rank);
  return Math.max(0, Math.min(1, g.sim / ref.top1000)) * 0.5;
}

// Log scale so #10 and #100 feel meaningfully different.
const rankProximity = (rank) => 0.5 + 0.5 * (1 - Math.log(rank) / Math.log(1000));

// Cold blue → amber → hot coral.
function heat(p) {
  const stops = [[0, 222, 45, 52], [0.45, 205, 55, 60], [0.55, 45, 80, 62], [0.78, 22, 90, 62], [1, 350, 85, 66]];
  let i = 0;
  while (i < stops.length - 2 && p > stops[i + 1][0]) i++;
  const [a, b] = [stops[i], stops[i + 1]];
  const t = (p - a[0]) / (b[0] - a[0] || 1);
  let h1 = a[1], h2 = b[1];
  if (Math.abs(h2 - h1) > 180) h2 += h2 < h1 ? 360 : -360;
  const mix = (x, y) => x + (y - x) * t;
  return `hsl(${(mix(h1, h2) + 360) % 360} ${mix(a[2], b[2])}% ${mix(a[3], b[3])}%)`;
}

function label(g, ref) {
  if (g.rank === 0) return "found";
  if (g.rank) return `${g.rank} / 1000`;
  if (g.sim < 0) return "frozen";
  return g.sim >= ref.top1000 * 0.75 ? "tepid" : "cold";
}

function word(g) {
  if (g.rank === 0) return "found it";
  if (!g.rank) return g.sim >= 0 ? "keep searching" : "far away";
  if (g.rank <= 10) return "burning";
  if (g.rank <= 100) return "hot";
  if (g.rank <= 400) return "warm";
  return "getting warmer";
}

function proxCell(g, ref) {
  const p = proximity(g, ref);
  return `<div class="prox" style="--c:${heat(p)}"><div class="bar"><i data-w="${(p * 100).toFixed(1)}"></i></div><span class="lbl">${label(g, ref)}</span></div>`;
}

// Let bar widths animate in after insertion.
function growBars(root) {
  requestAnimationFrame(() => root.querySelectorAll(".bar i[data-w]").forEach((el) => (el.style.width = el.dataset.w + "%")));
}

function setGlow(p) {
  document.body.style.setProperty("--glow", heat(p).replace(")", " / 0.10)"));
}

function toast(text, title) {
  let box = $(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; document.body.append(box); }
  const t = document.createElement("div");
  t.className = "toast";
  t.innerHTML = (title ? `<small>${esc(title)}</small>` : "") + esc(text);
  box.append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, title ? 7000 : 3200);
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); } catch { prompt("Copy this link:", text); return; }
  if (btn) { const o = btn.innerHTML; btn.textContent = "Copied"; setTimeout(() => (btn.innerHTML = o), 1400); }
}

const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function duration(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

// Server-sent events with automatic reconnect + a hook to resync state.
function live(url, handlers, onReconnect) {
  let es, first = true;
  const open = () => {
    es = new EventSource(url);
    es.onopen = () => { if (!first) onReconnect?.(); first = false; handlers.open?.(); };
    es.onerror = () => handlers.error?.();
    for (const [ev, fn] of Object.entries(handlers)) {
      if (!["open", "error"].includes(ev)) es.addEventListener(ev, (e) => fn(JSON.parse(e.data)));
    }
  };
  open();
  return () => es.close();
}

function renderMessages(el, msgs, me) {
  el.innerHTML = msgs.map((m) => `<div class="msg ${m.sender === me ? "me" : ""}"><small>${m.sender === me ? "You" : m.sender === "host" ? "Word-keeper" : "Seeker"} · ${clock(m.ts)}</small>${esc(m.text)}</div>`).join("")
    || `<div class="faint" style="font-size:13px">No messages yet.</div>`;
  el.scrollTop = el.scrollHeight;
}

const BRAND = `<a class="brand" href="/"><svg class="brand-mark" viewBox="0 0 24 24" fill="none"><circle cx="8.5" cy="12" r="5.5" stroke="currentColor" stroke-width="1.4"/><circle cx="15.5" cy="12" r="5.5" stroke="#f2b45a" stroke-width="1.4"/></svg>warmer</a>`;
document.querySelectorAll("[data-brand]").forEach((el) => (el.outerHTML = BRAND));
