"""Product benchmark — built-product quality, measured end to end.

The review benchmark measures whether the system judges code well; this
measures whether it BUILDS products well. Architecture follows the
WebGen-Bench insight (arXiv:2505.03733): quality is what INDEPENDENT
probes observe when exercised against the built product — never the
builder's own tests (circular) and never review verdicts alone.

A case = an FDR + behavioral probes. The full autopilot runs in a fresh
workspace; each probe is a self-contained script executed IN the built
workspace with the product's runtime env. Scores:

- build_rate: tasks that reached `built`
- probe_pass_rate: independent behaviors that actually work
- clean_review_rate: built tasks whose review was APPROVE-class

The composite is deliberately NOT averaged away: all three numbers are
reported; a build that compiles but fails its probes is visible as
exactly that.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.observability import CostModel
from ai_venture_studio.state import CLEAN_VERDICT_VALUES
from ai_venture_studio.testing import _as_text
from ai_venture_studio.testing import _run as _run_killing_the_group
from ai_venture_studio.upstream import init_workspace
from ai_venture_studio.upstream.autopilot import run_autopilot
from ai_venture_studio.upstream.provisioning import preview_env

_PROBE_TIMEOUT_S = 60


def _pid_alive(pid: int) -> bool:
    from ai_venture_studio.procs import pid_alive

    return pid_alive(pid)


def acquire_bench_lock(repo_dir: str | Path = ".") -> Path:
    """One bench at a time: concurrent runs interleave the same log,
    collide on preserved-workspace paths, and double provider spend
    (2026-07-26: two sessions launched run 6 within minutes of each
    other). A pidfile whose pid is dead is stale and reclaimed."""
    import os

    pidfile = Path(repo_dir) / ".mas" / "product-bench" / "bench.pid"
    if pidfile.exists():
        try:
            other = int(pidfile.read_text(encoding="utf-8").strip())
        except ValueError:
            other = 0
        if other and other != os.getpid() and _pid_alive(other):
            raise RuntimeError(
                f"another product-bench is already running (pid {other}, "
                f"pidfile {pidfile}) — refusing to start a duplicate. If that "
                "pid is not a bench, delete the pidfile and rerun."
            )
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    return pidfile


def release_bench_lock(pidfile: Path) -> None:
    import os

    try:
        if pidfile.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pidfile.unlink()
    except OSError:
        pass


#: Provider wording that means the ACCOUNT is dead, not the case. Matched
#: case-insensitively against the whole exception text.
#:
#: The first entry is quoted from the run that made this necessary: run 17
#: (2026-08-17) exhausted its credit during case 02 and the harness carried on,
#: spending 1541s on a dead account and then producing four identical
#: `error: BadRequestError: ... 'Your credit balance is too lo` rows in 0.3s
#: each. Four cases were recorded as unmeasured one at a time, when one look at
#: the first failure would have said all four were unmeasurable.
#:
#: A string table against someone else's error messages is a check that stops
#: firing the day they reword it, and nothing would say so — which is why it is
#: the FAST path and not the only one. `_repeated_failure` below is the
#: backstop, and it needs no vocabulary.
_ENVIRONMENT_MARKERS = (
    "credit balance is too low",
    "insufficient_quota",
    "exceeded your current quota",
    "billing",
    "invalid x-api-key",
    "authentication_error",
    "permission_error",
    "could not resolve authentication",
)

#: HTTP statuses that are about the caller, not the request. 429 is NOT here:
#: it is transient and the provider adapter already retries it six times with
#: backoff, so a 429 that reaches us has outlived a real overload event and the
#: next case deserves its own chance.
_ENVIRONMENT_STATUSES = (401, 402, 403)

#: How many consecutive cases must fail identically before the run is declared
#: environmental. Two, not one: one case failing is what the per-case error row
#: is FOR (a hung suite, a 529 that outlived its retries), and aborting on it
#: would turn one lost case into five. Two in a row with the same exception type
#: and the same message is not a property of either case.
_REPEAT_ABORT_THRESHOLD = 2


def _error_signature(exc: BaseException) -> str:
    """What makes two failures 'the same failure'.

    Type plus the first line of the message. The first line only, because
    provider errors embed a request id and a truncated body — two identical
    credit failures differ in their tails and would never compare equal.
    """
    first = str(exc).splitlines()[0] if str(exc).splitlines() else ""
    return f"{type(exc).__name__}: {first[:160]}"


def environment_failure(exc: BaseException) -> str:
    """Why every REMAINING case will fail too — or "" if this is just a case.

    The distinction the bench did not draw. `_run_product_bench`'s handler is
    commented "one case never kills the bench", which is right for a case that
    crashed and wrong for an account with no credit: the first is a finding
    about the machine, the second is a finding about the environment, and only
    the first belongs in a per-case row.

    Unrecognised failures return "" and keep the old behaviour. That direction
    is deliberate — a false positive here aborts a run that could have carried
    on, which is a NEW way to lose measurement, and this function exists to
    stop losing it.
    """
    status = getattr(exc, "status_code", None)
    if status in _ENVIRONMENT_STATUSES:
        return f"provider returned {status} — the API key is not usable"
    text = str(exc).lower()
    for marker in _ENVIRONMENT_MARKERS:
        if marker in text:
            return f"provider rejected the call: {marker}"
    return ""


class Probe(BaseModel):
    name: str
    script: str  # python source, exit 0 = behavior works


#: What a follow-up FDR is expected to do to the system (ADR-049). These are
#: the three answers the increment path can give, and the case says which one
#: it is asking for BEFORE the run, because an expectation written after the
#: fact is not a measurement.
#:
#:   already_satisfied — the request duplicates a promise; nothing is built
#:   raises_scr        — it contradicts one; under `--yes` the build proceeds
#:                       and the clash is recorded as an UNAPPROVED SCR
#:   completed         — it is a real addition and must not be refused
EXPECTATIONS = ("already_satisfied", "raises_scr", "completed")

#: The two things this bench measures. They are separate rates over separate
#: cases for the reason ADR-035 gives about denominators: "did it build what
#: was asked" and "did it correctly decline to build" are different questions,
#: and a case whose CORRECT outcome is that nothing was built would drag a
#: build rate down for doing the right thing.
AXES = ("build", "increment")


class ProductCase(BaseModel):
    name: str
    profile: str = "web"
    fdr: str
    axis: str = Field(
        default="build",
        description="which rate this case feeds: 'build' (the headline "
        "build/probe/clean rates) or 'increment' (the gate rate only)",
    )
    feature_fdrs: list[str] = Field(
        default_factory=list,
        description="granular follow-up FDRs applied via the feature flow",
    )
    feature_expectations: list[str] = Field(
        default_factory=list,
        description="one EXPECTATIONS value per feature FDR; empty means the "
        "case makes no claim about the increment path and scores no gate rate",
    )
    probes: list[Probe] = Field(default_factory=list)
    auto_probes: bool = Field(
        default=False,
        description="generate probes from the FDR against the built product "
        "(the real-user path) instead of hand-written fixtures",
    )

    def model_post_init(self, _ctx) -> None:
        if not self.probes and not self.auto_probes:
            raise ValueError("case needs probes or auto_probes: true")
        if self.axis not in AXES:
            raise ValueError(f"axis must be one of {AXES}, not {self.axis!r}")
        if self.feature_expectations:
            # Aligned by position, so a mismatch is a case file that means
            # something other than what it says — refused at load rather
            # than scored, because the run costs hours and the reading of
            # every row after it would be wrong.
            if len(self.feature_expectations) != len(self.feature_fdrs):
                raise ValueError(
                    f"{len(self.feature_expectations)} expectations for "
                    f"{len(self.feature_fdrs)} feature FDRs — they are paired "
                    "by position"
                )
            unknown = [e for e in self.feature_expectations if e not in EXPECTATIONS]
            if unknown:
                raise ValueError(f"unknown expectation(s) {unknown}: {EXPECTATIONS}")
        if self.axis == "increment" and not self.feature_expectations:
            raise ValueError(
                "an increment-axis case with no feature_expectations measures "
                "nothing — it would be excluded from the headline rates and "
                "contribute to no other"
            )


class ProbeResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class IncrementResult(BaseModel):
    """One follow-up FDR, what the case expected of it, and what happened.

    `actual` is recorded even when it matches, because a gate rate without
    the answers behind it is a number nobody can check — and the failure
    this measures is a gate that is *inert* rather than wrong, which reads
    as a clean "completed" on every row.
    """

    index: int
    fdr: str
    expected: str
    actual: str
    correct: bool
    detail: str = ""


class CaseSpend(BaseModel):
    """What a case cost, as far as it can honestly be known.

    `usd` is None — never 0.0 — when no price in `.mas/cost-model.yaml`
    covered the models this case used. ADR-053's rule applied to money: an
    unpriced run and a free run are different facts, and the difference is
    exactly the one a reader is trying to establish. `unpriced_calls` rides
    along so a partially-priced total announces itself as a FLOOR.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float | None = None
    unpriced_calls: int = 0

    @property
    def is_floor(self) -> bool:
        return self.unpriced_calls > 0


