"""The six commands where a human's decision enters the system, entered.

ADR-068 measured ADR-054's closing sentence across the whole CLI and found
42 of 78 commands that no test had ever entered. Thirty are not named in one.
Six of those thirty are the ones that matter most, and they are exactly the
system's safety story:

    brief-approve   Gate U1 — the human problem-selection decision
    plan-approve    Gate U2 — scope lock
    spec-approve    Gate U3 — the approval that makes a spec buildable
    scr-approve     grants exactly one regeneration of a locked spec
    automerge       merges a reviewed PR, under an armed ADR-031 policy
    deploy-execute  runs the deploy command a human wrote, same

The logic under all six is well covered — `automation.py`'s policy rules have
a dedicated module of their own, and `approve_brief`/`approve_plan`/
`approve_spec`/`approve_scr` are each exercised directly. What had never run
is the **CLI wrapper**, which is the precise layer ADR-054's defect lived in:
ten orphaned lines *below* an early `typer.Exit`, invisible to `--help`,
invisible to a test of the function underneath, and wrong on every healthy
run for eleven bench runs.

Note where the wrapper's own risk actually is, because it is not in the
policy decision. It is in the seven lines around it — `plan_approve` reaches
through `plan_result.tasks` and then `t.id` and `t.description`;
`scr_approve` indexes `data['spec_slug']` and `data['reason']`;
`automerge_cmd` reads four nested keys out of a final YAML and hands them to
`evaluate_merge` by keyword. None of that is checked by a test of the
callee, and all of it breaks silently when a return shape moves.

Hermetic, and the boundary is drawn where the money is. Both act-on-the-world
commands are driven with `--dry-run`, which returns *before* `forge.merge`
and *before* `subprocess.run(policy.command)`. Nothing here can merge
anything or deploy anything, and the one network call on the path —
`forge.head_branch`, which shells out to `gh` — is replaced, which is
recorded in the test that needs it rather than hidden in a fixture.
"""

from __future__ import annotations

import datetime

import pytest
import yaml
from typer.testing import CliRunner

from ai_venture_studio import automation, forge
from ai_venture_studio.cli import app

FUTURE = (datetime.date.today() + datetime.timedelta(days=90)).isoformat()


def _run(argv: list[str]):
    return CliRunner().invoke(app, argv)


# --- Gates U1 / U2 / U3, and the SCR --------------------------------------


@pytest.fixture
def workspace(tmp_path):
    """A workspace with discovery run, ready for Gate U1."""
    from ai_venture_studio.upstream import init_workspace, run_discovery

    root = init_workspace(tmp_path / "w", "w", "web")
    run_discovery(root, "a link shortener", provider="mock")
    return root


