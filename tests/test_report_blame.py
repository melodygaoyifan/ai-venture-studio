"""A failure the system caused must not be reported as the founder's fault.

From a real run: three tasks came back `spec_blocked` because our own EARS
lint rejected our own spec writer's output. The report told the founder

    三项因为需求描述不够清楚暂时没法开工
    (three could not start because the requirements were not clear enough)

which is false. Their description was never the thing being checked. A
founder who believes it will rewrite an FDR that was fine and change
nothing. The same report also said "five of nine" for a run that built six.
"""
from __future__ import annotations

import pytest

from ai_venture_studio.upstream.autopilot import (
    _OURS,
    _outcome_tally,
    _REPORT_SYSTEM,
    TaskOutcome,
)

OUTCOMES = [
    TaskOutcome(task_id="t2", title="商品浏览列表页", status="built",
                review_verdict="APPROVE"),
    TaskOutcome(task_id="t3", title="加购与模拟下单", status="built",
                review_verdict="REQUEST_CHANGES"),
    TaskOutcome(task_id="t1", title="商品抓取与缓存回退", status="spec_blocked",
                detail="4 EARS lint issue(s)"),
]


def test_the_count_is_arithmetic_not_prose():
    """The reporter said five of nine for six of nine."""
    tally = _outcome_tally(OUTCOMES)
    assert "**2 / 3**" in tally


def test_a_blocked_task_is_named_as_our_failure():
    tally = _outcome_tally(OUTCOMES)
    assert "商品抓取与缓存回退" in tally
    assert "not your requirements" in tally
    assert "这不是你的需求写得不好" in tally


def test_the_tally_distinguishes_clean_from_flagged_builds():
    """Three states, not two. `加购与模拟下单` is REQUEST_CHANGES — the reviewer
    refused to sign it off — and used to print the same "检查有意见 / review had
    notes" as an APPROVE_WITH_NOTES task the reviewer approved. The founder
    could not tell those two rows apart."""
    tally = _outcome_tally(OUTCOMES)
    assert "商品浏览列表页** — 已通过检查" in tally
    assert "加购与模拟下单** — 建好了，但检查要求改动" in tally
    with_notes = _outcome_tally([
        TaskOutcome(task_id="t4", title="结算页", status="built",
                    review_verdict="APPROVE_WITH_NOTES"),
    ])
    assert "结算页** — 建好了，检查有意见" in with_notes
    assert "要求改动" not in with_notes


@pytest.mark.parametrize("status", ["spec_blocked", "build_failed", "error"])
def test_every_failure_status_is_classified_as_ours(status):
    assert status in _OURS, (
        f"{status} would fall through and be shown as a bare status code"
    )


def test_an_unknown_status_degrades_to_the_status_itself():
    """Better a bare word than a wrong story about whose fault it is."""
    odd = [TaskOutcome(task_id="t9", title="x", status="something_new")]
    assert "something_new" in _outcome_tally(odd)


def test_the_reporter_is_told_not_to_blame_the_founder():
    prompt = _REPORT_SYSTEM
    assert "never the founder's" in prompt
    assert "NEVER write that their requirements were unclear" in prompt
    assert "Do not state counts" in prompt


def test_an_all_clean_run_says_so():
    clean = [TaskOutcome(task_id="t1", title="a", status="built",
                         review_verdict="APPROVE")]
    tally = _outcome_tally(clean)
    assert "**1 / 1**" in tally
    assert "❌" not in tally
