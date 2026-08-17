"""ADR-047 and ADR-048: the founder describes, the system decomposes.

Three things are pinned here, and they are all failures of memory:

- the constitution — what the founder said NOT to build reaches every
  planner, comes out of a document they already wrote, and is never
  repealed by a later document staying silent about it;
- the roadmap — a paragraph becomes small ordered steps, and the
  remainder is re-read against what the product now promises rather than
  believed as written on day one;
- the baseline — a checkpoint freezes what was promised then, so a
  founder can be told what a build changed about their promises.

The invariants, not the instance: an unreadable answer is never an empty
plan, a step is never marked done by a check that did not run, and a cap
that drops work says so.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest
import yaml

from ai_venture_studio.upstream import constitution as con
from ai_venture_studio.upstream import requirements as req
from ai_venture_studio.upstream import roadmap as rm
from ai_venture_studio.upstream.requirements import Requirement

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _fdr(section_4: str, *, heading: str = "## 4. NOT needed for now") -> str:
    return (
        "# Product Requirements (FDR)\n\n"
        "## 3. Must-have features\n\n- anyone can cancel an order\n\n"
        f"{heading}\n\n{section_4}\n\n"
        "## 5. Constraints or preferences\n\n- runs in a browser\n"
    )


class _Provider:
    """A provider that answers with canned text and counts its calls."""

    def __init__(self, *answers: str, truncated: bool = False):
        self.answers = list(answers) or [""]
        self.truncated = truncated
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


@pytest.fixture
def provider(monkeypatch):
    """Install a fake provider for whichever module is under test."""

    def install(module, *answers: str, truncated: bool = False) -> _Provider:
        fake = _Provider(*answers, truncated=truncated)
        monkeypatch.setattr(module, "get_provider", lambda _name: fake)
        monkeypatch.setattr(module, "last_response_truncated", lambda: fake.truncated)
        return fake

    return install


def _steps_yaml(*rows: dict) -> str:
    return yaml.safe_dump({"steps": list(rows)}, sort_keys=False, allow_unicode=True)


def _spec(root: Path, slug: str, *criteria: str) -> None:
    """A built spec on disk.

    Tests here go through the spec rather than writing `requirements.yaml`
    directly, because everything that reads the ledger syncs it first — it
    is derived, never hand-maintained (ADR-045) — so a hand-written ledger
    is a file the next read overwrites.
    """
    path = root / "specs" / slug
    path.mkdir(parents=True, exist_ok=True)
    (path / "spec.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": slug, "built": True, "status": "approved",
                "criteria": list(criteria),
                "test_skeletons": [
                    {"path": f"tests/test_{slug}.py", "purpose": "covers",
                     "covers": list(range(len(criteria)))}
                ],
            },
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _ledger(root: Path, *texts: str) -> None:
    _spec(root, "orders", *texts)
    req.sync_ledger(root)


# --------------------------------------------------------------------------
# the constitution — ADR-047
# --------------------------------------------------------------------------


def test_an_unfilled_template_rules_nothing_out() -> None:
    """The shipped templates carry a parenthetical example in section 4, and
    `TEMPLATE_EN`'s wraps across two lines. Either one becoming an invariant
    would put "e.g. no logins yet" into the constitution of every product
    ever built from the template."""
    from ai_venture_studio.upstream.fdr import TEMPLATE, TEMPLATE_EN

    assert con.not_needed_lines(TEMPLATE) == []
    assert con.not_needed_lines(TEMPLATE_EN) == []


def test_the_founders_own_words_become_the_invariant() -> None:
    """Bullets and numbering are the document's, not the founder's; the
    rest of the line is theirs, brackets and all."""
    lines = con.not_needed_lines(
        _fdr("- no online payments (yet)\n2. no admin panel\n• no logins")
    )
    assert lines == ["no online payments (yet)", "no admin panel", "no logins"]


def test_saying_there_are_none_is_not_an_invariant_called_none() -> None:
    assert con.not_needed_lines(_fdr("无")) == []
    assert con.not_needed_lines(_fdr("none")) == []
    assert con.not_needed_lines(_fdr("")) == []


def test_the_chinese_heading_is_found_too() -> None:
    """`fdr.TEMPLATE` and `studio_chat._HEADINGS` write section 4 in two
    languages and three wordings; the section is found by its NUMBER so a
    rewording of the prose does not silently empty the constitution."""
    text = _fdr("暂时不需要在线支付", heading="## 4. 暂时不要的功能 / NOT needed for now")
    assert con.not_needed_lines(text) == ["暂时不需要在线支付"]


def test_section_five_is_not_section_four(tmp_path: Path) -> None:
    """Constraints are not prohibitions. Reading past the section boundary
    would turn "runs in a browser" into "do not build a browser"."""
    assert "runs in a browser" not in con.not_needed_lines(_fdr("- no logins"))


def test_a_feature_that_says_nothing_does_not_repeal_the_founding_document(
    tmp_path: Path,
) -> None:
    """The one derivation that would empty the constitution on the first
    feature: reading silence as repeal."""
    con.sync_constitution(tmp_path, _fdr("- no online payments"), "FDR.md")
    con.sync_constitution(tmp_path, "a feature with no section 4 at all", "f1/fdr.md")

    live = con.live(tmp_path)
    assert [inv.text for inv in live] == ["no online payments"]
    assert live[0].origin == "FDR.md"


def test_removing_a_line_from_its_own_fdr_withdraws_it(tmp_path: Path) -> None:
    con.sync_constitution(tmp_path, _fdr("- no logins\n- no payments"), "FDR.md")
    sync = con.sync_constitution(tmp_path, _fdr("- no logins"), "FDR.md")

    assert sync.withdrawn == ["C-002"]
    withdrawn = [i for i in con.load_constitution(tmp_path) if i.status == "withdrawn"]
    assert withdrawn[0].text == "no payments"
    # Withdrawn, not deleted: the founder changed their mind, and which
    # document they changed it in is the record.
    assert "FDR.md" in withdrawn[0].withdrawn_note


def test_an_id_is_never_handed_to_a_different_invariant(tmp_path: Path) -> None:
    con.sync_constitution(tmp_path, _fdr("- no logins\n- no payments"), "FDR.md")
    con.sync_constitution(tmp_path, _fdr("- no logins"), "FDR.md")
    con.sync_constitution(tmp_path, _fdr("- no logins\n- no exports"), "FDR.md")

    by_id = {inv.id: inv.text for inv in con.load_constitution(tmp_path)}
    assert by_id["C-002"] == "no payments"
    assert by_id["C-003"] == "no exports"


def test_writing_it_again_brings_it_back(tmp_path: Path) -> None:
    con.sync_constitution(tmp_path, _fdr("- no payments"), "FDR.md")
    con.sync_constitution(tmp_path, _fdr(""), "FDR.md")
    con.sync_constitution(tmp_path, _fdr("- no payments"), "FDR.md")

    live = con.live(tmp_path)
    assert [(inv.id, inv.text) for inv in live] == [("C-001", "no payments")]
    assert live[0].withdrawn_note == ""


def test_syncing_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    """It runs on every build; a file that churns on every build is a diff
    the founder has to read and a merge conflict waiting to happen."""
    text = _fdr("- no logins")
    con.sync_constitution(tmp_path, text, "FDR.md")
    before = con.constitution_path(tmp_path).read_text(encoding="utf-8")
    again = con.sync_constitution(tmp_path, text, "FDR.md")

    assert not again.changed
    assert con.constitution_path(tmp_path).read_text(encoding="utf-8") == before


def test_nothing_ruled_out_says_so_rather_than_showing_an_empty_list() -> None:
    assert "not ruled anything out" in con.render_for_planner([])


def test_the_render_shows_every_invariant_and_names_what_it_dropped() -> None:
    """No retrieval step, on purpose: a "do not build this" list is short and
    every line applies to every plan, so slicing it by keyword overlap would
    hide the one the request is about to violate. The cap is a backstop and
    it announces itself (ADR-039)."""
    many = [
        con.Invariant(id=f"C-{i:03d}", text=f"no thing {i}", origin="FDR.md")
        for i in range(1, 31)
    ]
    rendered = con.render_for_planner(many, cap=5)

    assert "C-005" in rendered and "C-006" not in rendered
    assert "5 of 30 shown" in rendered
    assert "they still apply" in rendered


def test_both_planners_are_shown_what_the_founder_ruled_out() -> None:
    """The wiring, not the module: a constitution nothing reads is the state
    section 4 was already in before ADR-047."""
    from ai_venture_studio.upstream import autopilot, plan

    for source, system in (
        (inspect.getsource(plan.run_planning), plan.planner_system()),
        (inspect.getsource(autopilot.run_feature), autopilot._FEATURE_PLANNER_SYSTEM),
    ):
        assert "<ruled_out>" in source
        assert "sync_constitution" in source
        assert "<ruled_out>" in system


def test_the_newest_request_wins_over_the_constitution() -> None:
    """A founder who asks for a thing they once ruled out has changed their
    mind. The planner is told that explicitly — a boundary that refuses the
    person who drew it is a bug, and this file is not a gate."""
    from ai_venture_studio.upstream.autopilot import _FEATURE_PLANNER_SYSTEM

    assert "the request wins" in _FEATURE_PLANNER_SYSTEM
    assert "changed their mind" in _FEATURE_PLANNER_SYSTEM


# --------------------------------------------------------------------------
# the roadmap — ADR-048
# --------------------------------------------------------------------------


def test_a_paragraph_becomes_ordered_steps(provider) -> None:
    provider(
        rm,
        _steps_yaml(
            {"id": "S-001", "title": "start a group buy", "fdr": "the organiser opens one",
             "depends_on": []},
            {"id": "S-002", "title": "order", "fdr": "a resident orders", "depends_on": ["S-001"]},
        ),
    )
    plan = rm.propose("a group-buy thing for my building", provider="x")

    assert plan.checked
    assert [s.id for s in plan.steps] == ["S-001", "S-002"]
    assert plan.steps[1].depends_on == ["S-001"]
    assert all(s.status == "pending" for s in plan.steps)
    assert plan.described.startswith("a group-buy")


def test_output_that_does_not_parse_is_not_an_empty_plan(provider) -> None:
    """`checked=False` with no steps says "nobody produced a plan".
    `checked=True` with no steps would say "this product needs nothing
    built", which is never true and is exactly what a parse failure looks
    like from the outside."""
    provider(rm, "I'd be happy to help you plan this product!")
    plan = rm.propose("a thing", provider="x")

    assert plan.checked is False
    assert plan.steps == []
    assert "did not parse" in plan.note


def test_a_roadmap_cut_off_mid_list_is_not_a_plan(provider) -> None:
    provider(
        rm,
        _steps_yaml({"id": "S-001", "title": "a", "fdr": "a", "depends_on": []}),
        truncated=True,
    )
    plan = rm.propose("a thing", provider="x")

    assert plan.checked is False
    assert "cut off" in plan.note


def test_describing_nothing_calls_no_model(provider) -> None:
    fake = provider(rm, _steps_yaml({"id": "S-001", "title": "a", "fdr": "a"}))
    plan = rm.propose("   ", provider="x")

    assert plan.checked is False
    assert fake.calls == []


def test_a_dependency_loop_is_refused_rather_than_ordered(provider) -> None:
    provider(
        rm,
        _steps_yaml(
            {"id": "S-001", "title": "a", "fdr": "a", "depends_on": ["S-002"]},
            {"id": "S-002", "title": "b", "fdr": "b", "depends_on": ["S-001"]},
        ),
    )
    plan = rm.propose("a thing", provider="x")

    assert plan.checked is False
    assert "loop" in plan.note


def test_a_prerequisite_listed_second_is_reordered_not_refused(provider) -> None:
    """Order is the one part of a proposal code can fix without guessing."""
    provider(
        rm,
        _steps_yaml(
            {"id": "S-001", "title": "needs the other", "fdr": "a", "depends_on": ["S-002"]},
            {"id": "S-002", "title": "the other", "fdr": "b", "depends_on": []},
        ),
    )
    plan = rm.propose("a thing", provider="x")

    assert plan.checked is True
    assert [s.id for s in plan.steps] == ["S-002", "S-001"]


def test_an_edge_to_a_step_that_does_not_exist_is_dropped(provider) -> None:
    """Otherwise `next_step` waits forever for a step that was never
    proposed, and the founder is handed a roadmap with no next move."""
    provider(
        rm,
        _steps_yaml(
            {"id": "S-001", "title": "a", "fdr": "a", "depends_on": ["S-099"]},
        ),
    )
    plan = rm.propose("a thing", provider="x")

    assert plan.checked is True
    assert plan.steps[0].depends_on == []
    assert rm.next_step(plan) is not None


def test_a_step_with_no_request_in_it_is_not_a_step(provider) -> None:
    """`avs add` cannot be handed a heading."""
    provider(
        rm,
        _steps_yaml(
            {"id": "S-001", "title": "Phase one", "fdr": "", "depends_on": []},
            {"id": "S-002", "title": "order", "fdr": "a resident orders", "depends_on": []},
        ),
    )
    plan = rm.propose("a thing", provider="x")

    assert [s.title for s in plan.steps] == ["order"]


def test_a_backlog_is_cut_to_a_roadmap(provider) -> None:
    provider(
        rm,
        _steps_yaml(
            *[
                {"id": f"S-{i:03d}", "title": f"step {i}", "fdr": f"do {i}", "depends_on": []}
                for i in range(1, 40)
            ]
        ),
    )
    plan = rm.propose("a thing", provider="x")

    assert len(plan.steps) == rm.MAX_STEPS


def test_the_next_step_waits_for_its_prerequisite() -> None:
    plan = rm.Roadmap(
        checked=True,
        steps=[
            rm.Step(id="S-001", title="a", fdr="a", status="pending"),
            rm.Step(id="S-002", title="b", fdr="b", depends_on=["S-001"]),
        ],
    )
    assert rm.next_step(plan).id == "S-001"

    plan.steps[0].status = "done"
    assert rm.next_step(plan).id == "S-002"

    plan.steps[1].status = "done"
    assert rm.next_step(plan) is None


# --------------------------------------------------------------------------
# re-derivation: the roadmap is a proposal, not a contract
# --------------------------------------------------------------------------


def _two_steps() -> rm.Roadmap:
    return rm.Roadmap(
        checked=True,
        steps=[
            rm.Step(id="S-001", title="cancel", fdr="a resident cancels their order"),
            rm.Step(id="S-002", title="rate", fdr="a resident rates the organiser"),
        ],
    )


def test_a_step_the_product_already_promises_is_marked_done(tmp_path, monkeypatch) -> None:
    from ai_venture_studio.upstream import reconcile as rec

    _ledger(tmp_path, "a resident shall cancel their own order")
    monkeypatch.setattr(
        rec, "reconcile",
        lambda text, slice_, **_kw: rec.Reconciliation(
            checked=True,
            relations=[rec.Relation(requirement_id=r.id, relation="duplicate",
                                    reason="same promise")
                       for r in slice_.shown],
        ),
    )
    plan = _two_steps()
    report = rm.rederive(tmp_path, plan, provider="x")

    assert "S-001" in report.marked_done
    assert plan.steps[0].status == "done"
    assert "R-001" in plan.steps[0].note


def test_a_step_the_reconciler_could_not_read_stays_pending_and_says_so(
    tmp_path, monkeypatch
) -> None:
    """Better to build a step twice than to have it silently vanish from
    the founder's plan because a check failed to run."""
    from ai_venture_studio.upstream import reconcile as rec

    _ledger(tmp_path, "a resident shall cancel their own order")
    monkeypatch.setattr(
        rec, "reconcile",
        lambda *_a, **_kw: rec.Reconciliation(checked=False, note="did not parse"),
    )
    plan = _two_steps()
    report = rm.rederive(tmp_path, plan, provider="x")

    assert report.marked_done == []
    assert "S-001" in report.unchecked
    assert plan.steps[0].status == "pending"


