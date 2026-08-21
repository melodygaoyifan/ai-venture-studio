"""A measurement is bought once (ADR-052).

Run 17 (2026-08-17) measured case 01 over 3438 seconds of real spend, then
lost the account mid-case-02 and recorded four more cases as unmeasured, one
at a time, in 0.3s each. Nothing banked case 01, so the only way back to a
five-case run was to buy it again — and had the 8h `BENCH_TIMEOUT_S` fired
instead of the loop completing, the process group would have died with
`save_summary` never called and case 01 lost outright.

Three separate claims here, and they fail independently:

  * a finished case is on disk BEFORE the next one starts
  * a banked row is reused only when it is still true, and says it was reused
  * a dead environment stops the run instead of being rediscovered per case
"""

from __future__ import annotations

import datetime as _dt

import pytest

import ai_venture_studio.product_bench as pb

CASES = "benchmarks/products"
#: How many cases the suite has. Read off disk, not written down: since
#: ADR-066 `--limit` bounds what the run PAYS FOR and no longer bounds what
#: the scoreboard counts, so every case in the directory gets a row.
_SUITE = len(list(__import__("pathlib").Path(CASES).glob("*.yaml")))


def _case(name, *, total=2, built=2, clean=1, status="completed"):
    return pb.CaseResult(
        name=name,
        autopilot_status=status,
        tasks_total=total,
        tasks_built=built,
        clean_reviews=clean,
        probes=[pb.ProbeResult(name="p", passed=True)],
    )


def _bench(monkeypatch, outcomes):
    """Drive the bench where `outcomes` decides each case, by position."""
    calls = iter(outcomes)

    def _next(case, provider=None, **_):
        outcome = next(calls)
        if isinstance(outcome, BaseException):
            raise outcome
        # The stub returns a row named for the case actually asked for, so a
        # resume that skips the wrong case cannot pass by coincidence.
        outcome.name = case.name
        return outcome

    monkeypatch.setattr(pb, "run_case", _next)


# ---------------------------------------------------------------------------
# Banking
# ---------------------------------------------------------------------------


def test_a_finished_case_is_on_disk_before_the_next_one_runs(monkeypatch, tmp_path):
    """The whole point: the next case is what kills the process."""
    seen = {}

    def _next(case, provider=None, **_):
        # What exists on disk at the moment case 2 STARTS — not at the end of
        # the run, which is when `save_summary` would have got there.
        seen[case.name] = sorted(p.name for p in pb.checkpoint_dir(tmp_path).glob("*"))
        return _case(case.name)

    monkeypatch.setattr(pb, "run_case", _next)
    pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)
    second = sorted(seen)[1]
    assert seen[second], "case 2 started with nothing banked — a kill here loses case 1"


def test_a_crashed_case_is_never_banked(monkeypatch, tmp_path):
    """Banking a crash would make it permanent: a transient 529 would become
    a case this bench never measures again."""
    _bench(monkeypatch, [RuntimeError("pytest timed out")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert list(pb.checkpoint_dir(tmp_path).glob("*.yaml")) == []


def test_a_bank_that_fails_does_not_lose_the_run(monkeypatch, tmp_path):
    """The measurement in hand outranks the optimisation."""
    _bench(monkeypatch, [_case("x")])

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(pb, "write_checkpoint", _boom)
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert summary.cases_measured == 1


# ---------------------------------------------------------------------------
# Reuse, and the key that makes reuse safe
# ---------------------------------------------------------------------------


def test_a_resumed_case_is_not_run_again(monkeypatch, tmp_path):
    _bench(monkeypatch, [_case("a"), _case("b")])
    pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path)

    ran = []

    def _record(case, provider=None, **_):
        ran.append(case.name)
        return _case(case.name)

    monkeypatch.setattr(pb, "run_case", _record)
    summary = pb.run_product_bench(CASES, limit=2, repo_dir=tmp_path, resume=True)
    assert ran == [], "a resumed run re-paid for cases it had already measured"
    assert summary.cases_measured == 2


def test_a_resumed_row_says_it_was_resumed(monkeypatch, tmp_path):
    """Every number in a reused row is real, which is exactly what would hide
    it. A scoreboard that cannot distinguish measured from read-off-disk is
    claiming work it did not do."""
    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path, resume=True)
    assert summary.cases[0].resumed is True
    saved = pb.save_summary(summary, tmp_path)
    import yaml

    payload = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert payload["rates"]["resumed"] == [summary.cases[0].name]
    assert payload["cases"][0]["resumed"] is True


def test_a_row_from_another_build_is_refused(monkeypatch, tmp_path):
    """The confound this whole feature could have introduced: reusing a row
    measured on 0.97.0 inside a run of 0.100.0 averages two machines into one
    scoreboard — what ADR-049 narrowed `cases_total` to prevent, arriving
    through the optimisation meant to save money."""
    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)

    monkeypatch.setattr("ai_venture_studio.__version__", "0.0.1-other")
    ran = []
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: (ran.append(case.name)
                                                     or _case(case.name))
    )
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path, resume=True)
    assert len(ran) == 1, "a row measured on a different build was reused"


