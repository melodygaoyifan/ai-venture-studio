"""`--only` — the named-case slice — under ADR-066's rules (ADR-078).

`--limit` can only buy a prefix of the sorted suite. The run-19 debug needed
exactly cases 03/04/05 — the cases the fixes touch — without paying for
01/02 again, and the only way to reach them was to pay for the whole suite.
`--only CASE[,CASE]` cuts the purchase by name instead of by count, and it
inherits every honesty rule the counted slice earned: whole-suite
denominator, named skip rows, out of the tracked ledger, refused by the
criterion if a file arrives there anyway.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

import ai_venture_studio.product_bench as pb
from ai_venture_studio.bench_criterion import evaluate, truncated_runs

CASES = "benchmarks/products"
NAMES = sorted(p.stem for p in pathlib.Path(CASES).glob("*.yaml"))


def _completed(case, provider=None, **_):
    return pb.CaseResult(
        name=case.name,
        autopilot_status="completed",
        tasks_total=2,
        tasks_built=2,
        clean_reviews=1,
        probes=[pb.ProbeResult(name="p", passed=True)],
        axis=case.axis,
    )


@pytest.fixture
def bench(monkeypatch):
    monkeypatch.setattr(pb, "run_case", _completed)


def _write(summary, tmp_path):
    (tmp_path / "benchmarks" / "results").mkdir(parents=True, exist_ok=True)
    return pb.save_summary(summary, tmp_path, provider="anthropic")


def test_only_asks_exactly_the_named_cases(bench, tmp_path):
    summary = pb.run_product_bench(CASES, only=[NAMES[2]], repo_dir=tmp_path)
    assert summary.cases_measured + summary.gate_cases_measured == 1
    assert summary.cases_total + summary.gate_cases_total == len(NAMES), (
        "the named slice shrank the denominator — ADR-066's defect by name"
    )
    skipped = [c for c in summary.cases
               if c.autopilot_status.startswith(pb._ONLY_SKIP)]
    assert len(skipped) == len(NAMES) - 1
    assert all(not c.measured for c in skipped)
    assert summary.truncated is True
    assert summary.only_cases == [NAMES[2]]


def test_only_naming_the_whole_suite_is_a_complete_reading(bench, tmp_path):
    """`truncated` reads the rows, not the flag — same rule as `--limit 6`
    over six cases."""
    summary = pb.run_product_bench(CASES, only=list(NAMES), repo_dir=tmp_path)
    assert summary.truncated is False
    saved = _write(summary, tmp_path)
    data = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert "only_cases" not in data
    assert (tmp_path / "benchmarks" / "results" / saved.name).exists()


def test_a_typoed_only_is_refused_not_run_as_nothing(bench, tmp_path):
    """A typo that silently ran zero cases would write a scoreboard of pure
    skip rows and read like the suite had been asked."""
    with pytest.raises(RuntimeError, match="03-honesty-czech"):
        pb.run_product_bench(CASES, only=["03-honesty-czech"], repo_dir=tmp_path)


def test_a_named_slice_stays_out_of_the_tracked_results(bench, tmp_path):
    summary = pb.run_product_bench(CASES, only=[NAMES[0]], repo_dir=tmp_path)
    saved = _write(summary, tmp_path)
    assert saved.exists(), "the operator's own copy must still be written"
    assert not (tmp_path / "benchmarks" / "results" / saved.name).exists()
    data = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert data["only_cases"] == [NAMES[0]]
    # The slice was cut by name, not by count: a `limited_to: None` row
    # would be a marker about a flag that never went by.
    assert "limited_to" not in data


def test_the_criterion_refuses_a_named_slice_by_marker(tmp_path):
    """Layer 2: a hand-copied `--only` file in the tracked directory is
    refused and named, exactly like a `limited_to` one."""
    out = tmp_path / "benchmarks" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result-2026-08-25-0000.yaml").write_text(yaml.safe_dump({
        "build_rate": 0.0, "probe_pass_rate": 0.2, "clean_review_rate": 0.2,
        "cases": [], "only_cases": ["03-groupbuy-auto"],
        "rates": {"cases_measured": 1, "cases_total": 5},
    }), encoding="utf-8")
    assert truncated_runs(tmp_path) == [
        "benchmarks/results/result-2026-08-25-0000.yaml"
    ]
    assert evaluate(tmp_path).runs_considered == []