def render_spend(spend: CaseSpend | None) -> str:
    """One line, safe to print anywhere — the CLI table and the Discord alert.

    Shared rather than written twice, because the two renderings that matter
    are the one on the operator's screen and the one that arrives at 3am from
    the scheduler, and a caveat that appears in only one of them is a caveat
    the person who needed it did not get.
    """
    if spend is None:
        # No leading "cost" here: every caller supplies its own label, and the
        # first live run printed `cost cost not metered`. Caught by running it,
        # not by reading it (ADR-054).
        return "not metered"
    tokens = f"{spend.calls} calls · {spend.input_tokens + spend.output_tokens:,} tokens"
    if spend.usd is None:
        # Not "$0.00". The counts are exact; there is simply no price to
        # apply, and saying zero would answer a question nobody can answer.
        return f"{tokens} · unpriced (no .mas/cost-model.yaml — run `avs prices --import`)"
    if spend.is_floor:
        return (
            f"{tokens} · ≥${spend.usd:.2f} "
            f"(FLOOR — {spend.unpriced_calls} of {spend.calls} calls unpriced)"
        )
    return f"{tokens} · ${spend.usd:.2f}"


def bench_cost_model(repo_dir: str | Path = ".") -> CostModel:
    """The price table to read a bench run against — the OPERATOR's, not the case's.

    Two different things live in two different places and it is easy to fold
    them into one: the token LEDGER is written inside each case's throwaway
    workspace, while the PRICE TABLE is `.mas/cost-model.yaml` in the repo the
    bench was invoked from, put there by `avs prices --import`. A freshly
    created temp workspace has never had prices and never could, so pricing a
    case against its own `.mas` reports every call unpriced — technically
    honest, and useless for the one question the cost is recorded to answer.
    """
    from ai_venture_studio.observability import load_cost_model

    return load_cost_model(Path(repo_dir) / ".mas")


def _case_spend(workspace: Path | None, cost_model: CostModel) -> CaseSpend | None:
    """Read the ledger the case just wrote, before its workspace is deleted.

    Every case builds in its own `mkdtemp`, and `autopilot` flushes spend to
    THAT root — so the rows are already attributed by construction and no
    per-call tagging is needed. It has to be read here, though: the `finally`
    below removes the tree, and the ledger goes with it. That is why the
    bench has been unable to say what it cost since the day it was written,
    with the data sitting on disk the whole time.

    A final `flush` first, because `autopilot` flushes BETWEEN tasks and the
    last task's rows are still buffered when the case returns.

    Never raises: metering must not fail a case that ran.
    """
    if workspace is None:
        return None
    try:
        from ai_venture_studio import spend as _spend

        _spend.flush(workspace)
        entries = _spend.read_entries(workspace)
        if not entries:
            return None
        summary = _spend.summarize(entries, cost_model)
        return CaseSpend(
            calls=summary.calls,
            input_tokens=summary.input_tokens,
            output_tokens=summary.output_tokens,
            # A total of 0.0 across calls that were ALL unpriced is not a
            # cost of zero, it is the absence of a price table.
            usd=None if summary.unpriced_calls == summary.calls else summary.usd,
            unpriced_calls=summary.unpriced_calls,
        )
    except Exception:  # noqa: BLE001 — a metering failure is not a case failure
        return None


