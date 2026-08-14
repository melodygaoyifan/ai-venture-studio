"""A retried module is not a lesser build (item P1.1).

`retry-task` ran spec + build and stopped. In one real 小程序 run, four of
the seven modules that reached the founder had been built that way: no
reviewer had read them, no fix iteration could fire, and their rows in the
report carried an empty verdict beside modules `create` had reviewed
properly. Same product, two standards, and the weaker one was reached by
pressing the button the Studio offers on every failure.
"""

import shutil

import pytest

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.upstream import init_workspace

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


def _planned_workspace(path):
    """A workspace with an approved, locked plan whose tasks are NOT built —
    the state a founder is in when a module failed and the Studio offers
    the retry button."""
    from ai_venture_studio.upstream.autopilot import run_autopilot
    from ai_venture_studio.upstream.discover import approve_brief
    from ai_venture_studio.upstream.plan import approve_plan, run_planning

    root = init_workspace(path, path.name, "miniprogram")
    (root / "FDR.md").write_text(FDR, encoding="utf-8")
    run_autopilot(root, root / "FDR.md", provider="mock", yes=False)
    approve_brief(root)
    run_planning(root, provider="mock")
    approve_plan(root)
    return root


def test_retry_task_runs_the_review_gate_and_records_its_verdict(
    tmp_path, monkeypatch
):
    from typer.testing import CliRunner

    import ai_venture_studio.upstream.autopilot as autopilot_mod
    from ai_venture_studio.cli import app

    root = _planned_workspace(tmp_path / "prod")

    calls = []

    def _spy(root_arg, *, provider, model, label, task_id="", detail=""):
        calls.append({"label": label, "provider": provider})
        return "APPROVE_WITH_NOTES", detail, ["fix iteration (spy): none needed"], {}

    monkeypatch.setattr(autopilot_mod, "review_and_repair", _spy)

    retried = CliRunner().invoke(
        app, ["retry-task", "t1", "--repo-dir", str(root), "--provider", "mock"]
    )

    assert retried.exit_code == 0, retried.output
    assert calls, "a retried module reached the founder unreviewed"
    assert "APPROVE_WITH_NOTES" in retried.output

    import yaml

    rows = yaml.safe_load((root / "product" / "outcomes.yaml").read_text())
    t1 = next(r for r in rows if r["task_id"] == "t1")
    assert t1["review_verdict"] == "APPROVE_WITH_NOTES"


def test_retry_passes_the_founders_contract_to_spec_and_build(
    tmp_path, monkeypatch
):
    """The retry path was the only build path that never carried the FDR,
    so a retried module could invent field names its siblings had agreed
    on — the drift class that `source_contract` exists to close."""
    from typer.testing import CliRunner

    import ai_venture_studio.cli as cli_mod
    from ai_venture_studio.cli import app

    root = _planned_workspace(tmp_path / "prod2")

    seen = {}
    assert not hasattr(cli_mod, "run_spec_stage")  # imported inside the command

    import ai_venture_studio.upstream as upstream_mod

    original = upstream_mod.run_spec_stage

    def _spy(repo_dir, request, **kwargs):
        seen["source_contract"] = kwargs.get("source_contract", "")
        return original(repo_dir, request, **kwargs)

    monkeypatch.setattr(upstream_mod, "run_spec_stage", _spy)
    # No spy on the review gate here: this run exercises it for real.
    done = CliRunner().invoke(
        app, ["retry-task", "t1", "--repo-dir", str(root), "--provider", "mock"]
    )

    assert done.exit_code == 0, done.output
    assert "review:" in done.output, "the real gate ran and reported a verdict"
    assert "团购接龙" in seen.get("source_contract", "")