def test_a_step_nothing_matched_costs_no_model_call(tmp_path, monkeypatch) -> None:
    """Retrieval is deterministic and free; an empty ledger must not buy a
    round-trip per step to be told there is nothing to compare against."""
    from ai_venture_studio.upstream import reconcile as rec

    calls = []
    monkeypatch.setattr(rec, "reconcile", lambda *a, **k: calls.append(a) or None)
    plan = _two_steps()
    report = rm.rederive(tmp_path, plan, provider="x")

    assert calls == []
    assert report.still_pending == ["S-001", "S-002"]


def test_steps_past_the_cap_are_reported_unchecked_not_pending(
    tmp_path, monkeypatch
) -> None:
    from ai_venture_studio.upstream import reconcile as rec

    _ledger(tmp_path, "a resident shall cancel their own order")
    monkeypatch.setattr(
        rec, "reconcile",
        lambda *_a, **_kw: rec.Reconciliation(checked=True, relations=[]),
    )
    plan = _two_steps()
    report = rm.rederive(tmp_path, plan, provider="x", cap=1)

    assert report.unchecked == ["S-002"]
    assert report.still_pending == ["S-001"]


def test_a_roadmap_survives_a_round_trip(tmp_path) -> None:
    plan = _two_steps()
    plan.steps[0].status = "done"
    rm.save(tmp_path, plan)

    loaded = rm.load(tmp_path)
    assert [s.id for s in loaded.done] == ["S-001"]
    assert [s.id for s in loaded.pending] == ["S-002"]


