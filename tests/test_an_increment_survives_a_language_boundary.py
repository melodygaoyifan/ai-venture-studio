"""Bench run 19, case 05: three ways an increment died that were not the
product's fault.

The ledger is written in English EARS form by the spec pipeline; the
founder's follow-ups were Chinese. `relevant()` scores by content-word
overlap, so every score was zero, the reconciler was handed an EMPTY slice,
and the gate went silently inert (`checked: false`) — a feature that
DIRECTLY contradicted a live promise ("expose no route that deletes a
repair") was built without a murmur and deleted the promise's own test on
the way. ADR-048's inert-gate shape one layer up: ADR-050 fixed CJK-vs-CJK
matching; cross-language was still blind.

Two more deaths in the same case: a planner that examined the code and
correctly planned ZERO tasks for a reworded duplicate was reported as
`failed` (empty outcomes fell through the completion check), and the third
increment stopped at intake with three questions under `--yes` — a run
nobody is watching parked forever on questions nobody was there to answer.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_venture_studio.upstream import requirements as req
from ai_venture_studio.upstream.requirements import Requirement

ENGLISH_LEDGER = [
    Requirement(
        id="R-001",
        text="The system shall let a resident submit a repair request.",
        spec_slug="repairs", status="built",
        verified_by=["tests/test_repairs.py"],
    ),
    Requirement(
        id="R-002",
        text="The system shall expose no route that deletes a repair.",
        spec_slug="repairs", status="built",
        verified_by=["tests/test_no_delete.py"],
    ),
]

CHINESE_REQUEST = "住户应该能自己删掉自己发的报修单。必须有：删除入口。"


def test_a_request_in_another_language_still_sees_the_ledger(tmp_path: Path):
    """The run-19 defect, minimally: zero lexical overlap must degrade
    toward MORE context, not an empty slice."""
    req.save_ledger(tmp_path, ENGLISH_LEDGER)
    slice_ = req.relevant(tmp_path, CHINESE_REQUEST)
    assert slice_.fallback is True
    assert [r.id for r in slice_.shown] == ["R-001", "R-002"], (
        "the whole live ledger is the slice when retrieval could not rank"
    )
    rendered = req.render_slice(slice_)
    assert "unranked" in rendered
    assert "The system shall expose no route that deletes a repair." in rendered


def test_a_ranked_match_never_triggers_the_fallback(tmp_path: Path):
    req.save_ledger(tmp_path, ENGLISH_LEDGER)
    slice_ = req.relevant(tmp_path, "let a resident delete a repair request")
    assert slice_.fallback is False
    assert slice_.shown, "same-language retrieval still ranks"
    assert "unranked" not in req.render_slice(slice_)


def test_an_empty_ledger_stays_an_empty_slice(tmp_path: Path):
    """The fallback shows what EXISTS; a first FDR has nothing to show and
    the '(no existing requirement matched)' contract must hold."""
    slice_ = req.relevant(tmp_path, CHINESE_REQUEST)
    assert slice_.fallback is False
    assert not slice_.shown


def test_the_fallback_never_resurrects_a_retired_promise(tmp_path: Path):
    retired = [
        Requirement(id="R-001", text="The system shall do the old thing.",
                    spec_slug="s", status="superseded"),
    ]
    req.save_ledger(tmp_path, retired)
    assert not req.relevant(tmp_path, CHINESE_REQUEST).shown


def test_a_ledger_past_the_bound_says_what_it_dropped(tmp_path: Path):
    """ADR-039: a bound that drops work says so — even in the fallback."""
    big = [
        Requirement(id=f"R-{i + 1:03d}",
                    text="The system shall keep an English promise.",
                    spec_slug="s", status="built")
        for i in range(req._FALLBACK_CAP + 3)
    ]
    req.save_ledger(tmp_path, big)
    slice_ = req.relevant(tmp_path, CHINESE_REQUEST)
    assert slice_.fallback is True
    assert len(slice_.shown) == req._FALLBACK_CAP
    assert slice_.dropped == 3
    assert "were not" in req.render_slice(slice_)


# --- the whole path, run 19's exact shape ------------------------------------


def _init_product(tmp_path: Path):
    from ai_venture_studio.upstream.workspace import init_workspace

    root = init_workspace(tmp_path / "prod", "prod", "web")
    spec_dir = root / "specs" / "repairs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.yaml").write_text(
        yaml.safe_dump({
            "slug": "repairs", "title": "repairs", "status": "built",
            "request": "repairs", "profile": "web", "design": "",
            "criteria": [
                "The system shall let a resident submit a repair request.",
                "The system shall expose no route that deletes a repair.",
            ],
            "test_skeletons": [
                {"path": "tests/test_repairs.py", "purpose": "p", "covers": [0]},
                {"path": "tests/test_no_delete.py", "purpose": "p", "covers": [1]},
            ],
            "built": True,
        }, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    req.sync_ledger(root)
    return root


def test_a_cross_language_duplicate_is_refused_not_rebuilt(tmp_path, monkeypatch):
    """Run 19's miss, end to end: a Chinese request against an English
    ledger must still reach the reconciler with the promises in hand."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream.autopilot import run_feature

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = _init_product(tmp_path)
    fdr = root / "feature.md"
    fdr.write_text(
        f"MOCK_DUPLICATE {CHINESE_REQUEST}\n", encoding="utf-8"
    )
    # Precondition — this really is the blind spot: nothing lexical matches.
    assert req.relevant(root, fdr.read_text(encoding="utf-8")).fallback is True

    result = run_feature(root, fdr, provider="mock", yes=True)

    assert result.status == "already_satisfied"
    assert not result.outcomes
    feature_dir = sorted((root / "product" / "features").iterdir())[-1]
    saved = yaml.safe_load((feature_dir / "reconciliation.yaml").read_text())
    assert saved["checked"] is True, (
        "run 19 recorded checked: false here — the gate never looked"
    )


