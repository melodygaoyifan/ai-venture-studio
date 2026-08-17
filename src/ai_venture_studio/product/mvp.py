"""The MVP contract — what makes a first slice minimum AND viable.

The system already had scope-reduction machinery scattered across four
surfaces (P0's named cheapest test, Gate PL1's `test_first`, the PRD's
`non_goals >= 2`, the brief's scope_now/scope_later, the FDR's "NOT needed
for now", and the `thin` tier). What it had nowhere was the question that
makes an MVP an MVP rather than just a small build:

    does this slice, on its own, tell us whether the thing is worth building?

Doc 13 §29 already specified exactly that rule — "every MVP-tier hypothesis
must be validatable by the MVP-tier increments alone" — and it was never
implemented. `mvp_lint` implements it, plus the three checks the canon
requires and the code had let default to empty.

`thin` IS the MVP tier. This module deliberately adds no fourth scope value
and no parallel {mvp, v1, later} axis: the canon already drifted into two
scope vocabularies and a third would be worse than the gap.

## The AI delta

When the slice contains an AI-shaped capability, "minimum viable" changes
shape, because an AI demo is cheap and an AI product is not. MIT's 2025 GenAI
survey put 95% of enterprise pilots at zero measurable P&L impact, and
Gartner expects >40% of agentic projects cancelled by end-2027 — mostly on
cost, unclear value, and missing controls. Thoughtworks' framing is the one
worth encoding: a weekend demo is ~10% complete, not 90%.

So `ai_mvp_lint` refuses the five things practitioners converge on as
non-skippable, none of which are about model quality:

1. a simpler alternative was considered and named (`why_not_deterministic`) —
   the strongest published advice from Anthropic and Google PAIR alike is
   that the right answer is often a form, a lookup, or a rules engine;
2. the cost of being wrong is declared, and anything worse than recoverable
   cannot ship autonomous at MVP;
3. a fallback exists for "the model is wrong" — abstain, degrade, escalate.
   Air Canada shipped a chatbot without one and was held liable for what it
   said (Moffatt v. Air Canada, 2024);
4. an eval set is written BEFORE the implementation, so it cannot encode the
   bug it was meant to catch;
5. every volume metric is paired with a quality metric. Klarna's deflection
   numbers looked excellent for months before they concluded quality had
   dropped and rehired humans.

These are checks, not advice: an AI slice missing any of them is a demo, and
this module will say so rather than let it be called an MVP.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ai_venture_studio.lexicon import content

# The MVP tier is `thin` (product/prd.py SCOPE_TIERS). Named here so callers
# read intent rather than a magic string.
MVP_TIER = "thin"

COST_OF_BEING_WRONG = ("trivial", "recoverable", "expensive", "irreversible")
# Above `recoverable`, an MVP may not act on its own. Suggest-and-approve is
# the ceiling until evidence earns more.
_AUTONOMY_SAFE_AT_MVP = ("shadow", "suggest")
AUTONOMY_RUNGS = ("shadow", "suggest", "act_with_approval", "autonomous")

# Minimum hand-written eval cases before an AI slice counts as measured.
# Practitioner consensus starts at 20; below that a pass tells you nothing.
MIN_EVAL_CASES = 20

# "Build an MVP" is not a test — the same term of art P0's Falsifiability
# voter already polices, enforced deterministically here.
_BUILD_SHAPED = re.compile(
    r"\b(build|ship|implement|develop|code)\b.{0,20}\b(mvp|it|product|app|"
    r"feature|thing)\b|\bjust build\b|\bbuild the whole\b",
    re.I,
)
# A success signal has to be countable. "Users love it" is not a signal.
_MEASURABLE = re.compile(
    r"\d|\b(rate|count|percent|per cent|number of|how many|minutes|days|"
    r"weeks|per week|per day|stops?|starts?)\b",
    re.I,
)
# AI-shaped capability, detected from the founder's own words. Deliberately
# broad on recall and cheap: a false positive costs the author four fields,
# a false negative ships an unguarded AI feature.
_AI_SHAPED = re.compile(
    # Latin terms need word boundaries so "again" does not match "ai"...
    r"\b(ai|a\.i\.|llm|gpt|chatbot|chat bot|copilot|agent|assistant|"
    r"generative|summar(?:y|ise|ize|ising|izing)|recommend(?:ation)?s?|"
    r"predict(?:ion|ive)?|classif(?:y|ier|ication)|sentiment|embedding|"
    r"semantic search|natural language|auto-?(?:reply|respond|draft|write|"
    r"tag|categori[sz]e))\b"
    # ...and CJK must NOT, because there are no boundaries between Han
    # characters: `\b智能\b` never matches inside Chinese text, which silently
    # exempted every Chinese FDR from the AI contract.
    r"|(智能|人工智能|大模型|自动生成|自动回复|推荐|摘要|语义)",
    re.I,
)


class MVPFinding(BaseModel):
    rule: str
    message: str
    blocking: bool = True


class MVPSlice(BaseModel):
    """One MVP slice: the smallest thing that answers a real question."""

    hypothesis: str = ""          # what we believe and are testing
    increments: list[str] = Field(default_factory=list)  # user-visible
    success_signal: str = ""      # how we would know, countably
    not_now: list[str] = Field(default_factory=list)     # deliberately out
    cheapest_test: str = ""       # the cheaper thing we rejected, and why not


class AIFeature(BaseModel):
    """The extra contract an AI-shaped capability carries at MVP."""

    capability: str = ""
    why_not_deterministic: str = ""
    cost_of_being_wrong: str = ""
    fallback_behavior: str = ""
    autonomy_rung: str = "suggest"
    eval_cases: int = 0
    quality_metric: str = ""
    volume_metric: str = ""


def detect_ai_feature(*texts: str) -> str:
    """The AI-shaped phrase that triggers the AI delta, or "".

    Detection is lexical and runs over the founder's own words, so it is
    explainable: the finding quotes the phrase that fired it, and a founder
    who disagrees can say so rather than wonder why extra fields appeared.
    """
    for text in texts:
        match = _AI_SHAPED.search(text or "")
        if match:
            return match.group(0)
    return ""


def mvp_lint(slice_: MVPSlice, *, scope_tier: str = MVP_TIER) -> list[MVPFinding]:
    """Is this slice minimum AND viable? Findings block the gate.

    Only applied at the MVP tier: a `standard` or `deep` plan is not claiming
    to be a first slice, so holding it to first-slice rules would be noise.
    """
    if scope_tier != MVP_TIER:
        return []

    findings: list[MVPFinding] = []

    visible = [i for i in slice_.increments if i.strip()]
    if not visible:
        findings.append(MVPFinding(
            rule="no_user_visible_increment",
            message="the slice has no user-visible increment — nobody can react "
            "to it, so it cannot validate anything (doc 13 §29 ScopeDiscipline)",
        ))

    if not slice_.hypothesis.strip():
        findings.append(MVPFinding(
            rule="no_hypothesis",
            message="the slice states no hypothesis — without one, 'minimum' has "
            "no yardstick and the build is just a small build",
        ))
    elif visible:
        # Doc 13 §29's rule, implemented: the increments must be able to
        # settle the hypothesis by themselves. Lexically: the hypothesis's
        # content words should appear in what is actually being built.
        if not _hypothesis_covered(slice_.hypothesis, visible):
            findings.append(MVPFinding(
                rule="hypothesis_not_validatable_by_slice",
                message=f"nothing in the slice exercises the hypothesis "
                f"({slice_.hypothesis[:80]!r}) — either build the increment "
                "that tests it, or test the hypothesis a cheaper way first",
            ))

    if not slice_.success_signal.strip():
        findings.append(MVPFinding(
            rule="no_success_signal",
            message="no success signal — an MVP you cannot read the result of "
            "is a demo with a deadline",
        ))
    elif not _MEASURABLE.search(slice_.success_signal):
        findings.append(MVPFinding(
            rule="unmeasurable_success_signal",
            message=f"success signal {slice_.success_signal[:60]!r} has nothing "
            "countable in it — a number and a direction, not vibes",
        ))

    if not [n for n in slice_.not_now if n.strip()]:
        # Canon requires a non-empty out-of-scope list (doc 13 §29: "an empty
        # out_of_scope is a smell"); the schema had let it default to [].
        findings.append(MVPFinding(
            rule="nothing_deferred",
            message="nothing is listed as not-now — a slice that defers nothing "
            "is not a slice; name what you are deliberately not building",
        ))

    if slice_.cheapest_test.strip() and _BUILD_SHAPED.search(slice_.cheapest_test):
        findings.append(MVPFinding(
            rule="cheapest_test_is_the_build",
            message=f"the named cheapest test is the build itself "
            f"({slice_.cheapest_test[:60]!r}) — 'build it and see' is not a "
            "test; a mockup, a manual run, or a conversation usually is",
        ))
    return findings


_MVP_STOPWORDS = frozenset("""
the a an will would can do does is are to of and or in on for with that this
if then they we our their it its be by at as more less than when who what
without users user people want need needs use using make get see show tell
know whether cannot not have has had into from about there their been being
""".split())
# Stem length for the morphology this has to survive: a hypothesis says
# "progressing" while the increment says "progress", and "build" vs
# "building". Comparing whole words made those disagree and the check fired
# on a slice that was in fact well matched. Four characters is short enough
# to unify inflections and long enough that a collision means the words are
# genuinely related; a collision only ever makes this check quieter, which is
# the safe direction for a smell detector.
_STEM = 4


def _stems(text: str) -> set[str]:
    """Latin stems plus CJK bigrams — a Chinese FDR has no [a-z] words at
    all, and word-level matching would silently pass everything.

    This module was one of the four that learned the CJK half on its own,
    with its own `[一-鿿]` range that missed the extension blocks. The
    tokenizer is now `lexicon` (ADR-050); the stemming and `_MVP_STOPWORDS`
    stay here because they are this check's policy. Truncation is applied
    to Latin tokens only — four characters of Chinese is two words, and a
    bigram is already at its floor.
    """
    return {
        t[:_STEM] if t.isascii() else t
        for t in content(text or "", stopwords=_MVP_STOPWORDS, min_latin=3)
    }


def _hypothesis_covered(hypothesis: str, increments: list[str]) -> bool:
    """Do the increments plausibly exercise the hypothesis?

    Deliberately lexical and generous — a smell detector for the case doc 13
    names (a hypothesis about one thing, increments about another), not a
    semantic judge. The voter rosters do the reading; this catches the obvious
    disconnect for free and never blocks on a close call.
    """
    wanted = _stems(hypothesis)
    if not wanted:
        return True  # nothing to disagree with
    built: set[str] = set()
    for increment in increments:
        built |= _stems(increment)
    return bool(wanted & built)


def ai_mvp_lint(
    feature: AIFeature, *, scope_tier: str = MVP_TIER
) -> list[MVPFinding]:
    """The five things an AI slice may not skip. Findings block the gate.

    Unlike `mvp_lint` these apply at every tier: the reasons an AI feature
    needs a fallback and a paired quality metric do not weaken because the
    scope got wider.
    """
    findings: list[MVPFinding] = []

    if not feature.why_not_deterministic.strip():
        findings.append(MVPFinding(
            rule="no_simpler_alternative_considered",
            message="name the simpler thing this beat — a form, a lookup table, "
            "a saved filter, a rules engine — and why it was not enough. If the "
            "answer is 'a form would do', the honest MVP is the form",
        ))

    cost = feature.cost_of_being_wrong.strip().lower()
    if cost not in COST_OF_BEING_WRONG:
        findings.append(MVPFinding(
            rule="cost_of_being_wrong_undeclared",
            message="declare the cost of being wrong "
            f"({' | '.join(COST_OF_BEING_WRONG)}) — it decides how much "
            "autonomy this may have, so it cannot be left blank",
        ))
    elif cost == "irreversible":
        findings.append(MVPFinding(
            rule="irreversible_at_mvp",
            message="the cost of being wrong is irreversible — at MVP this must "
            "not act at all. Ship the human-operated version, keep the log, and "
            "earn autonomy from it",
        ))
    elif cost in ("expensive", "irreversible") and \
            feature.autonomy_rung not in _AUTONOMY_SAFE_AT_MVP:
        findings.append(MVPFinding(
            rule="autonomy_exceeds_cost_of_being_wrong",
            message=f"autonomy_rung {feature.autonomy_rung!r} with "
            f"cost_of_being_wrong {cost!r} — an expensive mistake may be "
            "suggested, never taken; drop to suggest until evidence earns more",
        ))

    if feature.autonomy_rung not in AUTONOMY_RUNGS:
        findings.append(MVPFinding(
            rule="unknown_autonomy_rung",
            message=f"autonomy_rung must be one of {AUTONOMY_RUNGS}",
        ))

    if not feature.fallback_behavior.strip():
        findings.append(MVPFinding(
            rule="no_fallback_behavior",
            message="say what happens when the model is wrong or unavailable: "
            "abstain, degrade to something deterministic, or escalate to a "
            "human. A feature with no defined wrong-answer path is the liability "
            "Air Canada shipped, not an MVP",
        ))

    if feature.eval_cases < MIN_EVAL_CASES:
        findings.append(MVPFinding(
            rule="eval_set_too_small",
            message=f"{feature.eval_cases} eval case(s); at least "
            f"{MIN_EVAL_CASES} hand-written cases, authored before the "
            "implementation, or a passing run tells you nothing",
        ))

    if feature.volume_metric.strip() and not feature.quality_metric.strip():
        findings.append(MVPFinding(
            rule="volume_metric_without_quality_metric",
            message=f"volume metric {feature.volume_metric[:40]!r} has no paired "
            "quality metric — deflection without confirmed resolution, or "
            "throughput without an edit rate, looks like success for months "
            "while quality drops",
        ))
    return findings


def gate_mvp_entry(
    slice_: MVPSlice,
    feature: AIFeature | None = None,
    *,
    scope_tier: str = MVP_TIER,
) -> dict:
    """The deterministic entry condition for building an MVP slice.

    Returns the gate surface: passed plus the findings, so the caller records
    why rather than just that.
    """
    findings = mvp_lint(slice_, scope_tier=scope_tier)
    if feature is not None:
        findings += ai_mvp_lint(feature, scope_tier=scope_tier)
    blocking = [f for f in findings if f.blocking]
    return {
        "passed": not blocking,
        "scope_tier": scope_tier,
        "ai_feature": bool(feature),
        "findings": [f.model_dump() for f in findings],
    }
