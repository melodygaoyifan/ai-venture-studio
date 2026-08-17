"""The one tokenizer (ADR-050).

Seven places in this repo split text into tokens. Four of them learned,
independently and in different words, that Chinese has no spaces and an
ASCII-letter rule therefore finds *nothing* in it. Three did not, and one
of those three — `requirements.tokens` — made ADR-046's duplicate gate
**inert** in the language the templates, the Studio default and every
benchmark case are written in: never firing, never erroring, and about to
be measured as a working gate (ADR-048).

The lesson kept having to be re-learned because there was nowhere to put
it. This module is that place. Every tokenizer in the system imports from
here, and the failure mode — a rule that reads English and is blind to
中文 — now has exactly one file to hide in.

What stays per-caller is *policy*: stopword lists, length floors,
stemming. Those genuinely differ (an incident report is hunting code
symbols; a requirement is hunting content words), and forcing them
together would be a different mistake. What does not differ, and is
therefore not a knob, is that a token stream must see CJK.
"""

from __future__ import annotations

import functools
import unicodedata

#: Chinese function characters — the 的/了/在 layer. Dropped as unigrams,
#: and a bigram made entirely of them is dropped too, because it carries
#: no more meaning than the characters do. A bigram that mixes one with a
#: content character is kept: 的话 is a word.
CJK_FUNCTION = frozenset("的了是在和与及也都很就把被对从为以要能会有个之其所并且或者")

#: The Latin token: ASCII alphanumerics plus the two characters that sit
#: *inside* real tokens rather than between them — `_` in an identifier,
#: `'` in a contraction. Deliberately ASCII-only; a rule that accepted
#: every Unicode letter would swallow CJK back into the Latin branch and
#: undo the whole point.
_LATIN_EXTRA = "_'"


@functools.lru_cache(maxsize=4096)
def is_cjk(ch: str) -> bool:
    """Han characters, by Unicode name rather than by a hard-coded range.

    A range literal ages: it was `[一-鿿]` in one caller and a different
    span in another, and neither covered the extension blocks. The name
    lookup covers every CJK ideograph block the running Python knows
    about, including ones added after this line was written.
    """
    return "CJK" in unicodedata.name(ch, "")


def tokenize(text: str, *, unigrams: bool = True) -> list[str]:
    """Text order, lowercased: Latin words, and CJK as grams.

    CJK yields **bigrams** because that is what discriminates — a single
    character appears in too many unrelated words to rank anything, and
    a bigram that straddles a word boundary is symmetric noise, scoring
    equally against every candidate, rather than a missed match. This is
    not a segmenter on purpose: no new runtime dependency, no dictionary
    to go stale.

    `unigrams=True` adds the single characters as well, which trades
    precision for recall. Ranking that must tolerate paraphrase wants
    them (付 links 付款 and 付钱); retrieval that must *discriminate* —
    the ADR-046 gate, MVP scope matching — does not, and passes False.
    """
    out: list[str] = []
    latin: list[str] = []
    cjk: list[str] = []

    def flush_latin() -> None:
        if latin:
            out.append("".join(latin))
            latin.clear()

    def flush_cjk() -> None:
        if not cjk:
            return
        if unigrams:
            out.extend(c for c in cjk if c not in CJK_FUNCTION)
        elif len(cjk) == 1 and cjk[0] not in CJK_FUNCTION:
            # A one-character run has no bigram to make, and dropping it
            # would lose the only token that run can produce.
            out.append(cjk[0])
        for i in range(len(cjk) - 1):
            pair = cjk[i : i + 2]
            if not all(c in CJK_FUNCTION for c in pair):
                out.append("".join(pair))
        cjk.clear()

    for ch in text.lower():
        if is_cjk(ch):
            flush_latin()
            cjk.append(ch)
        elif ch.isascii() and (ch.isalnum() or ch in _LATIN_EXTRA):
            flush_cjk()
            latin.append(ch)
        else:
            flush_latin()
            flush_cjk()
    flush_latin()
    flush_cjk()
    return out


def content(
    text: str,
    *,
    stopwords: frozenset[str] | set[str] = frozenset(),
    min_latin: int = 1,
    unigrams: bool = False,
) -> set[str]:
    """`tokenize` as a set, with the two policies every caller hand-rolled.

    `min_latin` applies to **Latin tokens only**. A length floor is a
    proxy for "this word carries meaning", and four characters of English
    is a word while four characters of Chinese is two. Applied to CJK
    grams the floor would drop every one of them and restore precisely
    the bug this module exists to end.
    """
    return {
        t
        for t in tokenize(text, unigrams=unigrams)
        if t not in stopwords and (not t.isascii() or len(t) >= min_latin)
    }


def content_length(text: str) -> int:
    """How much text is here, for callers measuring volume rather than
    matching — a thin-content check, a word budget.

    Latin words plus CJK *characters*, never the gram count: grams roughly
    double the character count, so counting them would report a Chinese
    page as twice its length while an ASCII-only rule reported it as
    empty. Both are wrong; this is the one that is neither.
    """
    latin = sum(1 for t in tokenize(text, unigrams=False) if t.isascii())
    return latin + sum(1 for ch in text if is_cjk(ch))
