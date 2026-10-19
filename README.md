# Warmer

A two-player semantic word game built on word2vec, in the spirit of Semantle.
Each player picks a secret word for the other. Both hunt for the other's word, guided by
how close each guess is in meaning, while watching the other's progress on their own word live.

## How to play

1. Choose a word at `/`. You get an invite link for your friend.
2. Your friend opens it, sees your name and clue, and **chooses a word for you** in return.
3. You each get a personal link with two tabs:
   - **Find their word**: guess, get hints, give up, and see the reveal.
   - **Them finding yours**: watch their guesses arrive live, with stats, a chart of their
     path, and which of your word's nearest neighbours they've found.

Both tabs share one chat. Guess lists can be sorted by **Closest** or **Recent**. Each view
remembers its own choice. Seekers can **hide** guesses (the eye beside the guess box, or on any row):
hidden guesses are scored as usual, but the other player sees `•••••••` instead of the word.

Games created before two-way play (one-way `/g/<id>` and `/h/<id>?k=…` links) keep working.

## How it works

- **Embeddings**: Google News word2vec (300-d, ~100B-word corpus). The first 1M entries
  (frequency-ordered) are filtered to 105k lowercase English tokens and stored as
  unit-normalised float16 in `data/vectors.npy` (63 MB). `data/dictionary.txt` (84k words)
  is the subset found in a standard English word list. Secret words and rankings use it,
  and any of the 105k words can be guessed. Rebuild with `scripts/build_vectors.py`.
- **Scoring**: cosine similarity × 100. A guess among the secret's 1,000 nearest dictionary
  words also gets a rank (1 = nearest). The colour scale goes from cold blue to hot coral,
  log-scaled by rank.
- **Model**: a *puzzle* is one secret word with a keeper and a seeker. A *match* pairs two
  puzzles so each player keeps one and seeks the other. Players are identified by the token
  in their personal link. Hidden guesses are masked on the server, so the keeper's browser
  never receives the word.
- **Live updates**: server-sent events, tailored per player (masking, "seek" vs "watch" side).
- **Storage**: SQLite. Schema changes are additive and applied automatically at startup.

| Path | What |
|---|---|
| `app.py` | Server: embeddings, API, live updates, storage |
| `static/create.html` | Start a game (`/`) and join one (`/j/<id>`) |
| `static/match.html` | Two-way game with Find / Watch tabs (`/m/<id>?k=…`) |
| `static/seeker.js`, `static/observer.js` | The guessing and watching panels |
| `static/play.html`, `static/host.html` | One-way games from v1 |
| `tests/` | API tests (pytest) |

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | HTTP port |
| `WARMER_DB` | `./warmer.db` | SQLite database path |
| `WARMER_DEFAULT_NAME` | *(empty)* | Pre-fills the creator's name on the start page |
| `WARMER_ENV` | *(empty)* | Label such as `staging` or `local`, shown as a badge on every page |

## Development workflow

```
feature work ──► dev ──(push)──► staging (optional, Render free)
                  │
                  └──(pull request + merge, explicit)──► main ──► production (Render)
```

- **`main` is production.** Render deploys every commit on `main`, so nothing lands there
  except through a deliberate merge.
- **`dev` is where new work goes.** Push freely. GitHub Actions runs the tests on every push.

**Run locally**

    pip install -r requirements.txt -r requirements-dev.txt
    WARMER_ENV=local uvicorn app:app --reload    # http://localhost:8000, reloads on save
    pytest -q

**Staging (one-time setup, optional)**: in Render, New → Web Service → this repo, branch
`dev`, runtime Docker, instance type Free, environment variable `WARMER_ENV=staging`.
It redeploys on every push to `dev`, uses its own throwaway database, and shows a
STAGING badge so it's never mistaken for the real game.

**Release to production**: on GitHub, open a pull request from `dev` into `main`
(Pull requests → New → base `main`, compare `dev`). Check the tests are green, then merge.
Render deploys within a few minutes. Your production games and links carry over, because
database changes are additive and applied at startup.

**Roll back**: in Render, open the service → Events → pick the previous deploy →
Rollback. Or revert the merge on GitHub.

## Deploy

The app is a single process (~200 MB RAM) that holds live connections in memory, so run
**one instance**. Any host that runs a Dockerfile works.

- **Render (easiest):** Dashboard → New → Blueprint → select this repo. `render.yaml`
  sets up a Starter instance (always on, ~$7/month) with a 1 GB persistent disk at `/data`
  (~$0.25/month), so games survive deploys and restarts. Deploys have a few seconds of
  downtime because a service with a disk runs as a single instance. To try it for free
  instead, set `plan: free` and remove the `disk` block: free instances sleep after
  15 idle minutes and the database resets on every restart.
- **Railway / Fly.io (persistent):** deploy the Dockerfile and mount a volume at `/data`
  so `/data/warmer.db` survives restarts.
