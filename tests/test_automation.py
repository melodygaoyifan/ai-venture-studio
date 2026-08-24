"""v0.39.0 — policy-armed merge and deploy execution (ADR-031).

Almost every test here asserts a REFUSAL. That ratio is the design: the
capability is trivial, the bounding is the work, and the property worth
regression-testing is that nothing merges unless a human said exactly when.
"""

from __future__ import annotations

import datetime
import pathlib

import pytest
import yaml

from ai_venture_studio.paths import skills_root
from ai_venture_studio.automation import (
    ALWAYS_HUMAN_PATHS,
    AUTOMERGE_POLICY,
    DEPLOY_EXEC_POLICY,
    PolicyError,
    evaluate_deploy,
    evaluate_merge,
    load_policy,
    read_log,
    record,
)
from ai_venture_studio.executables import resolve

FUTURE = (datetime.date.today() + datetime.timedelta(days=90)).isoformat()
PAST = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def _armed(**overrides) -> dict:
    policy = {
        "enabled": True,
        "branches": ["main"],
        "min_track_record": 2,
        "armed_by": "melody",
        "expires_at": FUTURE,
    }
    policy.update(overrides)
    return policy


def _write(tmp_path, policy: dict, filename: str = AUTOMERGE_POLICY):
    mas = tmp_path / ".mas"
    mas.mkdir(exist_ok=True)
    (mas / filename).write_text(yaml.safe_dump(policy), encoding="utf-8")


def _track_record(tmp_path, correct: int):
    """The ledger automation is earned from."""
    mas = tmp_path / ".mas"
    mas.mkdir(exist_ok=True)
    (mas / "deploy-track-record.yaml").write_text(
        yaml.safe_dump([
            {"review_id": f"r{i}", "verdict": "PROMOTE", "outcome": "correct"}
            for i in range(correct)
        ]),
        encoding="utf-8",
    )


def _merge(tmp_path, **overrides):
    kwargs = {
        "verdict": "APPROVE",
        "branch": "main",
        "changed_files": ["src/app.py"],
        "test_gate_status": "passed",
    }
    kwargs.update(overrides)
    return evaluate_merge(tmp_path, **kwargs)


# --- disarmed by default -------------------------------------------------------


def test_no_policy_means_no_merge(tmp_path):
    decision = _merge(tmp_path)
    assert decision.allowed is False
    assert "no armed automerge policy" in decision.reasons[0]


def test_present_but_disabled_policy_still_refuses(tmp_path):
    _write(tmp_path, {"enabled": False, "branches": ["main"]})
    assert _merge(tmp_path).allowed is False


def test_absent_policy_is_not_permission_for_deploy_either(tmp_path):
    decision = evaluate_deploy(
        tmp_path, verdict="PROMOTE", branch="main", changed_files=[]
    )
    assert decision.allowed is False
    assert "the button stays yours" in decision.reasons[0]


# --- policy validation refuses the dangerous shapes ---------------------------


@pytest.mark.parametrize(("overrides", "match"), [
    ({"branches": ["*"]}, "branch patterns are refused"),
    ({"branches": ["release/*"]}, "branch patterns are refused"),
    ({"branches": []}, "must name at least one branch"),
    ({"armed_by": "  "}, "must name the human"),
    ({"expires_at": ""}, "expires_at.*required"),
    ({"expires_at": "soon"}, "must be YYYY-MM-DD"),
    ({"expires_at": PAST}, "expired"),
    ({"min_track_record": 0}, "must be >= 1"),
])
def test_armed_policy_shapes_that_are_refused(tmp_path, overrides, match):
    _write(tmp_path, _armed(**overrides))
    with pytest.raises(PolicyError, match=match):
        load_policy(tmp_path, AUTOMERGE_POLICY)


def test_malformed_yaml_is_an_error_not_a_disarm(tmp_path):
    mas = tmp_path / ".mas"
    mas.mkdir()
    (mas / AUTOMERGE_POLICY).write_text("enabled: [unclosed", encoding="utf-8")
    with pytest.raises(PolicyError, match="not parseable"):
        load_policy(tmp_path, AUTOMERGE_POLICY)


def test_expiry_is_evaluated_against_a_given_day(tmp_path):
    _write(tmp_path, _armed(expires_at="2026-01-01"))
    assert load_policy(tmp_path, AUTOMERGE_POLICY, today="2025-12-31").enabled
    with pytest.raises(PolicyError, match="expired"):
        load_policy(tmp_path, AUTOMERGE_POLICY, today="2026-01-02")