def test_an_edited_case_is_refused(monkeypatch, tmp_path):
    """`autopilot._todo_and_skipped` keys on `(task_id, title)` rather than the
    id for this reason: skipping work is only safe when we can say what work
    it was. An edited FDR is a different question."""
    case = pb.load_cases(CASES)[0]
    key = pb.case_key(case, provider=None)
    edited = case.model_copy(update={"fdr": case.fdr + "\n\nAlso: export to CSV."})
    assert pb.case_key(edited, provider=None) != key
    assert pb.case_key(edited, provider=None)["case"] == key["case"]


def test_a_different_provider_is_refused(tmp_path):
    case = pb.load_cases(CASES)[0]
    assert (
        pb.case_key(case, provider="mock") != pb.case_key(case, provider="anthropic")
    )


def test_a_corrupt_checkpoint_is_no_checkpoint(monkeypatch, tmp_path):
    """Every rejection path ends in 'the case runs', which is what would have
    happened anyway."""
    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    for path in pb.checkpoint_dir(tmp_path).glob("*.yaml"):
        path.write_text("{{{ not yaml", encoding="utf-8")
    ran = []
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: (ran.append(case.name)
                                                     or _case(case.name))
    )
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path, resume=True)
    assert len(ran) == 1


def test_a_run_without_resume_measures_everything(monkeypatch, tmp_path):
    """A checkpoint lying around must never change a run nobody asked to
    resume. The default is what it has always been."""
    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    ran = []
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: (ran.append(case.name)
                                                     or _case(case.name))
    )
    summary = pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    assert len(ran) == 1 and summary.cases[0].resumed is False


def test_a_stale_checkpoint_is_refused_but_not_deleted(monkeypatch, tmp_path):
    """The bound is a read rule, not a cleanup pass. Deleting inside `.mas/`
    is the one thing this repo does not do — it holds unrecoverable run
    history, and it was wiped once (2026-07-26, runs 1-8's originals lost).
    Refusing to read gets the whole benefit and destroys nothing."""
    import yaml

    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    (path,) = list(pb.checkpoint_dir(tmp_path).glob("*.yaml"))
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    stale = _dt.datetime.now(_dt.UTC) - _dt.timedelta(
        days=pb._CHECKPOINT_MAX_AGE_DAYS + 1
    )
    payload["saved_at"] = stale.isoformat(timespec="seconds")
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    ran = []
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: (ran.append(case.name)
                                                     or _case(case.name))
    )
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path, resume=True)
    assert len(ran) == 1, "a three-week-old row was reused"
    assert path.exists(), "the stale checkpoint was deleted rather than ignored"


def test_a_checkpoint_that_cannot_date_itself_is_refused(monkeypatch, tmp_path):
    import yaml

    _bench(monkeypatch, [_case("a")])
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path)
    (path,) = list(pb.checkpoint_dir(tmp_path).glob("*.yaml"))
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    del payload["saved_at"]
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    ran = []
    monkeypatch.setattr(
        pb, "run_case", lambda case, provider=None, **_: (ran.append(case.name)
                                                     or _case(case.name))
    )
    pb.run_product_bench(CASES, limit=1, repo_dir=tmp_path, resume=True)
    assert len(ran) == 1


# ---------------------------------------------------------------------------
# A dead environment is one finding, not five
# ---------------------------------------------------------------------------


class _Status400(Exception):
    """Shaped like the SDK error that actually landed."""

    status_code = 400


CREDIT = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low"
)


def test_the_real_credit_error_is_recognised():
    """Pinned against the exact text from
    benchmarks/results/aborted-2026-08-17-1412-credit-exhausted.yaml."""
    assert pb.environment_failure(_Status400(CREDIT))


@pytest.mark.parametrize("status", [401, 402, 403])
def test_an_unusable_key_is_environmental(status):
    exc = Exception("nope")
    exc.status_code = status
    assert pb.environment_failure(exc)


def test_a_rate_limit_is_not_environmental():
    """429 is transient and the provider adapter already retries it six times
    with backoff; one that reaches here has outlived a real overload event and
    the next case deserves its own chance."""
    exc = Exception("rate limited")
    exc.status_code = 429
    assert pb.environment_failure(exc) == ""


def test_an_ordinary_case_crash_is_not_environmental():
    """The direction that matters: a false positive aborts a run that could
    have continued, which is a NEW way to lose measurement."""
    assert pb.environment_failure(RuntimeError("pytest timed out")) == ""
    assert pb.environment_failure(KeyError("new_content")) == ""


def test_a_dead_account_stops_the_run_at_the_first_case(monkeypatch, tmp_path):
    _bench(monkeypatch, [_Status400(CREDIT)])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    assert summary.aborted
    # Case 1 crashed; 2-4 were never asked and say so in one voice. Cases 5-6
    # were outside the limit and say something different — since ADR-066 the
    # limit no longer shrinks the suite, so every case in the directory gets a
    # row and the row says which of the two reasons applies to it.
    assert len(summary.cases) == _SUITE
    assert sum(1 for c in summary.cases if "environment" in c.autopilot_status) == 3
    assert sum(1 for c in summary.cases if "--limit" in c.autopilot_status) == _SUITE - 4