# --------------------------------------------------------------------------
# the baseline — what a build changed about the promises
# --------------------------------------------------------------------------


def test_a_checkpoint_freezes_what_the_product_promised(tmp_path) -> None:
    from ai_venture_studio.upstream.autopilot import tag_checkpoint

    root = tmp_path / "prod"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=60)
    _ledger(root, "a resident shall cancel their own order")

    tag = tag_checkpoint(root)
    assert req.load_baseline(root, tag) == {"R-001": "built"}


def test_since_names_what_was_added_and_what_was_superseded(tmp_path) -> None:
    _ledger(tmp_path, "cancel an order")
    req.write_baseline(tmp_path, "ap-checkpoint-001")

    ledger = req.load_ledger(tmp_path)
    ledger[0].status = "superseded"
    ledger.append(
        Requirement(id="R-002", text="rate the organiser", spec_slug="ratings",
                    status="built")
    )
    ledger.append(
        Requirement(id="R-003", text="an old idea", spec_slug="ratings", status="retired")
    )
    req.save_ledger(tmp_path, ledger)

    delta = req.since(tmp_path, "ap-checkpoint-001")
    assert delta.added == ["R-002"]
    assert delta.superseded == ["R-001"]
    # R-003 arrived already retired: it was never a promise this product
    # made, so counting it as one it LOST would be a delta about nothing.
    assert delta.retired == []