# --- armed, but every precondition still checked -------------------------------


def test_fully_satisfied_policy_allows_the_merge(tmp_path):
    _write(tmp_path, _armed())
    _track_record(tmp_path, 2)
    decision = _merge(tmp_path)
    assert decision.allowed is True, decision.reasons
    assert decision.reasons == []


@pytest.mark.parametrize(("overrides", "fragment"), [
    ({"verdict": "REQUEST_CHANGES"}, "not in ['APPROVE'"),
    ({"verdict": "ESCALATE_SECURITY_CRITICAL"}, "not in ['APPROVE'"),
    ({"verdict": "SOMETHING_NEW"}, "not in ['APPROVE'"),
    ({"branch": "feature/x"}, "is not in the armed list"),
    ({"test_gate_status": "failed"}, "test gate status"),
    ({"test_gate_status": None}, "test gate status"),
    ({"escalated": True}, "escalated to a human"),
])
def test_each_precondition_blocks_on_its_own(tmp_path, overrides, fragment):
    _write(tmp_path, _armed())
    _track_record(tmp_path, 5)
    decision = _merge(tmp_path, **overrides)
    assert decision.allowed is False
    assert any(fragment in reason for reason in decision.reasons), decision.reasons


def test_automation_is_earned_not_asserted(tmp_path):
    _write(tmp_path, _armed(min_track_record=5))
    _track_record(tmp_path, 2)
    decision = _merge(tmp_path)
    assert decision.allowed is False
    assert any("earned, not asserted" in r for r in decision.reasons)


def test_escalated_review_cannot_be_merged_even_with_an_approve_verdict(tmp_path):
    """A human override to APPROVE_WITH_NOTES is a human decision; the
    automation must not then re-decide the merge for them."""
    _write(tmp_path, _armed())
    _track_record(tmp_path, 5)
    decision = _merge(tmp_path, verdict="APPROVE_WITH_NOTES", escalated=True)
    assert decision.allowed is False


# --- paths that always demand a human -----------------------------------------


@pytest.mark.parametrize("path", [
    "migrations/0044_drop.sql",
    "infra/main.tf",
    "Dockerfile",
    ".github/workflows/ci.yml",
    "charts/app/values.yaml",
    "k8s/deploy.yaml",
    "CLAUDE.md",
    ".mas/project.yaml",
])
def test_sensitive_paths_block_regardless_of_policy(tmp_path, path):
    _write(tmp_path, _armed())
    _track_record(tmp_path, 9)
    decision = _merge(tmp_path, changed_files=["src/app.py", path])
    assert decision.allowed is False
    assert any("always require a human" in r for r in decision.reasons)


def test_automation_cannot_widen_its_own_permissions(tmp_path):
    """The policy files are on the always-human list: a diff that arms more
    automation can never itself be auto-merged."""
    _write(tmp_path, _armed())
    _track_record(tmp_path, 9)
    for policy_file in (".mas/automerge-policy.yaml", ".mas/deploy-exec-policy.yaml"):
        decision = _merge(tmp_path, changed_files=[policy_file])
        assert decision.allowed is False
        assert any("always require a human" in r for r in decision.reasons)
    assert any("policy" in pattern for pattern in ALWAYS_HUMAN_PATHS)


def test_extra_exclude_paths_are_honored(tmp_path):
    _write(tmp_path, _armed(exclude_paths=["src/payments/**"]))
    _track_record(tmp_path, 9)
    assert _merge(tmp_path, changed_files=["src/payments/charge.py"]).allowed is False
    assert _merge(tmp_path, changed_files=["src/app.py"]).allowed is True


# --- deploy execution ---------------------------------------------------------


def test_deploy_requires_promote_and_a_human_written_command(tmp_path):
    _write(tmp_path, _armed(command=["make", "deploy"]), DEPLOY_EXEC_POLICY)
    _track_record(tmp_path, 5)
    ok = evaluate_deploy(tmp_path, verdict="PROMOTE", branch="main", changed_files=[])
    assert ok.allowed is True

    held = evaluate_deploy(
        tmp_path, verdict="HOLD_FOR_HUMAN", branch="main", changed_files=[]
    )
    assert held.allowed is False
    assert any("not 'PROMOTE'" in r for r in held.reasons)


def test_armed_deploy_policy_without_a_command_refuses(tmp_path):
    _write(tmp_path, _armed(), DEPLOY_EXEC_POLICY)  # no command:
    _track_record(tmp_path, 5)
    decision = evaluate_deploy(
        tmp_path, verdict="PROMOTE", branch="main", changed_files=[]
    )
    assert decision.allowed is False
    assert any("names no `command:` argv" in r for r in decision.reasons)