def test_a_zero_task_plan_is_already_satisfied_not_failed(tmp_path, monkeypatch):
    """Increment 0's death: the planner examined the code, found nothing to
    build, planned zero tasks — and the empty outcome list was reported as
    `failed`. 'Nothing to do' is not a failure."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream.autopilot import run_feature

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = _init_product(tmp_path)
    fdr = root / "feature.md"
    fdr.write_text(
        "MOCK_NO_TASKS 住户可以通过报修入口提交家中损坏的物品。\n", encoding="utf-8"
    )
    result = run_feature(root, fdr, provider="mock", yes=True)

    assert result.status == "already_satisfied"
    assert not result.outcomes
    feature_dir = sorted((root / "product" / "features").iterdir())[-1]
    assert yaml.safe_load((feature_dir / "plan.yaml").read_text()) == []
    report = (feature_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "not built" in report


def test_yes_records_the_intake_questions_and_proceeds(tmp_path, monkeypatch):
    """Increment 3's death: `--yes` is an unattended run, and a run that
    stops to ask questions nobody is there to answer is parked forever.
    The questions are still RECORDED — visibly, with the fact that the
    build went ahead — so the founder can answer and re-run."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream.autopilot import run_feature

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = _init_product(tmp_path)
    fdr = root / "feature.md"
    # "just an idea" flips the mock assessor to not-ready with questions;
    # MOCK_NO_TASKS keeps the rest of the path cheap.
    fdr.write_text("just an idea MOCK_NO_TASKS 打个分\n", encoding="utf-8")

    result = run_feature(root, fdr, provider="mock", yes=True)

    assert result.status != "needs_answers", "an unattended run must not park"
    questions = (root / "FDR-QUESTIONS.md").read_text(encoding="utf-8")
    assert "--yes" in questions, "the record must say why nobody was asked"
    assert "谁会用它" in questions, "the questions themselves are kept"


def test_without_yes_intake_still_stops_to_ask(tmp_path, monkeypatch):
    """The strict behaviour is unchanged when a person IS there to answer."""
    import ai_venture_studio.upstream.build as build_mod
    from ai_venture_studio import testing as testing_mod
    from ai_venture_studio.upstream.autopilot import run_feature

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    monkeypatch.setattr(build_mod, "docker_available", lambda: False)

    root = _init_product(tmp_path)
    fdr = root / "feature.md"
    fdr.write_text("just an idea 打个分\n", encoding="utf-8")

    result = run_feature(root, fdr, provider="mock", yes=False)

    assert result.status == "needs_answers"
    assert "请回答这些问题" in (root / "FDR-QUESTIONS.md").read_text(encoding="utf-8")
