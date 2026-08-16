"""The scoreboard must contain every run it has (ADR-042's family).

`benchmarks/results/HISTORY.md` says of itself that "the table below is
authoritative for the headline numbers". It stopped being true silently:
`save_summary` dual-writes every result file here automatically, but the
table is written by hand, and runs 14 and 15 sat on disk for two days with
no row. Nothing failed — a hand-maintained ledger has no way to notice it
is behind, which is the same shape as a spec that passes every check by
being empty (ADR-041) and a failure that arrives as banner art (ADR-042):
the fact exists in the data and is absent from what a person reads.

So the test is the mechanism. A result file that lands here without a row
now fails the suite on the next run.
"""

from __future__ import annotations

import pathlib
import re

import yaml

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "results"
HISTORY = RESULTS / "HISTORY.md"


def _result_files() -> list[pathlib.Path]:
    return sorted(RESULTS.glob("result-*.yaml"))


def test_there_are_results_to_check():
    """Guard the guard: a glob that matches nothing passes every assertion
    below by vacuity, which is the failure this file exists to prevent."""
    assert _result_files(), f"no result files under {RESULTS}"


def test_every_saved_result_has_a_row_in_the_table():
    text = HISTORY.read_text(encoding="utf-8")
    missing = [path.name for path in _result_files() if path.name not in text]
    assert not missing, (
        "these runs are on disk but absent from HISTORY.md, which claims to "
        f"be authoritative for the headline numbers: {missing}"
    )


#: `| run | file | code | build | probes | clean | notes |`
_BUILD, _PROBES, _CLEAN = 4, 5, 6


def _rows_by_result_file() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        named = re.search(r"result-[\w-]+\.yaml", line)
        if named:
            rows[named.group(0)] = line.split("|")
    return rows


def _percent(cell: str) -> int | None:
    found = re.search(r"(\d+(?:\.\d+)?)\s*%", cell)
    return round(float(found.group(1))) if found else None


def test_each_row_carries_the_rates_its_result_file_recorded():
    """A row transcribed by hand can disagree with the file it names, and
    the disagreement is invisible unless something compares them.

    Within one point, because the table's rounding is not consistent —
    0.746 was written `75%` and 0.435 was written `43%`. Pinning a
    convention retroactively would mean editing recorded history to satisfy
    a test, which is backwards; the table is a record.
    """
    rows = _rows_by_result_file()
    checked = 0
    for path in _result_files():
        cells = rows.get(path.name)
        if cells is None:  # covered by the test above; not this one's job
            continue
        rates = (yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        rates = rates.get("rates") or rates
        for column, key in (
            (_BUILD, "build_rate"),
            (_PROBES, "probe_pass_rate"),
            (_CLEAN, "clean_review_rate"),
        ):
            value = rates.get(key)
            stated = _percent(cells[column]) if column < len(cells) else None
            if value is None or stated is None:
                continue
            assert abs(stated - value * 100) <= 1, (
                f"{path.name}: {key} is {value:.3f} ({value * 100:.1f}%) but "
                f"its HISTORY.md row states {stated}%"
            )
            checked += 1
    assert checked, "no rate was actually compared — the parse above is wrong"


def test_an_unmeasured_case_is_visible_in_its_row():
    """ADR-035: 83% of three cases and 83% of four are different claims, and
    the row is where a later reader meets the number first."""
    text = HISTORY.read_text(encoding="utf-8")
    for path in _result_files():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unmeasured = (data.get("rates") or data).get("unmeasured") or []
        if not unmeasured:
            continue
        row = next(
            (line for line in text.splitlines()
             if line.startswith("|") and path.name in line),
            None,
        )
        if row is None:
            continue
        assert re.search(r"\bof 4\b|\bunmeasured\b|\bexcluded\b", row), (
            f"{path.name} could not measure {unmeasured} and its row does "
            "not say the denominator changed"
        )
