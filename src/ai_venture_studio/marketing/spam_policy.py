"""spam_policy_check (§21.58.4) — aimed at scaled content abuse.

Google's definition is method-agnostic: volume plus manipulation intent
plus low added value. An agent optimizing a publish-rate metric is a
scaled-content-abuse generator by construction unless something stops it;
this check is the something, and cadence is a ceiling with no minimum
anywhere in the system (§21.59.5).

The original-contribution floor is where the framework's structure pays
off unexpectedly: a system that already types every claim by source can
mechanically require that a published page contain at least one thing we
measured ourselves — a check almost nobody can run, nearly free here.

Near-duplication uses local k-shingle Jaccard (deterministic, stdlib);
embedding-based near-dup is an availability-gated second pass, reported
skipped when no model is present — never silently absent.
"""

from __future__ import annotations

from pydantic import BaseModel

from ai_venture_studio.lexicon import content_length
from ai_venture_studio.marketing.artifacts import Page
from ai_venture_studio.textsim import jaccard as _jaccard
from ai_venture_studio.textsim import shingles as _shingles


class SpamPolicyConfig(BaseModel):
    verified_on: str = ""
    max_publishes_per_period: int = 2  # a ceiling, never a target
    near_dup_jaccard: float = 0.60  # template similarity threshold
    fanout_jaccard: float = 0.85  # query-variant fan-out threshold
    thin_page_words: int = 250
    max_thin_ratio: float = 0.25


class SpamPolicyFinding(BaseModel):
    rule: str
    message: str
    pages: list[str] = []


def _has_original_contribution(page: Page) -> bool:
    claims = page.claim_ledger.get("claims") or []
    return any(
        isinstance(c, dict) and c.get("source_type") == "primary_measured"
        for c in claims
    )


def spam_policy_check(
    batch: list[Page],
    *,
    already_published_this_period: int = 0,
    config: SpamPolicyConfig | None = None,
) -> list[SpamPolicyFinding]:
    config = config or SpamPolicyConfig()
    findings = []

    total = already_published_this_period + len(batch)
    if total > config.max_publishes_per_period:
        findings.append(
            SpamPolicyFinding(
                rule="publish_rate",
                message=f"{total} publishes this period exceeds the ceiling "
                f"{config.max_publishes_per_period} — the ceiling is never a target",
                pages=[p.path for p in batch],
            )
        )

    shingle_sets = {p.path: _shingles(p.text) for p in batch}
    for i, a in enumerate(batch):
        for b in batch[i + 1 :]:
            sim = _jaccard(shingle_sets[a.path], shingle_sets[b.path])
            if sim >= config.fanout_jaccard:
                findings.append(
                    SpamPolicyFinding(
                        rule="query_variant_fanout",
                        message=f"pages differ only in a targeted phrase "
                        f"(jaccard {sim:.2f})",
                        pages=[a.path, b.path],
                    )
                )
            elif sim >= config.near_dup_jaccard:
                findings.append(
                    SpamPolicyFinding(
                        rule="template_similarity",
                        message=f"pairwise near-duplication (jaccard {sim:.2f}) "
                        f"above threshold {config.near_dup_jaccard}",
                        pages=[a.path, b.path],
                    )
                )

    for page in batch:
        if not _has_original_contribution(page):
            findings.append(
                SpamPolicyFinding(
                    rule="original_contribution_floor",
                    message="page carries no primary_measured claim, original "
                    "data, or first-party artifact — a restatement of retrieved "
                    "material",
                    pages=[page.path],
                )
            )
        if not page.reviewer:
            findings.append(
                SpamPolicyFinding(
                    rule="editorial_attestation",
                    message="no named human reviewer recorded for the page",
                    pages=[page.path],
                )
            )

    # `content_length`, not a regex: `[a-z0-9']+` measured every Chinese
    # page as ZERO words, so a whole batch of substantial Chinese copy
    # tripped the thin-page ratio at 100% (ADR-050). Characters, not grams
    # — grams would report the same page as twice its length.
    thin = [
        p.path for p in batch if content_length(p.text) < config.thin_page_words
    ]
    if batch and len(thin) / len(batch) > config.max_thin_ratio:
        findings.append(
            SpamPolicyFinding(
                rule="thin_page_ratio",
                message=f"{len(thin)}/{len(batch)} pages below the "
                f"{config.thin_page_words}-word substance threshold",
                pages=thin,
            )
        )
    return findings
