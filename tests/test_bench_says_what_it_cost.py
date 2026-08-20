"""The bench reports what it cost, not only how long it took (ADR-057).

Every product-bench result since the first one has recorded `duration_s` and
nothing about money. The question that actually gates a rerun — "what will
this cost me" — was answerable only by opening the provider console and
guessing which charges belonged to which run.

The data was never missing. `spend.record()` has been metering every provider
call for many releases, `autopilot` flushes the ledger into the workspace root,
and `product_bench` then deletes that workspace in a `finally`. The number was
written to disk and thrown away, once per case, for the whole life of the
bench.

These tests pin the four things that make the answer honest rather than
merely present:

  1. unpriced is not zero (ADR-053 applied to money)
  2. partially priced announces itself as a FLOOR
  3. a resumed row contributes nothing — its cost belongs to the run that paid
  4. prices come from the OPERATOR's repo, not the throwaway case workspace
"""

from __future__ import annotations

from pathlib import Path

import yaml

import ai_venture_studio.product_bench as pb
from ai_venture_studio.observability import CostModel

# A price table with a single model in it. Deliberately not the real one:
# `prices.yaml` rots by design, and a test that asserted today's list price
# would fail the week Anthropic changed it, for no defect.
CASES = Path(__file__).parent.parent / "benchmarks" / "products"

PRICES = CostModel(prices={"model-a": {"input": 3.0, "output": 15.0}})


def _entries(root, rows):
    """Write a ledger the way a real case would have left one behind."""
    from ai_venture_studio import spend

    for model, in_tok, out_tok in rows:
        spend.record(model, in_tok, out_tok, stage="build")
    spend.flush(root)


def _drain_buffer():
    """The spend buffer is module-global; a leftover row would be attributed
    to whichever workspace flushed next. Real runs flush per task, so this
    only matters between tests."""
    from ai_venture_studio import spend

    with spend._lock:  # noqa: SLF001 — test hygiene on a module global
        spend._buffer.clear()  # noqa: SLF001


def test_a_case_records_what_it_spent_before_its_workspace_is_deleted(tmp_path):
    _drain_buffer()
    workspace = tmp_path / "case"
    workspace.mkdir()
    _entries(workspace, [("model-a", 1_000_000, 100_000)])

    spend = pb._case_spend(workspace, PRICES)

    assert spend is not None
    assert spend.calls == 1
    assert spend.input_tokens == 1_000_000
    assert spend.output_tokens == 100_000
    # 1M in at $3 + 0.1M out at $15 = 3.00 + 1.50
    assert spend.usd == 4.5
    assert not spend.is_floor


def test_an_unpriced_run_reports_no_dollars_rather_than_zero_dollars(tmp_path):
    """ADR-053's rule, applied to money.

    A run nobody could price and a run that cost nothing are different facts,
    and the difference is the whole reason someone opened the file. `$0.00`
    would answer the question wrongly and confidently.
    """
    _drain_buffer()
    workspace = tmp_path / "case"
    workspace.mkdir()
    _entries(workspace, [("model-unknown", 500, 500)])

    spend = pb._case_spend(workspace, CostModel())

    assert spend is not None
    assert spend.calls == 1
    assert spend.input_tokens == 500  # the counts are exact
    assert spend.usd is None  # ...only the price is missing
    assert "unpriced" in pb.render_spend(spend)
    assert "$0.00" not in pb.render_spend(spend)


def test_a_partly_priced_run_says_it_is_a_floor(tmp_path):
    _drain_buffer()
    workspace = tmp_path / "case"
    workspace.mkdir()
    _entries(
        workspace,
        [("model-a", 1_000_000, 0), ("model-unknown", 1_000_000, 1_000_000)],
    )

    spend = pb._case_spend(workspace, PRICES)

    assert spend is not None
    assert spend.usd == 3.0  # only the priced call
    assert spend.unpriced_calls == 1
    assert spend.is_floor
    line = pb.render_spend(spend)
    assert "FLOOR" in line
    assert "≥$3.00" in line


def test_prices_come_from_the_operators_repo_not_the_case_workspace(tmp_path):
    """The trap this design walked into once.

    The token LEDGER lives in the case's throwaway workspace; the PRICE TABLE
    lives in the repo the bench was invoked from. Pricing a case against its
    own `.mas/` reports every call unpriced forever, because a directory
    created by `mkdtemp` seconds ago has never held an operator's prices and
    never will.
    """
    repo = tmp_path / "repo"
    (repo / ".mas").mkdir(parents=True)
    (repo / ".mas" / "cost-model.yaml").write_text(
        yaml.safe_dump({"prices": {"model-a": {"input": 3.0, "output": 15.0}}}),
        encoding="utf-8",
    )
    assert pb.bench_cost_model(repo).prices["model-a"]["input"] == 3.0
    # A workspace that has never seen a price table prices nothing — which is
    # exactly what every case workspace looks like.
    assert pb.bench_cost_model(tmp_path / "fresh").prices == {}


