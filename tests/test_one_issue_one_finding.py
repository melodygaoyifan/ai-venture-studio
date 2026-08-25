"""One issue is one finding, however many files it appears in (ADR-039).

The reconstruction of bench run 13's review stage, from its preserved
workspaces: the build stage copied `tempfile.mktemp(suffix=".db")` into nine
test files, bandit raised B306 nine times, and the leader — whose dedupe key
is `(file_path, line_start, title)`, i.e. keyed on LOCATION — kept all nine.
Nine blocking findings, one issue, 60% of every blocking finding in the run.

The second half is what made it unclearable rather than merely noisy. The
repair pass is capped at 8 findings. Nine copies means eight get repaired,
the ninth survives BY CONSTRUCTION, and the re-review rejects the task
again — no matter how good the fix was. That is a dead end of exactly the
shape ADR-037 removed one function over, reached by a different road.

These tests pin the funnel end to end at the run-13 shape.
"""
from __future__ import annotations

import types

from ai_venture_studio.leader import ACTIONABLE_SEVERITIES, synthesize
from ai_venture_studio.state import (
    CLEAN_VERDICT_VALUES,
    Confidence,
    Severity,
    VoterFinding,
    VoterOutput,
    VoterStatus,
)
from ai_venture_studio.upstream import autopilot

#: The nine files run 13's build stage copied the same fixture line into.
NINE_FILES = [f"tests/test_module_{i}.py" for i in range(9)]


def _mktemp_finding(path: str, severity: Severity = Severity.MEDIUM) -> VoterFinding:
    """The literal run-13 finding, at one of its nine sites."""
    return VoterFinding(
        voter="tool:bandit",
        title="B306: blacklist",
        severity=severity,
        confidence=Confidence.CERTAIN,
        file_path=path,
        line_start=12,
        line_end=12,
        evidence='db = tempfile.mktemp(suffix=".db")',
        explanation="Use of insecure and deprecated function (mktemp).",
        verification="VERIFIED",
        score=90,
        taxonomy_hint="P6",
    )


def _output(findings: list[VoterFinding]) -> VoterOutput:
    return VoterOutput(
        voter="tool:bandit", model="deterministic",
        status=VoterStatus.OK, findings=findings,
    )


def test_nine_copies_of_one_issue_are_one_blocking_finding():
    """The run-13 shape, straight through the leader."""
    result = synthesize([_output([_mktemp_finding(p) for p in NINE_FILES])])

    blocking = [f for f in result.findings if f.severity in ACTIONABLE_SEVERITIES]
    assert len(blocking) == 1, (
        f"one issue in nine files produced {len(blocking)} blocking findings — "
        "the dedupe key is keyed on location again"
    )
    kept = blocking[0]
    assert kept.occurrences == 9
    # Every site travels with it: a folded finding loses a row, never a target.
    assert sorted([kept.file_path, *kept.also_in]) == sorted(NINE_FILES)
    assert "folded" in result.summary


def test_the_review_shows_a_folded_findings_scale():
    """Folding removes rows, not evidence. A location cell naming one file
    would read as a one-line problem when it is a nine-file one."""
    from ai_venture_studio.render import render_pr_comment

    outputs = [_output([_mktemp_finding(p) for p in NINE_FILES])]
    result = synthesize(outputs)
    md = render_pr_comment(
        result, review_id="r1", mode="standard", voter_outputs=outputs
    )
    assert "+8 more file(s)" in md


def test_a_folded_finding_is_inside_what_one_repair_pass_can_fix():
    """The half that made it UNCLEARABLE: 9 findings against an 8-finding cap.

    Eight get repaired, the ninth survives by construction, the re-review
    rejects again. The fold has to bring the count under the cap, or the
    dead end is still there.
    """
    result = synthesize([_output([_mktemp_finding(p) for p in NINE_FILES])])
    blocking = [f for f in result.findings if f.severity in ACTIONABLE_SEVERITIES]
    assert len(blocking) <= autopilot.MAX_REPAIR_FINDINGS


def test_folding_keeps_the_worst_severity_any_site_was_raised_at():
    """Folding must never soften what blocks — otherwise deduping is a way
    to launder a critical finding into a note.

    The severity assertion needs the fold assertion beside it. On the build
    with no folding at all, nine findings come back and the HIGH one is first
    anyway, because the list is ordered by severity — so `findings[0]` was
    green on a build that had nothing to soften. What makes the severity
    meaningful is that the nine sites really did collapse into one row.
    """
    findings = [_mktemp_finding(p) for p in NINE_FILES]
    findings[4].severity = Severity.HIGH
    result = synthesize([_output(findings)])
    assert len(result.findings) == 1, "there was nothing here to soften"
    assert result.findings[0].severity is Severity.HIGH


def test_two_different_issues_in_one_file_are_still_two_findings():
    """The fold is by ISSUE, not by voter. Collapsing a voter's whole output
    into one row would be the opposite failure — hiding real findings."""
    a = _mktemp_finding("tests/test_a.py")
    b = _mktemp_finding("tests/test_a.py")
    b.title = "B608: hardcoded_sql_expressions"
    b.line_start = b.line_end = 40
    result = synthesize([_output([a, b])])
    assert len(result.findings) == 2


