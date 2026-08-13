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

    # Each rate is None when the case produced no denominator for it — a
    # case that crashed before planning has no build rate, and averaging a
    # 0.0 in its place records "the machine failed" where the truth is "we
    # did not measure". The kill criterion reads these averages, so that
    # substitution lets an infrastructure crash fire a capability verdict.
    @property
    def build_rate(self) -> float | None:
        return self.tasks_built / self.tasks_total if self.tasks_total else None

    @property
    def probe_pass_rate(self) -> float | None:
        return (
            sum(1 for p in self.probes if p.passed) / len(self.probes)
            if self.probes
            else None
        )

    @property
    def clean_review_rate(self) -> float | None:
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
            if any(s != "completed" for s in statuses):
                result.status = "failed"

        built = [o for o in result.outcomes if o.status == "built"]
        clean = [
            o for o in built
            if o.review_verdict in ("APPROVE", "APPROVE_WITH_NOTES")
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
        if result.status != "completed" or not all(p.passed for p in probes):
            # Failure forensics: the temp workspace would vanish with the
            # scoreboard's most important evidence.
            preserved = _preserve_workspace(workspace, case.name, keep_dir)
        return CaseResult(
            name=case.name,
            autopilot_status=result.status,
            tasks_total=len(result.outcomes),
            tasks_built=len(built),
            clean_reviews=len(clean),
            # 200 chars cut the diagnosis mid-word in the scoreboard ("does not
            # match any EAR"), and the row is the durable record of the run —
            # the workspace it points at is gitignored and has been lost before.
            # A failing row now carries the whole reason plus the test summary
            # the outcome kept; a built row stays terse.
            outcomes=[
                {"task_id": o.task_id, "title": o.title, "status": o.status,
                 "review": o.review_verdict,
                 "detail": o.detail if o.status != "built" else o.detail[:200],
                 **({"test_summary": o.test_summary} if o.test_summary else {}),
                 **({"iterations": o.iterations} if o.iterations else {})}
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
        unmeasured=[r.name for r in results if r.build_rate is None],
    )


def save_summary(summary: BenchSummary, repo_dir: str | Path) -> Path:
    out_dir = Path(repo_dir) / ".mas" / "product-bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d-%H%M")
    path = out_dir / f"result-{stamp}.yaml"
    payload = summary.model_dump(mode="json")
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