# --- the log ------------------------------------------------------------------


def test_refusals_and_actions_are_both_logged(tmp_path):
    refused = _merge(tmp_path)
    record(tmp_path, refused, detail="review abc")
    _write(tmp_path, _armed())
    _track_record(tmp_path, 5)
    allowed = _merge(tmp_path)
    record(tmp_path, allowed, detail="review def: merged")

    entries = read_log(tmp_path)
    assert [e["allowed"] for e in entries] == [False, True]
    assert entries[0]["reasons"] and "no armed automerge policy" in entries[0]["reasons"][0]
    assert entries[1]["detail"].endswith("merged")
    assert all(e["action"] == "merge" for e in entries)


def test_empty_log_reads_as_empty(tmp_path):
    assert read_log(tmp_path) == []


# --- the charter says what the code does --------------------------------------


def test_claude_md_and_adr_record_the_reversal():
    repo = pathlib.Path(__file__).resolve().parents[1]
    charter = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert "ADR-031" in charter
    assert "disarmed by default" in charter.lower()
    assert "Auto-hotfix remains out" in charter

    adr = (repo / "docs" / "adr" / "031-policy-armed-automation.md").read_text()
    assert "Reverses" in adr and "Mechanism" in adr
    assert "auto-hotfix" in adr.lower()


# The `--admin` guard used to be asserted here against `github.merge_pr`, and
# again in `test_forge.py` against `forge.merge`. `forge` superseded the
# `github` module and `cli.py:3326` calls `forge.merge`, so this copy watched
# code nothing invoked — the weaker of two copies, on the dead path. Deleted
# with the module; `test_forge.py::test_merge_never_overrides_branch_protection`
# keeps the guarantee on the path that runs.


# --- branch resolution: never defaulted (the v0.39 follow-up fix) -------------


@pytest.mark.parametrize("evaluate", ["merge", "deploy"])
def test_unresolvable_branch_refuses_instead_of_assuming_main(tmp_path, evaluate):
    """`main` was the old fallback when `gh pr view` failed or HEAD was
    detached — an armed policy would then act on work it never named."""
    _write(tmp_path, _armed(command=["make", "deploy"]),
           AUTOMERGE_POLICY if evaluate == "merge" else DEPLOY_EXEC_POLICY)
    _track_record(tmp_path, 9)
    if evaluate == "merge":
        decision = _merge(tmp_path, branch="")
    else:
        decision = evaluate_deploy(
            tmp_path, verdict="PROMOTE", branch="", changed_files=[]
        )
    assert decision.allowed is False
    assert any("could not be determined" in r for r in decision.reasons)
    assert not any("is not in the armed list" in r for r in decision.reasons)


def test_deploy_review_records_the_branch_it_covers(tmp_path, monkeypatch):
    """The mirror must carry the branch, or deploy-execute has nothing to
    check against the policy."""
    import subprocess

    import ai_venture_studio.deploy.graph as deploy_graph
    from ai_venture_studio.deploy import run_deploy_review

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([resolve("git"), "init", "-q", "-b", "release-42"], cwd=repo, check=True)
    (repo / "helm").mkdir()
    # A branch only exists once something is committed on it: before that,
    # `rev-parse --abbrev-ref HEAD` fails and resolve_branch returns "".
    (repo / "README").write_text("x")
    subprocess.run([resolve("git"), "add", "."], cwd=repo, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo, check=True,
    )
    result = run_deploy_review(
        "main...HEAD", repo_dir=str(repo),
        skills_dir=str(skills_root() / "deploy"),
        provider_override="mock",
        diff_text=(
            "diff --git a/helm/values.yaml b/helm/values.yaml\n"
            "--- a/helm/values.yaml\n+++ b/helm/values.yaml\n"
            "@@ -1,1 +1,1 @@\n+replicaCount: 3\n"
        ),
    )
    assert result.branch == "release-42"
    final = sorted((repo / ".mas" / "deploy-reviews" / result.artifacts_dir.split("/")[-1])
                   .glob("[0-9]*-final.yaml"))
    data = yaml.safe_load(final[-1].read_text())
    assert data["branch"] == "release-42"

    # Detached HEAD is unknown, not "main".
    monkeypatch.setattr(
        deploy_graph.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "HEAD\n", ""),
    )
    assert deploy_graph.resolve_branch("main...HEAD", str(repo)) == ""