def test_the_same_issue_from_two_voters_stays_two_findings():
    """Two independent reviewers agreeing is signal, and the leader's
    semantic-merge pass is what clusters paraphrases across voters. The
    deterministic fold must not pre-empt it."""
    mine = _mktemp_finding("tests/test_a.py")
    theirs = _mktemp_finding("tests/test_b.py")
    theirs.voter = "security"
    result = synthesize([_output([mine]), _output([theirs])])
    assert len(result.findings) == 2


# --- the repair pass sees every site, and says so when it cannot -------------


def _review(verdict: str, findings):
    return types.SimpleNamespace(
        verdict=types.SimpleNamespace(value=verdict), findings=findings
    )


def test_the_repair_pass_is_shown_every_site_of_a_folded_finding(monkeypatch, tmp_path):
    """A folded finding repaired at one file and left at eight others gets
    raised again by the re-review at the sites nobody was shown."""
    for name in NINE_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('db = tempfile.mktemp(suffix=".db")\n', encoding="utf-8")

    folded = _mktemp_finding(NINE_FILES[0])
    folded.occurrences = 9
    folded.also_in = NINE_FILES[1:]

    seen: dict = {}

    def _fake_complete(self=None, **kwargs):
        seen.update(kwargs)
        return "files: []"

    monkeypatch.setattr(
        autopilot, "get_provider",
        lambda name: types.SimpleNamespace(complete=_fake_complete),
    )
    autopilot._fix_iteration(tmp_path, "mock", "m", [folded])

    prompt = seen.get("user", "")
    for name in NINE_FILES:
        assert name in prompt, f"the fix pass was never shown {name}"


def test_a_repair_cap_that_drops_findings_says_so(monkeypatch, tmp_path):
    """A bound nobody can see is indistinguishable from a fix that was not
    good enough — the row must not read the same for both."""
    many = [
        _mktemp_finding(f"app/module_{i}.py")
        for i in range(autopilot.MAX_REPAIR_FINDINGS + 3)
    ]
    for i, f in enumerate(many):
        f.title = f"issue number {i}"
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: _review("REQUEST_CHANGES", many),
    )
    monkeypatch.setattr(
        autopilot, "_fix_iteration", lambda *a, **k: (False, None, "")
    )
    _verdict, detail, _approvals, _by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="task-1",
    )
    assert f"{autopilot.MAX_REPAIR_FINDINGS} of {len(many)}" in detail
    assert "never shown" in detail


def test_a_file_cap_that_hides_a_site_says_so_too(monkeypatch, tmp_path):
    """The residual bound. A folded finding naming more sites than one prompt
    can carry is the run-13 dead end again, one level down — so the row has to
    name it rather than let it read as a fix that fell short."""
    folded = _mktemp_finding("tests/test_0.py")
    folded.occurrences = autopilot.MAX_REPAIR_FILES + 4
    folded.also_in = [
        f"tests/test_{i}.py" for i in range(1, autopilot.MAX_REPAIR_FILES + 4)
    ]
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: _review("REQUEST_CHANGES", [folded]),
    )
    monkeypatch.setattr(autopilot, "_fix_iteration", lambda *a, **k: (False, None, ""))
    _verdict, detail, _approvals, _by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="task-1",
    )
    assert "were not shown either" in detail


def test_one_definition_of_what_a_repair_pass_can_see():
    """`_repair_scope` is read by the pass that applies the bound and by the
    row that reports it. Two copies is how the first bound became an unnamed
    `[:8]` in two expressions that nobody could see fire."""
    findings = [_mktemp_finding(f"app/f{i}.py") for i in range(20)]
    for i, f in enumerate(findings):
        f.title = f"issue {i}"
    repairing, sites, omitted = _repair_scope_of(findings)
    assert len(repairing) == autopilot.MAX_REPAIR_FINDINGS
    assert len(sites) <= autopilot.MAX_REPAIR_FILES
    assert not set(sites) & set(omitted)


def _repair_scope_of(findings):
    return autopilot._repair_scope(findings)


def test_a_rejection_records_which_voter_rejected(monkeypatch, tmp_path):
    """Diagnosing run 13 meant hand-reading preserved review YAML to learn
    that one deterministic tool raised 60% of the blocking findings. A
    rejection rate without an author cannot tell a strict reviewer from a
    miscalibrated one."""
    tool = _mktemp_finding("app/a.py")
    other = _mktemp_finding("app/b.py")
    other.voter = "correctness"
    other.title = "missing WHERE clause"
    note = _mktemp_finding("app/c.py", severity=Severity.LOW)
    note.voter = "style"
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: _review("REQUEST_CHANGES", [tool, other, note]),
    )
    monkeypatch.setattr(autopilot, "_fix_iteration", lambda *a, **k: (False, None, ""))
    _verdict, _detail, _approvals, by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="task-1",
    )
    assert by_voter == {"tool:bandit": 1, "correctness": 1}, (
        "the row must name the blocking voters, and only the blocking ones"
    )


def test_a_clean_review_names_no_voter(monkeypatch, tmp_path):
    monkeypatch.setattr(
        autopilot, "_review_head",
        lambda root, provider: _review(
            CLEAN_VERDICT_VALUES[1], [_mktemp_finding("app/a.py", Severity.LOW)]
        ),
    )
    _verdict, _detail, _approvals, by_voter, _causes = autopilot.review_and_repair(
        tmp_path, provider="mock", model="m", label="task-1",
    )
    assert by_voter == {}
