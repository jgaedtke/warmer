"""Warmer — a two-player semantic word game built on word2vec.

Each player picks a secret word for the other; both hunt by semantic proximity
while watching the other's progress on their own word live.

A *puzzle* is one secret word with one keeper (who watches) and one seeker (who guesses).
A *match* pairs two puzzles so each player is the keeper of one and the seeker of the other.
Puzzles created before matches existed (match_id NULL) are one-way games served at
/g/<id> (seeker, open link) and /h/<id>?k=<host_token> (keeper).
"""
import asyncio
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
DB_PATH = os.environ.get("WARMER_DB", str(ROOT / "warmer.db"))
TOP_N = 1000  # how many nearest neighbours earn a rank
# Pre-fills the creator's name on the create page; a name the browser remembers takes precedence.
DEFAULT_NAME = os.environ.get("WARMER_DEFAULT_NAME", "").strip()[:40]
# Shown as a badge so a staging deploy is never mistaken for production.
ENV_LABEL = os.environ.get("WARMER_ENV", "").strip()[:20]

# ---------------------------------------------------------------- embeddings

WORDS: list[str] = (DATA / "words.txt").read_text().split("\n")
W2I = {w: i for i, w in enumerate(WORDS)}
VECS = np.load(DATA / "vectors.npy").astype(np.float32)  # unit-normalised rows
DICT_WORDS = (DATA / "dictionary.txt").read_text().split("\n")
DICT_IDX = np.array([W2I[w] for w in DICT_WORDS])
DICT_SET = set(DICT_WORDS)
# Frequency-ordered, so the head of the dictionary is everyday vocabulary.
RANDOM_POOL = [w for w in DICT_WORDS[:12000] if len(w) >= 4 and "-" not in w and "'" not in w]


def normalise(word: str) -> str:
    return re.sub(r"\s+", "", word.strip().lower())


@lru_cache(maxsize=256)
def neighbourhood(secret: str):
    """Similarity to every vocab word, plus rank map for the TOP_N nearest dictionary words."""
    sims = VECS @ VECS[W2I[secret]]
    dsims = sims[DICT_IDX]
    order = np.argsort(-dsims)
    ranked = [DICT_WORDS[i] for i in order if DICT_WORDS[i] != secret][:TOP_N]
    ranks = {w: r + 1 for r, w in enumerate(ranked)}
    return sims, ranked, ranks


def score(secret: str, word: str):
    sims, ranked, ranks = neighbourhood(secret)
    return float(sims[W2I[word]]), ranks.get(word)


def reference_points(secret: str):
    sims, ranked, _ = neighbourhood(secret)
    at = lambda r: round(float(sims[W2I[ranked[r - 1]]]) * 100, 2)
    return {"top1": at(1), "top10": at(10), "top1000": at(TOP_N)}


def nearest(secret: str, n: int):
    sims, ranked, _ = neighbourhood(secret)
    return [[x, round(float(sims[W2I[x]]) * 100, 1)] for x in ranked[:n]]


