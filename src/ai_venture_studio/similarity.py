"""Dependency-free lexical similarity — the embedding-free matcher.

Skill/block matching wanted embeddings, but the system must work with
zero extra providers configured (the founder has ONE key, maybe none for
embeddings). TF-IDF cosine over unicode word tokens plus CJK unigrams and
bigrams covers the actual need: paraphrase-tolerant ranking of a query
against a small catalog, in either 中文 or English. If a real embedding
provider lands later it slots behind `rank()` without touching callers.
"""

from __future__ import annotations

import math
from collections import Counter

from ai_venture_studio.lexicon import tokenize

# This module's tokenizer was the one that got CJK right first, and it
# stayed here where nothing else could import it while three other call
# sites shipped blind to Chinese. It now lives in `lexicon` and this is a
# re-export so `from ai_venture_studio.similarity import tokenize` keeps
# working; ranking wants `unigrams=True`, which is the default, because
# it must tolerate paraphrase rather than discriminate (ADR-050).
__all__ = ["tokenize", "rank"]


def rank(query: str, docs: list[str]) -> list[tuple[int, float]]:
    """Cosine-ranked (index, score) pairs, best first, zero-score dropped."""
    doc_tokens = [Counter(tokenize(d)) for d in docs]
    q_tokens = Counter(tokenize(query))
    n = len(docs)
    df = Counter()
    for tokens in doc_tokens:
        df.update(set(tokens))

    def idf(tok: str) -> float:
        return math.log((1 + n) / (1 + df[tok])) + 1.0

    def vec(tokens: Counter) -> dict[str, float]:
        return {t: c * idf(t) for t, c in tokens.items()}

    qv = vec(q_tokens)
    qnorm = math.sqrt(sum(w * w for w in qv.values())) or 1.0
    scored = []
    for i, tokens in enumerate(doc_tokens):
        dv = vec(tokens)
        dnorm = math.sqrt(sum(w * w for w in dv.values())) or 1.0
        dot = sum(w * dv.get(t, 0.0) for t, w in qv.items())
        score = dot / (qnorm * dnorm)
        if score > 0:
            scored.append((i, round(score, 4)))
    return sorted(scored, key=lambda p: -p[1])
