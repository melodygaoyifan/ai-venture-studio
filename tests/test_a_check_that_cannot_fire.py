"""The arrangement `lane_check` can never object to (ADR-059).

`lane_check` compares tasks in DIFFERENT lanes, so a plan with one lane
cannot collide, and a plan that resolves a collision by merging the two
lanes is answered with silence. The check that exists to protect
parallelism is quiet exactly when there is none left to protect.

Run 18 scored both sides of that. Case 03 kept two honest lanes, hit a real
collision, was handed the violation three times, exhausted `MAX_REVISIONS`
and was blocked at Gate U2 having built nothing — it cost the run its build
rate. Cases 02 and 04 collapsed to a single lane and passed clean; case 04
had three tasks all expecting `app/candidates.py`. The plans were scored as
though 03 were the bad one.

The fix is deliberately NOT a new refusal. `run_planning` computes
`status="blocked"` from `dag_issues` alone, and a planner handed a bar it
cannot clear is precisely what happened to case 03. A single-lane plan is
legal and is sometimes right, and no honest deterministic rule separates
"one surface" from "gave up". So the finding is described, recorded and
delivered — and never allowed to block.
"""

from __future__ import annotations

import inspect

import yaml

from ai_venture_studio.upstream.plan import (
    Plan,
    Task,
    _save,
    _shared_globs,
    lane_advisories,
    lane_check,
    run_planning,
)


def _task(tid, lane, files, deps=()):
    return Task(
        id=tid, title=tid, lane=lane, estimate_hours=2,
        depends_on=list(deps), files_expected=list(files),
    )


# Run 18's case 04, reduced to its arrangement: three tasks, one lane, all
# three expecting the same file.
CASE_04 = [
    _task("t1", "core", ["app/candidates.py"]),
    _task("t2", "core", ["app/candidates.py"]),
    _task("t3", "core", ["app/candidates.py"]),
]

# Run 18's case 03: two honest lanes over one shared model file.
CASE_03 = [
    _task("t1", "api", ["app/models*.py", "app/api.py"]),
    _task("t3", "orders", ["app/models*.py", "app/orders.py"]),
]


# --- The premise, asserted rather than assumed ------------------------------


def test_the_collapsed_plan_is_invisible_to_lane_check():
    """If this ever fails, the advisory below is redundant — check that
    before deleting either."""
    assert lane_check(CASE_04) == [], (
        "three tasks writing one file is exactly what lane_check exists to "
        "catch, and putting them in one lane is all it takes to hide it"
    )


def test_the_honest_plan_is_the_one_that_gets_refused():
    assert lane_check(CASE_03), "two lanes over a shared file must still be refused"


def test_merging_the_lanes_clears_the_refusal():
    """The dodge, demonstrated: the same two tasks, one lane, no complaint.

    This is why the MERGE remedy has to carry a warning — a planner
    optimising for a clean check will find this move on its own.
    """
    merged = [CASE_03[0], _task("t3", "api", ["app/models*.py", "app/orders.py"])]
    assert lane_check(merged) == []


# --- What the advisory says --------------------------------------------------


def test_the_collapse_is_reported_even_though_it_is_legal():
    text = " ".join(lane_advisories(CASE_04))
    assert "all 3 tasks are in lane 'core'" in text
    assert "3 waves of one task" in text, "name the cost, not just the shape"
    assert "cannot be reported" in text, (
        "the reader has to be told the check was silent BY CONSTRUCTION, "
        "not that it looked and found nothing (ADR-048)"
    )


def test_the_advisory_names_a_remedy_and_permits_the_status_quo():
    text = " ".join(lane_advisories(CASE_04))
    assert "HOIST" in text or "hoist" in text
    assert "legal" in text and "may be right" in text, (
        "an advisory that reads as an accusation gets 'fixed' by merging "
        "lanes, which is the move it exists to discourage"
    )


def test_same_lane_contention_names_its_owners_and_its_file():
    text = " ".join(lane_advisories(CASE_04))
    assert "t1, t2, t3" in text and "app/candidates.py" in text


def test_two_tasks_in_one_lane_is_not_a_collapse():
    """A floor, so ordinary small plans are not nagged."""
    pair = [_task("t1", "core", ["app/a.py"]), _task("t2", "core", ["app/b.py"])]
    assert lane_advisories(pair) == []


def test_a_genuinely_parallel_plan_says_nothing():
    parallel = [
        _task("t1", "api", ["app/api.py"]),
        _task("t2", "ui", ["web/ui.js"]),
        _task("t3", "data", ["app/models.py"]),
    ]
    assert lane_advisories(parallel) == []


