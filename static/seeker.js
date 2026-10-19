// The guessing side of a puzzle: guess box, latest-guess card, sortable guess list,
// hints, giving up, and the reveal. Used by the one-way play page and the match "Find" tab.
//
// cfg: { urls: { guess, hint, giveUp, visibility }, keeperName, finishCta }

function SeekerPanel(root, cfg) {
  root.innerHTML = `
    <div class="theme-banner hidden" data-el="theme"><small>Clue</small><span></span></div>
    <section data-el="finish" class="card finish hidden fade-in"></section>
    <div data-el="board">
      <form class="guess-form" data-el="form" autocomplete="off">
        <input class="input big" data-el="input" placeholder="Type a word…" maxlength="40" spellcheck="false" autocapitalize="off" aria-label="Your guess">
        <button type="button" class="btn icon-btn" data-el="hide" aria-pressed="false"></button>
        <button class="btn primary">Guess</button>
      </form>
      <div class="status-line" data-el="status"></div>
      <div data-el="latest"></div>
      <p class="ref" data-el="ref"></p>
      <div class="actions">
        <button class="btn sm ghost" data-el="hint">Hint</button>
        <button class="btn sm ghost" data-el="giveup">Give up</button>
      </div>
    </div>
    <section class="section-gap">
      <div class="list-head"><h2 data-el="count">Guesses</h2><div data-el="sort"></div></div>
      <table class="g-table"><thead><tr><th class="hide-sm">#</th><th>Word</th><th style="text-align:right">Similarity</th><th>Proximity</th><th class="v"></th></tr></thead><tbody data-el="rows"></tbody></table>
      <div class="empty" data-el="empty">Your guesses will appear here. Start anywhere — every word is a clue.</div>
    </section>
    <details class="section-gap faint how">
      <summary>How it works</summary>
      <p>Each guess is scored by cosine similarity between its word2vec vector (trained on ~100 billion words of Google News) and the secret word's vector, shown ×100. If your guess is among the 1,000 dictionary words closest to the secret, you also see its rank — <span class="mono">1 / 1000</span> is the very nearest. Similarity reflects how words are <em>used</em>, not strictly what they mean, so opposites and related ideas often score high.</p>
      <p>Turn on <b>hide</b> (the eye next to the guess box) to keep guesses private: they're scored as usual, but the word-keeper sees only <span class="mono">${MASK}</span>. Click the eye on any guess to change it later.</p>
    </details>`;

  const el = (n) => root.querySelector(`[data-el="${n}"]`);
  let S = null, latestN = null;
  let hideMode = store.get("warmer:hide") === "1";
  const keeper = () => cfg.keeperName() || "the word-keeper";
  const sortMode = sortToggle(el("sort"), "warmer:sort:seek", "closest", () => S && render());

  function renderHide() {
    const b = el("hide");
    b.innerHTML = (hideMode ? EYE_OFF : EYE) + `<span>${hideMode ? "hidden" : "visible"}</span>`;
    b.classList.toggle("on", hideMode);
    b.setAttribute("aria-pressed", hideMode);
    b.title = hideMode ? `New guesses are hidden from ${keeper()}` : `New guesses are visible to ${keeper()}`;
    el("input").classList.toggle("private", hideMode);
  }
  el("hide").onclick = () => {
    hideMode = !hideMode; store.set("warmer:hide", hideMode ? "1" : "0"); renderHide();
    status(hideMode ? `New guesses are hidden from ${keeper()} — they'll see the score, not the word.` : "");
    el("input").focus();
  };

  const status = (text, err) => { const s = el("status"); s.className = "status-line" + (err ? " err" : ""); s.textContent = text; };

  function render(flashN) {
    const ref = S.reference, mode = sortMode(), gs = sortGuesses(S.guesses, mode);
    el("count").textContent = `${S.guesses.length} guess${S.guesses.length === 1 ? "" : "es"}`;
    el("empty").classList.toggle("hidden", gs.length > 0);
    let sepDone = mode !== "closest";
    el("rows").innerHTML = gs.map((g, i) => {
      let sep = "";
      if (!sepDone && g.rank == null) { sepDone = true; if (i > 0) sep = `<tr class="sep"><td colspan="5"></td></tr>`; }
      const cls = [g.n === latestN ? "latest" : "", g.n === flashN ? "flash" : "", g.hidden ? "is-hidden" : ""].join(" ");
      const vis = g.rank === 0 ? "" : `<button class="vis ${g.hidden ? "on" : ""}" data-n="${g.n}" title="${g.hidden ? `Hidden from ${esc(keeper())} — click to show` : `Visible to ${esc(keeper())} — click to hide`}">${g.hidden ? EYE_OFF : EYE}</button>`;
      return sep + `<tr class="${cls}" style="--c:${heat(proximity(g, ref))}"><td class="n">${g.n}</td><td class="w">${guessWord(g)}</td><td class="s">${g.sim.toFixed(2)}</td><td class="p">${proxCell(g, ref)}</td><td class="v">${vis}</td></tr>`;
    }).join("");
    growBars(el("rows"));
    const last = S.guesses.find((g) => g.n === latestN);
    if (last) {
      const p = proximity(last, ref);
      el("latest").innerHTML = `<div class="latest-card fade-in" style="--c:${heat(p)}"><span class="word">${esc(last.word)}${last.hidden ? `<span class="hidden-note">${EYE_OFF}hidden</span>` : ""}</span><span class="meta"><b>${heatIcon(last)}${word(last)}</b>${last.sim.toFixed(2)} · ${label(last, ref)}</span></div>`;
    }
    if (S.guesses.length) cfg.onHeat?.(Math.max(...S.guesses.map((g) => proximity(g, ref))));
  }

  el("rows").onclick = async (e) => {
    const b = e.target.closest("button.vis");
    if (!b) return;
    const g = S.guesses.find((x) => x.n === +b.dataset.n);
    try { const r = await api(cfg.urls.visibility, { method: "POST", body: { n: g.n, hidden: !g.hidden } }); update(r.guess); }
    catch (err) { toast(err.message); }
  };

  el("form").onsubmit = async (e) => {
    e.preventDefault();
    const input = el("input"), w = input.value.trim();
    if (!w) return;
    status("");
    try {
      const r = await api(cfg.urls.guess, { method: "POST", body: { word: w, hidden: hideMode } });
      input.value = "";
      if (r.duplicate) { status(`You already tried “${r.guess.word}”.`); latestN = r.guess.n; render(r.guess.n); return; }
      add(r.guess);
    } catch (err) { status(err.message, true); input.select(); }
  };

  el("hint").onclick = async () => {
    try { const r = await api(cfg.urls.hint, { method: "POST" }); add(r.guess); toast(`Try thinking about “${r.guess.word}”.`, "Hint"); }
    catch (err) { toast(err.message); }
  };

  el("giveup").onclick = async () => {
    if (!confirm("Reveal the secret word? This ends your search.")) return;
    try { finish(await api(cfg.urls.giveUp, { method: "POST" }), true); } catch (err) { toast(err.message); }
  };

  function add(g) {
    if (!S.guesses.some((x) => x.n === g.n)) S.guesses.push(g);
    latestN = g.n;
    render(g.n);
  }

  function update(g) {
    const i = S.guesses.findIndex((x) => x.n === g.n);
    if (i >= 0) S.guesses[i] = g; else S.guesses.push(g);
    render();
  }

  function finish(state, animate) {
    S = { ...S, ...state, guesses: state.guesses || S.guesses };
    el("board").classList.add("hidden");
    const solved = state.status === "solved", n = S.guesses.length, guessed = new Set(S.guesses.map((g) => g.word));
    const f = el("finish");
    f.innerHTML = `
      ${solved ? `<div class="finish-flame">${flame("lit")}</div>` : ""}
      <h2>${solved ? `Found in ${n} guess${n === 1 ? "" : "es"}` : "The word was"}</h2>
      <div class="secret-word">${esc(state.secret)}</div>
      ${state.note ? `<p class="note">${esc(state.note)}</p>${S.keeper_name ? `<p class="muted" style="margin-top:8px">— ${esc(S.keeper_name)}</p>` : ""}` : ""}
      <div style="margin-top:30px;text-align:left"><h2>The 100 nearest words</h2><div class="chips">${state.neighbours.map(([w, s], i) =>
        `<span class="chip ${guessed.has(w) ? "hit" : ""}" style="--c:${heat(rankProximity(i + 1))}" title="#${i + 1}"><b>${esc(w)}</b>${s}</span>`).join("")}</div></div>
      ${cfg.finishCta ? `<div class="actions" style="justify-content:center;margin-top:28px">${cfg.finishCta}</div>` : ""}`;
    f.classList.remove("hidden");
    cfg.onHeat?.(solved ? 1 : 0.2);
    render();
    if (animate) f.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function load(state) {
    S = state;
    latestN = S.guesses.length ? S.guesses[S.guesses.length - 1].n : null;
    el("theme").classList.toggle("hidden", !S.theme);
    el("theme").querySelector("span").textContent = S.theme || "";
    el("hint").classList.toggle("hidden", !S.allow_hints);
    const r = S.reference;
    el("ref").innerHTML = `The nearest word scores <b>${r.top1}</b>, the 10th nearest <b>${r.top10}</b>, and the 1000th <b>${r.top1000}</b>.`;
    renderHide();
    if (S.status !== "playing") finish(S, false);
    else { el("board").classList.remove("hidden"); el("finish").classList.add("hidden"); render(); }
  }

  return {
    load, add, update, finish,
    get state() { return S; },
    has: (n) => !!S?.guesses.some((x) => x.n === n),
    focus: () => el("input").focus(),
  };
}