class CaseResult(BaseModel):
    name: str
    autopilot_status: str
    axis: str = "build"
    increments: list[IncrementResult] = Field(default_factory=list)
    tasks_total: int = 0
    tasks_built: int = 0
    clean_reviews: int = 0
    outcomes: list[dict] = Field(
        default_factory=list, description="per-task forensics: status + detail"
    )
    preserved_workspace: str = ""
    probes: list[ProbeResult] = Field(default_factory=list)
    duration_s: float = 0.0
    #: What this case cost. None when nothing was metered — a resumed row
    #: (its cost was paid on an earlier run and belongs to that run's
    #: total), a simulated provider, or a crash before the first call.
    #: `duration_s` has always been recorded beside it; the run that told
    #: you it took 3438 seconds could not tell you what those seconds
    #: bought, which is the number that decides whether to run it again.
    spend: CaseSpend | None = None
    #: Why this case produced nothing, when it ran and produced nothing.
    #: The rate says how badly; only this says why, and without it a 0.0
    #: from a refused plan is indistinguishable from a 0.0 from a pipeline
    #: that tried everything (ADR-043). Empty on cases that built.
    failure_reason: str = ""
    #: How the plan was laned: `"2 lane(s): api, data"`, plus any
    #: `lane_advisories` the plan carried. Discovering that run 18's cases 02
    #: and 04 had collapsed to a single lane — which is how a plan makes
    #: `lane_check` unable to fire — meant opening preserved workspaces by
    #: hand, and those workspaces had already been overwritten once. The
    #: arrangement is a deterministic fact about the plan; the row is where
    #: it survives the workspace.
    lanes: str = ""
    #: True when this row was REUSED from a checkpoint rather than measured
    #: in this run. A resumed run that did not say so would be a scoreboard
    #: claiming to have measured what it actually read off disk — the same
    #: class of lie as averaging an unmeasured case in as 0.0 (ADR-035), and
    #: harder to catch, because every number in the row is real.
    resumed: bool = False

    # WHETHER A CASE WAS MEASURED IS ONE DECISION, MADE HERE, READ BY EVERY
    # RATE. It used to be made per rate — `unmeasured` was derived from
    # `build_rate is None` while each rate averaged independently — so one
    # summary could exclude a case from two rates and count it in the third.
    # Run 16 did exactly that: `02-shortener-api` was named as excluded and
    # its two probes were averaged in as a real 0.0 anyway.
    @property
    def measured(self) -> bool:
        """Did the pipeline get to answer at all?

        No only when the harness itself died — `run_product_bench` records
        `error: <Type>: ...` for that, and a crash (a provider 529, a hung
        suite) says nothing about whether the machine can build software.

        A case that RAN and produced nothing IS measured, and scores zero.
        ADR-035 said so in words — "a case that ran and built nothing still
        scores a real 0.0" — and the code disagreed with it for any case
        that decomposed to no tasks at all: `tasks_total` 0 read as "no
        denominator" rather than as the failure it is. Run 16's case 02
        planned for six minutes, came back with no tasks, and was dropped
        from the build rate, which then reported 100% for a run that was
        asked for four products and delivered three.
        """
        return not self.autopilot_status.startswith("error")

    @property
    def build_rate(self) -> float | None:
        if not self.measured:
            return None
        # No tasks is not "no denominator". The founder asked for a product
        # and the pipeline produced nothing to build — the most complete
        # build failure available.
        return self.tasks_built / self.tasks_total if self.tasks_total else 0.0

    @property
    def probe_pass_rate(self) -> float | None:
        if not self.measured:
            return None
        # A measured case that declares no probes has no probe denominator
        # — the instrument is absent, which is not the same as the case
        # being excluded. `probegen` failing to produce any appends a
        # failing probe rather than reaching here (see run_case).
        return (
            sum(1 for p in self.probes if p.passed) / len(self.probes)
            if self.probes
            else None
        )

    @property
    def clean_review_rate(self) -> float | None:
        if not self.measured:
            return None
        # Deliberately NOT the zero that build_rate now returns: a case that
        # built nothing has no review to be clean, and the failure is already
        # fully counted one column left. Pinned by
        # `test_a_real_zero_is_still_a_zero`.
        return self.clean_reviews / self.tasks_built if self.tasks_built else None

    @property
    def gate_rate(self) -> float | None:
        """How often the increment path did what the case asked of it.

        None when the case made no claim about it — a case with no
        `feature_expectations` is not a gate scoring 100%, it is a case
        that did not ask the question (ADR-049).
        """
        if not self.measured or not self.increments:
            return None
        return sum(1 for i in self.increments if i.correct) / len(self.increments)


class BenchSummary(BaseModel):
    cases: list[CaseResult]
    # None when no build-axis case produced the denominator, matching
    # `CaseResult`'s own convention rather than contradicting it one level
    # up. `gate_rate` below was already typed this way; these three were the
    # holdout, and the type is what let a run that measured nothing be read
    # as a run that scored zero.
    build_rate: float | None
    probe_pass_rate: float | None
    clean_review_rate: float | None
    # Named, not just absent: a rate averaged over 3 of 4 cases and one
    # averaged over 4 are different measurements, and the reader cannot
    # tell them apart from the percentages alone.
    unmeasured: list[str] = Field(default_factory=list)
    # The increment axis (ADR-049). Separate rate, separate denominator,
    # separate cases: "did it build what was asked" and "did it correctly
    # decline to build" are different questions, and a case whose CORRECT
    # outcome is that nothing was built would drag a build rate down for
    # doing the right thing. Keeping the split also keeps the run-13..17
    # headline series comparable — the build-axis cases are exactly the
    # four that have always been in it.
    gate_rate: float | None = None
    gate_unmeasured: list[str] = Field(default_factory=list)
    #: Set when the run stopped early because the ENVIRONMENT died rather
    #: than because the cases finished. The rates below it are still honest
    #: about their own scope — that is what `unmeasured` is for — but a
    #: reader needs to know the difference between "these four cases failed"
    #: and "this run was never able to ask them".
    aborted: str = ""
    #: What the whole run cost, summed from the per-case rows. None when
    #: nothing was metered at all. Note this counts EVERY case, including
    #: ones excluded from the rates: a case that crashed or refused still
    #: spent money, and a total that quietly dropped it would answer "what
    #: will this cost me next time" with a number that has never been true.
    #: Resumed rows contribute nothing — their cost was paid by the run that
    #: measured them, and counting it twice would inflate the series.
    spend: CaseSpend | None = None
    #: The stamp this run filed its preserved workspaces under, and the stamp
    #: its result file is named with. One value for both, so `preserved_workspace`
    #: paths in the rows below and the filename above them agree by
    #: construction rather than by the clock happening not to tick between
    #: the first case and the save (ADR-058).
    run_stamp: str = ""

    def _axis(self, axis: str) -> list[CaseResult]:
        return [c for c in self.cases if c.axis == axis]

    @property
    def cases_total(self) -> int:
        """Denominator of the HEADLINE rates — build-axis cases only."""
        return len(self._axis("build"))

    @property
    def cases_measured(self) -> int:
        return self.cases_total - len(self.unmeasured)

    @property
    def gate_cases_total(self) -> int:
        return len(self._axis("increment"))

    @property
    def gate_cases_measured(self) -> int:
        return self.gate_cases_total - len(self.gate_unmeasured)


def load_cases(cases_dir: str | Path) -> list[ProductCase]:
    cases = [
        ProductCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(Path(cases_dir).glob("*.yaml"))
    ]
    if not cases:
        raise FileNotFoundError(f"no product cases in {cases_dir}")
    return cases


# ---------------------------------------------------------------------------
# CHECKPOINTS — never buy the same measurement twice.
#
# `save_summary` runs once, after every case returns. So a run that dies gets
# nothing: run 17 measured case 01 over 3438 seconds of real spend, then lost
# the account, and that hour is currently unrecoverable — the row exists only
# in the aborted result because the loop happened to reach the end. Kill the
# process instead (ADR-036 kills the whole group at BENCH_TIMEOUT_S = 8h, and
# run 16 already used 2.97h of it before a fifth case was added) and every
# finished case dies with it.
#
# This is the same rule the rest of the system already keeps, one level up.
# `autopilot._todo_and_skipped` skips tasks that were already built;
# `deploy.score_node` is "the expensive super-step a resume must never re-pay
# when it already completed"; `cli.py:1837` says it plainest — "a resumed run
# would rebuild these and charge you again". A bench case is the most
# expensive unit in the system and the only one with no such rule.
#
# What a resume may NOT do is quietly mix builds. Reusing a row measured on
# 0.97.0 inside a run of 0.100.0 produces a scoreboard averaging two different
# machines — the exact confound ADR-049 narrowed `cases_total` to prevent,
# arriving through the optimisation meant to save money. So a checkpoint
# carries its key and is refused unless all of it matches, and
# `autopilot._todo_and_skipped`'s lesson applies verbatim: it keys on
# `(task_id, title)` rather than the id, because "skipping work is only safe
# when we can say what work it was."
# ---------------------------------------------------------------------------


