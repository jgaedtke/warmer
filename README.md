# Warmer

A two-player semantic word game built on word2vec, in the spirit of Semantle.
One person chooses a secret word and shares a link. Their friend hunts for it, guided
by how close each guess is in meaning, while the creator watches every guess live.

## Run

    pip install -r requirements.txt
    python app.py            # http://localhost:8000  (PORT env var to change)

Settings (environment variables):

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | HTTP port |
| `WARMER_DB` | `./warmer.db` | SQLite database path |
| `WARMER_DEFAULT_NAME` | *(empty)* | Pre-fills the creator's name on the create page |

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

## How it works

- **Embeddings**: Google News word2vec (300-d, ~100B-word corpus). The first 1M entries
  (frequency-ordered) are filtered to 105k lowercase English tokens and stored as
  unit-normalised float16 in `data/vectors.npy` (63 MB). `data/dictionary.txt` (84k words)
  is the subset found in a standard English word list. Secret words and rankings use it,
  and any of the 105k words can be guessed. Rebuild with `scripts/build_vectors.py`.
- **Scoring**: cosine similarity × 100. A guess among the secret's 1,000 nearest dictionary
  words also gets a rank (1 = nearest). The colour scale goes from cold blue to hot coral,
  log-scaled by rank.
- **Pages**
  - `/`: the creator picks a word (live validation, nearest-neighbour preview, difficulty),
    an optional opening clue, a note revealed on solve, and whether hints are allowed.
  - `/g/<id>`: the player guesses, gets hints, gives up, and chats with the creator.
  - `/h/<id>?k=<key>`: the private observer view shows a live guess feed, a proximity chart,
    stats, which top neighbours have been hit, and lets the creator send nudges.
- **Live updates**: server-sent events, with presence shown in both directions
  ("Jason is watching" / "seeker online").
- **Storage**: SQLite (`warmer.db`, or the `WARMER_DB` env var).
