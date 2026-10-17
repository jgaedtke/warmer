# Kindred

A two-player semantic word game built on word2vec, in the spirit of Semantle.
One person chooses a secret word and shares a link. Their friend hunts for it, guided
by how close each guess is in meaning, while the creator watches every guess live.

## Run

    pip install -r requirements.txt
    python app.py            # http://localhost:8000  (PORT env var to change)

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
  ("Jamie is watching" / "seeker online").
- **Storage**: SQLite (`kindred.db`, or the `KINDRED_DB` env var).