#: How far back a `--resume` may reach. The key already refuses a checkpoint
#: from another build or another version of the case file, so an old one is
#: not *wrong* — but "the same build and the same case, three weeks ago" is a
#: claim about a machine nobody has run since, and a scoreboard should not be
#: able to quietly assemble itself out of last month.
#:
#: The bound is here rather than in a cleanup pass on purpose. Deleting inside
#: `.mas/` is the one thing this repo does not do: it holds unrecoverable run
#: history and forensics, and it was wiped once (2026-07-26, runs 1-8's
#: originals lost). Refusing to READ a stale checkpoint gets the whole benefit
#: and destroys nothing — the file stays for whoever is diagnosing the run it
#: came from.
_CHECKPOINT_MAX_AGE_DAYS = 14


def checkpoint_dir(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".mas" / "product-bench" / "checkpoints"


def case_key(case: ProductCase, *, provider: str | None) -> dict:
    """Everything that must be identical for a saved row to still be true."""
    import hashlib
    import json

    from ai_venture_studio import __version__

    payload = json.dumps(case.model_dump(mode="json"), sort_keys=True)
    return {
        "case": case.name,
        # The case FILE, not just its name. An FDR edited between runs is a
        # different question, and a row measured against the old one is not
        # an answer to it.
        "case_digest": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
        # The build under test. This is the field that stops a resume from
        # doing what the deploy hold was invented to prevent.
        "avs_version": __version__,
        "provider": provider or "anthropic",
    }


def write_checkpoint(
    result: CaseResult, case: ProductCase, *, provider: str | None,
    repo_dir: str | Path,
) -> Path | None:
    """Bank a finished case immediately, so a later death cannot spend it.

    Only MEASURED cases are banked. A case that crashed is exactly the one a
    resume must retry — banking it would make the resume permanent, turning a
    transient 529 into a case this bench never measures again.
    """
    if not result.measured:
        return None
    out = checkpoint_dir(repo_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{case.name}.yaml"
    payload = {
        "key": case_key(case, provider=provider),
        "saved_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "result": result.model_dump(mode="json"),
    }
    # Write-then-rename: a checkpoint half-written when the process group is
    # killed would be read back as a corrupt case on the next resume, which is
    # a worse failure than having no checkpoint at all.
    tmp_path = path.with_suffix(".yaml.partial")
    tmp_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    tmp_path.replace(path)
    return path


def read_checkpoint(
    case: ProductCase, *, provider: str | None, repo_dir: str | Path
) -> CaseResult | None:
    """The saved row for this exact case on this exact build, or None.

    Every rejection is silent-but-safe: an unreadable, stale or mismatched
    checkpoint means the case runs, which is what would have happened anyway.
    The only outcome this function must never produce is a row that does not
    match the key it was asked for.
    """
    path = checkpoint_dir(repo_dir) / f"{case.name}.yaml"
    if not path.exists():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(payload, dict) or payload.get("key") != case_key(
        case, provider=provider
    ):
        return None
    try:
        saved_at = datetime.datetime.fromisoformat(str(payload.get("saved_at")))
        age = datetime.datetime.now(datetime.UTC) - saved_at
    except (TypeError, ValueError):
        # A checkpoint that cannot say when it was written cannot be shown to
        # be recent, and this function's whole job is refusing what it cannot
        # verify.
        return None
    if age.days >= _CHECKPOINT_MAX_AGE_DAYS:
        return None
    try:
        result = CaseResult.model_validate(payload.get("result") or {})
    except Exception:  # noqa: BLE001 — a checkpoint we cannot parse is no checkpoint
        return None
    result.resumed = True
    return result


def preflight_provider(provider: str | None, model: str) -> str:
    """One cheap call before the expensive ones. "" when the account works.

    Run 17 spent 3438 seconds building case 01 and 1541 more against an
    account that had already stopped accepting calls. A single-token request
    up front turns that into a two-second refusal, and it is the only check
    here that costs anything at all — which is why it is worth having: every
    other guard in this module can only tell you afterwards.

    Errors that are NOT recognisably environmental are swallowed. This is a
    preflight, not a gate: refusing to start a three-hour run over an
    unrecognised blip would be the check causing the outage.
    """
    from ai_venture_studio.providers.base import get_provider

    name = provider or "anthropic"
    if name == "mock":
        return ""
    try:
        get_provider(name).complete(
            model=model, system="reply with ok", user="ok", max_tokens=1
        )
    except Exception as exc:  # noqa: BLE001 — classified, not handled
        return environment_failure(exc)
    return ""


def workspace_python(workspace: Path) -> str:
    """Environment parity: if the built product declares dependencies,
    probes (and the product they boot) run in an isolated env built from
    the product's OWN requirements — a framework outside this framework's
    venv must not read as a product failure."""
    import shutil

    requirements = workspace / "requirements.txt"
    if not requirements.exists() or not shutil.which("uv"):
        return sys.executable
    real_deps = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not real_deps:
        return sys.executable
    bin_dir, exe = (
        ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    )
    venv_python = workspace / ".probe-venv" / bin_dir / exe
    if venv_python.exists():
        return str(venv_python)
    created = subprocess.run(
        ["uv", "venv", str(workspace / ".probe-venv")],
        capture_output=True, text=True, timeout=120,
    )
    if created.returncode != 0:
        return sys.executable
    installed = subprocess.run(
        ["uv", "pip", "install", "-r", str(requirements),
         "--python", str(venv_python)],
        capture_output=True, text=True, timeout=300,
    )
    return str(venv_python) if installed.returncode == 0 else sys.executable


def _last_line_of(exc: subprocess.TimeoutExpired) -> str:
    """The last thing a wedged probe managed to say, if it said anything."""
    printed = (_as_text(exc.stdout) + "\n" + _as_text(exc.stderr)).strip().splitlines()
    return printed[-1][:160] if printed else ""


def run_probe(workspace: Path, probe: Probe) -> ProbeResult:
    """The probe runs IN the built workspace with the product's runtime
    env — it observes the product from outside, like a user's script."""
    import os

    with tempfile.NamedTemporaryFile(
        "w", suffix=f"-{probe.name}.py", delete=False
    ) as handle:
        handle.write(probe.script)
        probe_path = handle.name
    try:
        # testing._run, not subprocess.run: a probe routinely boots the
        # product's server, and subprocess.run's timeout kills the probe
        # alone — leaving that server alive, holding its port against the
        # next probe. Same fix as the test gate, one runner over.
        proc = _run_killing_the_group(
            [workspace_python(workspace), probe_path],
            cwd=workspace,
            timeout=_PROBE_TIMEOUT_S,
            env={**os.environ, "PYTHONPATH": str(workspace), **preview_env(workspace)},
        )
        detail = (proc.stdout or proc.stderr).strip().splitlines()
        return ProbeResult(
            name=probe.name,
            passed=proc.returncode == 0,
            detail=detail[-1][:200] if detail else "",
        )
    except subprocess.TimeoutExpired as exc:
        # What the probe printed before it wedged is the whole diagnosis;
        # "probe timed out" alone is the shape that left run 12 unexplained.
        printed = _last_line_of(exc)
        return ProbeResult(
            name=probe.name,
            passed=False,
            detail=f"probe timed out after {_PROBE_TIMEOUT_S}s"
                   + (f" — last output: {printed}" if printed else ""),
        )
    finally:
        Path(probe_path).unlink(missing_ok=True)


#: Re-exported under the name this module already used. The definition lives
#: beside the `Verdict` enum it is derived from — see `state.CLEAN_VERDICTS`.
CLEAN_VERDICTS = CLEAN_VERDICT_VALUES


def _lane_arrangement(workspace) -> str:
    """How the plan was laned, read off the workspace's own `plan.yaml`.

    A single-lane plan is the one arrangement `lane_check` can never object
    to, and until now the only way to notice a run had produced one was to
    open the preserved workspace — which run 18 had already overwritten
    (ADR-058). Best-effort by design: a case that never planned has no
    arrangement to report and that is not an error.
    """
    path = Path(workspace) / "product" / "plan.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(data, dict):
        return ""
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ""
    lanes = sorted(
        {str(t.get("lane", "core")) for t in tasks if isinstance(t, dict)}
    )
    arrangement = f"{len(lanes)} lane(s) over {len(tasks)} task(s): " + ", ".join(lanes)
    advisories = [
        str(c.get("problem", ""))
        for c in (data.get("critic_issues") or [])
        if isinstance(c, dict) and c.get("lens") == "parallelism"
    ]
    if advisories:
        arrangement += f" — {len(advisories)} parallelism advisory(ies)"
    return arrangement


def _row_detail(status: str, verdict: str | None, detail: str) -> str:
    """How much of a task's reason survives into the durable scoreboard row.

    A built-but-REJECTED task is a FAILING row as far as clean_review_rate is
    concerned, so it keeps its whole reason like any other failure. It used to
    be clipped to 200 chars along with the clean rows, and since a rejection
    that needed no repair wrote no reason at all, run 14 recorded 11
    rejections without one reason between them. Only a genuinely clean row
    stays terse — there is nothing to explain about an APPROVE.
    """
    if status != "built" or verdict not in CLEAN_VERDICTS:
        return detail
    return detail[:200]


# How many runs' preserved workspaces to keep on disk. Bounded, because each
# one is a full product tree; but bounded at a number of RUNS, not at one slot
# per case name. Five, so a comparison across two or three consecutive runs —
# the thing the last four investigations all needed — is always available.
_KEEP_WORKSPACE_RUNS = 5

_WORKSPACES_ROOT = Path(".mas") / "product-bench" / "workspaces"


def _preserve_workspace(
    workspace: Path | None,
    case_name: str,
    keep_dir: str | Path | None,
    run_stamp: str = "",
) -> str:
    """Copy a case workspace out of the temp dir before that dir is deleted.

    KEYED BY RUN, NOT BY CASE NAME. It used to be `workspaces/<case>`, with an
    `rmtree` of that path first — so run N's first act, for each case, was to
    delete the only copy of run N-1's evidence for that case. The result file
    kept pointing at the path, which now held different bytes; nothing recorded
    that the swap had happened. Run 18 destroyed run 17's four workspaces this
    way, and run 17 was the credit-exhaustion abort whose forensics were the
    reason anyone would look.

    A run stamp in the path makes the collision impossible rather than
    survivable, and `_prune_workspace_runs` bounds the disk cost by dropping
    whole old runs — a decision about age, which is reviewable, instead of a
    decision about name collision, which was invisible.
    """
    if workspace is None or not Path(workspace).exists():
        return ""
    import shutil as _shutil

    root = Path(keep_dir) if keep_dir else _WORKSPACES_ROOT
    keep = (root / run_stamp / case_name) if run_stamp else (root / case_name)
    if keep.exists():
        # Same run, same case, twice — should not happen, but overwriting is
        # what this function is being fixed for. Take a new name instead.
        for n in range(2, 100):
            candidate = keep.parent / f"{case_name}-{n}"
            if not candidate.exists():
                keep = candidate
                break
    keep.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copytree(workspace, keep, ignore=_shutil.ignore_patterns(".probe-venv"))
    return str(keep)


def _prune_workspace_runs(
    root: str | Path | None = None, keep: int = _KEEP_WORKSPACE_RUNS
) -> list[str]:
    """Drop all but the newest `keep` run directories. Returns what it removed.

    Only touches directories whose name looks like a run stamp, so a stray
    path under the root (including the pre-fix `workspaces/<case>` layout) is
    left alone rather than deleted by a rule that was not written for it.
    """
    import re
    import shutil as _shutil

    base = Path(root) if root else _WORKSPACES_ROOT
    if not base.is_dir() or keep < 1:
        return []
    stamped = sorted(
        (p for p in base.iterdir()
         if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{4}", p.name)),
        key=lambda p: p.name,
    )
    removed = []
    for path in stamped[:-keep]:
        _shutil.rmtree(path, ignore_errors=True)
        removed.append(path.name)
    return removed


def _proposed_scrs(workspace: Path) -> set[str]:
    """The UNAPPROVED spec-change requests sitting in the workspace.

    Read by name and by `status:` rather than by count, so a follow-up that
    happens to raise an SCR for an unrelated reason cannot be mistaken for
    the one this expectation is about, and an SCR a human later approves
    stops matching (ADR-046 only ever writes them `proposed`).
    """
    scr_dir = workspace / ".mas" / "scr"
    if not scr_dir.is_dir():
        return set()
    found = set()
    for path in scr_dir.glob("SCR-*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — a malformed SCR is not a crash here
            continue
        if isinstance(data, dict) and data.get("status") == "proposed":
            found.add(path.name)
    return found


def _score_increment(
    *, index: int, fdr: str, expected: str, status: str, new_scrs: set[str],
    intake_questions: list[str] | None = None,
) -> IncrementResult:
    """What the increment path actually did, against what the case asked.

    The observable differs per expectation because the OUTCOMES differ:
    a duplicate refuses and says so in the status, a contradiction under
    `--yes` proceeds to build and records the clash as an unapproved SCR
    (that is ADR-046's whole design — `--yes` may not approve a change to
    what the product promises), and a clean addition is just a build.
    So a raised SCR is checked on the filesystem, not inferred from the
    status, which for that path reads `completed` exactly like a case
    where the gate never fired at all. That indistinguishability is the
    failure mode this case exists to catch (ADR-048's inert gate).
    """
    # A raised SCR is the strongest signal available and outranks the
    # status, in BOTH directions: a follow-up expected to be a clean
    # addition that instead raised a contradiction is a wrong answer, not
    # a `completed` one, and reporting it as `completed` would hide the
    # gate misfiring behind a passing row.
    actual = "raises_scr" if new_scrs else status
    detail = f"status={status}; new proposed SCR(s): " + (
        ", ".join(sorted(new_scrs)) if new_scrs else "none"
    )
    # AND WHAT INTAKE ASKED FOR, when intake is where it stopped. A
    # `needs_answers` row is indistinguishable from a gate that answered
    # wrongly — same 0, same denominator — and the difference is the whole
    # reading: run 18's gate rate of 0% was three FDRs that never reached the
    # gate, which nobody could tell from the result file (ADR-058). The
    # questions are the evidence for which of the two it was.
    if status == "needs_answers" and intake_questions:
        asked = "; ".join(str(q) for q in intake_questions[:3])
        detail += (
            f"; STOPPED AT INTAKE, the gate never ran — assessor asked: {asked}"
        )
    return IncrementResult(
        index=index,
        fdr=fdr[:200],
        expected=expected,
        actual=actual,
        correct=actual == expected,
        detail=detail[:400],
    )


def run_case(
    case: ProductCase, *, provider: str | None = None,
    keep_dir: str | Path | None = None,
    cost_model: CostModel | None = None,
    run_stamp: str = "",
) -> CaseResult:
    import time

    start = time.monotonic()
    # mkdtemp + rmtree(ignore_errors): the T3 docker sandbox (present on
    # CI runners) writes root-owned __pycache__ inside, and on CPython
    # 3.12 TemporaryDirectory's ignore_cleanup_errors still raises through
    # its resetperms path there. A leaked tmp file on an ephemeral runner
    # is harmless; a crashed suite is not.
    tmp = tempfile.mkdtemp(prefix="avs-productbench-")
    workspace: Path | None = None
    try:
        workspace = init_workspace(Path(tmp) / case.name, case.name, case.profile)
        (workspace / "FDR.md").write_text(case.fdr, encoding="utf-8")
        result = run_autopilot(
            workspace,
            workspace / "FDR.md",
            provider=provider or "anthropic",
            yes=True,
        )
        all_outcomes = list(result.outcomes)
        statuses = [result.status]
        increments: list[IncrementResult] = []
        if result.status == "completed" and case.feature_fdrs:
            from ai_venture_studio.upstream.autopilot import run_feature

            for i, feature_fdr in enumerate(case.feature_fdrs):
                fdr_path = workspace / f".bench-feature-{i}.md"
                fdr_path.write_text(feature_fdr, encoding="utf-8")
                scrs_before = _proposed_scrs(workspace)
                feature_result = run_feature(
                    workspace, fdr_path, provider=provider or "anthropic", yes=True
                )
                statuses.append(feature_result.status)
                all_outcomes += feature_result.outcomes
                if i < len(case.feature_expectations):
                    increments.append(
                        _score_increment(
                            index=i,
                            fdr=feature_fdr,
                            expected=case.feature_expectations[i],
                            status=feature_result.status,
                            new_scrs=_proposed_scrs(workspace) - scrs_before,
                            intake_questions=list(
                                getattr(feature_result.assessment, "questions", None)
                                or []
                            ),
                        )
                    )
            result.outcomes = all_outcomes
            # `already_satisfied` is a CORRECT outcome, not a failed build:
            # the feature FDR asked for a promise the product already keeps
            # and the system declined to build it twice (ADR-046). Scoring
            # it as a failure would mean the only way to pass this bench is
            # to do the redundant work.
            if any(s not in ("completed", "already_satisfied") for s in statuses):
                result.status = "failed"

        built = [o for o in result.outcomes if o.status == "built"]
        clean = [
            o for o in built
            if o.review_verdict in CLEAN_VERDICTS
        ]
        case_probes = list(case.probes)
        probegen_dry = False
        if case.auto_probes:
            from ai_venture_studio.upstream import probegen as probegen_mod

            generated, gen_notes = probegen_mod.generate_probes(
                workspace, provider=provider or "anthropic"
            )
            if not generated:
                # Model-shaped output: one retry before declaring the case
                # unmeasured (run 9, case 03: zero probes silently scored
                # as 0% and nothing said so).
                generated, gen_notes = probegen_mod.generate_probes(
                    workspace, provider=provider or "anthropic"
                )
            probegen_dry = case.auto_probes and not generated
            case_probes += [Probe(name=g.name, script=g.script) for g in generated]
        probes = [run_probe(workspace, probe) for probe in case_probes]
        if probegen_dry:
            probes.append(ProbeResult(
                name="probe-generation",
                passed=False,
                detail=("probegen produced no probes after a retry — case "
                        "behavior UNMEASURED, scored as a failure. Why: "
                        + ("; ".join(gen_notes[:3]) if gen_notes else "no notes"))[:400],
            ))
        preserved = ""
        if (
            result.status != "completed"
            or not all(p.passed for p in probes)
            or len(clean) < len(built)
        ):
            # Failure forensics: the temp workspace would vanish with the
            # scoreboard's most important evidence.
            #
            # `len(clean) < len(built)` is here because the clean-review rate
            # is the number this bench most often has to explain, and it was
            # the one whose evidence was deleted by design. Run 16's clean
            # rate fell 55% → 31%; every rejected task lived in a case that
            # completed with all probes passing, so not one of their reviews
            # was kept, and diagnosing the fall had to proceed from the
            # truncated `detail` strings in the result YAML. A rejection is a
            # finding about the machine, not just about the product, and it
            # deserves the same forensics a crash gets. (ADR-036's family:
            # the run that could have proved what happened deleted itself.)
            preserved = _preserve_workspace(
                workspace, case.name, keep_dir, run_stamp
            )
        return CaseResult(
            name=case.name,
            autopilot_status=result.status,
            axis=case.axis,
            increments=increments,
            tasks_total=len(result.outcomes),
            tasks_built=len(built),
            clean_reviews=len(clean),
            # The pipeline knows why it stopped; the scoreboard used to be
            # the place that forgot. Run 16's case 02 came back `failed`
            # with no tasks, and the reason — the planner's output would not
            # parse, twice — was sitting in `product/plan.yaml` unread.
            failure_reason=result.blocked_reason,
            lanes=_lane_arrangement(workspace),
            # 200 chars cut the diagnosis mid-word in the scoreboard ("does not
            # match any EAR"), and the row is the durable record of the run —
            # the workspace it points at is gitignored and has been lost before.
            # A failing row now carries the whole reason plus the test summary
            # the outcome kept; a built row stays terse.
            outcomes=[
                {"task_id": o.task_id, "title": o.title, "status": o.status,
                 "review": o.review_verdict,
                 "detail": _row_detail(o.status, o.review_verdict, o.detail),
                 **({"test_summary": o.test_summary} if o.test_summary else {}),
                 **({"iterations": o.iterations} if o.iterations else {}),
                 # WHO rejected, not just how often. Diagnosing run 13's
                 # clean-review rate meant hand-reading preserved review YAML
                 # to discover that one deterministic tool raised 60% of every
                 # blocking finding; the row now carries that on its own.
                 **({"blocking_by_voter": o.blocking_by_voter}
                    if getattr(o, "blocking_by_voter", None) else {})}
                for o in result.outcomes
            ],
            preserved_workspace=preserved,
            probes=probes,
            duration_s=round(time.monotonic() - start, 1),
            spend=_case_spend(workspace, cost_model or CostModel()),
        )
    except BaseException as exc:
        # The case that CRASHED is the one whose workspace you need, and it
        # was the only one thrown away: preservation ran after the autopilot
        # call, so an exception jumped over it and the `finally` below deleted
        # the evidence. Run 12's case 04 hung, took its workspace with it, and
        # the hang stayed unexplained afterwards because there was nothing
        # left to look at — the product that hung no longer existed anywhere.
        # The path rides on the exception so the caller that turns it into an
        # error row can point at it.
        #
        # Nothing in here may replace the exception being handled: a copy that
        # fails, or an exception type that refuses attributes, would report a
        # bookkeeping error where the real failure was.
        try:
            exc.avs_preserved_workspace = _preserve_workspace(  # type: ignore[attr-defined]
                workspace, case.name, keep_dir, run_stamp
            )
            # A crashed case is where "what did that cost" matters MOST: run
            # 17 spent 3438 seconds and died, and the money was as
            # unrecoverable as the measurement. Rides on the exception for
            # the same reason the workspace path does — the caller builds the
            # error row and this is the only way the number reaches it.
            exc.avs_case_spend = _case_spend(workspace, cost_model or CostModel())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — forensics must not mask the failure
            pass
        raise
    finally:
        import shutil as _shutil_cleanup

        _shutil_cleanup.rmtree(tmp, ignore_errors=True)


def run_product_bench(
    cases_dir: str | Path, *, provider: str | None = None, limit: int | None = None,
    repo_dir: str | Path = ".", resume: bool = False,
) -> BenchSummary:
    pidfile = acquire_bench_lock(repo_dir)
    try:
        return _run_product_bench(
            cases_dir, provider=provider, limit=limit, repo_dir=repo_dir,
            resume=resume,
        )
    finally:
        release_bench_lock(pidfile)


def _run_product_bench(
    cases_dir: str | Path, *, provider: str | None = None, limit: int | None = None,
    repo_dir: str | Path = ".", resume: bool = False,
) -> BenchSummary:
    import time

    cases = load_cases(cases_dir)[: limit or None]
    # Loaded ONCE, here, and handed down: prices belong to the operator's repo,
    # never to the throwaway workspace each case builds in (see
    # `bench_cost_model`). Reading it per case would also let a price table
    # edited mid-run split one result file across two of them.
    cost_model = bench_cost_model(repo_dir)
    # STAMPED AT THE START, not at save time. Preserved workspaces are written
    # while the run is still going, so the name they are filed under has to
    # exist before the first case does. It is the same format the result file
    # uses, which is what lets a reader move from `result-<stamp>.yaml` to
    # `workspaces/<stamp>/` without a lookup table (ADR-058).
    run_stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M")
    dropped = _prune_workspace_runs(Path(repo_dir) / _WORKSPACES_ROOT)
    if dropped:
        print(f"  pruned preserved workspaces from {len(dropped)} older run(s): "
              f"{', '.join(dropped)}")
    results = []
    aborted = ""
    recent: list[str] = []
    for case in cases:
        if aborted:
            # THE REST OF THE RUN IS NOT FIVE SEPARATE FAILURES. Once the
            # account is gone, every remaining case fails for one reason, and
            # recording them one at a time — as run 17 did, four times, in
            # 0.3s each — spends the wall-clock to rediscover what the first
            # failure already said. They are unmeasured (`error:` prefix), so
            # ADR-035 keeps them out of every rate and names them in scope.
            results.append(
                CaseResult(
                    name=case.name,
                    autopilot_status=f"error: environment: {aborted}",
                    axis=case.axis,
                )
            )
            continue
        if resume:
            banked = read_checkpoint(case, provider=provider, repo_dir=repo_dir)
            if banked is not None:
                results.append(banked)
                continue
        start = time.monotonic()
        try:
            result = run_case(
                case, provider=provider, cost_model=cost_model,
                keep_dir=Path(repo_dir) / _WORKSPACES_ROOT,
                run_stamp=run_stamp,
            )
        except Exception as exc:  # noqa: BLE001 — one case never kills the bench
            # ...but one ENVIRONMENT does. The comment above stayed true for
            # eleven runs and was wrong for exactly one kind of failure, which
            # is the kind run 17 hit.
            reason = environment_failure(exc)
            recent.append(_error_signature(exc))
            if not reason and len(recent) >= _REPEAT_ABORT_THRESHOLD and (
                len(set(recent[-_REPEAT_ABORT_THRESHOLD:])) == 1
            ):
                # The backstop, and the half that cannot rot: whatever this
                # is, two cases in a row dying with a byte-identical error is
                # not a property of either case. It needs no vocabulary and
                # keeps working after the provider rewords its messages,
                # which the table above will not.
                reason = (
                    f"{_REPEAT_ABORT_THRESHOLD} consecutive cases failed "
                    f"identically ({recent[-1]}) — this is not case-specific"
                )
            if reason:
                aborted = reason
            # A crashed case still spent real wall-clock — a 0.0s error row
            # reads as "died instantly" when the failure may be an hour in.
            results.append(
                CaseResult(
                    name=case.name,
                    autopilot_status=f"error: {type(exc).__name__}: {str(exc)[:120]}",
                    # A crashed case still belongs to the axis it was written
                    # for, or it would be dropped from the increment rate and
                    # silently counted against the headline denominator.
                    axis=case.axis,
                    # Written by run_case on its way out. Without it the row
                    # names a failure whose evidence is already deleted.
                    preserved_workspace=getattr(exc, "avs_preserved_workspace", ""),
                    duration_s=round(time.monotonic() - start, 1),
                    # Same provenance as the workspace path above: metered on
                    # the way out of run_case, because the ledger dies with
                    # the temp tree. A run that aborts on a dead account has
                    # spent money right up to the moment it stopped.
                    spend=getattr(exc, "avs_case_spend", None),
                )
            )
            continue
        recent.clear()
        results.append(result)
        # BANKED BEFORE THE NEXT CASE STARTS, not at the end of the run. The
        # next case is the one that can take the process down, and everything
        # measured before it is worth more than the run that is still going.
        try:
            write_checkpoint(result, case, provider=provider, repo_dir=repo_dir)
        except OSError:  # noqa: PERF203 — a bank that fails must not lose the run
            # Deliberately not fatal and deliberately not silent-forever: the
            # measurement in hand is worth more than the optimisation, and
            # `save_summary` still has it. The cost of losing this is one
            # re-run, which is the cost of the world before this existed.
            pass

    def _avg(values: list[float | None]) -> float | None:
        # Only cases that produced the denominator count. A case with no
        # data is dropped from that rate, never entered as a zero.
        #
        # And when NOTHING produced the denominator, the rate is None — not
        # 0.0. `CaseResult` has returned None for exactly this since ADR-035;
        # this function flattened it back to a zero one level up, which is
        # the same defect the ADR was written about. It matters most in the
        # path nobody had taken: a run of increment cases only has an empty
        # build axis, and a 0.0 here is read by `bench_criterion` as a run
        # that scored zero rather than one that never asked.
        measured = [v for v in values if v is not None]
        return sum(measured) / len(measured) if measured else None

    # The headline three are averaged over BUILD-axis cases only, so the
    # run-13..17 series stays comparable when increment cases are added
    # beside it: an increment case whose correct answer is "nothing was
    # built" would otherwise enter the build rate as a 0.0 for being right
    # (ADR-049).
    build = [r for r in results if r.axis == "build"]
    increment = [r for r in results if r.axis == "increment"]
    gate_values = [r.gate_rate for r in increment if r.gate_rate is not None]
    # Summed over EVERY case, not just the measured ones — see the field's
    # own note. Rows without a spend contribute nothing rather than zero,
    # which is the same distinction `_avg` makes one block above.
    metered = [r.spend for r in results if r.spend is not None and not r.resumed]
    run_spend = (
        CaseSpend(
            calls=sum(s.calls for s in metered),
            input_tokens=sum(s.input_tokens for s in metered),
            output_tokens=sum(s.output_tokens for s in metered),
            # A run is priced only to the extent its parts were. If any case
            # went unpriced the total is a floor, and if none was priced at
            # all there is no total to report — only counts.
            usd=(
                None
                if all(s.usd is None for s in metered)
                else round(sum(s.usd or 0.0 for s in metered), 6)
            ),
            unpriced_calls=sum(s.unpriced_calls for s in metered),
        )
        if metered
        else None
    )
    return BenchSummary(
        cases=results,
        build_rate=_avg([r.build_rate for r in build]),
        probe_pass_rate=_avg([r.probe_pass_rate for r in build]),
        clean_review_rate=_avg([r.clean_review_rate for r in build]),
        # Read from the case's own one decision, never re-derived from a
        # rate — deriving it from `build_rate is None` is what let the list
        # disagree with the averages it was describing.
        unmeasured=[r.name for r in build if not r.measured],
        # None, not 0.0, when no increment case reported: a rate of zero
        # says the gate answered wrongly every time, and a run that never
        # asked must not be readable as that.
        gate_rate=(sum(gate_values) / len(gate_values)) if gate_values else None,
        gate_unmeasured=[
            r.name for r in increment if not r.measured or r.gate_rate is None
        ],
        aborted=aborted,
        spend=run_spend,
        run_stamp=run_stamp,
    )


def _round_rate(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def save_summary(
    summary: BenchSummary, repo_dir: str | Path, *, provider: str | None = None,
) -> Path:
    out_dir = Path(repo_dir) / ".mas" / "product-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    # The run's own stamp when it has one, so the result file and
    # `.mas/product-bench/workspaces/<stamp>/` name the same run. Falling back
    # to now() keeps hand-built summaries (and the tests) working.
    stamp = summary.run_stamp or datetime.datetime.now(datetime.UTC).strftime(
        "%Y-%m-%d-%H%M"
    )
    path = out_dir / f"result-{stamp}.yaml"
    payload = summary.model_dump(mode="json")
    # Which build produced these numbers. Attributing run 14 to a version took
    # diffing git commit timestamps against the result's filename, and that
    # only worked because the release happened to land 9 minutes before the
    # run — a row that cannot name its own build cannot be compared to the row
    # above it, which is the entire point of a series.
    from ai_venture_studio import __version__

    payload["avs_version"] = __version__
    # WHAT produced these numbers, for the same reason as the line above it
    # records which build did. `avs product-bench --provider mock` is a
    # supported invocation and the result file it wrote was byte-identical in
    # shape to a real one — no field named the instrument, so the capability
    # criterion read a regex table's output as a measurement of the system.
    # Recorded on every run, not only simulated ones: a field that appears
    # exactly when something is wrong is a field nobody thinks to look for.
    payload["provider"] = provider or "anthropic"
    payload["rates"] = {
        # `null`, not omitted, when the rate had nothing to average: a key
        # that is present and null says this run considered the rate and
        # found no denominator, where an absent key is indistinguishable
        # from a file written before the field existed.
        "build_rate": _round_rate(summary.build_rate),
        "probe_pass_rate": _round_rate(summary.probe_pass_rate),
        "clean_review_rate": _round_rate(summary.clean_review_rate),
        # The denominator travels with the numbers into the series the kill
        # criterion reads — "75%" over three of four cases is not the same
        # reading as "75%" over four, and a later reader has only this file.
        "cases_measured": summary.cases_measured,
        # Build-axis cases, not every case in the directory: adding an
        # increment case must not silently change what "of 4" meant in
        # every row above this one (ADR-049).
        "cases_total": summary.cases_total,
        "unmeasured": list(summary.unmeasured),
        # Which rows this run MEASURED and which it read back off disk. A
        # resumed run whose result file does not distinguish them is a
        # scoreboard claiming work it did not do, and every number in the
        # reused row is real enough to hide it.
        "resumed": [c.name for c in summary.cases if c.resumed],
    }
    # Cost is placed deliberately rather than left where `model_dump` put it,
    # and it sits OUTSIDE `rates` on purpose: it is not a rate, and a number
    # the kill criterion must never read has no business sitting in the block
    # it reads. The caveat travels in the file with the number, because the
    # person who opens this in six months is exactly the person who cannot
    # ask whether the price table was complete.
    spend = payload.pop("spend", None)
    if spend:
        payload["cost"] = dict(spend)
        if spend["usd"] is None:
            payload["cost"]["note"] = (
                "no price in .mas/cost-model.yaml covered this run — token "
                "counts are exact, there is simply no price to apply. Not a "
                "cost of zero."
            )
        elif spend["unpriced_calls"]:
            payload["cost"]["note"] = (
                f"FLOOR, not a total: {spend['unpriced_calls']} of "
                f"{spend['calls']} calls used a model with no price in "
                f".mas/cost-model.yaml. The real cost is higher by whatever "
                f"those calls cost."
            )
    if summary.aborted:
        # Above the rates in the file, because it changes how to read them:
        # "four cases failed" and "this run never got to ask them" are
        # different findings and the percentages look the same.
        payload["aborted"] = summary.aborted
    if summary.gate_cases_total:
        payload["rates"]["increment"] = {
            "gate_rate": (
                round(summary.gate_rate, 3) if summary.gate_rate is not None else None
            ),
            "cases_measured": summary.gate_cases_measured,
            "cases_total": summary.gate_cases_total,
            "unmeasured": list(summary.gate_unmeasured),
        }
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path.write_text(rendered, encoding="utf-8")
    # Durable copy: .mas/ is gitignored and was lost once (2026-07-26, the
    # entire bench history) — scoreboards are small and secret-free, so they
    # also land in the tracked benchmarks/results/.
    #
    # Except for a simulated run, which is not a capability reading and so has
    # no business in the ledger the kill criterion reads. It still lands in
    # `.mas/` above: a mock run is a real exercise of the HARNESS, and its
    # scoreboard is how you check the harness worked. Two layers, because they
    # catch different mistakes — this one keeps simulated numbers out of the
    # tracked directory, and `bench_criterion._scan` refuses to count one that
    # gets there anyway (copied by hand, written by an older build, resumed
    # from a checkpoint). Either alone would be silent about the other's case.
    from ai_venture_studio.providers.base import is_simulated

    tracked = Path(repo_dir) / "benchmarks" / "results"
    if (Path(repo_dir) / "benchmarks").is_dir() and not is_simulated(provider):
        tracked.mkdir(exist_ok=True)
        (tracked / path.name).write_text(rendered, encoding="utf-8")
    return path
