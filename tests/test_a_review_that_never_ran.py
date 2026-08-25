"""A task the reviewer never saw is not a task the reviewer rejected.

Run 18's `04-direction-workbench t4` built, passed 26 tests, and was recorded
`review: null` with an EMPTY detail — the only row in the whole run that gave
no reason at all. It then scored against `clean_review_rate` exactly as if a
voter had objected to it.

Nothing had objected. The DoR gate refused the diff at 2361 lines against a
2000-line ceiling, so the graph stopped at step 2 and no voter ever ran. And
1697 of those 2361 lines — 72% — were the autopilot's own generated paperwork
(`product/*.yaml`, `specs/*/spec.md`, FDR.md); the code under review was 664
lines. The reason was written twice, into `state["dor_reasons"]` and onto disk
as `02-dor_fail.yaml`, and `_review_head`'s `review, _ = run_review(...)`
discarded it both times.

Two defects, one row:

* the row could not say why (ADR-042 — the evidence was one frame away), and
* the case's own instrument failure was charged to the product, which is the
  failure ADR-061 fixed for probes and left open for reviews.

So this file asserts the ADR-061 rule at task scope: unjudged is neither clean
nor unclean, the exclusion is NAMED, and the row says which gate refused it.
"""

from __future__ import annotations

import pathlib

import pytest

from ai_venture_studio import product_bench
from ai_venture_studio.product_bench import BenchSummary, CaseResult
from ai_venture_studio.upstream import autopilot

REPO = pathlib.Path(__file__).resolve().parents[1]


def _case(**kw) -> CaseResult:
    base = {
        "name": "04-direction-workbench",
        "autopilot_status": "completed",
        "tasks_total": 7,
        "tasks_built": 7,
        "clean_reviews": 3,
    }
    base.update(kw)
    return CaseResult(**base)


# --- the sentinel carries what the tuple used to drop ----------------------


def test_a_refused_review_is_falsy_so_every_old_caller_is_unchanged():
    """The whole point of a sentinel over a new return arity.

    `if review` / `if after` / `after.findings if after else []` appear at
    four call sites and in a dozen test stubs; all of them must keep reading
    a non-review exactly as they read `None`.
    """
    refused = autopilot.ReviewDidNotRun(["diff too large (2361 lines > 2000)"])
    assert not refused
    assert bool(refused) is False
    assert refused.findings == []
    # The idiom `_should_roll_back` uses, verbatim.
    assert list(refused.findings if refused else []) == []
    # And the idiom `review_and_repair` uses to read the verdict.
    assert (refused.verdict.value if refused else None) is None


def test_the_reason_survives_the_call_it_used_to_die_in():
    refused = autopilot.ReviewDidNotRun(
        ["diff too large (2361 lines > 2000); split the PR"]
    )
    assert "2361" in refused.why
    assert "split the PR" in refused.why


def test_an_empty_reason_still_says_something():
    """`run_review` can return no leader without the DoR gate speaking. A
    sentinel whose `why` is the empty string would put us back where we
    started — a row that explains nothing."""
    assert autopilot.ReviewDidNotRun([]).why == "no verdict was produced"
    assert autopilot.ReviewDidNotRun(None).why == "no verdict was produced"


def test_review_head_hands_back_the_dor_reasons(monkeypatch, tmp_path):
    """The fix at the seam: `run_review`'s second element stops being `_`."""
    calls = {}

    def _fake_run_review(target, **kw):
        calls["target"] = target
        return None, {"dor_reasons": ["empty diff — nothing to review"]}

    monkeypatch.setattr(
        "ai_venture_studio.orchestrator.run_review", _fake_run_review
    )
    got = autopilot._review_head(tmp_path, "mock")
    assert not got
    assert got.why == "empty diff — nothing to review"
    assert calls["target"] == "HEAD~1..HEAD"


def test_a_real_leader_is_returned_untouched(monkeypatch, tmp_path):
    """The sentinel must not wrap a review that DID run."""
    sentinel = object()
    monkeypatch.setattr(
        "ai_venture_studio.orchestrator.run_review",
        lambda target, **kw: (sentinel, {"dor_reasons": []}),
    )
    assert autopilot._review_head(tmp_path, "mock") is sentinel


# --- the row says why -----------------------------------------------------


def test_the_row_names_the_gate_that_refused_it(monkeypatch, tmp_path):
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: autopilot.ReviewDidNotRun(
            ["diff too large (2361 lines > 2000); split the PR"]
        ),
    )
    verdict, detail, _approvals, _by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="slug", task_id="t4",
    )
    assert verdict is None
    assert "the review did not run" in detail
    assert "2361" in detail, (
        "the row carried an empty detail in run 18 — the reason is the fix"
    )


