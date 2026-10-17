"""Kindred — a two-player semantic word game built on word2vec.

One person picks a secret word and shares a link; their friend hunts for it by
semantic proximity while the creator watches every guess live.
"""
import asyncio
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
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
DB_PATH = os.environ.get("KINDRED_DB", str(ROOT / "kindred.db"))
TOP_N = 1000  # how many nearest neighbours earn a rank

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


# ---------------------------------------------------------------- storage

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


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
        """
    )


def get_game(game_id: str):
    with db() as con:
        g = con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not g:
        raise HTTPException(404, "Game not found")
    return g


def game_guesses(game_id: str):
    with db() as con:
        rows = con.execute("SELECT * FROM guesses WHERE game_id=? ORDER BY n", (game_id,)).fetchall()
    return [dict(n=r["n"], word=r["word"], sim=r["sim"], rank=r["rank"], hint=bool(r["hint"]), ts=r["ts"]) for r in rows]


def game_messages(game_id: str):
    with db() as con:
        rows = con.execute("SELECT sender, text, ts FROM messages WHERE game_id=? ORDER BY ts", (game_id,)).fetchall()
    return [dict(r) for r in rows]


def status(g):
    return "solved" if g["solved_at"] else "revealed" if g["gave_up"] else "playing"


# ---------------------------------------------------------------- live updates

class Hub:
    """Fan-out of game events to connected SSE clients, with presence counts per role."""

    def __init__(self):
        self.subs: dict[str, list[tuple[str, asyncio.Queue]]] = {}

    def presence(self, game_id):
        roles = [r for r, _ in self.subs.get(game_id, [])]
        return {"players": roles.count("player"), "hosts": roles.count("host")}

    def publish(self, game_id, event, data, roles=("player", "host")):
        for role, q in self.subs.get(game_id, []):
            if role in roles:
                q.put_nowait((event, data))

    async def stream(self, game_id, role, request: Request):
        q: asyncio.Queue = asyncio.Queue()
        self.subs.setdefault(game_id, []).append((role, q))
        self.publish(game_id, "presence", self.presence(game_id))
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
            self.subs[game_id].remove((role, q))
            self.publish(game_id, "presence", self.presence(game_id))


hub = Hub()
app = FastAPI(title="Kindred")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def sse(gen):
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- pages

@app.get("/")
def page_create():
    return FileResponse(STATIC / "create.html")


@app.get("/g/{game_id}")
def page_play(game_id: str):
    return FileResponse(STATIC / "play.html")


@app.get("/h/{game_id}")
def page_host(game_id: str):
    return FileResponse(STATIC / "host.html")


# ---------------------------------------------------------------- creator API

@app.get("/api/check")
def check_word(word: str):
    """Lets the creator preview how a secret word plays before committing to it."""
    w = normalise(word)
    if w not in DICT_SET:
        known = w in W2I
        return {"ok": False, "word": w,
                "reason": "Too obscure to be a secret word — try a more common form." if known else "Not in the vocabulary."}
    sims, ranked, _ = neighbourhood(w)
    ref = reference_points(w)
    # Difficulty: how tightly clustered the neighbourhood is. A low 1000th-neighbour score means a sparse, harder space.
    spread = ref["top1000"]
    difficulty = "gentle" if spread >= 30 else "moderate" if spread >= 22 else "challenging"
    return {"ok": True, "word": w, "neighbours": [[x, round(float(sims[W2I[x]]) * 100, 1)] for x in ranked[:12]],
            "reference": ref, "difficulty": difficulty}


@app.get("/api/random")
def random_word():
    return {"word": random.choice(RANDOM_POOL)}


class NewGame(BaseModel):
    secret: str
    host_name: str = Field("", max_length=40)
    theme: str = Field("", max_length=200)
    note: str = Field("", max_length=600)
    allow_hints: bool = True


@app.post("/api/games")
def create_game(body: NewGame):
    w = normalise(body.secret)
    if w not in DICT_SET:
        raise HTTPException(400, "That word isn't in the dictionary.")
    game_id = secrets.token_urlsafe(6)
    token = secrets.token_urlsafe(16)
    with db() as con:
        con.execute("INSERT INTO games (id, host_token, secret, host_name, theme, note, allow_hints, created) VALUES (?,?,?,?,?,?,?,?)",
                    (game_id, token, w, body.host_name.strip(), body.theme.strip(), body.note.strip(), int(body.allow_hints), time.time()))
    return {"id": game_id, "host_token": token, "play_url": f"/g/{game_id}", "host_url": f"/h/{game_id}?k={token}"}


def require_host(game_id: str, k: str):
    g = get_game(game_id)
    if not secrets.compare_digest(g["host_token"], k or ""):
        raise HTTPException(403, "Invalid host key")
    return g


@app.get("/api/games/{game_id}/host")
def host_state(game_id: str, k: str):
    g = require_host(game_id, k)
    sims, ranked, _ = neighbourhood(g["secret"])
    return {"id": game_id, "secret": g["secret"], "host_name": g["host_name"], "theme": g["theme"], "note": g["note"],
            "allow_hints": bool(g["allow_hints"]), "status": status(g), "created": g["created"],
            "solved_at": g["solved_at"], "reference": reference_points(g["secret"]),
            "neighbours": [[x, round(float(sims[W2I[x]]) * 100, 1)] for x in ranked[:30]],
            "guesses": game_guesses(game_id), "messages": game_messages(game_id), "presence": hub.presence(game_id)}


@app.get("/api/games/{game_id}/host/stream")
async def host_stream(game_id: str, k: str, request: Request):
    require_host(game_id, k)
    return sse(hub.stream(game_id, "host", request))


class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=280)


@app.post("/api/games/{game_id}/host/message")
def host_message(game_id: str, k: str, body: Message):
    g = require_host(game_id, k)
    if re.search(rf"\b{re.escape(g['secret'])}\b", body.text.lower()) and status(g) == "playing":
        raise HTTPException(400, "Careful — that message contains the secret word!")
    return post_message(game_id, "host", body.text)


# ---------------------------------------------------------------- player API

def public_state(g):
    finished = status(g) != "playing"
    sims, ranked, _ = neighbourhood(g["secret"])
    return {"id": g["id"], "host_name": g["host_name"], "theme": g["theme"], "allow_hints": bool(g["allow_hints"]),
            "status": status(g), "reference": reference_points(g["secret"]), "top_n": TOP_N,
            "secret": g["secret"] if finished else None, "note": g["note"] if finished else None,
            "neighbours": [[x, round(float(sims[W2I[x]]) * 100, 1)] for x in ranked[:100]] if finished else None}


@app.get("/api/games/{game_id}")
def player_state(game_id: str):
    g = get_game(game_id)
    return {**public_state(g), "guesses": game_guesses(game_id), "messages": game_messages(game_id),
            "presence": hub.presence(game_id)}


@app.get("/api/games/{game_id}/stream")
async def player_stream(game_id: str, request: Request):
    get_game(game_id)
    return sse(hub.stream(game_id, "player", request))


def record_guess(g, word, hint=False):
    sim, rank = score(g["secret"], word)
    solved = word == g["secret"]
    with db() as con:
        existing = con.execute("SELECT * FROM guesses WHERE game_id=? AND word=?", (g["id"], word)).fetchone()
        if existing:
            return {"duplicate": True, "guess": dict(n=existing["n"], word=word, sim=existing["sim"], rank=existing["rank"], hint=bool(existing["hint"]), ts=existing["ts"])}
        n = con.execute("SELECT COUNT(*) FROM guesses WHERE game_id=?", (g["id"],)).fetchone()[0] + 1
        guess = dict(n=n, word=word, sim=100.0 if solved else round(sim * 100, 2), rank=0 if solved else rank, hint=hint, ts=time.time())
        con.execute("INSERT INTO guesses VALUES (?,?,?,?,?,?,?)", (g["id"], n, word, guess["sim"], guess["rank"], int(hint), guess["ts"]))
        if solved:
            con.execute("UPDATE games SET solved_at=? WHERE id=?", (guess["ts"], g["id"]))
    hub.publish(g["id"], "guess", guess)
    if solved:
        hub.publish(g["id"], "finished", public_state(get_game(g["id"])))
    return {"guess": guess, "solved": solved}


class Guess(BaseModel):
    word: str = Field(..., max_length=40)


@app.post("/api/games/{game_id}/guess")
def guess(game_id: str, body: Guess):
    g = get_game(game_id)
    if status(g) != "playing":
        raise HTTPException(409, "This game is over.")
    w = normalise(body.word)
    if w not in W2I:
        raise HTTPException(422, f"“{w}” isn't in the vocabulary.")
    return record_guess(g, w)


@app.post("/api/games/{game_id}/hint")
def hint(game_id: str):
    """Reveal a word halfway between the player's best rank and the secret."""
    g = get_game(game_id)
    if status(g) != "playing":
        raise HTTPException(409, "This game is over.")
    if not g["allow_hints"]:
        raise HTTPException(403, "Hints are turned off for this game.")
    _, ranked, _ = neighbourhood(g["secret"])
    guesses = game_guesses(game_id)
    taken = {x["word"] for x in guesses}
    best = min([x["rank"] for x in guesses if x["rank"]] or [TOP_N + 1])
    target = max(1, best // 2) if best <= TOP_N else int(TOP_N * 0.9)
    for r in list(range(target, best)) + list(range(target - 1, 0, -1)):
        if ranked[r - 1] not in taken:
            return record_guess(g, ranked[r - 1], hint=True)
    raise HTTPException(409, "No hints left — you're already as close as it gets!")


@app.post("/api/games/{game_id}/give-up")
def give_up(game_id: str):
    g = get_game(game_id)
    if status(g) == "playing":
        with db() as con:
            con.execute("UPDATE games SET gave_up=? WHERE id=?", (time.time(), game_id))
        hub.publish(game_id, "finished", public_state(get_game(game_id)))
    return public_state(get_game(game_id))


@app.post("/api/games/{game_id}/message")
def player_message(game_id: str, body: Message):
    get_game(game_id)
    return post_message(game_id, "player", body.text)


def post_message(game_id, sender, text):
    msg = {"sender": sender, "text": text.strip(), "ts": time.time()}
    with db() as con:
        con.execute("INSERT INTO messages VALUES (?,?,?,?)", (game_id, sender, msg["text"], msg["ts"]))
    hub.publish(game_id, "message", msg)
    return msg


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
