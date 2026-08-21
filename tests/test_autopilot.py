import shutil

import pytest

from ai_venture_studio import testing as testing_mod
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.autopilot import run_autopilot
from ai_venture_studio.upstream.fdr import write_template
from ai_venture_studio.executables import resolve

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)

GOOD_FDR = """# 产品需求
小区团长发起团购接龙，邻居在小程序里下单，团长看到按商品汇总的数量和应收金额。
必须有：发起接龙、下单、汇总。暂时不要：在线支付。
成功：第一周 10 个团长发起过接龙。
"""


@pytest.fixture(autouse=True)
def _no_docker(monkeypatch):
    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    import ai_venture_studio.upstream.build as build_mod

    monkeypatch.setattr(build_mod, "docker_available", lambda: False)


def _workspace(tmp_path, fdr_text: str):
    root = init_workspace(tmp_path / "prod", "prod", "miniprogram")
    (root / "FDR.md").write_text(fdr_text, encoding="utf-8")
    return root


def test_template_and_guide_written(tmp_path):
    """English by default since v0.53; Chinese on request, same six
    questions either way."""
    path = write_template(tmp_path / "w")
    assert path.name == "FDR.md"
    assert "Fill this in using your own words" in path.read_text()

    zh = write_template(tmp_path / "zh", lang="zh")
    assert "不需要任何技术词汇" in zh.read_text()
    guide = (tmp_path / "w" / "FDR-GUIDE.md").read_text()
    assert "Four rules" in guide
    assert "One FDR = one thing" in guide  # the granularity contract


def test_inadequate_fdr_yields_questions_not_a_build(tmp_path):
    root = _workspace(tmp_path, "just an idea: something for my neighborhood")
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "needs_answers"
    questions = (root / "FDR-QUESTIONS.md").read_text()
    assert "谁会用它" in questions
    assert not (root / "product" / "plan.yaml").exists()  # nothing built on guesses


def test_confirmation_pause_without_yes(tmp_path):
    root = _workspace(tmp_path, GOOD_FDR)
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=False)
    assert result.status == "awaiting_confirmation"
    assert (root / "product" / "CONFIRMATION.md").exists()
    assert not (root / "product" / "plan.yaml").exists()  # paused before building


def test_full_autopilot_builds_every_task(tmp_path):
    root = _workspace(tmp_path, GOOD_FDR)
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status == "completed", [o.model_dump() for o in result.outcomes]
    assert len(result.outcomes) == 3  # mock plan: t1 -> t2 -> t3
    assert all(o.status == "built" for o in result.outcomes)
    assert all(o.review_verdict for o in result.outcomes)
    # Every machine approval is on the record, never silent.
    assert any("Gate U2" in a for a in result.auto_approvals)
    assert sum("Gate U3" in a for a in result.auto_approvals) == 3
    report = (root / "product" / "BUILD-REPORT.md").read_text()
    assert "plain-language" in report or "确认" in report
    # Three build commits exist beyond init.
    import subprocess

    log = subprocess.run(
        [resolve("git"), "log", "--oneline"], cwd=root, capture_output=True, text=True
    ).stdout
    assert log.count("feat(") == 3


def test_a_crashed_run_resumes_instead_of_re_paying_built_tasks(tmp_path, monkeypatch):
    """Plan item 15's upstream half, as one end-to-end example.

    A task is the expensive unit here — spec + build + review, minutes and
    real money each — so a run interrupted at task 2 must not rebuild task 1.
    """
    import ai_venture_studio.upstream.autopilot as autopilot_mod

    root = _workspace(tmp_path, GOOD_FDR)
    built_calls: list[str] = []
    real_build = autopilot_mod.run_build

    def counting_build(repo, slug, **kwargs):
        built_calls.append(slug)
        # Die partway through the second task, after the first is on disk.
        if len(built_calls) == 2:
            raise RuntimeError("simulated crash mid-build")
        return real_build(repo, slug, **kwargs)

    monkeypatch.setattr(autopilot_mod, "run_build", counting_build)
    with pytest.raises(RuntimeError, match="simulated crash"):
        run_autopilot(root, root / "FDR.md", provider="mock", yes=True)

    # The completed task was persisted as it finished, not at the end.
    import yaml

    outcomes = yaml.safe_load((root / "product" / "outcomes.yaml").read_text())
    assert [o["status"] for o in outcomes] == ["built"]
    first_slug = built_calls[0]

    # Resume: the built task is skipped, the crashed one is attempted again.
    monkeypatch.setattr(autopilot_mod, "run_build", real_build)
    result = run_autopilot(root, root / "FDR.md", provider="mock", yes=True)
    assert result.status in ("completed", "failed")
    assert any("resumed:" in note for note in result.auto_approvals)
    rebuilt = [o for o in result.outcomes if o.task_id == outcomes[0]["task_id"]]
    assert rebuilt and rebuilt[0].status == "built"
    assert first_slug not in built_calls[2:], "a built task must not be rebuilt"


def test_a_stale_outcome_claiming_built_is_not_trusted(tmp_path):
    """outcomes.yaml is a record, not an authority: if the spec is not built
    on disk, the task is redone rather than skipped."""
    import yaml

    from ai_venture_studio.upstream.autopilot import _resume_outcomes

    root = _workspace(tmp_path, GOOD_FDR)
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(yaml.safe_dump([
        {"task_id": "t1", "title": "ghost", "status": "built", "detail": ""},
    ]), encoding="utf-8")
    assert _resume_outcomes(root) == []  # nothing on disk backs the claim
