"""Use-case coverage: does the design actually work for everything it claims?

The canon claims five domain profiles, three editions, and a five-rung
substrate ladder where *stages below their infrastructure floor are
inactive-never-degraded* (ADR-U15). Those are testable claims, and this
module tests them as a matrix rather than trusting that each part works
because its own unit test passes.

It found a real gap on first run: `STAGE_FLOORS` defines floors for eight
stages, but only `code_review` and `deploy_review` consulted them — so an S0
team with no git could still run `build`, and `triage` ran with no
observability configured. Six of eight stages now enforce their floor, and
the parametrized test below is what keeps it that way.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest
import yaml
from typer.testing import CliRunner

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.adoption.substrate import (
    STAGE_FLOORS,
    Rung,
    StageInactiveError,
    check_stage,
    load_substrate_profile,
)
from ai_venture_studio.cli import app
from ai_venture_studio.editions import EDITIONS, edition_lint, resolve_edition
from ai_venture_studio.upstream import approve_spec, init_workspace, run_build
from ai_venture_studio.upstream.spec import run_spec_stage
from ai_venture_studio.upstream.workspace import available_profiles

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

# The capability declarations that put a workspace on each rung. The schema
# declares what you HAVE; the rung is derived (§18.47.1).
LADDER: dict[str, dict] = {
    "S0": {"vcs": "none", "pr_flow": False, "ci": False,
           "observability": ["none"], "progressive_delivery": False,
           "languages": ["python"]},
    "S1": {"vcs": "git", "pr_flow": True, "ci": False,
           "observability": ["none"], "progressive_delivery": False,
           "languages": ["python"]},
    "S2": {"vcs": "git", "pr_flow": True, "ci": True,
           "observability": ["none"], "progressive_delivery": False,
           "languages": ["python"]},
    "S3": {"vcs": "git", "pr_flow": True, "ci": True,
           "observability": ["sentry"], "progressive_delivery": False,
           "languages": ["python"]},
    "S4": {"vcs": "git", "pr_flow": True, "ci": True,
           "observability": ["sentry"], "progressive_delivery": True,
           "languages": ["python"]},
}


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _substrate(root: pathlib.Path, rung: str) -> None:
    (root / ".mas").mkdir(exist_ok=True)
    (root / ".mas" / "substrate-profile.yaml").write_text(
        yaml.safe_dump({"substrate": LADDER[rung]}), encoding="utf-8"
    )


# --- every domain profile, end to end ----------------------------------------


@pytest.mark.parametrize("profile", available_profiles())
def test_every_profile_specs_and_builds(tmp_path, profile):
    """web / miniprogram / app / game / data all reach a built artifact —
    the profiles are composable deltas, so none may break the spine."""
    root = init_workspace(tmp_path / profile, profile, profile)
    spec = run_spec_stage(root, "an item store API", provider="mock")
    assert spec.status == "proposed", spec.block_reasons
    approve_spec(root, spec.slug)
    result = run_build(root, spec.slug, provider="mock")
    assert result.status == "built", result.detail


def test_the_profile_set_is_what_the_docs_claim():
    # enterprise-web (2026-07): the web profile plus the governance
    # constraints an IT/security review asks about — audit records,
    # /api/health, env-only config, versioned integration endpoints.
    assert set(available_profiles()) == {
        "web", "miniprogram", "app", "game", "data", "enterprise-web",
    }


# --- every edition -----------------------------------------------------------


@pytest.mark.parametrize("edition", EDITIONS)
def test_every_edition_resolves_and_only_narrows(tmp_path, edition):
    root = init_workspace(tmp_path / edition, "w", "web")
    path = resolve_edition(root, edition)
    raw = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    assert edition_lint(raw) == []  # narrowing-only, no widening keys


def test_the_edition_set_is_what_the_docs_claim():
    assert set(EDITIONS) == {"solo", "enterprise", "engineer"}


# --- the substrate ladder, as a matrix ---------------------------------------


EXPECTED_ACTIVATION = {
    # rung: stages that must be INACTIVE at it (everything else runs)
    "S0": {"coding", "code_review", "test", "deploy_review", "maintenance"},
    "S1": {"test", "maintenance"},
    "S2": {"maintenance"},
    "S3": set(),
    "S4": set(),
}


@pytest.mark.parametrize("rung", sorted(LADDER))
def test_stage_activation_matches_the_declared_floors(tmp_path, rung):
    root = tmp_path / rung
    root.mkdir()
    _substrate(root, rung)
    assert load_substrate_profile(root).rung() is Rung[rung]

    inactive = set()
    for stage in STAGE_FLOORS:
        try:
            check_stage(root, stage)
        except StageInactiveError:
            inactive.add(stage)
    assert inactive == EXPECTED_ACTIVATION[rung], rung


def test_the_upstream_stages_are_the_zero_infrastructure_wedge(tmp_path):
    """Doc 18's central adoption claim: discovery, planning and
    specification need nothing, so they are the wedge into a team with no
    infrastructure at all."""
    root = tmp_path / "s0"
    root.mkdir()
    _substrate(root, "S0")
    for stage in ("discovery", "planning", "specification"):
        assert check_stage(root, stage) is not None  # active, not raising


def test_absent_profile_gates_nothing(tmp_path):
    """No declaration means no gating — an existing workspace must not
    suddenly refuse to work because this feature exists."""
    root = tmp_path / "undeclared"
    root.mkdir()
    for stage in STAGE_FLOORS:
        assert check_stage(root, stage) is None


# --- the gap this module found: every floor must be ENFORCED, not just known -


CLI_GATED = [
    ("discovery", ["discover", "an idea"]),
    ("planning", ["plan"]),
    ("specification", ["spec", "a thing"]),
    ("coding", ["build", "some-slug"]),
    ("code_review", ["review", "main...HEAD"]),
    ("deploy_review", ["deploy-review", "main...HEAD"]),
    ("maintenance", ["triage", "incident.yaml"]),
]


@pytest.mark.parametrize(("stage", "argv"), CLI_GATED, ids=[c[0] for c in CLI_GATED])
def test_every_stage_command_enforces_its_floor(tmp_path, stage, argv):
    """Knowing a floor and enforcing it are different things. Before v0.49
    only code_review and deploy_review consulted STAGE_FLOORS, so six stages
    ran below their floor while the docs said they could not."""
    root = tmp_path / stage
    root.mkdir()
    # The rung at which this stage is genuinely INACTIVE. deploy_review is
    # the designed exception: above S0 it DEGRADES to config-lint-only rather
    # than going inactive (§19 — a lint-only deploy review still helps),
    # which the separate degradation test below pins.
    inactive_at = [
        rung for rung, off in EXPECTED_ACTIVATION.items() if stage in off
    ]
    if not inactive_at:
        pytest.skip(f"{stage} is never inactive — it is the wedge (S0 floor)")
    below = max(inactive_at)  # the highest rung where it still refuses
    _substrate(root, below)
    (root / "incident.yaml").write_text(
        yaml.safe_dump({"title": "x", "body": "y"}), encoding="utf-8"
    )

    result = CliRunner().invoke(app, [*argv, "--repo-dir", str(root)])
    assert result.exit_code == 4, (
        f"{stage} ran at {below.name} despite a floor of "
        f"{STAGE_FLOORS[stage].name}: "
        f"exit={result.exit_code}\n{result.output[:400]}"
    )
    flat = " ".join(result.output.split())
    assert "STAGE_INACTIVE" in flat or "inactive" in flat.lower()
    assert "readiness" in flat  # tells the operator how to climb


@pytest.mark.parametrize(("stage", "argv"), CLI_GATED, ids=[c[0] for c in CLI_GATED])
def test_no_stage_command_gates_at_or_above_its_floor(tmp_path, stage, argv):
    """The other half: a stage AT its floor must not be refused. A guard that
    blocks legitimate work is worse than no guard."""
    root = tmp_path / f"{stage}-ok"
    root.mkdir()
    at_floor = STAGE_FLOORS[stage].name
    _substrate(root, at_floor)
    (root / "incident.yaml").write_text(
        yaml.safe_dump({"title": "x", "body": "y"}), encoding="utf-8"
    )
    result = CliRunner().invoke(app, [*argv, "--repo-dir", str(root)])
    # It may fail for unrelated reasons (no workspace, no spec, no git remote)
    # — it must simply not fail with the substrate refusal.
    assert result.exit_code != 4, (
        f"{stage} was refused AT its own floor {at_floor}:\n{result.output[:400]}"
    )


def test_deploy_review_degrades_rather_than_refusing_above_s0(tmp_path):
    """ADR-U15's deliberate asymmetry: without progressive delivery a deploy
    review cannot judge canary machinery, but a config lint still helps — so
    it degrades and says so, and (per v0.33) can never PROMOTE from there."""
    root = tmp_path / "degrade"
    root.mkdir()
    _substrate(root, "S3")  # CI + observability, no progressive delivery
    activation = check_stage(root, "deploy_review")
    assert activation is not None
    assert activation.status.value == "DEGRADED"
    assert "progressive delivery" in activation.note
