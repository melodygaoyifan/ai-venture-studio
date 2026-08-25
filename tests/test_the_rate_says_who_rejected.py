"""ADR-078: the clean rate carries its own attribution.

Run 19's headline read "clean 0%", and learning that every unclean row was
machine-caused — Gate 2 blocks from a host-shadowed suite, voters that
never returned a verdict — took a debugging session of reading prose
details (ADR-075/076). The attribution was knowable at scoring time.
`review_and_repair` now returns the rejection as countable tags beside the
words, the outcome row keeps them, and `summarise` tallies them into
`unclean_causes` so the scoreboard prints who the rate is charging:
the machine (gate2, voters_no_verdict, no_review) or the reviewer's
verdict on the code (findings:*).
"""

from types import SimpleNamespace

from ai_venture_studio.product_bench import CaseResult, summarise
from ai_venture_studio.state import Severity, Verdict
from ai_venture_studio.upstream import autopilot


def _finding(severity: Severity, voter: str = "security") -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, title="a finding", voter=voter,
        file_path="app/x.py", also_in=[],
    )


def test_rejection_causes_name_every_trigger():
    review = SimpleNamespace(
        verdict=Verdict.REQUEST_CHANGES, summary="",
        findings=[_finding(Severity.CRITICAL), _finding(Severity.MEDIUM)],
        blocked_voters=[],
    )
    assert autopilot._rejection_causes(review, gate2_blocked=True) == [
        "gate2", "findings:critical", "findings:medium",
    ]
    # A review that never ran is its own cause, not silence.
    assert autopilot._rejection_causes(None, gate2_blocked=False) == ["no_review"]
    # A rejection this function cannot explain still counts as itself.
    empty = SimpleNamespace(
        verdict=Verdict.REQUEST_CHANGES, summary="", findings=[],
        blocked_voters=["security"],  # one blocked voter cannot reject alone
    )
    assert autopilot._rejection_causes(empty, gate2_blocked=False) == ["other"]


def test_review_and_repair_returns_the_causes(tmp_path, monkeypatch):
    # The run-16 shape: every finding LOW, two voters never answered — the
    # voters are what rejected the task, and the tags must say so.
    review = SimpleNamespace(
        verdict=Verdict.REQUEST_CHANGES, summary="",
        findings=[_finding(Severity.LOW)],
        blocked_voters=["security", "correctness"],
    )
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)

    verdict, detail, approvals, by_voter, causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="mock", label="t1",
    )
    assert verdict == "REQUEST_CHANGES"
    assert causes == ["voters_no_verdict"]
    assert by_voter == {}  # the LOW finding is not blocking


def test_summarise_tallies_unclean_causes():
    case = CaseResult(
        name="01-case",
        autopilot_status="completed",
        tasks_total=4,
        tasks_built=4,
        clean_reviews=1,
        unreviewed=["t4"],
        outcomes=[
            {"task_id": "t1", "title": "a", "status": "built",
             "review": "REQUEST_CHANGES", "detail": "",
             "rejection_causes": ["gate2"]},
            # A row from before the tags existed: visible as "unrecorded",
            # never silently dropped — the tally must sum to the unclean rows.
            {"task_id": "t2", "title": "b", "status": "built",
             "review": "REQUEST_CHANGES", "detail": ""},
            {"task_id": "t3", "title": "c", "status": "built",
             "review": "APPROVE", "detail": ""},
            # Unjudged is not unclean (ADR-074): out of the tally entirely.
            {"task_id": "t4", "title": "d", "status": "built",
             "review": None, "detail": "",
             "rejection_causes": ["no_review"]},
        ],
    )
    summary = summarise([case])
    assert summary.unclean_causes == {"gate2": 1, "unrecorded": 1}
