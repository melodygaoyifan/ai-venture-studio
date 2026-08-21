"""The enterprise journey, end to end, against a realistic brownfield repo.

The fixture is modeled on the repo that drove this branch: an enterprise
data-pipeline (solver package, FastAPI surface, preprocessing, archive
noise, filesystem-path string literals, GitLab CI, its own CLAUDE.md).
Every stop of the adoption journey — comprehend, init --adopt with an
edition and a named gate owner, the readiness starter profile, substrate
declaration, governance posture, the Ready-to-build preflight, and the
rendered enterprise panel — is asserted here, so enterprise mode cannot
rot against exactly the kind of repo it was built for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

from ai_venture_studio.cli import app
from ai_venture_studio.studio_i18n import STRINGS
from ai_venture_studio.executables import resolve

runner = CliRunner()


def _t(key):
    return STRINGS[key]["en"]


def _mapop_like_repo(tmp_path: Path) -> Path:
    """A miniature of the real pilot repo: multi-module python pipeline,
    HTTP surface, filesystem-path literals (the wireup trap), GitLab CI,
    an operator-owned CLAUDE.md."""
    root = tmp_path / "pipeline"
    (root / "map_optimizer").mkdir(parents=True)
    (root / "map_optimizer" / "__init__.py").write_text("")
    (root / "map_optimizer" / "solver.py").write_text(
        "import sys\n"
        "def solve(budget):\n"
        "    # the wireup trap: filesystem strings must never read as routes\n"
        "    if sys.executable.startswith('/opt/homebrew'):\n"
        "        pass\n"
        "    if sys.prefix.startswith('/usr/lib'):\n"
        "        pass\n"
        "    return {'spend': budget}\n"
    )
    (root / "mapop_api").mkdir()
    (root / "mapop_api" / "__init__.py").write_text("")
    (root / "mapop_api" / "main.py").write_text(
        "app = object()\n"
        "@app.get(\"/api/health\")\n"
        "def health(): ...\n"
        "@app.post(\"/api/optimize\")\n"
        "def optimize(): ...\n"
        "@app.get(\"/api/outputs/bulk\")\n"
        "def bulk(): ...\n"
    )
    (root / "data_preprocessing").mkdir()
    (root / "data_preprocessing" / "clean.py").write_text(
        "from map_optimizer import solver\n"
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_solver.py").write_text(
        "from map_optimizer.solver import solve\n\n"
        "def test_solve():\n    assert solve(10)['spend'] == 10\n"
    )
    (root / ".gitlab-ci.yml").write_text("review:\n  script: [avs review --from-ci]\n")
    (root / "CLAUDE.md").write_text("# Pipeline constraints\n- operator-owned\n")
    subprocess.run([resolve("git"), "init", "-q"], cwd=root, check=True)
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root, check=True,
    )
    return root


def test_enterprise_journey_on_a_mapop_like_repo(tmp_path, monkeypatch):
    from ai_venture_studio.studio_modes import (
        build_preflight,
        enterprise_panel,
        governance_posture,
    )

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # …and the _FILE form the preflight also resolves, or "no credential in
    # this test env" is false on any machine that keeps its key in a file.
    monkeypatch.delenv("ANTHROPIC_API_KEY_FILE", raising=False)
    monkeypatch.delenv("AVS_ANTHROPIC_MODE", raising=False)
    root = _mapop_like_repo(tmp_path)

    # 1 · adopt with the enterprise edition — refused without a gate owner.
    refused = runner.invoke(app, [
        "init", str(root), "--adopt", "--profile", "data",
        "--edition", "enterprise",
    ])
    assert refused.exit_code == 2 and "gate-owner" in refused.output

    adopted = runner.invoke(app, [
        "init", str(root), "--adopt", "--profile", "data",
        "--edition", "enterprise", "--gate-owner", "Melody Gao",
    ])
    assert adopted.exit_code == 0, adopted.output
    # Brownfield next-steps, not "write a spec" for an existing product.
    assert "avs readiness" in adopted.output
    # The operator's CLAUDE.md survived the adoption.
    assert "operator-owned" in (root / "CLAUDE.md").read_text()
    # The map read the code: modules and the HTTP surface, with the
    # filesystem literals screened out.
    map_data = yaml.safe_load(
        (root / ".mas" / "codebase-map.yaml").read_text()
    )
    routes = map_data["routes"]
    assert any("api" in r for r in routes)
    assert not any(r.startswith(("/usr", "/opt")) for r in routes)

    # 2 · readiness without a profile prints a detected starter, not a
    # dead end — git and CI detected from the repo itself.
    starter = runner.invoke(app, ["readiness", "--repo-dir", str(root)])
    assert starter.exit_code == 0
    assert "vcs: git" in starter.output and "ci: true" in starter.output

    # 3 · declare the substrate → posture flips, the ladder gates.
    posture = governance_posture(root)
    assert "substrate" in posture["unconfigured"]
    (root / ".mas" / "substrate-profile.yaml").write_text(yaml.safe_dump({
        "substrate": {"vcs": "git", "pr_flow": True, "ci": True,
                      "observability": ["none"],
                      "progressive_delivery": False,
                      "languages": ["python"]}}))
    posture = governance_posture(root)
    assert "substrate" in posture["measured"]
    assert "edition" in posture["measured"]

    ladder = runner.invoke(app, ["readiness", "--repo-dir", str(root)])
    assert "S2" in ladder.output and "maintenance" in ladder.output

    # 4 · the Ready-to-build preflight tells this workspace the truth.
    rows = {r["item"]: r for r in build_preflight(root)}
    assert rows["governance"]["state"] == "ready"
    assert "Melody Gao" in rows["governance"]["found"]
    assert rows["substrate"]["state"] == "ready"
    assert rows["model"]["state"] == "todo"  # no credential in this test env
    assert rows["forge"]["state"] == "todo"  # no origin remote on a fixture

    # 5 · the enterprise panel renders every card off the same files.
    page = enterprise_panel(root, _t)
    for marker in ("Ready to build?", "Governance posture",
                   "Model door & egress", "Codebase (what avs found)",
                   "Melody Gao", "Stage activation",
                   "Deploy reviews (Gate 5)", "disarmed"):
        assert marker in page, f"missing {marker!r}"


def test_preflight_cli_matches_the_studio_card_and_gates_strictly(
    tmp_path, monkeypatch
):
    """Studio–CLI parity: `avs preflight` prints the same six checks, and
    --strict turns readiness into a pipeline gate."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY_FILE", raising=False)
    root = _mapop_like_repo(tmp_path)
    runner.invoke(app, [
        "init", str(root), "--adopt", "--profile", "data",
        "--edition", "enterprise", "--gate-owner", "Melody Gao",
    ])

    report = runner.invoke(app, ["preflight", "--repo-dir", str(root)])
    assert report.exit_code == 0  # report-only by default
    for item in ("model", "git identity", "forge", "governance",
                 "substrate", "studio access"):
        assert item in report.output
    assert "Melody Gao" in report.output

    gated = runner.invoke(
        app, ["preflight", "--repo-dir", str(root), "--strict"]
    )
    assert gated.exit_code == 1  # no model credential, no forge remote

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    still = runner.invoke(
        app, ["preflight", "--repo-dir", str(root), "--strict"]
    )
    assert still.exit_code == 1  # forge remote still missing — honest gate
