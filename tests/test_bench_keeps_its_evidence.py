"""Preserved workspaces are keyed by RUN, not overwritten per case (ADR-058).

Run 18's first act, for each of its four cases, was to delete run 17's
preserved workspace for that case — `_preserve_workspace` wrote to
`workspaces/<case>` and `rmtree`'d it first. Run 17 was the credit-exhaustion
abort; its workspaces were the only record of what the money had bought. The
result file still pointed at the path, which by then held different bytes, and
nothing anywhere recorded the substitution.
"""

from pathlib import Path

from ai_venture_studio.product_bench import (
    BenchSummary,
    _preserve_workspace,
    _prune_workspace_runs,
    save_summary,
)


def _workspace(tmp_path: Path, name: str, marker: str) -> Path:
    ws = tmp_path / "src" / name
    ws.mkdir(parents=True)
    (ws / "README.md").write_text(marker, encoding="utf-8")
    return ws


def test_a_later_run_does_not_overwrite_an_earlier_runs_workspace(tmp_path):
    keep = tmp_path / "keep"
    first = _preserve_workspace(
        _workspace(tmp_path, "01-case", "run 17"), "01-case", keep, "2026-08-17-1412"
    )
    second = _preserve_workspace(
        _workspace(tmp_path, "01-case-b", "run 18"), "01-case", keep, "2026-08-20-1459"
    )
    assert first != second
    # THE POINT: the earlier run's bytes are still on disk and still its own.
    assert Path(first, "README.md").read_text() == "run 17"
    assert Path(second, "README.md").read_text() == "run 18"


def test_the_run_stamp_is_in_the_path(tmp_path):
    keep = tmp_path / "keep"
    path = Path(
        _preserve_workspace(
            _workspace(tmp_path, "c", "x"), "03-groupbuy-auto", keep, "2026-08-20-1459"
        )
    )
    # A reader holding `result-2026-08-20-1459.yaml` can find this by name.
    assert path.parent.name == "2026-08-20-1459"
    assert path.name == "03-groupbuy-auto"


def test_the_same_case_twice_in_one_run_still_does_not_overwrite(tmp_path):
    keep = tmp_path / "keep"
    a = _preserve_workspace(_workspace(tmp_path, "a", "first"), "c", keep, "2026-01-01-0000")
    b = _preserve_workspace(_workspace(tmp_path, "b", "second"), "c", keep, "2026-01-01-0000")
    assert a != b
    assert Path(a, "README.md").read_text() == "first"


def test_pruning_drops_whole_old_runs_and_keeps_the_newest(tmp_path):
    root = tmp_path / "workspaces"
    stamps = [f"2026-08-{day:02d}-1200" for day in range(10, 20)]
    for stamp in stamps:
        (root / stamp / "01-case").mkdir(parents=True)
    removed = _prune_workspace_runs(root, keep=3)
    assert removed == stamps[:-3]
    assert sorted(p.name for p in root.iterdir()) == stamps[-3:]


def test_pruning_leaves_anything_that_is_not_a_run_stamp_alone(tmp_path):
    """Including the pre-fix `workspaces/<case>` layout already on disk."""
    root = tmp_path / "workspaces"
    (root / "01-groupbuy-api").mkdir(parents=True)   # old layout, a case name
    (root / "notes.txt").parent.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("keep me")
    for stamp in ("2026-08-10-1200", "2026-08-11-1200"):
        (root / stamp).mkdir()
    _prune_workspace_runs(root, keep=1)
    assert (root / "01-groupbuy-api").is_dir()
    assert (root / "notes.txt").exists()
    assert not (root / "2026-08-10-1200").exists()


def test_the_result_file_is_named_with_the_runs_own_stamp(tmp_path):
    summary = BenchSummary(
        cases=[], build_rate=None, probe_pass_rate=None, clean_review_rate=None,
        run_stamp="2026-08-20-1459",
    )
    path = save_summary(summary, tmp_path)
    assert path.name == "result-2026-08-20-1459.yaml"


def test_a_summary_without_a_stamp_still_saves(tmp_path):
    """Hand-built summaries predate the field and must keep working."""
    summary = BenchSummary(
        cases=[], build_rate=None, probe_pass_rate=None, clean_review_rate=None,
    )
    path = save_summary(summary, tmp_path)
    assert path.name.startswith("result-") and path.exists()
