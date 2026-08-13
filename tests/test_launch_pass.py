"""P20 — the platform's own launch artifacts, kept green by the suite.

The launch PRD, post, and pre-registered experiment are committed
artifacts; this test re-validates them on every run, so a drive-by edit
that breaks the launch's own discipline fails CI exactly like any other
regression (doc 25 §76.4).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from ai_venture_studio.evidence import load_metric_vocabulary
from ai_venture_studio.experiment import fdr_plan_check, load_design, verify_at_analysis
from ai_venture_studio.marketing import (
    BrandConfig,
    ComplianceProfile,
    Draft,
    Page,
    RegisteredClaim,
    ReleaseContract,
    brand_and_safety_scan,
    check_substantiation,
    disclosure_lint,
    geo_extractability_check,
    spam_policy_check,
)
from ai_venture_studio.product import PRD, lint_ledger, prd_lint

REPO = Path(__file__).parent.parent
TODAY = dt.date(2026, 7, 26)


def _platform_ledger() -> dict:
    return yaml.safe_load((REPO / "claims" / "platform.yaml").read_text())


def _register() -> ReleaseContract:
    return ReleaseContract(
        prd_ref="PRD-LAUNCH-2026-01",
        instrumentation_verified=True,
        claims_available=[
            RegisteredClaim(**{k: v for k, v in c.items()
                               if k in ("id", "text", "source_type", "evidence", "n")})
            for c in _platform_ledger()["claims"]
        ],
    )


def test_launch_prd_stays_clean():
    prd = PRD(**yaml.safe_load((REPO / "launch" / "prd.yaml").read_text())["prd"])
    issues, tasks = prd_lint(
        prd,
        (REPO / "launch" / "prd.md").read_text(),
        vocabulary=load_metric_vocabulary(REPO / "metrics"),
        ledger_claim_ids={c["id"] for c in _platform_ledger()["claims"]},
    )
    assert issues == [], [i.model_dump() for i in issues]
    # Nothing is left to instrument: the one outcome whose event did not yet
    # exist was withdrawn in v0.81.0 (ADR-033).
    assert tasks == []
    assert "2 consecutive weekly runs" in prd.kill_criteria[0]  # verbatim


def test_launch_post_survives_the_backstops():
    text = (REPO / "launch" / "post.md").read_text()

    unsubstantiated = check_substantiation(text, _register(), tol=0.02)
    assert unsubstantiated == [], [f.model_dump() for f in unsubstantiated]

    draft = Draft(id="launch-post", channel="content_geo", text=text,
                  ai_generated=True, advertising=True)
    disclosure = disclosure_lint(draft, ComplianceProfile())
    assert disclosure == [], [f.model_dump() for f in disclosure]
    brand = brand_and_safety_scan(draft, ComplianceProfile(), BrandConfig())
    assert brand == [], [f.model_dump() for f in brand]

    page = Page(
        path="/launch/post", title="One document in, an honest product decision out",
        text=text, author_name="Melody Gao",
        author_identity_url="https://github.com/melodygaoyifan",
        canonical_url="https://github.com/melodygaoyifan/ai-venture-studio/blob/main/launch/post.md",
        published_at="2026-07-26", reviewer="melody",
        claim_ledger=_platform_ledger(),
    )
    geo = geo_extractability_check(page)
    assert geo == [], [f.model_dump() for f in geo]
    spam = spam_policy_check([page])
    assert spam == [], [f.model_dump() for f in spam]


def test_show_hn_draft_survives_the_backstops():
    """The Show HN draft is a committed artifact under the same gates as
    the launch post — an edit that overclaims fails CI before it can be
    posted. Posting itself stays human (ADR-U19)."""
    text = (REPO / "launch" / "show-hn.md").read_text()

    unsubstantiated = check_substantiation(text, _register(), tol=0.02)
    assert unsubstantiated == [], [f.model_dump() for f in unsubstantiated]

    draft = Draft(id="show-hn", channel="content_geo", text=text,
                  ai_generated=True, advertising=True)
    disclosure = disclosure_lint(draft, ComplianceProfile())
    assert disclosure == [], [f.model_dump() for f in disclosure]
    brand = brand_and_safety_scan(draft, ComplianceProfile(), BrandConfig())
    assert brand == [], [f.model_dump() for f in brand]

    page = Page(
        path="/launch/show-hn",
        title="Show HN: An AI venture studio whose README cannot overclaim",
        text=text, author_name="Melody Gao",
        author_identity_url="https://github.com/melodygaoyifan",
        canonical_url="https://github.com/melodygaoyifan/ai-venture-studio/blob/main/launch/show-hn.md",
        published_at="2026-07-28", reviewer="melody",
        claim_ledger=_platform_ledger(),
    )
    geo = geo_extractability_check(page)
    assert geo == [], [f.model_dump() for f in geo]
    spam = spam_policy_check([page])
    assert spam == [], [f.model_dump() for f in spam]


def test_show_hn_first_comment_survives_the_backstops():
    """The HN submission is title + repo URL, so the description ships as
    the author's first comment. It is the same kind of outbound artifact as
    the post and is gated identically — this is what stops an edit made in
    launch-day haste from asserting past the ledger."""
    text = (REPO / "launch" / "show-hn-comment.txt").read_text()

    unsubstantiated = check_substantiation(text, _register(), tol=0.02)
    assert unsubstantiated == [], [f.model_dump() for f in unsubstantiated]

    draft = Draft(id="show-hn-comment", channel="content_geo", text=text,
                  ai_generated=True, advertising=True)
    assert disclosure_lint(draft, ComplianceProfile()) == []
    assert brand_and_safety_scan(draft, ComplianceProfile(), BrandConfig()) == []

    page = Page(
        path="/launch/show-hn-comment",
        title="Show HN: An AI venture studio whose README cannot overclaim",
        text=text, author_name="Melody Gao",
        author_identity_url="https://github.com/melodygaoyifan",
        canonical_url="https://github.com/melodygaoyifan/ai-venture-studio",
        published_at="2026-07-28", reviewer="melody",
        claim_ledger=_platform_ledger(),
    )
    assert geo_extractability_check(page) == []
    assert spam_policy_check([page]) == []


def test_launch_experiment_pin_holds_and_ledger_lints():
    text = (REPO / "launch" / "experiment.yaml").read_text()
    design = load_design(text)
    verify_at_analysis(text, design.preregistration_hash)  # post-hoc edits void this
    assert fdr_plan_check(design) == []
    assert lint_ledger(_platform_ledger(), "launch", today=TODAY) == []
