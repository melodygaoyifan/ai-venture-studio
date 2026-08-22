"""ADR-056. A result file that does not say what produced it.

`avs product-bench --provider mock` is a documented invocation. It ran the
whole harness against a provider that answers from a regex table, wrote a
result file byte-identical in shape to a real one — same rates, same
denominators, same `avs_version` — dual-wrote it into the tracked
`benchmarks/results/`, and `bench_criterion` counted it as a reading of this
system's capability. Nothing anywhere recorded which instrument took the
measurement, so nothing downstream could have told the difference.

The same shape as ADR-054: a number in the capability ledger that a reader
cannot check the provenance of. There it was a criterion that crashed before
reporting; here it is a criterion that reports confidently on a fixture set.

Two layers, because each is silent about the other's case:

  1. `save_summary` does not dual-write a simulated run into the tracked
     ledger. It still writes `.mas/`, because a mock run IS a real exercise
     of the harness and its scoreboard is how you check the harness worked.
  2. `_scan` refuses to count a simulated result that reaches the directory
     anyway — copied by hand, written by a build from before this fix,
     restored from a backup — and REPORTS it rather than dropping it in
     silence, the rule the aborted-run list already established.

And the compatibility direction, which is the one that could do real damage:
a file with no `provider:` key is read as REAL. Every result written before
v0.105.0 lacks the field and every one of them was a genuine run. Guessing
the other way would silently delete eleven capability readings from the
series the kill criterion evaluates.
"""

from __future__ import annotations

import collections
import pathlib

import yaml

from ai_venture_studio import bench_criterion as bc
from ai_venture_studio.product_bench import BenchSummary, CaseResult, save_summary
from ai_venture_studio.providers.base import is_simulated


def _summary(build: float = 1.0, probe: float = 1.0) -> BenchSummary:
    case = CaseResult(
        name="01-case", axis="build", measured=True, built=True,
        probe_pass=True, clean_review=True, autopilot_status="ok",
    )
    return BenchSummary(
        cases=[case], build_rate=build, probe_pass_rate=probe,
        clean_review_rate=1.0,
    )


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "benchmarks").mkdir(parents=True)
    return tmp_path


def test_every_result_names_the_provider_that_produced_it(tmp_path):
    """Recorded on every run, not only the simulated ones.

    A field that appears exactly when something is wrong is a field nobody
    thinks to look for, and its absence is then indistinguishable from a file
    written before it existed.
    """
    for provider, expected in ((None, "anthropic"), ("mock", "mock"),
                               ("anthropic", "anthropic")):
        repo = _repo(tmp_path / str(provider))
        path = save_summary(_summary(), repo, provider=provider)
        assert yaml.safe_load(path.read_text())["provider"] == expected


def test_a_simulated_run_never_enters_the_tracked_ledger(tmp_path):
    repo = _repo(tmp_path)
    saved = save_summary(_summary(), repo, provider="mock")
    # It still exists — running the harness against mock is a legitimate way
    # to check the harness, and the scoreboard is the output of that check.
    assert saved.is_file()
    assert saved.parent == repo / ".mas" / "product-bench"
    tracked = repo / "benchmarks" / "results"
    assert not tracked.exists() or list(tracked.iterdir()) == []


def test_a_real_run_still_enters_the_tracked_ledger(tmp_path):
    """The half of the fix that is easy to break and silent when broken."""
    repo = _repo(tmp_path)
    saved = save_summary(_summary(), repo, provider="anthropic")
    tracked = repo / "benchmarks" / "results" / saved.name
    assert tracked.is_file()
    assert bc.load_runs(repo)[0].build_rate == 1.0


def test_a_simulated_result_that_reaches_the_ledger_is_not_counted(tmp_path):
    """Layer 2: the writer is not the only way a file gets into that directory."""
    repo = _repo(tmp_path)
    (repo / "benchmarks" / "results").mkdir()
    saved = save_summary(_summary(build=0.0, probe=0.0), repo, provider="mock")
    # Copied in by hand, or written there by a build from before this fix.
    (repo / "benchmarks" / "results" / saved.name).write_text(saved.read_text())

    assert bc.load_runs(repo) == []
    state = bc.evaluate(repo)
    assert state.streak == 0
    assert not state.fires


def test_the_excluded_file_is_named_rather_than_silently_dropped(tmp_path):
    """A file in the directory and absent from the ledger, unexplained, is a
    reason to distrust the ledger — the rule `aborted_skipped` established."""
    repo = _repo(tmp_path)
    (repo / "benchmarks" / "results").mkdir()
    saved = save_summary(_summary(), repo, provider="mock")
    (repo / "benchmarks" / "results" / saved.name).write_text(saved.read_text())

    assert [pathlib.Path(p).name for p in bc.simulated_runs(repo)] == [saved.name]
    assert bc.evaluate(repo).simulated_skipped == bc.simulated_runs(repo)


