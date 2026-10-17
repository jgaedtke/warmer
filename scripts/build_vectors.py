"""Build data/vectors.npy + data/words.txt + data/dictionary.txt from Google News word2vec.

The source binary is sorted by corpus frequency, so we stream-decompress it and stop
after the first N entries, keeping only lowercase alphabetic tokens.

Usage:
  curl -sSL https://github.com/RaRe-Technologies/gensim-data/releases/download/word2vec-google-news-300/word2vec-google-news-300.gz \
    | python3 scripts/build_vectors.py 1000000 data/

  dictionary.txt is the subset of words present in an English word list
  (npm: an-array-of-english-words); pass its index.json as a 3rd arg.
"""
import gzip
import json
import os
import re
import sys

import numpy as np

N = int(sys.argv[1])
out = sys.argv[2]
english = set(json.load(open(sys.argv[3]))) if len(sys.argv) > 3 else None

f = gzip.open(sys.stdin.buffer)
dim = int(f.readline().split()[1])
pat = re.compile(r"^[a-z][a-z'-]*[a-z]$|^[a-z]$")
words, vecs = [], []
for i in range(N):
    w = bytearray()
    while True:
        c = f.read(1)
        if c == b" ":
            break
        if c != b"\n":
            w += c
    v = f.read(dim * 4)
    try:
        s = w.decode("utf8")
    except UnicodeDecodeError:
        continue
    if pat.match(s) and "--" not in s:
        words.append(s)
        vecs.append(np.frombuffer(v, dtype="<f4"))
    if i % 100000 == 0:
        print(i, len(words), file=sys.stderr, flush=True)

M = np.vstack(vecs).astype(np.float32)
M /= np.linalg.norm(M, axis=1, keepdims=True)
np.save(os.path.join(out, "vectors.npy"), M.astype(np.float16))
open(os.path.join(out, "words.txt"), "w").write("\n".join(words))
if english:
    open(os.path.join(out, "dictionary.txt"), "w").write("\n".join(w for w in words if w in english))
print("done", len(words), file=sys.stderr)
