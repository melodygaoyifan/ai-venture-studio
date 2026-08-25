"""When the machine refuses, the record must name the thing that refused.

ADR-043's second and third halves, both found in bench run 16.

  - `02-shortener-api` planned for six minutes, revised twice, and came back
    with no tasks. The scoreboard said `failed`. The cause — the planner's
    YAML would not parse — was on disk in `product/plan.yaml` and reached
    neither the result file nor the revision prompt: the parse branch kept
    `type(exc).__name__` and threw away the message, so the model was asked
    to fix a break it was never shown, and failed the same way twice.

  - Three of the run's eleven rejections (01-t4, 03-t3, 04-t6) had nothing
    but LOW findings, a severity that cannot block. They were rejected by
    `leader.synthesize`'s *other* trigger — two voters that returned no
    verdict — and the row beside them named the low findings instead. Every
    one of those rows also carried an empty `blocking_by_voter`, so the
    evidence was there and the sentence a person reads said something else.

Same failure both times, and the same one as ADR-041/042: the pipeline knew
why it stopped and the durable record kept a word for the category instead
of the fact.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import types
from pathlib import Path

import pytest

from ai_venture_studio.state import (
    Confidence,
    LeaderResult,
    Severity,
    Verdict,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)
from ai_venture_studio.upstream import autopilot

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


def _finding(severity: Severity, title: str = "unchecked input") -> VoterFinding:
    return VoterFinding(
        voter="correctness", title=title, severity=severity,
        confidence=Confidence.CERTAIN, file_path="app/main.py",
        line_start=1, line_end=1, evidence="x = 1",
        explanation="e", verification="confirmed", score=90,
    )


# --------------------------------------------------------------------------
# The planner's refusal
# --------------------------------------------------------------------------


class _Unparseable:
    """A planner that answers in prose, which is what case 02 hit."""

    BROKEN = "tasks:\n  - id: t1\n   title: *not a yaml anchor\n"

    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, **kwargs):
        from ai_venture_studio.product.stage_engine import (
            PRODUCT_LEADER_MARKER,
            PRODUCT_VERIFIER_MARKER,
            PRODUCT_VOTER_MARKER,
        )

        system = kwargs.get("system", "")
        if PRODUCT_VOTER_MARKER in system:
            return "findings: []"
        if PRODUCT_VERIFIER_MARKER in system:
            return "verdict: refuted\nreason: stub"
        if PRODUCT_LEADER_MARKER in system:
            return "summary: stub leader"
        self.prompts.append(kwargs.get("user", ""))
        return self.BROKEN


def _planned(monkeypatch, tmp_path):
    from ai_venture_studio.upstream import approve_brief, init_workspace, run_discovery
    from ai_venture_studio.upstream import plan as plan_mod

    root = init_workspace(tmp_path / "p", "p", "web")
    run_discovery(root, "a link shortener", provider="mock")
    approve_brief(root)
    stub = _Unparseable()
    monkeypatch.setattr(plan_mod, "get_provider", lambda name: stub)
    return plan_mod.run_planning(root, provider="mock"), stub


def test_an_unparseable_plan_records_what_the_parser_objected_to(
    monkeypatch, tmp_path
):
    """`ScannerError` is a category. `line 3, column 9: ...` is the fact."""
    plan, _stub = _planned(monkeypatch, tmp_path)

    assert plan.status == "blocked"
    issues = " | ".join(plan.dag_issues)
    assert "unparseable planner output" in issues
    assert ":" in issues.split("unparseable planner output (")[1].split(")")[0], (
        "the issue names the exception class and nothing the parser said — "
        "this is run 16's case 02, which cost the run a whole product"
    )
    assert len(issues) > 60, issues


def test_the_revision_prompt_shows_the_model_the_break_it_must_fix(
    monkeypatch, tmp_path
):
    """The reason this cost a product rather than a retry: both revisions
    were asked to fix a problem they were never shown."""
    _plan, stub = _planned(monkeypatch, tmp_path)

    assert len(stub.prompts) >= 2, "the planner must have been given a retry"
    retry = stub.prompts[1]
    assert "failed to parse" in retry
    # Whatever the parser said, some of it has to be in the prompt. Compare
    # against the exception the same input actually raises rather than a
    # hard-coded message, so a yaml/pydantic upgrade cannot quietly empty
    # this out.
    from ai_venture_studio.upstream.plan import extract_mapping

    try:
        extract_mapping(_Unparseable.BROKEN, ("tasks",))
    except Exception as exc:  # noqa: BLE001 — that is the point
        detail = " ".join(str(exc).split())
    else:  # pragma: no cover - the fixture is deliberately broken
        pytest.fail("the fixture parses; it no longer reproduces case 02")
    assert detail[:40] in " ".join(retry.split()), (
        f"the revision prompt named the exception class but not its "
        f"message: {retry[-300:]!r}"
    )


def test_every_failed_autopilot_result_says_why():
    """THE INVARIANT, not the instance.

    Case 02's reason was lost at one of three `status="failed"` returns.
    Fixing the three does not stop a fourth from being added, so the
    constructor call itself is what gets pinned.
    """
    tree = ast.parse(Path(inspect.getfile(autopilot)).read_text(encoding="utf-8"))
    failures = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "AutopilotResult":
            continue
        kwargs = {kw.arg for kw in node.keywords}
        status = next(
            (kw.value for kw in node.keywords if kw.arg == "status"), None
        )
        if not isinstance(status, ast.Constant) or status.value != "failed":
            continue
        if "blocked_reason" not in kwargs:
            failures.append(node.lineno)
    assert not failures, (
        "autopilot.py returns status='failed' with no blocked_reason at "
        f"line(s) {failures} — a run that produced nothing reports the "
        "single word 'failed' again"
    )


def test_the_reason_travels_from_the_pipeline_to_the_scoreboard_row(
    monkeypatch, tmp_path
):
    """`AutopilotResult.blocked_reason` is only worth setting if the bench
    result file — the durable record; the workspace is gitignored — keeps
    it."""
    import ai_venture_studio.product_bench as pb
    from ai_venture_studio.upstream.autopilot import AutopilotResult

    monkeypatch.setattr(
        pb, "run_autopilot",
        lambda *a, **k: AutopilotResult(
            status="failed",
            blocked_reason="planning blocked: unparseable planner output "
                           "(ScannerError: line 3, column 9)",
        ),
    )
    monkeypatch.setattr(
        pb, "run_probe",
        lambda ws, probe: pb.ProbeResult(name=probe.name, passed=False),
    )
    case = pb.ProductCase(
        name="planned-nothing", profile="web", fdr="# FDR\nA link shortener.",
        probes=[pb.Probe(name="smoke", script="raise SystemExit(1)")],
    )
    result = pb.run_case(case, provider="mock", keep_dir=tmp_path / "keep")

    assert result.measured and result.build_rate == 0.0
    assert "ScannerError" in result.failure_reason


def test_the_alert_names_the_cause_not_the_category():
    """The other place a person reads the run. `failed` is a category; the
    Discord line used to carry only that."""
    from ai_venture_studio import notify
    from ai_venture_studio.product_bench import BenchSummary, CaseResult

    summary = BenchSummary(
        cases=[CaseResult(
            name="planned-nothing", autopilot_status="failed",
            failure_reason="planning blocked: unparseable planner output "
                           "(ScannerError: line 3, column 9)",
        )],
        build_rate=0.0, probe_pass_rate=0.0, clean_review_rate=0.0,
    )
    alert = notify.bench_alert(summary, workspace="w")
    body = "\n".join(alert.lines)
    assert "planned-nothing" in body
    assert "ScannerError" in body, body


# --------------------------------------------------------------------------
# The reviewers' refusal
# --------------------------------------------------------------------------


def _blocked_review(findings, blocked=("security", "correctness")):
    return LeaderResult(
        verdict=Verdict.REQUEST_CHANGES, summary="s",
        findings=list(findings), blocked_voters=list(blocked),
    )


def test_two_silent_voters_are_enough_to_reject(monkeypatch, tmp_path):
    """The trigger nothing downstream knew about: `len(blocked) == 2`
    rejects on its own, with no finding at any blocking severity."""
    from ai_venture_studio.leader import synthesize

    result = synthesize([
        VoterOutput(voter="a", model="m", status=VoterStatus.BLOCKED_TOOL_FAILURE),
        VoterOutput(voter="b", model="m", status=VoterStatus.BLOCKED_TOOL_FAILURE),
    ])
    assert result.verdict.value not in autopilot.CLEAN_VERDICT_VALUES
    assert len(result.blocked_voters) == 2


def test_a_rejection_by_silence_says_so_and_names_the_silent(
    monkeypatch, tmp_path
):
    """Run 16's 01-t4/03-t3/04-t6, exactly: LOW findings only, two voters
    that never answered, and a row that blamed the LOW findings."""
    review = _blocked_review([_finding(Severity.LOW, "B310: blacklist")])
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (False, None, "")
    )
    _verdict, detail, _approvals, by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t4",
    )

    assert "security" in detail and "correctness" in detail, (
        f"the reason names the findings and not the voters that rejected: "
        f"{detail!r}"
    )
    assert "rejected the task" in detail, (
        "with no actionable finding present the silent voters ARE the "
        "rejection, and the row must not leave that to be re-derived"
    )
    assert by_voter == {}, (
        "the tell that this shape exists — no voter raised a blocking "
        "finding, yet the task was rejected"
    )


def test_a_finding_that_blocks_keeps_the_note_but_not_the_claim(
    monkeypatch, tmp_path
):
    """Both causes present: the voters are still worth naming, but they are
    no longer what rejected the task, and the note must not say they were."""
    review = _blocked_review([_finding(Severity.HIGH, "sql injection")])
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (False, None, "")
    )
    _v, detail, _a, _bv, _c = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t1",
    )
    assert "2 voter(s) returned no verdict" in detail
    assert "rejected the task" not in detail
    assert "sql injection" in detail


def test_a_review_that_answered_carries_no_note():
    """The note is for the silence, not decoration on every rejection."""
    assert autopilot._blocked_voter_note(
        _blocked_review([_finding(Severity.MEDIUM)], blocked=())
    ) == ""
    # And the pre-fix shape — a review object with no such attribute at all
    # — must not raise; `_review_head` is stubbed with one in three suites.
    assert autopilot._blocked_voter_note(
        types.SimpleNamespace(findings=[], verdict=None)
    ) == ""


# --- Gate 2's reason reaches the row (ADR-058) -------------------------------
#
# The test gate is the one rejection in the system that knows its exact cause
# at the moment it decides: it downgrades an APPROVE deterministically and
# writes why into `leader.summary`. Nothing read that field. So the best-
# evidenced rejection arrived at the scoreboard as the worst-explained one —
# and where a voter also happened to be blocked, the row printed "this is what
# rejected the task" next to a voter that had not.


def _gate2_review(findings=(), blocked=(), reason="failed: 1 failed, 27 passed"):
    from ai_venture_studio.orchestrator.graph import GATE2_BLOCK_PREFIX

    return LeaderResult(
        verdict=Verdict.REQUEST_CHANGES,
        summary=f"{GATE2_BLOCK_PREFIX}{reason}] suite is green on the diff",
        findings=list(findings),
        blocked_voters=list(blocked),
    )


def test_the_gate_2_reason_reaches_the_bench_row(monkeypatch, tmp_path):
    review = _gate2_review()
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (False, None, "")
    )
    _v, detail, _a, _bv, _c = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t1",
    )
    assert "Gate 2 blocked" in detail
    assert "1 failed, 27 passed" in detail, (
        f"the row names the rejection but not the suite that caused it: {detail!r}"
    )


def test_gate_2_takes_the_decisive_claim_away_from_a_blocked_voter(
    monkeypatch, tmp_path
):
    """Run 18's `01-groupbuy-api t3`: one blocked voter, zero findings.

    Neither of `synthesize`'s triggers fires on that shape — one blocked voter
    is not two, and there is no actionable finding — so the rejection did not
    come from the leader at all. The old note said it did.
    """
    review = _gate2_review(blocked=("context",))
    monkeypatch.setattr(autopilot, "_review_head", lambda root, provider: review)
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (False, None, "")
    )
    _v, detail, _a, _bv, _c = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="t3",
    )
    assert "Gate 2 blocked" in detail
    assert "1 voter(s) returned no verdict" in detail   # still worth naming
    assert "rejected the task" not in detail, (
        f"the test gate rejected this task, not the voter: {detail!r}"
    )


def test_a_review_gate_2_did_not_touch_carries_no_gate_note():
    assert autopilot._gate2_note(_blocked_review([_finding(Severity.LOW)])) == ""
    assert autopilot._gate2_note(types.SimpleNamespace(findings=[])) == ""


def test_the_marker_the_writer_writes_is_the_one_the_reader_reads():
    """One constant, two files — the drift this replaced was a bare f-string."""
    import inspect as _inspect

    from ai_venture_studio.orchestrator import graph

    source = _inspect.getsource(graph.test_gate_node)
    assert "GATE2_BLOCK_PREFIX" in source, (
        "Gate 2 is formatting its marker inline again; the bench-row reader "
        "will not follow it"
    )
    assert graph.gate2_reason(
        f"{graph.GATE2_BLOCK_PREFIX}mutation survived] rest"
    ) == "mutation survived"
    assert graph.gate2_reason("an ordinary summary") == ""
