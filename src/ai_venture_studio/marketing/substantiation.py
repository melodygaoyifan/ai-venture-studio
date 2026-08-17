"""claim_substantiation_check (§21.58.1) — the register is the whitelist.

Every product-capability assertion in a draft must resolve to a
`claims_available` entry, and every quantitative assertion must match the
registered value within a stated tolerance. Unmapped assertions fail
closed — the register is the whitelist, not a hint.

`unmeasured_superlative` gets its own rule because "fastest" and "the only
tool that…" are comparative claims about third parties, requiring evidence
about products we do not control. The correct output is almost always to
delete the superlative.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ai_venture_studio.marketing.register import RegisteredClaim, ReleaseContract
from ai_venture_studio.lexicon import content
from ai_venture_studio.superlatives import compile_gate

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_QUANT = re.compile(
    r"(\d+(?:\.\d+)?\s*%|\$\s?\d|\b\d+(?:\.\d+)?\s*(?:x|×)\b|\b\d[\d,]*\b)"
)
# Shared with the platform gate (ADR-039), which had drifted from this list.
# `most \w+` stays marketing-only: founder copy has no reason to say "at most
# once", and the README does.
_SUPERLATIVE = compile_gate(("cheapest", r"most \w+"))
_CAPABILITY = re.compile(
    r"\b(exports?|imports?|supports?|handles?|processes|integrates?|delivers?|"
    r"generates?|builds?|reviews?|saves?|reduces?|increases?|automates?|scales?)\b",
    re.I,
)
_STOPWORDS = frozenset(
    "a an the in on at of to for with under over and or is are it its our your "
    "we you than from by as that this per".split()
)


class SubstantiationFinding(BaseModel):
    rule: str  # unsubstantiated | number_drift | unmeasured_superlative
    sentence: str
    message: str


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _content_words(text: str) -> set[str]:
    """Content words of a sentence, for the register match.

    Was `[a-z0-9']+`, which found nothing in a Chinese claim: the register
    match scored 0.0 overlap for every sentence, so a registered claim was
    never recognised as registered and the finding fired on substantiated
    copy. Tokenizer from `lexicon` (ADR-050); the stopwords and the
    digit-drop stay here — numbers are `_numbers`' job, and counting them
    twice would let a bare figure carry the overlap.
    """
    return {
        w
        for w in content(text, stopwords=_STOPWORDS)
        if not w.isdigit()
    }


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in _NUMBER.findall(text)]


def _asserts_something(sentence: str) -> bool:
    return bool(
        _QUANT.search(sentence)
        or _SUPERLATIVE.search(sentence)
        or _CAPABILITY.search(sentence)
    )


def _match_register(
    sentence: str, register: ReleaseContract
) -> RegisteredClaim | None:
    """Entity+predicate match, not fuzzy string: the registered claim's
    content words must substantially appear in the sentence."""
    words = _content_words(sentence)
    best, best_overlap = None, 0.0
    for claim in register.claims_available:
        claim_words = _content_words(claim.text)
        if not claim_words:
            continue
        overlap = len(claim_words & words) / len(claim_words)
        if overlap > best_overlap:
            best, best_overlap = claim, overlap
    return best if best_overlap >= 0.6 else None


def check_substantiation(
    draft_text: str, register: ReleaseContract, tol: float = 0.0
) -> list[SubstantiationFinding]:
    findings = []
    for sent in split_sentences(draft_text):
        if not _asserts_something(sent):
            continue
        hit = _match_register(sent, register)
        if hit is None:
            findings.append(
                SubstantiationFinding(
                    rule="unsubstantiated",
                    sentence=sent,
                    message="no claims_available entry supports this assertion",
                )
            )
            continue
        for n_draft, n_reg in zip(_numbers(sent), _numbers(hit.text)):
            if abs(n_draft - n_reg) > tol * max(abs(n_reg), 1e-9):
                findings.append(
                    SubstantiationFinding(
                        rule="number_drift",
                        sentence=sent,
                        message=f"draft says {n_draft:g}, register says {n_reg:g}",
                    )
                )
        if _SUPERLATIVE.search(sent) and hit.source_type != "primary_measured":
            findings.append(
                SubstantiationFinding(
                    rule="unmeasured_superlative",
                    sentence=sent,
                    message="comparative/superlative requires primary_measured "
                    "evidence — usually the fix is deleting the superlative",
                )
            )
    return findings
