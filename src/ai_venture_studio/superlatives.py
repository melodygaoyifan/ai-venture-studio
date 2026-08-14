"""One comparative vocabulary, shared by both claim gates (ADR-039).

Two gates refuse unmeasured superlatives: `product.platform_claims` reads
README.md and the published benchmark page, `marketing.substantiation` reads
founder-facing copy. They had two hand-maintained word lists, and the lists
had already drifted — `slowest` and "the only tool that…" were caught in
marketing copy and waved through in the README, for no reason anyone had
recorded. Same concept, two definitions, nothing pinning them: ADR-037's
shape on a third concept.

`#1` was in BOTH lists and could never match in EITHER. It was written
`\\b#1\\b`, and `\\b` requires a word/non-word transition — a space and a `#`
are both non-word, so the boundary never held. Two gates, one dead
alternative, for as long as it had been there. It is matched here by a
boundary that works, and pinned by a test.

Three carve-outs stay, all narrow and all deliberate. The test is whether the
phrase ranks us against products we do not control, or names an ordering over
our own data:

- `cheapest test` is the framework's own term of art (§20.54.3) — a
  design instruction about which check to run first, not a claim about a
  competitor. Exempt in the platform gate only, where the docs use it.
- `most <anything>` is marketing-only. The README says "at most once", and
  a gate that reads that as a superlative teaches its reader to route
  around it. The platform gate takes the specific comparatives instead.
- `worst case` / `worst finding` / `worst severity` name a position in our
  own severity ordering. Shared, because the term of art is the same on
  both sides.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: Ranking words that assert something about products we do not control, and
#: so can never be substantiated from our own measurements. Shared verbatim by
#: both gates — extend HERE, not in a caller.
SHARED_SUPERLATIVES: tuple[str, ...] = (
    "fastest",
    "slowest",
    "best",
    # "worst case" / "worst finding" / "worst severity" is severity ordering
    # over our OWN data — the third carve-out, and shared rather than
    # per-gate, because the term of art is the same in both places.
    r"worst(?!\s+(?:case|finding|severity))",
    "number one",
    "leading",
    "unmatched",
    "unrivalled",
    "unrivaled",
    "state.of.the.art",
    "SOTA",
    "only (?:tool|platform|product|system)",
)

#: `#1` with a boundary that actually holds: not preceded by a word character
#: or another `#`, not followed by a digit (so "#10" is a link, not a claim).
HASH_ONE = r"(?<![\w#])#1(?!\d)"


def compile_gate(extra: Sequence[str] = ()) -> re.Pattern[str]:
    """Word-bounded alternation over the shared vocabulary plus `extra`.

    `extra` carries a gate's documented carve-outs — a caller adding a plain
    ranking word to it is putting the drift back.
    """
    body = "|".join([*SHARED_SUPERLATIVES, *extra])
    return re.compile(rf"\b(?:{body})\b|{HASH_ONE}", re.I)