def test_a_checkpoint_with_no_baseline_reports_nothing_not_no_change(tmp_path) -> None:
    """Every checkpoint tagged before baselines existed is one of these.
    "+0 since checkpoint 2" would be a measurement of the missing file."""
    _ledger(tmp_path, "cancel an order")
    assert req.since(tmp_path, "ap-checkpoint-001") is None


def test_a_baseline_that_will_not_write_does_not_cost_the_checkpoint(
    tmp_path, monkeypatch
) -> None:
    """A checkpoint is the founder's undo. Losing it because a bookkeeping
    file would not write trades the important guarantee for the small one."""
    from ai_venture_studio.upstream import autopilot

    root = tmp_path / "prod"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=60)
    monkeypatch.setattr(
        req, "write_baseline",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("read-only")),
    )

    assert autopilot.tag_checkpoint(root) == "ap-checkpoint-001"


# --------------------------------------------------------------------------
# what the founder actually types
# --------------------------------------------------------------------------


def test_the_founder_gets_the_next_step_as_a_file_add_can_take(tmp_path) -> None:
    """The whole loop is two commands, neither of them a document: run
    `avs roadmap`, then `avs add FDR-NEXT.md --yes`, and repeat."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import NEXT_FDR, app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["roadmap", "团长发起接龙。住户下单。团长看汇总。",
         "--repo-dir", str(tmp_path), "--provider", "mock"],
    )

    assert result.exit_code == 0, result.output
    assert "0/3 built" in result.output
    assert f"avs add {NEXT_FDR} --yes" in result.output

    saved = rm.load(tmp_path)
    assert [s.id for s in saved.steps] == ["S-001", "S-002", "S-003"]
    handed = (tmp_path / NEXT_FDR).read_text(encoding="utf-8")
    assert saved.steps[0].fdr in handed
    assert "S-001" in handed


def test_re_describing_the_product_does_not_un_build_it(
    tmp_path, monkeypatch
) -> None:
    """A founder who re-describes their product halfway through has not
    un-built anything. A fresh proposal that reported 0/3 would walk them
    back into work `avs add` would then correctly refuse."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app
    from ai_venture_studio.upstream import reconcile as rec

    _spec(tmp_path, "orders", "团长发起接龙")
    monkeypatch.setattr(
        rec, "reconcile",
        # Only the step whose words ARE the promise: retrieval is a net,
        # and a stub that called every retrieved candidate a duplicate
        # would be asserting the net rather than the verdict.
        lambda text, slice_, **_kw: rec.Reconciliation(
            checked=True,
            relations=[rec.Relation(requirement_id=r.id, relation="duplicate",
                                    reason="already there")
                       for r in slice_.shown if r.text == text],
        ),
    )
    result = CliRunner().invoke(
        app,
        ["roadmap", "团长发起接龙。住户下单。团长看汇总。",
         "--repo-dir", str(tmp_path), "--provider", "mock"],
    )

    assert result.exit_code == 0, result.output
    assert "1/3 built" in result.output
    assert "already built: S-001" in result.output
    assert rm.load(tmp_path).steps[0].status == "done"