# ---------------------------------------------------------------- storage

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def migrate():
    """Create tables and add columns introduced after v1; safe to run on every start."""
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS games (
              id TEXT PRIMARY KEY, host_token TEXT NOT NULL, secret TEXT NOT NULL,
              host_name TEXT, theme TEXT, note TEXT, allow_hints INTEGER DEFAULT 1,
              created REAL, solved_at REAL, gave_up REAL
            );
            CREATE TABLE IF NOT EXISTS guesses (
              game_id TEXT, n INTEGER, word TEXT, sim REAL, rank INTEGER,
              hint INTEGER DEFAULT 0, ts REAL, PRIMARY KEY (game_id, word)
            );
            CREATE TABLE IF NOT EXISTS messages (
              game_id TEXT, sender TEXT, text TEXT, ts REAL
            );
            CREATE TABLE IF NOT EXISTS matches (
              id TEXT PRIMARY KEY, token_a TEXT NOT NULL, token_b TEXT,
              name_a TEXT, name_b TEXT, created REAL, joined REAL
            );
            """
        )
        added = {
            "games": [("match_id", "TEXT"), ("keeper", "TEXT")],
            "guesses": [("hidden", "INTEGER DEFAULT 0")],
        }
        for table, cols in added.items():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        con.execute("CREATE INDEX IF NOT EXISTS games_match ON games (match_id)")


migrate()


def get_game(game_id: str):
    with db() as con:
        g = con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not g:
        raise HTTPException(404, "Game not found")
    return g


def as_guess(r):
    return dict(n=r["n"], word=r["word"], sim=r["sim"], rank=r["rank"], hint=bool(r["hint"]),
                hidden=bool(r["hidden"]), ts=r["ts"])


def game_guesses(game_id: str):
    with db() as con:
        rows = con.execute("SELECT * FROM guesses WHERE game_id=? ORDER BY n", (game_id,)).fetchall()
    return [as_guess(r) for r in rows]


def masked(guess):
    """What the keeper sees of a guess the seeker chose to hide. The winning guess is never hidden."""
    if guess["hidden"] and guess["rank"] != 0:
        return {**guess, "word": None}
    return guess


def game_messages(channel: str):
    with db() as con:
        rows = con.execute("SELECT sender, text, ts FROM messages WHERE game_id=? ORDER BY ts", (channel,)).fetchall()
    return [dict(r) for r in rows]


def status(g):
    return "solved" if g["solved_at"] else "revealed" if g["gave_up"] else "playing"


# ---------------------------------------------------------------- puzzle views

def puzzle_core(g):
    return {"id": g["id"], "keeper_name": g["host_name"], "theme": g["theme"], "allow_hints": bool(g["allow_hints"]),
            "status": status(g), "reference": reference_points(g["secret"]), "top_n": TOP_N,
            "created": g["created"], "solved_at": g["solved_at"]}


def seeker_view(g):
    finished = status(g) != "playing"
    return {**puzzle_core(g), "guesses": game_guesses(g["id"]),
            "secret": g["secret"] if finished else None, "note": g["note"] if finished else None,
            "neighbours": nearest(g["secret"], 100) if finished else None}


def observer_view(g):
    return {**puzzle_core(g), "secret": g["secret"], "note": g["note"], "neighbours": nearest(g["secret"], 30),
            "guesses": [masked(x) for x in game_guesses(g["id"])]}


# ---------------------------------------------------------------- live updates

class Hub:
    """Fan-out of events to connected SSE clients. Each subscriber has a role on its channel
    (legacy: player/host, match: a/b) and payloads may be tailored per role."""

    def __init__(self):
        self.subs: dict[str, list[tuple[str, asyncio.Queue]]] = {}

    def presence(self, channel):
        counts: dict[str, int] = {}
        for role, _ in self.subs.get(channel, []):
            counts[role] = counts.get(role, 0) + 1
        return counts

    def publish(self, channel, event, data):
        """`data` is a payload, or a function of the subscriber's role returning one (None = skip)."""
        for role, q in self.subs.get(channel, []):
            payload = data(role) if callable(data) else data
            if payload is not None:
                q.put_nowait((event, payload))

    async def stream(self, channel, role, request: Request):
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(channel, []).append((role, q))
        self.publish(channel, "presence", self.presence(channel))
        try:
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            self.subs[channel].remove((role, q))
            self.publish(channel, "presence", self.presence(channel))


hub = Hub()


def other(seat):
    return "b" if seat == "a" else "a"


def roles(g):
    """(channel, seeker role, keeper role) for a puzzle."""
    if g["match_id"]:
        return g["match_id"], other(g["keeper"]), g["keeper"]
    return g["id"], "player", "host"


def publish_guess(g, guess, event="guess"):
    channel, seeker, keeper = roles(g)
    hub.publish(channel, event, lambda role: {"side": "seek", "guess": guess} if role == seeker
                else {"side": "watch", "guess": masked(guess)} if role == keeper else None)


def publish_finished(g):
    g = get_game(g["id"])
    channel, seeker, keeper = roles(g)
    seek, watch = seeker_view(g), observer_view(g)
    hub.publish(channel, "finished", lambda role: {"side": "seek", "state": seek} if role == seeker
                else {"side": "watch", "state": watch} if role == keeper else None)


app = FastAPI(title="Warmer")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# Fingerprint of the front-end code. Pages reference /static/x.js?v=<this>, so each release
# loads fresh URLs instead of whatever a browser cached from the last one (a cached old
# common.js under a new page breaks it).
ASSET_VERSION = hashlib.sha256(b"".join(f.read_bytes() for f in sorted(STATIC.iterdir()) if f.is_file())).hexdigest()[:10]
ASSET_REF = re.compile(r'((?:src|href)="/static/[\w.-]+\.(?:js|css|svg))"')