def test_a_result_with_no_provider_key_is_read_as_real(tmp_path):
    """The eleven runs already in the series predate the field.

    Reading an absent `provider:` as simulated would drop every real
    capability reading this project has ever taken out of the criterion's
    view — and the criterion would then report, correctly and uselessly,
    that there is no data.
    """
    repo = _repo(tmp_path)
    results = repo / "benchmarks" / "results"
    results.mkdir()
    legacy = {
        "build_rate": 0.75, "probe_pass_rate": 0.5, "clean_review_rate": 0.38,
        "rates": {"cases_measured": 4, "cases_total": 4},
    }
    (results / "result-2026-07-01-0900.yaml").write_text(yaml.safe_dump(legacy))

    runs = bc.load_runs(repo)
    assert len(runs) == 1
    assert runs[0].build_rate == 0.75
    assert bc.simulated_runs(repo) == []


def test_the_registry_is_the_only_place_that_names_simulated_providers():
    """One definition of "not a measuring instrument".

    A second copy is a second definition of "real run", and the thing the two
    would drift about is which files decide whether a human is asked to
    consider killing the project (ADR-038, ADR-051).
    """
    assert is_simulated("mock")
    assert not is_simulated("anthropic")
    # None means "the default", which is real. The asymmetry is the point:
    # a reader that cannot tell must assume the reading was genuine.
    assert not is_simulated(None)

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "ai_venture_studio"
    hits = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "SIMULATED_PROVIDERS" in p.read_text(encoding="utf-8")
    ]
    assert hits == ["providers/base.py"], hits

    # AND THE ASSERTION ABOVE DOES NOT SAY WHAT ITS NAME SAYS. It finds the
    # places that spell `SIMULATED_PROVIDERS`, and the way a second definition
    # actually arrives is `== "mock"` written out by hand — which this test
    # was blind to for its whole life (ADR-067). Five such sites exist today.
    # They are not a defect the registry can fix from here: each is a
    # *routing* decision (pick the stub, pick the cheap model), not a reading
    # of whether a bench result measured anything, and rewriting them is a
    # `src/` change this release is not allowed to make.
    #
    # So the debt is pinned at its true size instead of left unsaid. A sixth
    # site fails this line, and the fix for that failure is to call
    # `is_simulated`, never to raise the number.
    # Counted per FILE, not per line number: a line number moves when anything
    # above it is edited, and a guard that fails for a reason it is not named
    # for gets deleted rather than read.
    literal = collections.Counter(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if p.relative_to(src).as_posix() != "providers/base.py"
        for line in p.read_text(encoding="utf-8").splitlines()
        if '"mock"' in line and ("==" in line or "!=" in line)
    )
    assert dict(literal) == {
        "cli.py": 2,
        "product_bench.py": 1,
        "studio.py": 1,
        "upstream/autopilot.py": 1,
    }, (
        "a site asks 'is this the stub?' by hand instead of calling "
        f"is_simulated(): {dict(literal)}"
    )


def test_the_watchdog_does_not_read_a_simulated_run_as_the_bench_having_run(tmp_path):
    """The second reader of the same directory (ADR-051).

    `cadence` measures liveness — "has the bench run lately" — with its own
    glob. A simulated result landing there would answer yes about a run that
    measured nothing, and the alert channel would print `all clear`, which is
    the one thing that module's own comment says a watchdog must never do.
    """
    import datetime as _dt

    from ai_venture_studio import cadence

    repo = _repo(tmp_path)
    (repo / "benchmarks" / "products-real").mkdir()
    results = repo / "benchmarks" / "results"
    results.mkdir()
    saved = save_summary(_summary(), repo, provider="mock")
    (results / saved.name).write_text(saved.read_text())

    status = cadence._bench_status(repo, _dt.date(2026, 8, 20))
    assert status is not None
    assert status.last_run == "", status.last_run
    assert status.state == "never_run", status.state


def test_the_alert_says_which_instrument_took_the_reading():
    """The third reader, after the ledger and the watchdog.

    `bench_alert` is sent even on a clean run, because somebody is waiting on
    the weekly number. A simulated run posted into that channel is the same
    percentages with none of the meaning.
    """
    from ai_venture_studio import notify

    simulated = notify.bench_alert(_summary(), workspace="ws", provider="mock")
    assert "SIMULATED" in simulated.heading
    assert any("harness, not the system" in line for line in simulated.lines)

    for real in (None, "anthropic"):
        alert = notify.bench_alert(_summary(), workspace="ws", provider=real)
        assert "SIMULATED" not in alert.heading
        assert not any("harness, not the system" in x for x in alert.lines)


def test_the_real_ledger_in_this_repo_still_reads_as_a_series():
    """Not a fixture: the actual benchmarks/results/ this project ships.

    The compatibility rule above is only worth anything if it holds against
    the files it was written for.
    """
    repo = pathlib.Path(__file__).resolve().parents[1]
    if not (repo / bc.RESULTS_DIR).is_dir():
        return
    runs = bc.load_runs(repo)
    assert len(runs) >= 10, f"the recorded series collapsed to {len(runs)} runs"
    assert bc.simulated_runs(repo) == []