def test_a_metering_failure_never_fails_a_case_that_ran(tmp_path):
    """Cost is a reading ABOUT the run; it must not be able to change it."""
    _drain_buffer()
    assert pb._case_spend(None, PRICES) is None
    # A workspace with no ledger at all: nothing was metered, which is not
    # the same as nothing was spent, so it reports None rather than a zero.
    assert pb._case_spend(tmp_path, PRICES) is None
    # No label of its own — the caller supplies one, and a `render_spend`
    # that carried the word "cost" printed `cost cost not metered`.
    assert pb.render_spend(None) == "not metered"


def _summary(**kw):
    """The three headline rates are required fields; none of these tests is
    about them."""
    kw.setdefault("build_rate", 1.0)
    kw.setdefault("probe_pass_rate", 1.0)
    kw.setdefault("clean_review_rate", 1.0)
    return pb.BenchSummary(**kw)


def _result(name, *, spend=None, resumed=False):
    return pb.CaseResult(
        name=name,
        autopilot_status="done",
        tasks_total=1,
        tasks_built=1,
        clean_reviews=1,
        spend=spend,
        resumed=resumed,
    )


def test_a_resumed_row_contributes_nothing_to_the_runs_total(monkeypatch, tmp_path):
    """Its cost was paid by the run that measured it.

    A `--resume` run that re-counted banked rows would inflate every total in
    the series, and the inflation grows with how many times a flaky run was
    resumed — which is exactly when someone is looking at the cost.

    Driven through `run_product_bench` rather than by re-summing the rows
    here: a test that restates the production arithmetic passes whether or
    not the production arithmetic is wired in.
    """
    paid = pb.CaseSpend(calls=2, input_tokens=10, output_tokens=5, usd=1.25)
    banked = pb.CaseSpend(calls=99, input_tokens=999, output_tokens=999, usd=100.0)
    rows = iter([_result("a", spend=paid), _result("b", spend=banked, resumed=True)])
    monkeypatch.setattr(pb, "run_case", lambda case, provider=None, **_: next(rows))

    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)

    assert summary.spend is not None
    assert summary.spend.calls == 2
    assert summary.spend.usd == 1.25


def test_a_run_that_metered_nothing_has_no_total_rather_than_a_zero(
    monkeypatch, tmp_path
):
    """The same distinction one level up. A mock run meters nothing at all,
    and `$0.00` for it would be a number the series could compare against a
    real one."""
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: _result("a")
    )
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.spend is None


def test_the_saved_result_carries_the_cost_and_its_caveat(tmp_path):
    """Outside `rates`, and with the caveat attached.

    Outside because cost is not a rate and `bench_criterion` reads that block.
    With the caveat because the person opening this file in six months is
    exactly the person who cannot go back and ask whether the price table was
    complete.
    """
    summary = _summary(
        cases=[_result("a")],
        spend=pb.CaseSpend(calls=3, input_tokens=10, output_tokens=5, usd=None,
                           unpriced_calls=3),
    )
    path = pb.save_summary(summary, tmp_path, provider="mock")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "cost" not in payload["rates"]
    assert payload["cost"]["calls"] == 3
    assert payload["cost"]["usd"] is None
    assert "not a cost of zero" in payload["cost"]["note"].lower()


def test_a_floor_total_says_so_in_the_file_too(tmp_path):
    summary = _summary(
        cases=[_result("a")],
        spend=pb.CaseSpend(calls=4, input_tokens=10, output_tokens=5, usd=2.0,
                           unpriced_calls=1),
    )
    payload = yaml.safe_load(
        pb.save_summary(summary, tmp_path, provider="mock").read_text(encoding="utf-8")
    )
    assert payload["cost"]["usd"] == 2.0
    assert "FLOOR" in payload["cost"]["note"]
    assert "1 of 4" in payload["cost"]["note"]


def test_the_alert_carries_the_cost(tmp_path):
    """The scheduler's run finishes at 3am and the alert is the whole record."""
    from ai_venture_studio.notify import bench_alert

    summary = _summary(
        cases=[_result("a")],
        cases_total=1,
        spend=pb.CaseSpend(calls=3, input_tokens=1_000_000, output_tokens=0, usd=3.0),
    )
    alert = bench_alert(summary, workspace="w", saved="s")
    assert "$3.00" in alert.render()