def test_a_review_that_ran_says_nothing_extra(monkeypatch, tmp_path):
    """The control. A real verdict must not pick up the new sentence."""

    class _Verdict:
        value = "APPROVE"

    class _Review:
        verdict = _Verdict()
        findings: list = []

    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: _Review())
    verdict, detail, _a, _b, _c = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="slug", task_id="t4",
    )
    assert verdict == "APPROVE"
    assert "the review did not run" not in detail


# --- the denominator ------------------------------------------------------


def test_an_unreviewed_task_leaves_the_clean_denominator():
    """3 clean of 7 built was 42.9%; of the 6 actually judged it is 50%."""
    charged = _case()
    assert charged.clean_review_rate == pytest.approx(3 / 7)
    honest = _case(unreviewed=["t4"])
    assert honest.clean_review_rate == pytest.approx(3 / 6)


def test_it_is_not_counted_clean_either():
    """The other direction, which is the one that would flatter the run.

    Excluding a task must not quietly promote it: with 3 of 7 clean and one
    unreviewed, the rate is 3/6 — never 4/7, and never 1.0.
    """
    honest = _case(unreviewed=["t4"])
    assert honest.clean_review_rate == pytest.approx(0.5)
    assert honest.clean_reviews == 3


def test_a_case_where_no_task_was_reviewed_has_no_rate():
    """Not 0.0. A rate of zero says every review objected."""
    none_judged = _case(
        tasks_built=2, clean_reviews=0, unreviewed=["t1", "t2"]
    )
    assert none_judged.clean_review_rate is None


def test_the_exclusion_is_named_or_it_is_a_lie():
    summary = BenchSummary(
        cases=[_case(unreviewed=["t4"])],
        build_rate=1.0,
        probe_pass_rate=1.0,
        clean_review_rate=0.5,
        no_review_reading=["04-direction-workbench:t4"],
    )
    assert summary.no_review_reading == ["04-direction-workbench:t4"]


def test_the_summary_builds_the_list_from_the_cases(monkeypatch):
    """Derived, never hand-passed — the disagreement `unmeasured` used to
    have with the averages it described came from re-deriving it."""
    built = product_bench.summarise(
        [_case(unreviewed=["t4"]), _case(name="01-groupbuy-api", unreviewed=[])],
        aborted="",
    )
    assert built.no_review_reading == ["04-direction-workbench:t4"]


def test_increment_cases_are_in_the_list_too():
    """The clean rate averages build-axis cases only, but the LIST is a
    statement about the run, and an increment case builds a base product
    through the same path."""
    built = product_bench.summarise(
        [_case(axis="increment", name="05-increment-repairs", unreviewed=["t1"])],
        aborted="",
    )
    assert built.no_review_reading == ["05-increment-repairs:t1"]


# --- the record ----------------------------------------------------------


def test_the_saved_result_carries_it(tmp_path):
    summary = BenchSummary(
        cases=[_case(unreviewed=["t4"])],
        build_rate=1.0,
        probe_pass_rate=1.0,
        clean_review_rate=0.5,
        no_review_reading=["04-direction-workbench:t4"],
    )
    path = product_bench.save_summary(summary, tmp_path, provider="mock")
    import yaml

    saved = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    assert saved["no_review_reading"] == ["04-direction-workbench:t4"], (
        "the workspace is gitignored and has been lost before — if the "
        "exclusion is not in the result file, a later reader cannot find it"
    )


def test_the_caveat_reaches_the_alert_not_just_the_terminal():
    """ADR-061's own lesson, applied rather than restated: a qualifier that
    only ever renders on the operator's screen is one the 3am reader did not
    get."""
    from ai_venture_studio import notify

    summary = BenchSummary(
        cases=[_case(unreviewed=["t4"])],
        build_rate=1.0,
        probe_pass_rate=1.0,
        clean_review_rate=0.5,
        no_review_reading=["04-direction-workbench:t4"],
    )
    text = notify.bench_alert(summary).render()
    assert "04-direction-workbench:t4" in text
    assert "Unjudged, not unclean" in text


def test_the_cli_prints_the_narrower_denominator():
    source = (REPO / "src" / "ai_venture_studio" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert "no_review_reading" in source, (
        "the rate is printed by the CLI; an exclusion it does not print is "
        "one the operator reads as a quality number"
    )
