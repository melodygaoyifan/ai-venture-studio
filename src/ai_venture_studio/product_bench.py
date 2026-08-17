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


class Probe(BaseModel):
    name: str
    script: str  # python source, exit 0 = behavior works


class ProductCase(BaseModel):
    name: str
    profile: str = "web"
    fdr: str
    feature_fdrs: list[str] = Field(
        default_factory=list,
        description="granular follow-up FDRs applied via the feature flow",
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


class ProbeResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class CaseResult(BaseModel):
    name: str
    autopilot_status: str
    tasks_total: int = 0
    tasks_built: int = 0
    clean_reviews: int = 0
    outcomes: list[dict] = Field(
        default_factory=list, description="per-task forensics: status + detail"
    )
    preserved_workspace: str = ""
    probes: list[ProbeResult] = Field(default_factory=list)
    duration_s: float = 0.0
    #: Why this case produced nothing, when it ran and produced nothing.
    #: The rate says how badly; only this says why, and without it a 0.0
    #: from a refused plan is indistinguishable from a 0.0 from a pipeline
    #: that tried everything (ADR-043). Empty on cases that built.
    failure_reason: str = ""

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


class BenchSummary(BaseModel):
    cases: list[CaseResult]
    build_rate: float
    probe_pass_rate: float
    clean_review_rate: float
    # Named, not just absent: a rate averaged over 3 of 4 cases and one
    # averaged over 4 are different measurements, and the reader cannot
    # tell them apart from the percentages alone.
    unmeasured: list[str] = Field(default_factory=list)

    @property
    def cases_measured(self) -> int:
        return len(self.cases) - len(self.unmeasured)


def load_cases(cases_dir: str | Path) -> list[ProductCase]:
    cases = [
        ProductCase.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(Path(cases_dir).glob("*.yaml"))
    ]
    if not cases:
        raise FileNotFoundError(f"no product cases in {cases_dir}")
    return cases


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


def _preserve_workspace(
    workspace: Path | None, case_name: str, keep_dir: str | Path | None
) -> str:
    """Copy a case workspace out of the temp dir before that dir is deleted."""
    if workspace is None or not Path(workspace).exists():
        return ""
    import shutil as _shutil

    keep = Path(keep_dir or Path(".mas") / "product-bench" / "workspaces") / case_name
    _shutil.rmtree(keep, ignore_errors=True)
    keep.parent.mkdir(parents=True, exist_ok=True)
    _shutil.copytree(workspace, keep, ignore=_shutil.ignore_patterns(".probe-venv"))
    return str(keep)


def run_case(
    case: ProductCase, *, provider: str | None = None,
    keep_dir: str | Path | None = None,
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
        if result.status == "completed" and case.feature_fdrs:
            from ai_venture_studio.upstream.autopilot import run_feature

            for i, feature_fdr in enumerate(case.feature_fdrs):
                fdr_path = workspace / f".bench-feature-{i}.md"
                fdr_path.write_text(feature_fdr, encoding="utf-8")
                feature_result = run_feature(
                    workspace, fdr_path, provider=provider or "anthropic", yes=True
                )
                statuses.append(feature_result.status)
                all_outcomes += feature_result.outcomes
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
            preserved = _preserve_workspace(workspace, case.name, keep_dir)
        return CaseResult(
            name=case.name,
            autopilot_status=result.status,
            tasks_total=len(result.outcomes),
            tasks_built=len(built),
            clean_reviews=len(clean),
            # The pipeline knows why it stopped; the scoreboard used to be
            # the place that forgot. Run 16's case 02 came back `failed`
            # with no tasks, and the reason — the planner's output would not
            # parse, twice — was sitting in `product/plan.yaml` unread.
            failure_reason=result.blocked_reason,
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
                workspace, case.name, keep_dir
            )
        except Exception:  # noqa: BLE001 — forensics must not mask the failure
            pass
        raise
    finally:
        import shutil as _shutil_cleanup

        _shutil_cleanup.rmtree(tmp, ignore_errors=True)


def run_product_bench(
    cases_dir: str | Path, *, provider: str | None = None, limit: int | None = None,
    repo_dir: str | Path = ".",
) -> BenchSummary:
    pidfile = acquire_bench_lock(repo_dir)
    try:
        return _run_product_bench(cases_dir, provider=provider, limit=limit)
    finally:
        release_bench_lock(pidfile)


def _run_product_bench(
    cases_dir: str | Path, *, provider: str | None = None, limit: int | None = None
) -> BenchSummary:
    import time

    cases = load_cases(cases_dir)[: limit or None]
    results = []
    for case in cases:
        start = time.monotonic()
        try:
            results.append(run_case(case, provider=provider))
        except Exception as exc:  # noqa: BLE001 — one case never kills the bench
            # A crashed case still spent real wall-clock — a 0.0s error row
            # reads as "died instantly" when the failure may be an hour in.
            results.append(
                CaseResult(
                    name=case.name,
                    autopilot_status=f"error: {type(exc).__name__}: {str(exc)[:120]}",
                    # Written by run_case on its way out. Without it the row
                    # names a failure whose evidence is already deleted.
                    preserved_workspace=getattr(exc, "avs_preserved_workspace", ""),
                    duration_s=round(time.monotonic() - start, 1),
                )
            )

    def _avg(values: list[float | None]) -> float:
        # Only cases that produced the denominator count. A case with no
        # data is dropped from that rate, never entered as a zero.
        measured = [v for v in values if v is not None]
        return sum(measured) / len(measured) if measured else 0.0

    return BenchSummary(
        cases=results,
        build_rate=_avg([r.build_rate for r in results]),
        probe_pass_rate=_avg([r.probe_pass_rate for r in results]),
        clean_review_rate=_avg([r.clean_review_rate for r in results]),
        # Read from the case's own one decision, never re-derived from a
        # rate — deriving it from `build_rate is None` is what let the list
        # disagree with the averages it was describing.
        unmeasured=[r.name for r in results if not r.measured],
    )


def save_summary(summary: BenchSummary, repo_dir: str | Path) -> Path:
    out_dir = Path(repo_dir) / ".mas" / "product-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"result-{stamp}.yaml"
    payload = summary.model_dump(mode="json")
    # Which build produced these numbers. Attributing run 14 to a version took
    # diffing git commit timestamps against the result's filename, and that
    # only worked because the release happened to land 9 minutes before the
    # run — a row that cannot name its own build cannot be compared to the row
    # above it, which is the entire point of a series.
    from ai_venture_studio import __version__

    payload["avs_version"] = __version__
    payload["rates"] = {
        "build_rate": round(summary.build_rate, 3),
        "probe_pass_rate": round(summary.probe_pass_rate, 3),
        "clean_review_rate": round(summary.clean_review_rate, 3),
        # The denominator travels with the numbers into the series the kill
        # criterion reads — "75%" over three of four cases is not the same
        # reading as "75%" over four, and a later reader has only this file.
        "cases_measured": summary.cases_measured,
        "cases_total": len(summary.cases),
        "unmeasured": list(summary.unmeasured),
    }
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    path.write_text(rendered, encoding="utf-8")
    # Durable copy: .mas/ is gitignored and was lost once (2026-07-26, the
    # entire bench history) — scoreboards are small and secret-free, so they
    # also land in the tracked benchmarks/results/.
    tracked = Path(repo_dir) / "benchmarks" / "results"
    if (Path(repo_dir) / "benchmarks").is_dir():
        tracked.mkdir(exist_ok=True)
        (tracked / path.name).write_text(rendered, encoding="utf-8")
    return path