def test_brief_approve_takes_gate_u1(workspace):
    result = _run(["brief-approve", "--repo-dir", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "approved:" in result.output
    assert "next: avs plan" in result.output


def test_plan_approve_locks_scope_and_lists_what_is_ready(workspace):
    """The wrapper's own work: `len(plan_result.tasks)`, then `t.id` and
    `t.description` for every task `next_tasks` returns. A test of
    `approve_plan` says nothing about either."""
    from ai_venture_studio.upstream import approve_brief, run_planning

    approve_brief(workspace)
    run_planning(workspace, provider="mock")

    result = _run(["plan-approve", "--repo-dir", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "scope locked:" in result.output
    assert "task(s)" in result.output


def test_spec_approve_takes_gate_u3(workspace):
    from ai_venture_studio.upstream import (
        approve_brief,
        approve_plan,
        run_planning,
        run_spec_stage,
    )

    approve_brief(workspace)
    run_planning(workspace, provider="mock")
    approve_plan(workspace)
    spec = run_spec_stage(workspace, "a redirect endpoint", provider="mock")

    result = _run(["spec-approve", spec.slug, "--repo-dir", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "approved:" in result.output
    assert f"avs build {spec.slug}" in result.output


def test_scr_approve_names_the_spec_and_the_reason(workspace):
    """`data['spec_slug']` and `data['reason']` are read by the wrapper and
    by nothing else. A dict key that moves is an ADR-054 defect that only
    the CLI path can see."""
    from ai_venture_studio.upstream import (
        approve_brief,
        approve_plan,
        run_planning,
        run_spec_stage,
    )
    from ai_venture_studio.upstream.spec import raise_scr

    approve_brief(workspace)
    run_planning(workspace, provider="mock")
    approve_plan(workspace)
    spec = run_spec_stage(workspace, "a redirect endpoint", provider="mock")
    path = raise_scr(workspace, spec.slug, "the shortener needs custom aliases")
    number = int(path.stem.split("-")[1])

    result = _run(["scr-approve", str(number), "--repo-dir", str(workspace)])
    assert result.exit_code == 0, result.output
    assert f"approved SCR-{number:03d}" in result.output
    assert spec.slug in result.output
    assert "custom aliases" in result.output
    assert "regenerate it once" in result.output


# --- the two commands that act on the world -------------------------------


def _final(root, review_id: str, **fields) -> None:
    d = root / ".mas" / "reviews" / review_id
    d.mkdir(parents=True)
    payload = {
        "target": "https://github.com/o/r/pull/1",
        "verdict": "APPROVE",
        "test_report": {"status": "passed"},
        "diff": {"changed_files": ["src/app.py"]},
        "hitl": {},
    }
    payload.update(fields)
    (d / "01-final.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _deploy_final(root, deploy_id: str, **fields) -> None:
    d = root / ".mas" / "deploy-reviews" / deploy_id
    d.mkdir(parents=True)
    # NOT a Dockerfile, a chart, a k8s manifest or a `.tf` — those are in
    # ALWAYS_HUMAN_PATHS and no policy can arm them, which the test below
    # pins deliberately rather than by accident.
    payload = {"verdict": "PROMOTE", "branch": "main",
               "deploy_files": ["deploy/render.yaml"]}
    payload.update(fields)
    (d / "01-final.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")


def _arm(root, filename: str, **overrides) -> None:
    policy = {
        "enabled": True,
        "branches": ["main"],
        "min_track_record": 2,
        "armed_by": "melody",
        "expires_at": FUTURE,
    }
    policy.update(overrides)
    mas = root / ".mas"
    mas.mkdir(parents=True, exist_ok=True)
    (mas / filename).write_text(yaml.safe_dump(policy), encoding="utf-8")


def _track_record(root, correct: int) -> None:
    mas = root / ".mas"
    mas.mkdir(parents=True, exist_ok=True)
    (mas / "deploy-track-record.yaml").write_text(
        yaml.safe_dump([
            {"review_id": f"r{i}", "verdict": "PROMOTE", "outcome": "correct"}
            for i in range(correct)
        ]),
        encoding="utf-8",
    )


def test_automerge_refuses_a_review_that_does_not_exist(tmp_path):
    """The early exit ADR-054's defect hid below. Worth pinning on its own,
    because every other assertion here depends on getting past it."""
    result = _run(["automerge", "nope", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "no finished review" in result.output


def test_automerge_refuses_by_default_and_says_why(tmp_path, monkeypatch):
    """No policy file at all — the ADR-031 default. The wrapper must print
    every reason and exit non-zero, and the refusal must reach the log."""
    monkeypatch.setattr(forge, "head_branch", lambda target: "main")
    _final(tmp_path, "rev1")

    result = _run(["automerge", "rev1", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "merge refused" in result.output
    assert "no armed automerge policy" in result.output

    logged = automation.read_log(tmp_path)
    assert logged, "a refusal that is not logged did not happen"
    assert logged[-1]["allowed"] is False


def test_automerge_refuses_when_the_branch_cannot_be_determined(tmp_path):
    """`forge.head_branch` is left real here, and with no `gh` and no network
    it returns None. ADR-031's rule is that this becomes a refusal and never
    an assumed 'main' — the wrapper's `or ""` is what makes that reachable."""
    _arm(tmp_path, automation.AUTOMERGE_POLICY)
    _track_record(tmp_path, 2)
    _final(tmp_path, "rev2")

    result = _run(["automerge", "rev2", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "could not be determined" in result.output


def test_automerge_reaches_the_merge_under_a_satisfied_policy(tmp_path, monkeypatch):
    """`--dry-run` returns before `forge.merge`, so this enters every line of
    the wrapper's happy path and still cannot merge anything."""
    monkeypatch.setattr(forge, "head_branch", lambda target: "main")
    _arm(tmp_path, automation.AUTOMERGE_POLICY)
    _track_record(tmp_path, 2)
    _final(tmp_path, "rev3")

    result = _run(["automerge", "rev3", "--repo-dir", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "merge would proceed" in result.output
    assert "squash" in result.output


def test_deploy_execute_refuses_a_review_that_does_not_exist(tmp_path):
    result = _run(["deploy-execute", "nope", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "no finished deploy review" in result.output


def test_deploy_execute_refuses_by_default_and_says_why(tmp_path):
    _deploy_final(tmp_path, "dep1")

    result = _run(["deploy-execute", "dep1", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "deploy not executed" in result.output
    assert "no armed deploy-exec policy" in result.output
    assert automation.read_log(tmp_path)[-1]["allowed"] is False


def test_deploy_execute_refuses_a_verdict_that_is_not_promote(tmp_path):
    _arm(tmp_path, automation.DEPLOY_EXEC_POLICY, command=["make", "deploy"])
    _track_record(tmp_path, 2)
    _deploy_final(tmp_path, "dep2", verdict="HOLD")

    result = _run(["deploy-execute", "dep2", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "not 'PROMOTE'" in result.output


def test_deploy_execute_shows_the_human_written_command_before_running_it(tmp_path):
    """The system never composes this argv, so the wrapper prints it back
    before acting. `--dry-run` returns before `subprocess.run`, which is the
    only reason this test is allowed to exist in a hermetic suite."""
    _arm(tmp_path, automation.DEPLOY_EXEC_POLICY, command=["make", "deploy-prod"])
    _track_record(tmp_path, 2)
    _deploy_final(tmp_path, "dep3")

    result = _run(["deploy-execute", "dep3", "--repo-dir", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "make deploy-prod" in result.output
    assert "deploy would proceed" in result.output


def test_a_malformed_policy_is_an_error_not_a_silent_disarm(tmp_path):
    """The `PolicyError` arm of both wrappers, which is the branch that
    decides whether a broken policy file reads as 'no automation' (quietly
    correct-looking) or as a fault. ADR-031 says fault: exit 2, not 1."""
    _deploy_final(tmp_path, "dep4")
    mas = tmp_path / ".mas"
    (mas / automation.DEPLOY_EXEC_POLICY).write_text(
        "enabled: [unclosed", encoding="utf-8"
    )

    result = _run(["deploy-execute", "dep4", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 2, result.output
    # Flattened: rich wraps the console to the terminal width, so the phrase
    # arrives split across lines. Asserting on the raw output would make this
    # test pass or fail on how wide the window is.
    assert "not parseable YAML" in " ".join(result.output.split())


def test_an_armed_policy_still_cannot_arm_an_always_human_path(tmp_path):
    """A Dockerfile change is refused whatever the policy says. The point of
    driving it through the CLI is that this is the wrapper's ONLY signal
    that `deploy_files` reached `evaluate_deploy` at all — the key is read
    out of the final YAML here and nowhere else."""
    _arm(tmp_path, automation.DEPLOY_EXEC_POLICY, command=["make", "deploy"])
    _track_record(tmp_path, 2)
    _deploy_final(tmp_path, "dep5", deploy_files=["Dockerfile"])

    result = _run(["deploy-execute", "dep5", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output
    flat = " ".join(result.output.split())
    assert "always require a human" in flat
    assert "Dockerfile" in flat
