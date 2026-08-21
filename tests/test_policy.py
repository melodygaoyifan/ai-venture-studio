"""Per-project policy thresholds (doc 09 open item) — configurable, bounded,
loud on typos, and never silently weakening a report."""

from __future__ import annotations

import pytest
import yaml

from ai_venture_studio import scoring
from ai_venture_studio.maintenance.review import CONFIDENCE_MIN
from ai_venture_studio.orchestrator.graph import MAX_REVIEWABLE_LINES
from ai_venture_studio.policy import _BOUNDS, Policy, PolicyError, load_policy
from ai_venture_studio.state import Confidence, Severity, VoterFinding
from ai_venture_studio.executables import resolve


def _write(tmp_path, block=None, extra=None):
    mas = tmp_path / ".mas"
    mas.mkdir(exist_ok=True)
    data = {"name": "p", "profile": "web", **(extra or {})}
    if block is not None:
        data["policy"] = block
    (mas / "project.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


def test_defaults_track_the_module_constants_they_replace():
    """policy.py duplicates these numbers to avoid an import cycle; if the
    constants move and the bounds don't, this fails instead of drifting."""
    assert _BOUNDS["max_reviewable_lines"][0] == MAX_REVIEWABLE_LINES
    assert _BOUNDS["report_threshold"][0] == scoring.REPORT_THRESHOLD
    assert _BOUNDS["high_severity_threshold"][0] == scoring.HIGH_SEVERITY_THRESHOLD
    assert _BOUNDS["rootcause_confidence_min"][0] == CONFIDENCE_MIN


def test_absent_file_and_absent_block_both_mean_defaults(tmp_path):
    assert load_policy(tmp_path) == Policy()
    _write(tmp_path)
    assert load_policy(tmp_path) == Policy()


def test_overrides_load_and_round_trip(tmp_path):
    _write(tmp_path, {"max_reviewable_lines": 1200, "report_threshold": 85})
    policy = load_policy(tmp_path)
    assert policy.max_reviewable_lines == 1200
    assert policy.report_threshold == 85
    assert policy.high_severity_threshold == scoring.HIGH_SEVERITY_THRESHOLD
    assert policy.as_dict()["report_threshold"] == 85
    assert Policy(**policy.as_dict()) == policy


def test_unknown_key_is_an_error_not_a_silent_default(tmp_path):
    _write(tmp_path, {"report_treshold": 85})  # typo
    with pytest.raises(PolicyError, match="unknown policy key"):
        load_policy(tmp_path)


@pytest.mark.parametrize(("block", "match"), [
    ({"report_threshold": 10}, "admissible range"),      # below the floor
    ({"max_reviewable_lines": 99999}, "admissible range"),  # above the ceiling
    ({"report_threshold": "high"}, "must be an integer"),
    ({"report_threshold": True}, "must be an integer"),   # bool is not an int here
])
def test_out_of_range_and_wrong_type_refuse_to_run(tmp_path, block, match):
    _write(tmp_path, block)
    with pytest.raises(PolicyError, match=match):
        load_policy(tmp_path)


def test_non_mapping_policy_block_errors(tmp_path):
    _write(tmp_path, ["report_threshold: 85"])
    with pytest.raises(PolicyError, match="must be a mapping"):
        load_policy(tmp_path)


def test_weakened_labels_only_the_looser_direction(tmp_path):
    strict = Policy(max_reviewable_lines=500, report_threshold=95)
    assert strict.weakened() == []  # stricter needs no announcement
    loose = Policy(max_reviewable_lines=4000, report_threshold=55)
    labels = loose.weakened()
    assert any("max_reviewable_lines=4000" in label for label in labels)
    assert any("report_threshold=55" in label for label in labels)


def _finding(severity, score):
    return VoterFinding(
        voter="security", title="t", severity=severity, confidence=Confidence.LIKELY,
        file_path="a.py", line_start=1, line_end=1, evidence="e", explanation="x",
        score=score,
    )


def test_scoring_honors_the_policy_thresholds():
    medium_75 = _finding(Severity.MEDIUM, 75)
    assert not scoring.passes_threshold(medium_75)  # default bar is 80
    assert scoring.passes_threshold(medium_75, Policy(report_threshold=70))

    high_55 = _finding(Severity.HIGH, 55)
    assert not scoring.passes_threshold(high_55)  # default high bar is 60
    assert scoring.passes_threshold(high_55, Policy(high_severity_threshold=50))


def test_dor_gate_uses_the_project_ceiling_and_records_the_policy(tmp_path):
    from ai_venture_studio.orchestrator.graph import dor_gate_node

    _write(tmp_path, {"max_reviewable_lines": 60})
    added = "\n".join(f"+line {i}" for i in range(100))
    diff = (f"diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            f"@@ -1,1 +1,100 @@\n{added}\n")
    state = {"target": "bench://big", "diff": {"raw": diff}}
    update = dor_gate_node(state, repo_dir=str(tmp_path))
    assert update["dor_pass"] is False
    assert "60" in update["dor_reasons"][0]
    assert update["policy"]["max_reviewable_lines"] == 60
    assert update["policy_weakened"] == []  # 60 is stricter than the default


def test_weakened_policy_is_stamped_into_the_leader_summary(tmp_path):
    from ai_venture_studio.orchestrator.graph import leader_node

    state = {
        "policy": Policy(report_threshold=55).as_dict(),
        "policy_weakened": ["report_threshold=55 (default 80)"],
        "voter_outputs": [{"voter": "style", "model": "m", "status": "OK",
                           "findings": []}],
        "mode": "fast",
    }
    update = leader_node(state, provider_override="mock")
    assert update["leader"]["summary"].startswith("[policy weakened: report_threshold=55")


def test_maintenance_confidence_floor_is_policy_configurable(tmp_path):
    """The mock root-cause pass returns 75; a floor above it must escalate."""
    import subprocess

    from ai_venture_studio.maintenance import Incident, MaintenanceVerdict, run_maintenance

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([resolve("git"), "init", "-q"], cwd=repo, check=True)
    (repo / "billing.py").write_text("def invoice_total(items):\n    return sum(items)\n")
    subprocess.run([resolve("git"), "add", "."], cwd=repo, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "billing: invoice_total over items"], cwd=repo, check=True,
    )
    _write(repo, {"rootcause_confidence_min": 90})
    incident = Incident(id="inc-policy", title="TypeError in invoice_total",
                        body="TypeError in billing.py invoice_total when items is None")
    result = run_maintenance(incident, repo_dir=str(repo), provider="mock")
    assert result.root_cause.confidence == 75
    assert result.verdict is MaintenanceVerdict.ESCALATE_INCIDENT_UNRESOLVED