@app.middleware("http")
async def revalidate(request: Request, call_next):
    """Browsers may keep files but must check they're current before using them."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@lru_cache(maxsize=None)
def page_html(name):
    return ASSET_REF.sub(rf'\1?v={ASSET_VERSION}"', (STATIC / name).read_text())


def page(name):
    return HTMLResponse(page_html(name), headers={"Cache-Control": "no-cache"})


def sse(gen):
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- pages

@app.get("/")
def page_create():
    return page("create.html")


@app.get("/j/{match_id}")
def page_join(match_id: str):
    return page("create.html")


@app.get("/m/{match_id}")
def page_match(match_id: str):
    return page("match.html")


@app.get("/g/{game_id}")
def page_play(game_id: str):
    return page("play.html")


@app.get("/h/{game_id}")
def page_host(game_id: str):
    return page("host.html")


# ---------------------------------------------------------------- shared API

@app.get("/api/config")
def config():
    return {"default_name": DEFAULT_NAME, "env": ENV_LABEL}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/check")
def check_word(word: str):
    """Lets a player preview how a secret word plays before committing to it."""
    w = normalise(word)
    if w not in DICT_SET:
        known = w in W2I
        return {"ok": False, "word": w,
                "reason": "Too obscure to be a secret word — try a more common form." if known else "Not in the vocabulary."}
    ref = reference_points(w)
    # Difficulty: how tightly clustered the neighbourhood is. A low 1000th-neighbour score means a sparse, harder space.
    spread = ref["top1000"]
    difficulty = "gentle" if spread >= 30 else "moderate" if spread >= 22 else "challenging"
    return {"ok": True, "word": w, "neighbours": nearest(w, 12), "reference": ref, "difficulty": difficulty}


@app.get("/api/random")
def random_word():
    return {"word": random.choice(RANDOM_POOL)}


class WordChoice(BaseModel):
    secret: str
    name: str = Field("", max_length=40)
    theme: str = Field("", max_length=200)
    note: str = Field("", max_length=600)
    allow_hints: bool = True


class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=280)


class Guess(BaseModel):
    word: str = Field(..., max_length=40)
    hidden: bool = False


class Visibility(BaseModel):
    n: int
    hidden: bool


def valid_secret(word):
    w = normalise(word)
    if w not in DICT_SET:
        raise HTTPException(400, "That word isn't in the dictionary.")
    return w


def insert_puzzle(con, choice: WordChoice, secret, match_id=None, keeper=None, host_token=""):
    pid = secrets.token_urlsafe(6)
    con.execute("INSERT INTO games (id, host_token, secret, host_name, theme, note, allow_hints, created, match_id, keeper) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, host_token, secret, choice.name.strip(), choice.theme.strip(), choice.note.strip(),
                 int(choice.allow_hints), time.time(), match_id, keeper))
    return pid


# ---------------------------------------------------------------- puzzle actions

def ensure_playing(g):
    if status(g) != "playing":
        raise HTTPException(409, "This game is over.")


def record_guess(g, word, hint=False, hidden=False):
    sim, rank = score(g["secret"], word)
    solved = word == g["secret"]
    with db() as con:
        existing = con.execute("SELECT * FROM guesses WHERE game_id=? AND word=?", (g["id"], word)).fetchone()
        if existing:
            return {"duplicate": True, "guess": as_guess(existing)}
        n = con.execute("SELECT COUNT(*) FROM guesses WHERE game_id=?", (g["id"],)).fetchone()[0] + 1
        guess = dict(n=n, word=word, sim=100.0 if solved else round(sim * 100, 2), rank=0 if solved else rank,
                     hint=hint, hidden=hidden and not solved, ts=time.time())
        con.execute("INSERT INTO guesses (game_id, n, word, sim, rank, hint, hidden, ts) VALUES (?,?,?,?,?,?,?,?)",
                    (g["id"], n, word, guess["sim"], guess["rank"], int(hint), int(guess["hidden"]), guess["ts"]))
        if solved:
            con.execute("UPDATE games SET solved_at=? WHERE id=?", (guess["ts"], g["id"]))
    publish_guess(g, guess)
    if solved:
        publish_finished(g)
    return {"guess": guess, "solved": solved}


def do_guess(g, body: Guess):
    ensure_playing(g)
    w = normalise(body.word)
    if w not in W2I:
        raise HTTPException(422, f"“{w}” isn't in the vocabulary.")
    return record_guess(g, w, hidden=body.hidden)


def do_hint(g):
    """Reveal a word halfway between the seeker's best rank and the secret."""
    ensure_playing(g)
    if not g["allow_hints"]:
        raise HTTPException(403, "Hints are turned off for this game.")
    _, ranked, _ = neighbourhood(g["secret"])
    guesses = game_guesses(g["id"])
    taken = {x["word"] for x in guesses}
    best = min([x["rank"] for x in guesses if x["rank"]] or [TOP_N + 1])
    target = max(1, best // 2) if best <= TOP_N else int(TOP_N * 0.9)
    for r in list(range(target, best)) + list(range(target - 1, 0, -1)):
        if ranked[r - 1] not in taken:
            return record_guess(g, ranked[r - 1], hint=True)
    raise HTTPException(409, "No hints left — you're already as close as it gets!")


def do_give_up(g):
    if status(g) == "playing":
        with db() as con:
            con.execute("UPDATE games SET gave_up=? WHERE id=?", (time.time(), g["id"]))
        publish_finished(g)
    return seeker_view(get_game(g["id"]))


def do_visibility(g, body: Visibility):
    """The seeker can hide a guess from the keeper (or show it again) at any time."""
    with db() as con:
        r = con.execute("SELECT * FROM guesses WHERE game_id=? AND n=?", (g["id"], body.n)).fetchone()
        if not r:
            raise HTTPException(404, "No such guess.")
        if r["rank"] == 0:
            raise HTTPException(409, "The winning guess can't be hidden.")
        con.execute("UPDATE guesses SET hidden=? WHERE game_id=? AND n=?", (int(body.hidden), g["id"], body.n))
        guess = as_guess(con.execute("SELECT * FROM guesses WHERE game_id=? AND n=?", (g["id"], body.n)).fetchone())
    publish_guess(g, guess, event="guess-update")
    return {"guess": guess}


def post_message(channel, sender, text):
    msg = {"sender": sender, "text": text.strip(), "ts": time.time()}
    with db() as con:
        con.execute("INSERT INTO messages VALUES (?,?,?,?)", (channel, sender, msg["text"], msg["ts"]))
    hub.publish(channel, "message", msg)
    return msg


def guard_secret(g, text):
    if g and status(g) == "playing" and re.search(rf"\b{re.escape(g['secret'])}\b", text.lower()):
        raise HTTPException(400, "Careful — that message contains your secret word!")


# ---------------------------------------------------------------- matches (two-way games)

def match_seat(match_id: str, k: str):
    with db() as con:
        m = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        raise HTTPException(404, "Game not found")
    for seat in ("a", "b"):
        token = m[f"token_{seat}"]
        if token and secrets.compare_digest(token, k or ""):
            return m, seat
    raise HTTPException(403, "Invalid personal link")


def match_puzzle(match_id, keeper):
    with db() as con:
        return con.execute("SELECT * FROM games WHERE match_id=? AND keeper=?", (match_id, keeper)).fetchone()


def seek_puzzle(match_id, seat):
    """The puzzle this seat is guessing, i.e. the other player's word."""
    g = match_puzzle(match_id, other(seat))
    if not g:
        raise HTTPException(409, "Your friend hasn't chosen a word yet.")
    return g


@app.post("/api/matches")
def create_match(body: WordChoice):
    secret = valid_secret(body.secret)
    mid, token = secrets.token_urlsafe(6), secrets.token_urlsafe(16)
    with db() as con:
        con.execute("INSERT INTO matches (id, token_a, name_a, created) VALUES (?,?,?,?)",
                    (mid, token, body.name.strip(), time.time()))
        insert_puzzle(con, body, secret, match_id=mid, keeper="a")
    return {"id": mid, "token": token, "url": f"/m/{mid}?k={token}", "invite_url": f"/j/{mid}"}


@app.get("/api/matches/{match_id}/invite")
def match_invite(match_id: str):
    with db() as con:
        m = con.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        raise HTTPException(404, "Game not found")
    g = match_puzzle(match_id, "a")
    return {"id": match_id, "name": m["name_a"], "theme": g["theme"] if g else "", "joined": bool(m["token_b"])}


@app.post("/api/matches/{match_id}/join")
def join_match(match_id: str, body: WordChoice):
    secret = valid_secret(body.secret)
    token = secrets.token_urlsafe(16)
    with db() as con:
        cur = con.execute("UPDATE matches SET token_b=?, name_b=?, joined=? WHERE id=? AND token_b IS NULL",
                          (token, body.name.strip(), time.time(), match_id))
        if cur.rowcount != 1:
            exists = con.execute("SELECT 1 FROM matches WHERE id=?", (match_id,)).fetchone()
            raise HTTPException(409 if exists else 404,
                                "This game already has two players." if exists else "Game not found")
        insert_puzzle(con, body, secret, match_id=match_id, keeper="b")
    hub.publish(match_id, "joined", {"name": body.name.strip()})
    return {"id": match_id, "token": token, "url": f"/m/{match_id}?k={token}"}


@app.get("/api/matches/{match_id}")
def match_state(match_id: str, k: str):
    m, seat = match_seat(match_id, k)
    mine, theirs = match_puzzle(match_id, seat), match_puzzle(match_id, other(seat))
    return {"id": match_id, "me": seat, "names": {"a": m["name_a"], "b": m["name_b"]}, "joined": bool(m["token_b"]),
            "invite_url": f"/j/{match_id}",
            "watch": observer_view(mine) if mine else None, "seek": seeker_view(theirs) if theirs else None,
            "messages": game_messages(match_id), "presence": hub.presence(match_id)}


@app.get("/api/matches/{match_id}/stream")
async def match_stream(match_id: str, k: str, request: Request):
    _, seat = match_seat(match_id, k)
    return sse(hub.stream(match_id, seat, request))


@app.post("/api/matches/{match_id}/guess")
def match_guess(match_id: str, k: str, body: Guess):
    _, seat = match_seat(match_id, k)
    return do_guess(seek_puzzle(match_id, seat), body)


@app.post("/api/matches/{match_id}/hint")
def match_hint(match_id: str, k: str):
    _, seat = match_seat(match_id, k)
    return do_hint(seek_puzzle(match_id, seat))


@app.post("/api/matches/{match_id}/give-up")
def match_give_up(match_id: str, k: str):
    _, seat = match_seat(match_id, k)
    return do_give_up(seek_puzzle(match_id, seat))


@app.post("/api/matches/{match_id}/visibility")
def match_visibility(match_id: str, k: str, body: Visibility):
    _, seat = match_seat(match_id, k)
    return do_visibility(seek_puzzle(match_id, seat), body)


@app.post("/api/matches/{match_id}/message")
def match_message(match_id: str, k: str, body: Message):
    _, seat = match_seat(match_id, k)
    guard_secret(match_puzzle(match_id, seat), body.text)
    return post_message(match_id, seat, body.text)


# ---------------------------------------------------------------- one-way games (v1 links)

def require_host(game_id: str, k: str):
    g = get_game(game_id)
    if g["match_id"] or not secrets.compare_digest(g["host_token"], k or ""):
        raise HTTPException(403, "Invalid host key")
    return g


def legacy_game(game_id: str):
    g = get_game(game_id)
    if g["match_id"]:
        raise HTTPException(404, "Game not found")  # match puzzles are only reachable with a personal link
    return g


@app.post("/api/games")
def create_game(body: WordChoice):
    secret = valid_secret(body.secret)
    token = secrets.token_urlsafe(16)
    with db() as con:
        gid = insert_puzzle(con, body, secret, host_token=token)
    return {"id": gid, "host_token": token, "play_url": f"/g/{gid}", "host_url": f"/h/{gid}?k={token}"}


@app.get("/api/games/{game_id}/host")
def host_state(game_id: str, k: str):
    g = require_host(game_id, k)
    return {**observer_view(g), "messages": game_messages(game_id), "presence": hub.presence(game_id)}


@app.get("/api/games/{game_id}/host/stream")
async def host_stream(game_id: str, k: str, request: Request):
    require_host(game_id, k)
    return sse(hub.stream(game_id, "host", request))


@app.post("/api/games/{game_id}/host/message")
def host_message(game_id: str, k: str, body: Message):
    g = require_host(game_id, k)
    guard_secret(g, body.text)
    return post_message(game_id, "host", body.text)


@app.get("/api/games/{game_id}")
def player_state(game_id: str):
    g = legacy_game(game_id)
    return {**seeker_view(g), "messages": game_messages(game_id), "presence": hub.presence(game_id)}


@app.get("/api/games/{game_id}/stream")
async def player_stream(game_id: str, request: Request):
    legacy_game(game_id)
    return sse(hub.stream(game_id, "player", request))


@app.post("/api/games/{game_id}/guess")
def guess(game_id: str, body: Guess):
    return do_guess(legacy_game(game_id), body)


@app.post("/api/games/{game_id}/hint")
def hint(game_id: str):
    return do_hint(legacy_game(game_id))


@app.post("/api/games/{game_id}/give-up")
def give_up(game_id: str):
    return do_give_up(legacy_game(game_id))


@app.post("/api/games/{game_id}/visibility")
def visibility(game_id: str, body: Visibility):
    return do_visibility(legacy_game(game_id), body)


@app.post("/api/games/{game_id}/message")
def player_message(game_id: str, body: Message):
    legacy_game(game_id)
    return post_message(game_id, "player", body.text)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
