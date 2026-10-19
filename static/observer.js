// The watching side of a puzzle: your secret word, live stats, the path chart, a sortable
// guess feed, and which nearest neighbours have been found. Hidden guesses arrive from the
// server with `word: null` and are shown masked.
//
// cfg: { seekerName }   Slots for the page: [data-el="aside"] beside the word, [data-el="side"] atop the right column.

function ObserverPanel(root, cfg) {
  root.innerHTML = `
    <div class="obs-head">
      <div>
        <h2 style="margin:0">Your secret word <button class="btn sm ghost" data-el="blur" title="Hide for screen-sharing">hide</button></h2>
        <div class="secret-word" data-el="secret"></div>
        <div class="muted" data-el="theme" style="font-size:14px"></div>
      </div>
      <div data-el="aside"></div>
    </div>
    <div class="stats">
      <div class="stat"><div class="k">Guesses</div><div class="v" data-el="s-count">0</div></div>
      <div class="stat"><div class="k">Closest rank</div><div class="v" data-el="s-rank">—</div></div>
      <div class="stat"><div class="k">Best similarity</div><div class="v" data-el="s-sim">—</div></div>
      <div class="stat"><div class="k">Elapsed</div><div class="v" data-el="s-time">—</div></div>
    </div>
    <div class="grid host" style="margin-top:16px">
      <div>
        <div class="card chart">
          <div class="list-head"><h2>The path so far</h2><span class="faint mono" style="font-size:11px">similarity by guess · line = best so far</span></div>
          <div data-el="chart"></div>
        </div>
        <div class="card">
          <div class="list-head"><h2>Guesses</h2><div data-el="sort"></div></div>
          <ul class="feed" data-el="feed"></ul>
          <div class="empty" data-el="empty">Waiting for the first guess…</div>
        </div>
      </div>
      <div>
        <div data-el="side"></div>
        <div class="card">
          <h2>Nearest neighbours</h2>
          <p class="faint" style="font-size:13px;margin:-6px 0 12px">Lit up once they've guessed them.</p>
          <div class="chips" data-el="neighbours"></div>
        </div>
      </div>
    </div>`;

  const el = (n) => root.querySelector(`[data-el="${n}"]`);
  let S = null;
  const fresh = new Set();
  const sortMode = sortToggle(el("sort"), "warmer:sort:watch", "recent", () => S && render());

  el("blur").onclick = () => {
    const blurred = el("secret").classList.toggle("blur");
    el("blur").textContent = blurred ? "show" : "hide";
  };
  el("secret").onclick = () => { if (el("secret").classList.contains("blur")) el("blur").click(); };

  function render() {
    const ref = S.reference, gs = S.guesses;
    el("s-count").textContent = gs.length;
    const ranked = gs.filter((g) => g.rank != null);
    const bestRank = ranked.length ? Math.min(...ranked.map((g) => g.rank)) : null;
    el("s-rank").textContent = bestRank === 0 ? "found" : bestRank ? `#${bestRank}` : "—";
    el("s-rank").style.color = bestRank !== null ? heat(proximity({ rank: bestRank }, ref)) : "";
    el("s-sim").textContent = gs.length ? Math.max(...gs.map((g) => g.sim)).toFixed(2) : "—";

    el("empty").classList.toggle("hidden", gs.length > 0);
    el("feed").innerHTML = sortGuesses(gs, sortMode()).map((g) =>
      `<li class="${fresh.has(g.n) ? "new" : ""}" style="--c:${heat(proximity(g, ref))}"><time>${clock(g.ts).slice(0, 5)}</time><span class="fw">${guessWord(g)}</span><span class="fv">${heatIcon(g)}${g.sim.toFixed(2)} · ${label(g, ref)}</span></li>`).join("");
    fresh.clear();

    const guessed = new Set(gs.map((g) => g.word).filter(Boolean));
    el("neighbours").innerHTML = S.neighbours.map(([w, s], i) =>
      `<span class="chip ${guessed.has(w) ? "hit" : ""}" style="--c:${heat(rankProximity(i + 1))}" title="#${i + 1}"><b>${esc(w)}</b>${s}</span>`).join("");

    chart();
    tick();
  }

  function chart() {
    const gs = S.guesses, ref = S.reference, W = 640, H = 200, P = { l: 34, r: 12, t: 10, b: 22 };
    if (!gs.length) { el("chart").innerHTML = `<div class="empty">The chart fills in as they guess.</div>`; return; }
    const sims = gs.map((g) => g.sim);
    const yMin = Math.min(0, ...sims) - 5, yMax = Math.max(ref.top1 + 5, ...sims, 40);
    const n = Math.max(gs.length, 2);
    const x = (i) => P.l + (i / (n - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (v - yMin) / (yMax - yMin)) * (H - P.t - P.b);
    let best = -Infinity;
    const bestPts = gs.map((g, i) => { best = Math.max(best, g.sim); return `${x(i).toFixed(1)},${y(best).toFixed(1)}`; });
    const refLine = (v, txt) => `<line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}" stroke-dasharray="3 4"/><text x="${W - P.r}" y="${y(v) - 4}" text-anchor="end">${txt}</text>`;
    el("chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}">
      <line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${y(0)}" y2="${y(0)}"/>
      <text x="${P.l - 6}" y="${y(0) + 3}" text-anchor="end">0</text>
      ${refLine(ref.top1000, "#1000")}${refLine(ref.top10, "#10")}
      <polyline points="${bestPts.join(" ")}" fill="none" stroke="#f2b45a" stroke-width="1.6" stroke-linejoin="round" opacity=".85" vector-effect="non-scaling-stroke"/>
      ${gs.map((g, i) => `<circle cx="${x(i)}" cy="${y(g.sim)}" r="3.2" fill="${heat(proximity(g, ref))}"><title>${g.word == null ? MASK : esc(g.word)} — ${g.sim.toFixed(2)}</title></circle>`).join("")}
      <text x="${P.l}" y="${H - 4}">1</text><text x="${W - P.r}" y="${H - 4}" text-anchor="end">${gs.length}</text>
    </svg>`;
  }

  function tick() {
    if (!S || !S.guesses.length) return;
    const start = S.guesses[0].ts, end = S.solved_at || (S.status === "playing" ? Date.now() / 1000 : S.guesses[S.guesses.length - 1].ts);
    el("s-time").textContent = duration(end - start);
  }
  setInterval(tick, 1000);

  return {
    load(state) {
      S = state;
      el("secret").textContent = S.secret;
      el("theme").textContent = S.theme ? `Clue: ${S.theme}` : "";
      render();
    },
    // Returns true when the guess is new to this view.
    add(g) {
      if (S.guesses.some((x) => x.n === g.n)) return false;
      S.guesses.push(g); fresh.add(g.n); render();
      return true;
    },
    update(g) {
      const i = S.guesses.findIndex((x) => x.n === g.n);
      if (i >= 0) S.guesses[i] = g; else S.guesses.push(g);
      render();
    },
    finish(state) { S = { ...S, ...state }; render(); },
    get state() { return S; },
    el,
  };
}