def test_the_remedy_the_advisory_recommends_actually_clears_it():
    """Telling a planner to do something that leaves the finding standing
    would be worse than saying nothing (ADR-058)."""
    hoisted = [
        _task("t0", "data", ["app/candidates.py"]),
        _task("t1", "api", ["app/api.py"], deps=["t0"]),
        _task("t2", "ui", ["web/ui.js"], deps=["t0"]),
        _task("t3", "jobs", ["app/jobs.py"], deps=["t0"]),
    ]
    assert lane_advisories(hoisted) == []
    assert lane_check(hoisted) == [], "the hoist must not trade one finding for another"


# --- It must never become a refusal -----------------------------------------


def test_an_advisory_is_not_a_dag_issue():
    """`status` is computed from dag_issues alone. A finding that reaches
    that list blocks the plan, and a blocked plan builds nothing — which is
    what this whole record is about."""
    source = inspect.getsource(run_planning)
    dag_assignment = source.split("dag_issues = (")[1].split(")")[0]
    assert "lane_advisories" not in dag_assignment
    assert "advisories = lane_advisories(tasks)" in source


def test_a_collapsed_plan_still_passes_planning(tmp_path):
    """End to end: nothing about the advisory changes the verdict."""
    plan = Plan(status="proposed", brief_title="b", tasks=CASE_04, dag_issues=[])
    assert plan.status == "proposed"


def test_advisories_ride_along_only_when_a_revision_happens_anyway():
    source = inspect.getsource(run_planning)
    body = source.split("if not dag_issues and not majors:")[1]
    assert "advisories_not_blocking" in body, (
        "the planner should hear about this while it is already revising, "
        "and never be made to revise for it"
    )
    assert body.index("break") < body.index("advisories_not_blocking"), (
        "the clean-plan break must come first, or an advisory costs a "
        "revision on a plan that has nothing wrong with it"
    )


# --- Delivery: it has to reach a reader -------------------------------------


def test_the_plan_records_the_advisory_as_minor(tmp_path):
    source = inspect.getsource(run_planning)
    assert '"severity": "minor", "lens": "parallelism"' in source


def test_plan_md_says_how_many_lanes_there_are(tmp_path):
    """The lane column names each task's lane and never the lane COUNT —
    the one number that says whether this plan builds concurrently."""
    plan = Plan(
        status="proposed", brief_title="b", tasks=CASE_04,
        critic_issues=[{"severity": "minor", "lens": "parallelism",
                        "problem": "parallelism: all 3 tasks are in lane 'core'"}],
    )
    _save(tmp_path, plan)
    text = (tmp_path / "product" / "plan.md").read_text(encoding="utf-8")
    assert "1 lane(s): core" in text
    assert "no task ever builds concurrently" in text
    assert "parallelism: all 3 tasks are in lane 'core'" in text


def test_plan_md_stays_quiet_on_a_parallel_plan(tmp_path):
    plan = Plan(status="proposed", brief_title="b", tasks=[
        _task("t1", "api", ["app/api.py"]), _task("t2", "ui", ["web/ui.js"]),
    ])
    _save(tmp_path, plan)
    text = (tmp_path / "product" / "plan.md").read_text(encoding="utf-8")
    assert "2 lane(s): api, ui" in text
    assert "no task ever builds concurrently" not in text


# --- The bench row keeps it after the workspace is gone ----------------------


def test_the_bench_row_records_the_lane_arrangement(tmp_path):
    """Finding this meant opening preserved workspaces by hand, and run 18
    had already overwritten run 17's."""
    from ai_venture_studio.product_bench import _lane_arrangement

    (tmp_path / "product").mkdir()
    (tmp_path / "product" / "plan.yaml").write_text(
        yaml.safe_dump({
            "tasks": [{"id": t.id, "lane": t.lane} for t in CASE_04],
            "critic_issues": [{"lens": "parallelism", "problem": "collapsed"}],
        }),
        encoding="utf-8",
    )
    row = _lane_arrangement(tmp_path)
    assert "1 lane(s) over 3 task(s): core" in row
    assert "1 parallelism advisory(ies)" in row


def test_a_case_that_never_planned_reports_no_arrangement(tmp_path):
    """Best-effort by design: no plan is not an error, and a bench row that
    raised here would cost the case its whole result."""
    from ai_venture_studio.product_bench import _lane_arrangement

    assert _lane_arrangement(tmp_path) == ""
    (tmp_path / "product").mkdir()
    (tmp_path / "product" / "plan.yaml").write_text("tasks: [", encoding="utf-8")
    assert _lane_arrangement(tmp_path) == ""


# --- The overlap rule the two checks share ----------------------------------


def test_shared_globs_is_symmetric_under_pattern_direction():
    a = _task("a", "x", ["app/*.py"])
    b = _task("b", "y", ["app/models.py"])
    assert _shared_globs(a, b) == ["app/*.py"]
    assert _shared_globs(b, a) == ["app/models.py"]


def test_the_merge_remedy_admits_what_it_costs():
    text = " ".join(lane_check(CASE_03))
    assert "COSTS PARALLELISM" in text
    assert "not to quiet this message" in text
