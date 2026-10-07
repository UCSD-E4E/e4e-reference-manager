"""Topic clustering: group a project's papers by their embeddings (k-means on cosine
similarity), then let the LLM name each cluster from its papers' titles.

Pure helpers here; the endpoint lives in routers/auto_groups.py.
"""
from __future__ import annotations

import math
import re

import numpy as np

# Below this many papers with embeddings, clusters say nothing useful.
MIN_TOPIC_ITEMS = 6
MAX_TOPICS = 15
TITLES_PER_PROMPT = 15

TOPIC_SYSTEM = (
    "You are a research librarian. The user lists paper titles that share one research "
    "topic. Reply with ONLY a short name for that shared topic: 1 to 4 lowercase words, "
    "broad enough to cover every title. No quotes, no explanation."
)


def choose_k(n: int) -> int:
    """Number of topics for n papers: ~sqrt(n/2), at least 2, at most MAX_TOPICS."""
    return max(2, min(MAX_TOPICS, round(math.sqrt(n / 2))))


def cluster(vectors: list[list[float]], k: int, *, iters: int = 50, seed: int = 0) -> list[int]:
    """Spherical k-means (k-means++ seeding, fixed seed so a rerun on the same project
    gives the same topics). Returns one cluster label per vector."""
    x = np.asarray(vectors, dtype=np.float64)
    x /= np.linalg.norm(x, axis=1, keepdims=True).clip(min=1e-12)
    rng = np.random.default_rng(seed)
    k = min(k, len(x))

    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d = np.min([1.0 - x @ c for c in centers], axis=0).clip(min=0)
        total = d.sum()
        idx = rng.choice(len(x), p=d / total) if total > 0 else rng.integers(len(x))
        centers.append(x[idx])
    c = np.array(centers)

    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        new = np.argmax(x @ c.T, axis=1)
        if _ and np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                m = members.sum(axis=0)
                c[j] = m / max(np.linalg.norm(m), 1e-12)
    return labels.tolist()


def topic_prompt(titles: list[str]) -> str:
    return "Titles:\n" + "\n".join(f"- {t}" for t in titles[:TITLES_PER_PROMPT])


def parse_topic_name(text: str | None) -> str | None:
    """First line of the reply, minus quotes / a 'Topic:' label / trailing punctuation."""
    if not text or not text.strip():
        return None
    line = text.strip().splitlines()[0]
    line = re.sub(r"^\s*(topic|name)\s*:\s*", "", line, flags=re.IGNORECASE)
    line = line.strip().strip("\"'`*").strip().rstrip(".!").strip().lower()
    return line[:60] or None
