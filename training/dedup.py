"""
Groups descriptions that are the same ad in all but name.

Exact duplicates are easy (same text hash). The harder case is the same
apartment re-posted by two agencies, or one agency's template with only
the price line changed — different bytes, same training example. Left in,
they over-weight one ad in training and, worse, leak across the
train/test split so evaluation scores look better than the model is.

Each description gets a cluster id; Step 4 splits by cluster, never by
row. Pure Python on purpose: at a few thousand ads the pairwise pass
takes seconds, and the length-ratio bound below prunes most pairs.
"""

import hashlib
import re
from collections.abc import Sequence

_WORD = re.compile(r"[^\W_]+")


def content_hash(text: str) -> str:
    """Hash that ignores case, punctuation and whitespace differences."""
    normalized = " ".join(_WORD.findall(text.lower()))
    return hashlib.sha256(normalized.encode()).hexdigest()


def shingles(text: str, size: int = 3) -> frozenset[str]:
    words = _WORD.findall(text.lower())
    if len(words) < size:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(" ".join(words[i : i + size]) for i in range(len(words) - size + 1))


def cluster_near_duplicates(texts: Sequence[str], threshold: float = 0.8) -> list[int]:
    """Return a cluster id per text; texts with Jaccard >= threshold share one.

    Clustering is transitive (union-find), so A~B and B~C puts all three
    together even if A and C alone fall under the threshold — which is the
    right call for leakage: C would still leak A's content via B.
    """
    sets = [shingles(t) for t in texts]
    parent = list(range(len(texts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Jaccard(a, b) <= len(small) / len(large), so once sorted by size the
    # inner loop can stop as soon as that ratio drops below the threshold.
    order = sorted(range(len(sets)), key=lambda i: len(sets[i]))
    for pos, i in enumerate(order):
        a = sets[i]
        if not a:
            continue
        for j in order[pos + 1 :]:
            b = sets[j]
            if len(a) < threshold * len(b):
                break
            if find(i) == find(j):
                continue
            intersection = len(a & b)
            if intersection / (len(a) + len(b) - intersection) >= threshold:
                parent[find(j)] = find(i)

    # Renumber roots densely (0, 1, 2, ...) in input order for readable output.
    ids: dict[int, int] = {}
    return [ids.setdefault(find(i), len(ids)) for i in range(len(texts))]