def test_the_cases_that_were_never_asked_are_unmeasured_not_failures(
    monkeypatch, tmp_path
):
    """ADR-035 unchanged: they are excluded from the rates and named in the
    scope, never averaged in as 0.0."""
    _bench(monkeypatch, [_case("a"), _Status400(CREDIT)])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    assert summary.build_rate == 1.0
    assert summary.cases_measured == 1
    # Three within the limit: the case that hit the dead account, and the two
    # after it that were never asked. Counted by REASON rather than by the
    # length of `unmeasured`, which now also holds the cases the `--limit`
    # never reached — both kinds are unmeasured, and this test is about the
    # kind the abort produced (ADR-066).
    assert sum(
        1 for c in summary.cases
        if not c.measured and "--limit" not in c.autopilot_status
    ) == 3


def test_an_abort_does_not_run_the_remaining_cases(monkeypatch, tmp_path):
    """The point is the wall-clock, not the tidy report. Run 17 spent 1541s
    on a dead account and then rediscovered the same fact three more times."""
    asked = []

    def _next(case, provider=None, **_):
        asked.append(case.name)
        raise _Status400(CREDIT)

    monkeypatch.setattr(pb, "run_case", _next)
    pb.run_product_bench(CASES, limit=5, repo_dir=tmp_path)
    assert len(asked) == 1


def test_two_identical_failures_abort_without_any_vocabulary(monkeypatch, tmp_path):
    """The backstop, and the half that cannot rot. A string table matched
    against someone else's error messages stops firing the day they reword it
    and nothing says so; two cases dying byte-identically is not a property of
    either case, and needs no table."""
    _bench(monkeypatch, [RuntimeError("gateway exploded"),
                         RuntimeError("gateway exploded"),
                         _case("never-reached")])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    assert summary.aborted and "consecutive" in summary.aborted
    assert summary.cases_measured == 0


def test_two_DIFFERENT_failures_do_not_abort(monkeypatch, tmp_path):
    """One case never kills the bench — the original rule, still true for
    everything except a failure that is demonstrably not about the case."""
    _bench(monkeypatch, [RuntimeError("pytest timed out"),
                         KeyError("new_content"),
                         _case("c")])
    summary = pb.run_product_bench(CASES, limit=3, repo_dir=tmp_path)
    assert summary.aborted == ""
    assert summary.cases_measured == 1


def test_a_success_between_two_failures_resets_the_streak(monkeypatch, tmp_path):
    _bench(monkeypatch, [RuntimeError("boom"), _case("b"), RuntimeError("boom"),
                         _case("d")])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    assert summary.aborted == ""
    assert summary.cases_measured == 2


def test_the_abort_reason_reaches_the_saved_result(monkeypatch, tmp_path):
    """'four cases failed' and 'this run never got to ask them' are different
    findings and the percentages look identical."""
    import yaml

    _bench(monkeypatch, [_case("a"), _Status400(CREDIT)])
    summary = pb.run_product_bench(CASES, limit=4, repo_dir=tmp_path)
    saved = pb.save_summary(summary, tmp_path)
    payload = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert "credit balance is too low" in payload["aborted"]


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_the_preflight_refuses_a_dead_account(monkeypatch):
    class _Dead:
        def complete(self, **kw):
            raise _Status400(CREDIT)

    monkeypatch.setattr(
        "ai_venture_studio.providers.base.get_provider", lambda name: _Dead()
    )
    assert pb.preflight_provider("anthropic", "claude-opus-4-8")


def test_the_preflight_passes_a_live_account(monkeypatch):
    class _Live:
        def complete(self, **kw):
            return "ok"

    monkeypatch.setattr(
        "ai_venture_studio.providers.base.get_provider", lambda name: _Live()
    )
    assert pb.preflight_provider("anthropic", "claude-opus-4-8") == ""


def test_the_preflight_swallows_an_unrecognised_blip(monkeypatch):
    """It is a preflight, not a gate. Refusing to start a three-hour run over
    an unrecognised error would be the check causing the outage."""
    class _Flaky:
        def complete(self, **kw):
            raise TimeoutError("connection reset")

    monkeypatch.setattr(
        "ai_venture_studio.providers.base.get_provider", lambda name: _Flaky()
    )
    assert pb.preflight_provider("anthropic", "claude-opus-4-8") == ""


def test_the_preflight_never_calls_the_mock_provider(monkeypatch):
    """Hermetic runs must not pay a network round-trip to be told they work."""
    def _boom(name):
        raise AssertionError("the preflight called a provider for mock")

    monkeypatch.setattr("ai_venture_studio.providers.base.get_provider", _boom)
    assert pb.preflight_provider("mock", "claude-opus-4-8") == ""
