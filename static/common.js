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

const FLAME_OUTER = "M12 3c.4 2.6 2.3 4.2 3.7 6.1 1.3 1.8 1.9 3.4 1.9 5.2 0 3.4-2.5 6.2-5.6 6.2s-5.6-2.8-5.6-6.2c0-2.2 1-3.8 2.3-5.1.2 1.4.8 2.4 1.7 2.9C10.2 9.5 10.6 5.9 12 3z";
const FLAME_INNER = "M12 20.5c-1.5 0-2.6-1.1-2.6-2.6 0-1.5 1.2-2.5 2.1-3.7.6 1 2.9 2.2 2.9 3.8 0 1.4-1 2.5-2.4 2.5z";

function flame(cls = "") {
  return `<svg class="flame ${cls}" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path class="outer" d="${FLAME_OUTER}"/><path class="inner" d="${FLAME_INNER}"/></svg>`;
}

// Only the top 100 earn a flame: lit for the top 10, an outline for the rest.
function heatIcon(g) {
  if (g.rank === 0 || (g.rank && g.rank <= 10)) return flame("lit");
  if (g.rank && g.rank <= 100) return flame();
  return "";
}

function proxCell(g, ref) {
  const p = proximity(g, ref);
  return `<div class="prox" style="--c:${heat(p)}"><div class="bar ${p > 0.8 ? "glow" : ""}"><i data-w="${(p * 100).toFixed(1)}"></i></div><span class="lbl">${heatIcon(g)}${label(g, ref)}</span></div>`;
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

// `nameOf(sender)` turns a sender id into a display name; the viewer's own messages read "You".
function renderMessages(el, msgs, me, nameOf) {
  el.innerHTML = msgs.map((m) => `<div class="msg ${m.sender === me ? "me" : ""}"><small>${m.sender === me ? "You" : esc(nameOf(m.sender))} · ${clock(m.ts)}</small>${esc(m.text)}</div>`).join("")
    || `<div class="faint" style="font-size:13px">No messages yet.</div>`;
  el.scrollTop = el.scrollHeight;
}

// ---------- guesses

const MASK = "•••••••";  // fixed length, so a hidden guess doesn't leak its word length

function guessWord(g) {
  const text = g.word == null ? `<span class="masked" title="Hidden by the seeker">${MASK}</span>` : esc(g.word);
  return text + (g.hint ? '<span class="tag">hint</span>' : "");
}

const sorters = {
  closest: (a, b) => b.sim - a.sim || b.n - a.n,
  recent: (a, b) => b.n - a.n,
};
const sortGuesses = (gs, mode) => [...gs].sort(sorters[mode] || sorters.closest);

// Tiny wrapper so storage failures (private mode, blocked cookies) never break the page.
const store = {
  get(k, d = null) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

// Segmented "Closest | Recent" control, remembered per view in this browser.
function sortToggle(el, key, fallback, onChange) {
  let mode = store.get(key, fallback);
  const render = () => {
    el.innerHTML = ["closest", "recent"].map((m) =>
      `<button type="button" class="${m === mode ? "on" : ""}" data-m="${m}" aria-pressed="${m === mode}">${m === "closest" ? "Closest" : "Recent"}</button>`).join("");
  };
  el.classList.add("seg");
  el.onclick = (e) => {
    const m = e.target.closest("button")?.dataset.m;
    if (!m || m === mode) return;
    mode = m; store.set(key, m); render(); onChange(m);
  };
  render();
  return () => mode;
}

const EYE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.8"/></svg>`;
const EYE_OFF = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.6c.5-.1.9-.1 1.4-.1 6.4 0 10 6.5 10 6.5a17 17 0 0 1-3.2 3.9M6.6 6.7C3.7 8.5 2 12 2 12s3.6 6.5 10 6.5c1.6 0 3-.4 4.3-1"/><path d="M9.9 9.9a2.8 2.8 0 0 0 4.2 4.2"/></svg>`;

const BRAND = `<a class="brand" href="/"><svg class="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <defs><linearGradient id="brand-grad" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#f2b45a"/><stop offset="1" stop-color="#f06a5d"/></linearGradient></defs>
  <path d="${FLAME_OUTER}" stroke="url(#brand-grad)" stroke-width="1.5" stroke-linejoin="round"/><path class="ember" d="${FLAME_INNER}" fill="#f2b45a"/></svg>warmer</a>`;
document.querySelectorAll("[data-brand]").forEach((el) => (el.outerHTML = BRAND));

// Non-production deploys (WARMER_ENV, e.g. "staging") get a visible badge next to the logo.
api("/api/config").then((c) => {
  if (!c.env) return;
  const b = document.createElement("span");
  b.className = "env-badge"; b.textContent = c.env;
  $(".brand")?.append(b);
  document.title = `[${c.env}] ${document.title}`;
}).catch(() => {});
