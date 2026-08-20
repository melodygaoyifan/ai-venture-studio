"""The run retries its own failures, and a retry knows why the last attempt died.

Two standing problems, one mechanism:

STOP-AND-ASK. Every failed task used to end the story with a retry button the
founder had to press — and the bench record shows that button usually worked
(t1/t2 recovered on the second pass, t5/t9 built on retry). Pressing it takes
no judgment, only patience, so the run now presses it itself: one bounded
pass, dependency order, everything recorded in auto_approvals.

THE SAME ERROR REAPPEARING. A retry that does not know why the last attempt
failed repeats it — the spec writer picks the same rejected phrasing, the
implementer re-invents the same phantom import (run-3 forensics). The
previous attempt's diagnosis now travels into both the spec writer's and the
implementer's prompts as <previous_attempt_failed>.

Plus the bug that made recovery invisible: a resumed run that rebuilt a
previously-failed task APPENDED a second outcome row, so the tally counted
the ghost and a fully-built product reported `failed` — a manufactured
stop-and-ask.
"""

import shutil

import pytest

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.autopilot import (
    _AUTO_RETRYABLE,
    TaskOutcome,
    record_outcome,
    run_autopilot,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

FDR = """# 产品需求
小区团长发起团购接龙，邻居在小程序里下单，团长看到按商品汇总的数量和应收金额。
必须有：发起接龙、下单、汇总。暂时不要：在线支付。
成功：第一周 10 个团长发起过接龙。
"""


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _workspace(tmp_path):
    root = init_workspace(tmp_path / "prod", "prod", "miniprogram")
    (root / "FDR.md").write_text(FDR, encoding="utf-8")
    return root


def _block_first_attempt(monkeypatch, task_marker: str, reason: str):
    """Make run_spec_stage fail the FIRST attempt at one task, capture every
    prior_failure it is handed, and behave normally otherwise."""
    import ai_venture_studio.upstream.autopilot as autopilot_mod

    real = autopilot_mod.run_spec_stage
    seen: dict = {"blocked_once": False, "prior_failures": {}}

    def flaky(repo, request, **kwargs):
        marker = request[request.rfind("task:"):].rstrip(")")
        seen["prior_failures"].setdefault(marker, []).append(
            kwargs.get("prior_failure", "")
        )
        spec = real(repo, request, **kwargs)
        if task_marker in request and not seen["blocked_once"]:
            seen["blocked_once"] = True
            spec.status = "blocked"
            spec.block_reasons = [reason]
        return spec

    monkeypatch.setattr(autopilot_mod, "run_spec_stage", flaky)
    return seen


def test_a_failed_task_is_retried_and_the_run_completes(tmp_path, monkeypatch):
    """The founder's retry button, pressed by the machine: a task whose spec
    blocked on the first attempt recovers within the same run."""
    root = _workspace(tmp_path)
    _block_first_attempt(monkeypatch, "task:t2", "synthetic EARS wall")

    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    assert result.status == "completed", [o.model_dump() for o in result.outcomes]
    rows = {o.task_id: o for o in result.outcomes}
    assert len(result.outcomes) == len(rows), "one row per task, no ghosts"
    assert rows["t2"].status == "built"
    assert "(recovered on auto-retry)" in rows["t2"].detail
    assert any(
        "auto-retry: 1 failed task(s)" in line and "1 recovered (t2)" in line
        for line in result.auto_approvals
    ), result.auto_approvals


def test_the_retry_knows_why_the_first_attempt_died(tmp_path, monkeypatch):
    """The 'same error reappears' fix: the retry's spec writer receives the
    first attempt's diagnosis, not a blank slate."""
    root = _workspace(tmp_path)
    seen = _block_first_attempt(monkeypatch, "task:t2", "synthetic EARS wall")

    run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    attempts = seen["prior_failures"]["task:t2"]
    assert attempts[0] == "", "the first attempt has no prior failure"
    assert "synthetic EARS wall" in attempts[1]
    assert "spec_blocked" in attempts[1]


def test_a_retry_that_also_fails_keeps_both_diagnoses(tmp_path, monkeypatch):
    """A later human retry starts from the accumulated history, not the last
    symptom alone."""
    import ai_venture_studio.upstream.autopilot as autopilot_mod

    root = _workspace(tmp_path)
    real = autopilot_mod.run_spec_stage

    def always_blocked(repo, request, **kwargs):
        spec = real(repo, request, **kwargs)
        if "task:t2" in request:
            spec.status = "blocked"
            spec.block_reasons = ["permanent synthetic wall"]
        return spec

    monkeypatch.setattr(autopilot_mod, "run_spec_stage", always_blocked)

    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    assert result.status == "failed"
    row = next(o for o in result.outcomes if o.task_id == "t2")
    assert row.status == "spec_blocked"
    assert "auto-retry also failed" in row.detail
    assert "first attempt: spec_blocked" in row.detail
    assert len([o for o in result.outcomes if o.task_id == "t2"]) == 1


def test_a_resumed_rebuild_replaces_the_failed_row(tmp_path, monkeypatch):
    """The duplicate-row bug: run 1 leaves t2 failed; run 2 builds it. The
    old code appended a second t2 row, so built_count could never equal
    len(outcomes) and a fully-built product reported `failed`."""
    import ai_venture_studio.upstream.autopilot as autopilot_mod

    root = _workspace(tmp_path)
    real = autopilot_mod.run_spec_stage

    def always_blocked(repo, request, **kwargs):
        spec = real(repo, request, **kwargs)
        if "task:t2" in request:
            spec.status = "blocked"
            spec.block_reasons = ["wall that exists only in run 1"]
        return spec

    monkeypatch.setattr(autopilot_mod, "run_spec_stage", always_blocked)
    first = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert first.status == "failed"
    monkeypatch.setattr(autopilot_mod, "run_spec_stage", real)

    second = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    assert second.status == "completed", [o.model_dump() for o in second.outcomes]
    t2_rows = [o for o in second.outcomes if o.task_id == "t2"]
    assert len(t2_rows) == 1, "the failed row was replaced, not shadowed"
    assert t2_rows[0].status == "built"


def test_record_outcome_replaces_by_task_id():
    outcomes = [
        TaskOutcome(task_id="t1", title="a", status="built"),
        TaskOutcome(task_id="t2", title="旧标题", status="spec_blocked"),
    ]

    record_outcome(outcomes, TaskOutcome(task_id="t2", title="新标题", status="built"))
    record_outcome(outcomes, TaskOutcome(task_id="t3", title="c", status="built"))

    assert [o.task_id for o in outcomes] == ["t1", "t2", "t3"]
    assert outcomes[1].status == "built" and outcomes[1].title == "新标题"


def test_every_system_failure_status_is_auto_retryable():
    """The retryable set is exactly `_OURS` — the statuses the report already
    tells the founder are our fault, never theirs. Mechanical retries only;
    a human judgment gate is not in this set."""
    from ai_venture_studio.upstream.autopilot import _OURS

    assert _AUTO_RETRYABLE == frozenset(_OURS)


class _RecordingProvider:
    """Delegates to the mock provider, keeping every prompt it saw."""

    def __init__(self):
        from ai_venture_studio.providers import get_provider

        self._inner = get_provider("mock")
        self.prompts: list[str] = []

    def complete(self, **kwargs):
        self.prompts.append(kwargs.get("user", ""))
        return self._inner.complete(**kwargs)

    def chat(self, **kwargs):
        self.prompts.append(str(kwargs.get("messages", "")))
        return self._inner.chat(**kwargs)


def test_prior_failure_reaches_the_spec_writers_prompt(tmp_path, monkeypatch):
    import ai_venture_studio.upstream.spec as spec_mod

    root = _workspace(tmp_path)
    recorder = _RecordingProvider()
    monkeypatch.setattr(spec_mod, "get_provider", lambda name: recorder)

    spec_mod.run_spec_stage(
        root, "an item store API (task:t1)", provider="mock",
        prior_failure="previous attempt status: spec_blocked\ndetail: THE-OLD-WALL",
    )

    hits = [p for p in recorder.prompts if "<previous_attempt_failed" in p]
    assert hits, "the spec writer never saw the previous failure"
    assert "THE-OLD-WALL" in hits[0]


def test_prior_failure_reaches_the_implementers_prompt(tmp_path, monkeypatch):
    import ai_venture_studio.upstream.build as build_mod
    import ai_venture_studio.upstream.spec as spec_mod

    root = _workspace(tmp_path)
    spec = spec_mod.run_spec_stage(root, "an item store API (task:t1)", provider="mock")
    from ai_venture_studio.upstream.spec import approve_spec

    approve_spec(root, spec.slug)

    recorder = _RecordingProvider()
    monkeypatch.setattr(build_mod, "get_provider", lambda name: recorder)

    build_mod.run_build(
        root, spec.slug, provider="mock", model="mock",
        prior_failure="previous attempt status: build_failed\ndetail: THE-OLD-WALL",
    )

    hits = [p for p in recorder.prompts if "<previous_attempt_failed" in p]
    assert hits, "the implementer never saw the previous failure"
    assert "THE-OLD-WALL" in hits[0]


def test_retry_task_cli_passes_the_recorded_failure(tmp_path, monkeypatch):
    """`avs retry-task` reads the failed row from outcomes.yaml and hands it
    to the writer — the human-initiated retry gets the same memory the
    automatic one does."""
    import yaml
    from typer.testing import CliRunner

    import ai_venture_studio.upstream as upstream_mod
    from ai_venture_studio.cli import app
    from ai_venture_studio.upstream.autopilot import run_autopilot as _ap
    from ai_venture_studio.upstream.discover import approve_brief
    from ai_venture_studio.upstream.plan import approve_plan, run_planning

    root = _workspace(tmp_path)
    _ap(root, root / "FDR.md", provider="mock", yes=False)
    approve_brief(root)
    run_planning(root, provider="mock")
    approve_plan(root)
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(yaml.safe_dump([
        {"task_id": "t1", "title": "x", "status": "spec_blocked",
         "detail": "RECORDED-WALL", "test_summary": "pytest said no"},
    ]), encoding="utf-8")

    seen = {}
    real = upstream_mod.run_spec_stage

    def spy(repo, request, **kwargs):
        seen["prior_failure"] = kwargs.get("prior_failure", "")
        return real(repo, request, **kwargs)

    monkeypatch.setattr(upstream_mod, "run_spec_stage", spy)
    done = CliRunner().invoke(
        app, ["retry-task", "t1", "--repo-dir", str(root), "--provider", "mock"]
    )

    assert done.exit_code == 0, done.output
    assert "RECORDED-WALL" in seen["prior_failure"]
    assert "pytest said no" in seen["prior_failure"]


def test_recorded_failure_is_best_effort():
    from ai_venture_studio.cli import _recorded_failure

    assert _recorded_failure("/nonexistent", "t1") == ""


def test_a_rerun_attempts_a_previously_failed_task_with_its_history(
    tmp_path, monkeypatch
):
    """Cross-run error memory: the failure context used to reach only
    same-run retries, so "continue the build" re-attempted every failed task
    BLIND — same inputs, same writer, same wall. Run 2's FIRST attempt at a
    task run 1 could not build now carries run 1's diagnosis."""
    import ai_venture_studio.upstream.autopilot as autopilot_mod

    root = _workspace(tmp_path)
    real = autopilot_mod.run_spec_stage

    def always_blocked(repo, request, **kwargs):
        spec = real(repo, request, **kwargs)
        if "task:t2" in request:
            spec.status = "blocked"
            spec.block_reasons = ["THE-RUN-ONE-WALL"]
        return spec

    monkeypatch.setattr(autopilot_mod, "run_spec_stage", always_blocked)
    assert run_autopilot(root, root / "FDR.md", provider="mock", yes=True).status == "failed"

    seen: dict = {}

    def spy(repo, request, **kwargs):
        if "task:t2" in request and "t2" not in seen:
            seen["t2"] = kwargs.get("prior_failure", "")
        return real(repo, request, **kwargs)

    monkeypatch.setattr(autopilot_mod, "run_spec_stage", spy)
    second = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    assert second.status == "completed"
    assert "THE-RUN-ONE-WALL" in seen["t2"], (
        "run 2's first attempt was blind to run 1's failure"
    )
    assert "spec_blocked" in seen["t2"]