def test_asking_for_a_roadmap_before_there_is_one_says_how_to_make_one(
    tmp_path,
) -> None:
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    result = CliRunner().invoke(app, ["roadmap", "--repo-dir", str(tmp_path)])

    assert result.exit_code == 2
    assert "avs roadmap" in result.output


def test_the_requirements_view_names_the_promise_and_what_checks_it(tmp_path) -> None:
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    _spec(tmp_path, "orders", "a resident shall cancel their own order")
    result = CliRunner().invoke(app, ["requirements", "--repo-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "R-001" in result.output
    assert "cancel their own order" in result.output
    assert "tests/test_orders.py" in result.output


def test_a_product_that_promises_nothing_says_so(tmp_path) -> None:
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    result = CliRunner().invoke(app, ["requirements", "--repo-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "promises nothing yet" in result.output


def test_a_superseded_promise_is_hidden_until_it_is_asked_for(tmp_path) -> None:
    """The default view is what the product promises NOW; a founder reading
    a list of live promises must not find a replaced one among them."""
    from typer.testing import CliRunner

    from ai_venture_studio.cli import app

    _spec(tmp_path, "orders", "a resident shall cancel their own order")
    req.save_ledger(
        tmp_path,
        [
            Requirement(id="R-001", text="an organiser shall close a group buy",
                        spec_slug="orders", status="superseded",
                        superseded_by="product/features/f1/fdr.md"),
        ],
    )

    runner = CliRunner()
    hidden = runner.invoke(app, ["requirements", "--repo-dir", str(tmp_path)])
    assert hidden.exit_code == 0, hidden.output
    assert "R-001" not in hidden.output
    assert "R-002" in hidden.output

    shown = runner.invoke(app, ["requirements", "--repo-dir", str(tmp_path), "--all"])
    assert "R-001" in shown.output
    assert "replaced by product/features/f1/fdr.md" in shown.output
