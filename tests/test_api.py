import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as warmer

client = TestClient(warmer.app)


def new_match(secret="lighthouse", name="Jason", **kw):
    r = client.post("/api/matches", json={"secret": secret, "name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def join(mid, secret="guitar", name="Alex"):
    r = client.post(f"/api/matches/{mid}/join", json={"secret": secret, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def guess(url, word, hidden=False):
    r = client.post(url, json={"word": word, "hidden": hidden})
    assert r.status_code == 200, r.text
    return r.json()["guess"]


def listen(channel, *roles):
    """Attach fake SSE subscribers so we can see exactly what each role is sent."""
    queues = {role: asyncio.Queue() for role in roles}
    warmer.hub.subs.setdefault(channel, []).extend(queues.items())
    return queues


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ---------------------------------------------------------------- matches

def test_match_lifecycle():
    m = new_match()
    a = f"k={m['token']}"
    state = client.get(f"/api/matches/{m['id']}?{a}").json()
    assert state["me"] == "a" and not state["joined"] and state["seek"] is None
    assert state["watch"]["secret"] == "lighthouse"
    # Can't guess before the friend has chosen a word.
    assert client.post(f"/api/matches/{m['id']}/guess?{a}", json={"word": "sea"}).status_code == 409

    inv = client.get(f"/api/matches/{m['id']}/invite").json()
    assert inv == {"id": m["id"], "name": "Jason", "theme": "", "joined": False}

    j = join(m["id"])
    b = f"k={j['token']}"
    assert client.get(f"/api/matches/{m['id']}/invite").json()["joined"] is True
    # A second join is refused.
    assert client.post(f"/api/matches/{m['id']}/join", json={"secret": "ocean"}).status_code == 409

    # Each player guesses the *other* player's word.
    ga = guess(f"/api/matches/{m['id']}/guess?{a}", "guitar")
    assert ga["rank"] == 0
    gb = guess(f"/api/matches/{m['id']}/guess?{b}", "pier")
    assert gb["rank"] and gb["rank"] <= 10

    sa = client.get(f"/api/matches/{m['id']}?{a}").json()
    sb = client.get(f"/api/matches/{m['id']}?{b}").json()
    assert sa["seek"]["status"] == "solved" and sa["seek"]["secret"] == "guitar"
    assert sb["seek"]["status"] == "playing" and sb["seek"]["secret"] is None
    assert [g["word"] for g in sa["watch"]["guesses"]] == ["pier"]
    assert sa["names"] == {"a": "Jason", "b": "Alex"} and sb["me"] == "b"


def test_match_auth():
    m = new_match()
    assert client.get(f"/api/matches/{m['id']}?k=nope").status_code == 403
    assert client.get(f"/api/matches/nope?k={m['token']}").status_code == 404
    assert client.post("/api/matches", json={"secret": "xyzzq"}).status_code == 400
    # Match puzzles are never reachable through the open one-way endpoints.
    j = join(m["id"])
    pid = client.get(f"/api/matches/{m['id']}?k={j['token']}").json()["seek"]["id"]
    assert client.get(f"/api/games/{pid}").status_code == 404
    assert client.post(f"/api/games/{pid}/guess", json={"word": "sea"}).status_code == 404


def test_hidden_guesses_are_masked_for_the_keeper():
    m = new_match()
    j = join(m["id"])
    a, b = f"k={m['token']}", f"k={j['token']}"
    q = listen(m["id"], "a", "b")

    g = guess(f"/api/matches/{m['id']}/guess?{b}", "pier", hidden=True)
    assert g["hidden"] and g["word"] == "pier"

    # Live events: B (seeker) sees the word, A (keeper) gets it masked but scored.
    (ev_b,) = [d for e, d in drain(q["b"]) if e == "guess"]
    (ev_a,) = [d for e, d in drain(q["a"]) if e == "guess"]
    assert ev_b == {"side": "seek", "guess": g}
    assert ev_a["side"] == "watch" and ev_a["guess"]["word"] is None
    assert ev_a["guess"]["sim"] == g["sim"] and ev_a["guess"]["rank"] == g["rank"]

    # Snapshot views agree, and the hidden word never lights up the keeper's neighbour list.
    assert client.get(f"/api/matches/{m['id']}?{a}").json()["watch"]["guesses"][0]["word"] is None
    assert client.get(f"/api/matches/{m['id']}?{b}").json()["seek"]["guesses"][0]["word"] == "pier"

    # Unhide later: the keeper now sees it.
    r = client.post(f"/api/matches/{m['id']}/visibility?{b}", json={"n": g["n"], "hidden": False})
    assert r.status_code == 200
    (ev_a,) = [d for e, d in drain(q["a"]) if e == "guess-update"]
    assert ev_a["guess"]["word"] == "pier"

    # The winning guess can't be hidden, even if submitted in hidden mode.
    win = guess(f"/api/matches/{m['id']}/guess?{b}", "lighthouse", hidden=True)
    assert win["rank"] == 0 and not win["hidden"]
    assert client.post(f"/api/matches/{m['id']}/visibility?{b}", json={"n": win["n"], "hidden": True}).status_code == 409


def test_message_guard_checks_own_word_only():
    m = new_match()
    j = join(m["id"])
    url = f"/api/matches/{m['id']}/message"
    assert client.post(f"{url}?k={m['token']}", json={"text": "the LIGHTHOUSE!"}).status_code == 400
    assert client.post(f"{url}?k={m['token']}", json={"text": "lighthouses are nice"}).status_code == 200
    assert client.post(f"{url}?k={j['token']}", json={"text": "lighthouse?"}).status_code == 200
    msgs = client.get(f"/api/matches/{m['id']}?k={m['token']}").json()["messages"]
    assert [x["sender"] for x in msgs] == ["a", "b"]


def test_hint_and_give_up():
    m = new_match()
    j = join(m["id"])
    b = f"k={j['token']}"
    h = client.post(f"/api/matches/{m['id']}/hint?{b}").json()["guess"]
    assert h["hint"] and h["rank"] <= 1000
    st = client.post(f"/api/matches/{m['id']}/give-up?{b}").json()
    assert st["status"] == "revealed" and st["secret"] == "lighthouse" and len(st["neighbours"]) == 100
    assert client.post(f"/api/matches/{m['id']}/guess?{b}", json={"word": "sea"}).status_code == 409


# ---------------------------------------------------------------- one-way games (v1)

def test_one_way_game_still_works():
    r = client.post("/api/games", json={"secret": "ocean", "name": "Jason"}).json()
    gid, k = r["id"], r["host_token"]
    g = guess(f"/api/games/{gid}/guess", "sea", hidden=True)
    assert client.get(f"/api/games/{gid}/host?k={k}").json()["guesses"][0]["word"] is None
    assert client.get(f"/api/games/{gid}").json()["guesses"][0]["word"] == "sea"
    assert client.post(f"/api/games/{gid}/visibility", json={"n": g["n"], "hidden": False}).status_code == 200
    assert client.get(f"/api/games/{gid}/host?k={k}").json()["guesses"][0]["word"] == "sea"
    assert client.get(f"/api/games/{gid}/host?k=wrong").status_code == 403


def test_migrates_a_v1_database(tmp_path, monkeypatch):
    """A database written by the first release (no matches, no hidden column) keeps working."""
    path = tmp_path / "v1.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE games (id TEXT PRIMARY KEY, host_token TEXT NOT NULL, secret TEXT NOT NULL,
          host_name TEXT, theme TEXT, note TEXT, allow_hints INTEGER DEFAULT 1, created REAL, solved_at REAL, gave_up REAL);
        CREATE TABLE guesses (game_id TEXT, n INTEGER, word TEXT, sim REAL, rank INTEGER,
          hint INTEGER DEFAULT 0, ts REAL, PRIMARY KEY (game_id, word));
        CREATE TABLE messages (game_id TEXT, sender TEXT, text TEXT, ts REAL);
        INSERT INTO games VALUES ('old1', 'tok', 'lighthouse', 'Jason', 'coast', 'foghorn', 1, 1.0, NULL, NULL);
        INSERT INTO guesses VALUES ('old1', 1, 'sea', 34.27, 477, 0, 2.0);
        INSERT INTO messages VALUES ('old1', 'host', 'hi', 3.0);
    """)
    con.commit(); con.close()

    monkeypatch.setattr(warmer, "DB_PATH", str(path))
    warmer.migrate()
    warmer.migrate()  # idempotent

    host = client.get("/api/games/old1/host?k=tok").json()
    assert host["secret"] == "lighthouse" and host["guesses"][0] == {
        "n": 1, "word": "sea", "sim": 34.27, "rank": 477, "hint": False, "hidden": False, "ts": 2.0}
    assert client.get("/api/games/old1").json()["messages"][0]["text"] == "hi"
    g = guess("/api/games/old1/guess", "pier")
    assert g["n"] == 2
    assert new_match()["id"]  # new tables work on the migrated file too


# ---------------------------------------------------------------- caching

def test_pages_reference_versioned_assets():
    """Each release must load fresh JS/CSS, never a copy cached from the previous release."""
    for path in ["/", "/j/x", "/m/x", "/g/x", "/h/x"]:
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
        refs = __import__("re").findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
        assert refs and all(f"?v={warmer.ASSET_VERSION}" in u for u in refs), refs
        for u in refs:
            assert client.get(u).headers["cache-control"] == "no-cache"
