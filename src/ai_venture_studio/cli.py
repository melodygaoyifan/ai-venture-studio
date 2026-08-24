"""CLI entry point: `avs review <target>`.

Target is a GitHub PR URL (requires `gh` auth) or a local git revision
range such as `main...HEAD`.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ai_venture_studio.adoption import StageInactiveError, check_stage
from ai_venture_studio.orchestrator import is_interrupted, resume_review, run_review
from ai_venture_studio.state import Verdict

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()

from ai_venture_studio.paths import skills_root as _skills_root
from ai_venture_studio.executables import resolve

_DEFAULT_SKILLS = _skills_root()


def _version_callback(value: bool) -> None:
    if value:
        from ai_venture_studio import __version__

        console.print(__version__)
        raise typer.Exit


@app.callback()
def _root(
    version: bool = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Print the installed version and exit",
    ),
) -> None:
    """ai-venture-studio — multi-agent review-side SDLC system."""


@app.command()
def review(
    target: str = typer.Argument(
        None,
        help="GitHub PR / GitLab MR URL or git range (e.g. main...HEAD); "
        "omit with --from-ci",
    ),
    repo_dir: str = typer.Option(".", help="Repository to review in"),
    skills_dir: str = typer.Option(str(_DEFAULT_SKILLS), help="Voter skills directory"),
    mode: str = typer.Option(None, help="Override mode: fast | standard | deep"),
    provider: str = typer.Option(
        None,
        help="Force one provider for all voters (e.g. 'mock' for offline runs; "
        "heterogeneity is the default posture)",
    ),
    from_ci: bool = typer.Option(
        False, "--from-ci",
        help="Derive the target from CI predefined variables (GitLab CI "
        "merge-request pipelines, GitHub Actions pull_request events) — "
        "the webhook-less entry point for locked-down networks",
    ),
):
    from ai_venture_studio.ci import CITargetError, detect_ci_target
    from ai_venture_studio.forge import recognize_unsupported

    if from_ci:
        if target is not None:
            console.print("[red]--from-ci derives the target; do not pass one[/red]")
            raise typer.Exit(code=2)
        try:
            target = detect_ci_target()
        except CITargetError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        console.print(f"[dim]CI target: {target}[/dim]")
    elif target is None:
        console.print("[red]a target is required (or pass --from-ci in a pipeline)[/red]")
        raise typer.Exit(code=2)
    unsupported = recognize_unsupported(target)
    if unsupported:
        # A recognized-but-unsupported forge must fail here, loudly — the
        # fallthrough would hand the URL to `git diff` and produce garbage.
        console.print(
            f"[red]{unsupported} pull-request URLs are recognized but not "
            "yet supported; review the local range instead "
            "(avs review origin/<target-branch>...HEAD)[/red]"
        )
        raise typer.Exit(code=2)
    # Substrate ladder guard (ADR-U15): no-op unless the workspace declares
    # .mas/substrate-profile.yaml; below-floor stages refuse loudly.
    try:
        check_stage(repo_dir, "code_review")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc

    result, state = run_review(
        target,
        repo_dir=repo_dir,
        skills_dir=skills_dir,
        provider_override=provider,
        mode_override=mode,
    )

    if not state.get("dor_pass"):
        console.print("[yellow]Not ready for review (Gate 1 failed):[/yellow]")
        for reason in state.get("dor_reasons", []):
            console.print(f"  - {reason}")
        raise typer.Exit(code=2)

    if is_interrupted(state):
        console.print(
            f"\n[bold red]{state['leader']['verdict']}[/bold red] — paused at "
            "Gate 3 (Review Gate) for human decision."
        )
        if state.get("hitl_issue_url"):
            console.print(f"Issue: {state['hitl_issue_url']}")
        elif state.get("hitl_note"):
            console.print(f"(no issue created: {state['hitl_note']})")
        console.print(
            f"Resume with: avs resume {state['review_id']} "
            f"--decision ack   (or --decision override:REQUEST_CHANGES)"
        )
        raise typer.Exit(code=3)

    # Every terminating path above returns or exits, so a missing result here
    # means the graph ended in a state none of them describe. An `assert`
    # would say so only until someone ran under `-O`, and then the next line
    # would raise AttributeError on None instead — a real invariant deserves
    # a real check and a diagnosable message.
    if result is None:
        console.print(
            "[red]Internal error: the review graph produced no result and did "
            "not pause.[/red]\n"
            f"Last node: {state.get('__last_node__', 'unknown')}; "
            f"review_id: {state.get('review_id', 'unknown')}; "
            f"artifacts: {state.get('artifacts_dir', 'none')}"
        )
        raise typer.Exit(code=2)
    color = {
        Verdict.APPROVE: "green",
        Verdict.APPROVE_WITH_NOTES: "green",
        Verdict.REQUEST_CHANGES: "yellow",
    }.get(result.verdict, "red")
    console.print(
        f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}"
    )
    from ai_venture_studio.adoption import adoption_banners

    for banner in adoption_banners(repo_dir):
        console.print(f"[dim]{banner}[/dim]")

    if result.findings:
        table = Table(show_lines=False)
        table.add_column("Sev")
        table.add_column("Location")
        table.add_column("Finding")
        for f in result.findings:
            table.add_row(
                f.severity.value,
                f"{f.file_path}:{f.line_start}",
                f"{f.title} [{f.voter}]",
            )
        console.print(table)

    console.print(f"\nArtifacts: {state['artifacts_dir']}")
    if result.verdict.is_escalation:
        raise typer.Exit(code=3)


@app.command()
def resume(
    review_id: str = typer.Argument(..., help="Review ID shown when the run paused"),
    decision: str = typer.Option(
        ..., help="'ack' to accept the verdict, or 'override:<VERDICT>'"
    ),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Continue a review paused at Gate 3 (Review Gate)."""
    result, state = resume_review(review_id, decision, repo_dir=repo_dir)
    if result is None:
        # A resume that neither produces a verdict nor re-pauses means the
        # checkpoint did not carry the run to an end state — usually a
        # checkpoint written by a different version. Say which one.
        console.print(
            f"[red]Resume produced no verdict for {review_id}.[/red]\n"
            "The checkpoint did not carry the run to a decision — it may have "
            "been written by a different version. Re-run the review to get a "
            "fresh one."
        )
        raise typer.Exit(code=2)
    console.print(
        f"\nResumed with decision [bold]{decision}[/bold] → "
        f"[bold]{result.verdict.value}[/bold] — {result.summary}"
    )
    console.print(f"Artifacts: {state['artifacts_dir']}")


@app.command()
def replay(
    review_id: str = typer.Argument(None, help="Review ID; omit to list reviews"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
    demo: bool = typer.Option(
        False, "--demo",
        help="Replay the vendored demo review offline — no API key, no "
             "workspace: proves the audit trail is real before you trust "
             "us with a key (doc 25 §73.1, rung R1)"),
):
    """Replay a past review's audit trail from its YAML mirror."""
    from ai_venture_studio.replay import load_replay, summarize_step

    if demo:
        from ai_venture_studio.editions import EDITIONS_ROOT

        reviews_dir = EDITIONS_ROOT / "demo" / "reviews"
        review_id = review_id or next(
            p.name for p in sorted(reviews_dir.iterdir()) if p.is_dir()
        )
        console.print("[dim]offline demo bundle — a real review of this repo's "
                      "own code, redacted; every step below was written by the "
                      "pipeline at run time[/dim]")
    else:
        reviews_dir = Path(repo_dir) / ".mas" / "reviews"
    if review_id is None:
        rows = sorted(p.name for p in reviews_dir.iterdir() if p.is_dir())
        for name in rows:
            console.print(name)
        if not rows:
            console.print("(no reviews recorded)")
        return

    rep = load_replay(reviews_dir, review_id)
    table = Table(show_lines=False, title=f"review {rep.review_id}")
    table.add_column("#")
    table.add_column("node")
    table.add_column("at")
    table.add_column("summary")
    for step in rep.steps:
        table.add_row(
            str(step.step),
            step.node,
            step.written_at.strftime("%H:%M:%S"),
            summarize_step(step),
        )
    console.print(table)
    console.print(
        f"verdict: [bold]{rep.verdict}[/bold]"
        + (f" · {rep.duration_s:.1f}s" if rep.duration_s is not None else "")
    )


@app.command()
def compound(
    repo_dir: str = typer.Option(".", help="Repository whose review record to aggregate"),
    days: int = typer.Option(7, help="Signal window in days"),
    provider: str = typer.Option("anthropic", help="Proposer provider"),
    model: str = typer.Option("claude-opus-4-8", help="Proposer model"),
    pr: bool = typer.Option(
        False, "--pr", help="Open a CLAUDE.md update PR (human still merges)"
    ),
):
    """Weekly compounding loop: aggregate review signals, propose CLAUDE.md
    constraints, optionally open the human-gated update PR (§09.8)."""
    import datetime
    import subprocess

    from ai_venture_studio import compound as comp

    date = datetime.date.today().isoformat()
    signals = comp.collect_signals(repo_dir, days=days)
    proposals = comp.propose(signals, provider=provider, model=model)
    report = comp.render_proposal(signals, proposals, date=date)

    out_dir = Path(repo_dir) / ".mas" / "compound"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"proposal-{date}.md"
    report_path.write_text(report, encoding="utf-8")
    console.print(report)
    console.print(f"\nProposal written to {report_path}")

    # The proposer's call is buffered in-process by the provider adapter and
    # only reaches the ledger when someone who knows the workspace flushes it.
    # Nobody did, so every compounding run spent unmetered — invisible while
    # this was a hand-run command, and a standing unmetered cost now that a
    # scheduler fires it. ADR-032 removed the spending CAP and deliberately
    # kept the metering; this is the kept half.
    from ai_venture_studio import spend

    spend.flush(repo_dir)
    cost = spend.summarize_workspace(repo_dir)
    if cost.calls:
        console.print(
            f"[dim]{spend.render_plain(cost, what='This workspace')}[/dim]"
        )

    if not proposals:
        raise typer.Exit(code=0)
    if not pr:
        console.print("Re-run with --pr to open the CLAUDE.md update PR.")
        raise typer.Exit(code=0)

    branch = f"avs/compound-{date}"
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [resolve("git"), *args], cwd=repo_dir, capture_output=True, text=True
        )

    git("checkout", "-B", branch)
    comp.apply_to_claude_md(repo_dir, proposals, date=date)
    git("add", "CLAUDE.md")
    git("commit", "-m", f"compound: propose {len(proposals)} CLAUDE.md constraint(s) ({date})")
    push = git("push", "-u", "origin", branch)
    if push.returncode != 0:
        console.print(f"[yellow]push failed: {push.stderr.strip()[:200]}[/yellow]")
        raise typer.Exit(code=1)
    from ai_venture_studio import forge

    ok, output = forge.create_change_request(
        repo_dir, branch,
        f"[compound] CLAUDE.md constraints — {date}",
        report + "\n\n🤖 opened by the avs compounding loop",
    )
    git("checkout", "-")
    if not ok:
        console.print(f"[yellow]PR/MR create failed: {output[:200]}[/yellow]")
        raise typer.Exit(code=1)
    console.print(output.splitlines()[-1] if output else "(no forge output)")


_DEFAULT_CASES = Path(__file__).resolve().parent.parent.parent / "benchmarks" / "cases"


@app.command()
def bench(
    cases_dir: str = typer.Option(str(_DEFAULT_CASES), help="Labeled benchmark cases"),
    skills_dir: str = typer.Option(str(_DEFAULT_SKILLS), help="Voter skills directory"),
    provider: str = typer.Option(None, help="Force one provider (e.g. 'mock')"),
    limit: int = typer.Option(None, help="Run only the first N cases"),
    repo_dir: str = typer.Option(".", help="Where to record the result"),
):
    """Run the labeled benchmark; v0.1.0 bars: recall >=40%, precision >=50%."""
    from ai_venture_studio.bench import run_benchmark, save_result

    result = run_benchmark(
        cases_dir, skills_dir=skills_dir, provider_override=provider, limit=limit
    )
    table = Table(title="benchmark")
    for col in ("case", "verdict", "recall", "findings (matched)", "s"):
        table.add_column(col)
    for c in result.cases:
        table.add_row(
            c.name,
            c.verdict,
            f"{c.expected_matched}/{c.expected_total}",
            f"{c.findings_total} ({c.findings_matched})",
            str(c.duration_s),
        )
    console.print(table)
    verdict = "PASS" if result.passes() else "FAIL"
    console.print(
        f"recall [bold]{result.recall:.0%}[/bold] (bar 40%) · "
        f"precision [bold]{result.precision:.0%}[/bold] (bar 50%) → [bold]{verdict}[/bold]"
    )
    console.print(f"saved: {save_result(result, repo_dir)}")
    if not result.passes():
        raise typer.Exit(code=1)


_DEPLOY_SKILLS = _skills_root() / "deploy"


@app.command("deploy-review")
def deploy_review(
    target: str = typer.Argument(..., help="GitHub PR URL or git range"),
    repo_dir: str = typer.Option(".", help="Repository to review in"),
    skills_dir: str = typer.Option(str(_DEPLOY_SKILLS), help="Deploy voter skills"),
    provider: str = typer.Option(None, help="Force one provider (e.g. 'mock')"),
):
    """Gate 5 — Deployment Review MAS (§09.11). Recommends; never deploys."""
    from ai_venture_studio.deploy import run_deploy_review

    try:
        activation = check_stage(repo_dir, "deploy_review")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    lint_only = activation is not None and activation.status.value == "DEGRADED"
    if lint_only:
        console.print(f"[yellow]deploy_review DEGRADED — {activation.note}[/yellow]")

    result = run_deploy_review(
        target, repo_dir=repo_dir, skills_dir=skills_dir,
        provider_override=provider, lint_only=lint_only,
    )
    color = "green" if result.verdict.value == "PROMOTE" else (
        "yellow" if result.verdict.value == "HOLD_FOR_HUMAN" else "red"
    )
    console.print(f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}")
    if result.findings:
        table = Table(show_lines=False)
        for col in ("Sev", "Location", "Finding"):
            table.add_column(col)
        for f in result.findings:
            table.add_row(
                f.severity.value, f"{f.file_path}:{f.line_start}", f"{f.title} [{f.voter}]"
            )
        console.print(table)
    console.print(f"Artifacts: {result.artifacts_dir}")
    if result.verdict.value.startswith("ESCALATE_"):
        raise typer.Exit(code=3)


@app.command()
def triage(
    incident_file: str = typer.Argument(..., help="Incident file (.json/.yaml/.txt)"),
    repo_dir: str = typer.Option(".", help="Repository to correlate against"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    days: int = typer.Option(7, help="Correlation window for recent commits"),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Assistive tier: attempt a fix-PR when a root cause is proposed "
        "(this flag IS the human approval; the PR still re-enters code review)",
    ),
):
    """Gate 6 intake — Maintenance MAS (§09.12): triage + root-cause."""
    # Substrate ladder guard (ADR-U15): a stage below its
    # infrastructure floor is inactive-never-degraded. No-op unless the
    # workspace declares .mas/substrate-profile.yaml.
    try:
        check_stage(repo_dir, "maintenance")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    from ai_venture_studio.maintenance import Incident, run_maintenance

    incident = Incident.load(incident_file)
    result = run_maintenance(
        incident, repo_dir=repo_dir, provider=provider, days=days
    )
    color = {
        "TRIAGED_LOW_PRIORITY": "green",
        "ROOT_CAUSE_PROPOSED": "yellow",
    }.get(result.verdict.value, "red")
    console.print(f"\n[bold {color}]{result.verdict.value}[/bold {color}] — {result.summary}")
    if result.root_cause:
        console.print(f"hypothesis: {result.root_cause.hypothesis}")
        console.print(f"next action: {result.root_cause.next_action}")
    if result.suspects:
        console.print("suspects: " + ", ".join(s["sha"] for s in result.suspects))
    console.print(f"Artifacts: {result.artifacts_dir}")

    if fix and result.verdict.value == "ROOT_CAUSE_PROPOSED":
        from ai_venture_studio.maintenance.fixpr import generate_fix_pr

        attempt = generate_fix_pr(
            incident, result.root_cause, repo_dir=repo_dir, provider=provider
        )
        console.print(
            f"\nfix attempt: [bold]{attempt.status}[/bold]"
            + (f" · branch {attempt.branch}" if attempt.branch else "")
            + (f" · {attempt.pr_url}" if attempt.pr_url else "")
        )
        if attempt.detail:
            console.print(f"  {attempt.detail}")
        if attempt.files_changed:
            console.print(f"  files: {', '.join(attempt.files_changed)}")
    elif fix:
        console.print("\nfix attempt skipped: no root cause proposed")

    if result.verdict.value.startswith("ESCALATE_"):
        raise typer.Exit(code=3)


@app.command("deploy-outcome")
def deploy_outcome(
    review_id: str = typer.Argument(..., help="Deploy review ID"),
    outcome: str = typer.Option(..., help="'correct' or 'incorrect'"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Record the human verdict on a past deploy recommendation (§09.11.5).
    Streaks of correct PROMOTEs make the stage eligible for assistive tier."""
    from ai_venture_studio.deploy import track_record

    if not track_record.mark_outcome(repo_dir, review_id, outcome):
        console.print(f"[red]no deploy review {review_id!r} on record[/red]")
        raise typer.Exit(code=1)
    ready = track_record.readiness(repo_dir)
    console.print(
        f"recorded. streak: {ready.streak}/{ready.needed} correct PROMOTEs"
        + (" — [bold]eligible for assistive tier[/bold]" if ready.eligible else "")
    )


@app.command()
def serve(
    repo_dir: str = typer.Option(".", help="Repository the server operates on"),
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8422, help="Port"),
):
    """Webhook mode: GitHub PR / GitLab MR events -> reviews, incident
    POSTs -> triage.
    Requires AUTOPRODUCT_WEBHOOK_SECRET for signature verification."""
    from ai_venture_studio.server import serve as run_server

    run_server(repo_dir, host=host, port=port)


@app.command()
def worker(
    repo_dir: str = typer.Option(".", help="Repository the worker operates on"),
    queue_db: str = typer.Option(
        ".mas/queue.db", help="SQLite queue (same path the server enqueues to)"
    ),
    max_jobs: int = typer.Option(
        0, help="Exit after N jobs (0 = run forever); nonzero also exits when idle"
    ),
):
    """Queue worker: drains jobs the server enqueued when
    AUTOPRODUCT_QUEUE_DB is set. Run several for parallel throughput on
    one host; multi-host needs a shared broker (see jobqueue docstring)."""
    from ai_venture_studio.jobqueue import worker_loop

    done = worker_loop(queue_db, repo_dir, max_jobs=max_jobs or None)
    console.print(f"worker exited after {done} job(s)")


@app.command()
def init(
    directory: str = typer.Argument(..., help="Workspace directory to create"),
    name: str = typer.Option(None, help="Project name (defaults to directory name)"),
    profile: str = typer.Option(
        ..., help="Domain profile: web | enterprise-web | miniprogram | "
                  "app | game | data"),
    tier: str = typer.Option(
        "standard", help="thin | standard | deep. thin plans ONE working "
                         "end-to-end slice first; narrows, never widens."
    ),
    edition: str = typer.Option(
        None, help="Edition preset: enterprise | solo | engineer (doc 24; "
                   "narrowing-only, linted at init)"),
    gate_owner: str = typer.Option(
        None, help="Named human per gate class — required by the enterprise "
                   "edition (§69.1)"),
    from_bench: str = typer.Option(
        None, "--from-bench",
        help="Seed FDR.md from a product-bench case (e.g. 01-groupbuy-api) — "
             "templates are the same fixtures the benchmark runs, so a "
             "template that rots fails CI, not you (doc 25 §73.2)"),
    adopt: bool = typer.Option(
        False, "--adopt",
        help="Adopt an EXISTING codebase: read it first (avs map), write the "
             "derived deps baseline, and keep your CLAUDE.md. Use this "
             "instead of a plain init on a repo you did not build here."),
):
    """Create a greenfield workspace: profile constraints, CLAUDE.md, specs/."""
    from ai_venture_studio.upstream import init_workspace

    resolved_edition = None
    if edition:
        import yaml as _yaml

        from ai_venture_studio.editions import EditionError, load_edition_preset

        try:
            resolved_edition = load_edition_preset(edition)
        except EditionError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        if resolved_edition.get("gate_policy", {}).get(
            "require_gate_owner"
        ) and not gate_owner:
            console.print(
                "[red]edition 'enterprise' requires --gate-owner: a named human "
                "per gate class is the 12%-conversion profile, enforced at init "
                "(§69.1)[/red]"
            )
            raise typer.Exit(code=2)

    try:
        root = init_workspace(
            directory, name or Path(directory).name, profile, scope_tier=tier
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if adopt:
        # Read before writing anything else: on a repo the system did not
        # build, the map IS the onboarding step. Without it the planner and
        # the feature flow guess from filenames.
        import yaml as _yaml

        from ai_venture_studio.upstream.comprehend import (
            comprehend_repo,
            derive_deps,
            render_summary,
            write_map,
        )

        result = comprehend_repo(root)
        write_map(result, root)
        console.print(render_summary(result))
        deps_path = root / ".mas" / "deps.yaml"
        if not deps_path.exists() and result.modules:
            deps_path.write_text(
                _yaml.safe_dump(derive_deps(result), sort_keys=False,
                                allow_unicode=True),
                encoding="utf-8",
            )
            console.print(
                f"\ndeps baseline written to {deps_path} — what the code does "
                "today, not a design; existing violations are the baseline that "
                "must only shrink"
            )
        if not result.total_files:
            console.print(
                "[yellow]no source files found — check the directory; an empty "
                "map means later stages are working blind[/yellow]"
            )
    if from_bench:
        import yaml as _yaml

        bench_root = Path(__file__).resolve().parents[2] / "benchmarks"
        case = next(
            (p for sub in ("products-real", "products")
             for p in [bench_root / sub / f"{from_bench}.yaml"] if p.exists()),
            None,
        )
        if case is None:
            console.print(f"[red]no bench case named {from_bench!r}[/red]")
            raise typer.Exit(code=2)
        fdr = str((_yaml.safe_load(case.read_text()) or {}).get("fdr", ""))
        (Path(root) / "FDR.md").write_text(fdr, encoding="utf-8")
        console.print(f"FDR.md seeded from bench case {from_bench}")
    if resolved_edition:
        import yaml as _yaml

        if gate_owner:
            resolved_edition["gate_policy"]["gate_owner"] = gate_owner
        target = Path(root) / ".mas" / "edition.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_yaml.safe_dump(resolved_edition, sort_keys=False))
        console.print(
            f"edition [bold]{edition}[/bold] resolved → {target} · start at "
            f"{resolved_edition.get('docs_entry', 'editions/')}"
        )
    console.print(f"workspace ready: {root}")
    if adopt:
        # A brownfield adoption's next step is governance and review, not
        # writing a spec for a product that already exists.
        console.print(
            f"next: avs readiness (the rung you occupy) · "
            f"avs review main...HEAD --repo-dir {root} · "
            f"avs studio {root} (the governance dashboard)"
        )
    else:
        console.print(
            f"next: avs spec \"<what you want to build>\" --repo-dir {root}"
        )


@app.command()
def spec(
    request: str = typer.Argument(..., help="What you want to build, in plain words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Spec stage: EARS criteria + test skeletons, linted and critiqued."""
    # Substrate ladder guard (ADR-U15): a stage below its
    # infrastructure floor is inactive-never-degraded. No-op unless the
    # workspace declares .mas/substrate-profile.yaml.
    try:
        check_stage(repo_dir, "specification")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    from ai_venture_studio.upstream import run_spec_stage

    result = run_spec_stage(repo_dir, request, provider=provider)
    color = {"proposed": "green", "blocked": "red"}.get(result.status, "yellow")
    console.print(
        f"\n[bold {color}]{result.status}[/bold {color}] — {result.title} "
        f"({len(result.criteria)} criteria, {result.revisions} revision(s))"
    )
    for i, criterion in enumerate(result.criteria):
        console.print(f"  {i}. {criterion}")
    if result.lint_issues:
        console.print(f"[red]lint issues: {result.lint_issues}[/red]")
    console.print(f"spec: {Path(repo_dir) / 'specs' / result.slug / 'spec.md'}")
    if result.status == "proposed":
        console.print(
            f"Gate U3: avs spec-approve {result.slug} --repo-dir {repo_dir}"
        )


@app.command("spec-approve")
def spec_approve(
    slug: str = typer.Argument(..., help="Spec slug"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Gate U3 — human approval that makes a spec buildable."""
    from ai_venture_studio.upstream import approve_spec

    result = approve_spec(repo_dir, slug)
    console.print(
        f"approved: {result.title}\n"
        f"next: avs build {slug} --repo-dir {repo_dir}"
    )


@app.command()
def build(
    slug: str = typer.Argument(..., help="Approved spec slug"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    review: bool = typer.Option(
        True, help="Run the review pipeline on the built commit"
    ),
):
    """Coding stage: test-first implementation of an approved spec; the
    commit is handed to the review pipeline (Gate U4 -> Gate 1)."""
    # Substrate ladder guard (ADR-U15): a stage below its
    # infrastructure floor is inactive-never-degraded. No-op unless the
    # workspace declares .mas/substrate-profile.yaml.
    try:
        check_stage(repo_dir, "coding")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    from ai_venture_studio.upstream import run_build

    result = run_build(repo_dir, slug, provider=provider)
    color = {"built": "green"}.get(result.status, "red")
    console.print(
        f"\n[bold {color}]{result.status}[/bold {color}] — {result.iterations} "
        f"iteration(s); {len(result.files_written)} file(s); {result.test_summary}"
    )
    if result.detail:
        console.print(result.detail)
    if result.status != "built":
        raise typer.Exit(code=1)
    console.print(f"commit {result.commit}: {', '.join(result.files_written)}")
    if review:
        console.print("\nhanding to review stage (avs review HEAD~1)…")
        review_result, state = run_review(
            "HEAD~1..HEAD",
            repo_dir=repo_dir,
            skills_dir=str(_DEFAULT_SKILLS),
            provider_override=provider if provider == "mock" else None,
        )
        if review_result:
            console.print(
                f"review verdict: [bold]{review_result.verdict.value}[/bold] — "
                f"{review_result.summary}"
            )


@app.command()
def discover(
    idea: str = typer.Argument(..., help="Your product idea, in plain words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
):
    """Discovery stage: evidence-tagged ProductBrief + hypothesis ledger."""
    # Substrate ladder guard (ADR-U15): a stage below its
    # infrastructure floor is inactive-never-degraded. No-op unless the
    # workspace declares .mas/substrate-profile.yaml.
    try:
        check_stage(repo_dir, "discovery")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    from ai_venture_studio.upstream import run_discovery

    brief = run_discovery(repo_dir, idea, provider=provider)
    console.print(f"\n[bold]{brief.title}[/bold] — {brief.status}")
    for h in brief.hypotheses:
        console.print(f"  ({h.evidence}) {h.statement}")
    console.print(f"scope_now: {brief.scope_now}")
    console.print(f"brief: {Path(repo_dir) / 'product' / 'brief.md'}")
    console.print("Gate U1: avs brief-approve")


@app.command("brief-approve")
def brief_approve(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Gate U1 — the human problem-selection decision."""
    from ai_venture_studio.upstream import approve_brief

    brief = approve_brief(repo_dir)
    console.print(f"approved: {brief.title}\nnext: avs plan")


@app.command()
def plan(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    replan: bool = typer.Option(
        False, "--replan",
        help="Discard a locked plan and decompose again (Gate U2)",
    ),
):
    """Planning stage: task DAG from the approved brief (dag-checked)."""
    # Substrate ladder guard (ADR-U15): a stage below its
    # infrastructure floor is inactive-never-degraded. No-op unless the
    # workspace declares .mas/substrate-profile.yaml.
    try:
        check_stage(repo_dir, "planning")
    except StageInactiveError as exc:
        console.print(f"[red]{exc}[/red]")
        console.print("Run `avs readiness` for the rung roadmap.")
        raise typer.Exit(code=4) from exc
    from ai_venture_studio.upstream import run_planning
    from ai_venture_studio.upstream.plan import ScopeLocked

    try:
        result = run_planning(repo_dir, provider=provider, replan=replan)
    except ScopeLocked as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if result.status == "locked" and not replan:
        console.print(
            f"[yellow]locked[/yellow] — reusing the approved plan "
            f"({len(result.tasks)} task(s)); --replan to decide scope again"
        )
    color = {"proposed": "green", "blocked": "red"}.get(result.status, "yellow")
    console.print(f"\n[bold {color}]{result.status}[/bold {color}] — {len(result.tasks)} task(s)")
    for t in result.tasks:
        deps = f" <- {','.join(t.depends_on)}" if t.depends_on else ""
        console.print(f"  {t.id} [{t.lane}] {t.title}{deps} ({t.estimate_hours}h)")
    if result.dag_issues:
        console.print(f"[red]dag issues: {result.dag_issues}[/red]")
    if result.status == "proposed":
        console.print("Gate U2 (scope lock): avs plan-approve")


@app.command("plan-approve")
def plan_approve(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Gate U2 — lock scope; changes after this go through an SCR."""
    from ai_venture_studio.upstream import approve_plan, next_tasks

    plan_result = approve_plan(repo_dir)
    ready = next_tasks(repo_dir)
    console.print(f"scope locked: {len(plan_result.tasks)} task(s)")
    for t in ready:
        console.print(f"  ready: {t.id} — avs spec \"{t.description}\"")


@app.command()
def create(
    directory: str = typer.Argument(..., help="Where your product lives (created if new)"),
    profile: str = typer.Option(
        None,
        help="web | miniprogram | app — required for a NEW workspace only; "
        "an existing one already declares its profile",
    ),
    tier: str = typer.Option("standard", help="thin | standard | deep. thin builds ONE working end-to-end slice first (fastest to something real); you add the next piece with `avs add`. Narrows, never widens."),
    fdr: str = typer.Option(None, help="Your FDR file (default: <dir>/FDR.md)"),
    yes: bool = typer.Option(False, "--yes", help="Confirm the plan and build everything"),
    replan: bool = typer.Option(
        False, "--replan",
        help="Discard the locked plan and decide scope again (Gate U2). "
        "Without this, a locked plan is reused as-is.",
    ),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    token_ceiling: int = typer.Option(
        None,
        help="Stop the run between tasks once it passes this many tokens — a "
             "termination bound on a looping build, NOT a spending cap "
             "(ADR-032). Built modules are kept and a re-run continues. "
             "0 disables it.",
    ),
):
    """The non-technical flow: write ONE document (the FDR), the system
    builds the product. First run writes the FDR template + guide."""
    from ai_venture_studio.upstream import init_workspace
    from ai_venture_studio.upstream.autopilot import (
        DEFAULT_TOKEN_CEILING,
        run_autopilot,
    )
    from ai_venture_studio.upstream.fdr import write_template


    root = Path(directory).resolve()
    # --profile answers a question only a NEW workspace has. Demanding it
    # again for a workspace that already declares one made every re-run
    # (resume, --yes after a confirmation, a second feature) an error that
    # taught the founder to pass a flag that is then ignored — and a
    # mistyped one would have read as a request to change profiles.
    if not (root / ".mas" / "project.yaml").exists():
        if not profile:
            console.print(
                "[red]--profile is required for a new workspace: "
                "web | miniprogram | app[/red]"
            )
            raise typer.Exit(code=2)
        try:
            init_workspace(root, root.name, profile, scope_tier=tier)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
    elif profile:
        from ai_venture_studio.upstream.workspace import load_project

        declared = load_project(root).profile
        if profile != declared:
            console.print(
                f"[red]this workspace is a {declared!r} project; --profile "
                f"{profile!r} would be a different product.[/red] Build it in "
                "its own directory, or drop the flag to continue this one."
            )
            raise typer.Exit(code=2)
    fdr_path = Path(fdr) if fdr else root / "FDR.md"
    if not fdr_path.exists() or not fdr_path.read_text(encoding="utf-8").strip():
        write_template(root)
        console.print(
            f"第一步：用自己的话填写 {root / 'FDR.md'}（参考 {root / 'FDR-GUIDE.md'}），"
            f"然后重新运行这条命令。\n"
            f"Step 1: fill in {root / 'FDR.md'} in your own words (see FDR-GUIDE.md), "
            f"then run this command again."
        )
        return

    # Narrate the run. `avs create` printed nothing between "start" and a report
    # that can be an hour away — the exact complaint the launch PRD is built on
    # ("stared at the terminal for 40 minutes with no idea whether it was
    # progressing or stuck"). The Studio grew a per-task panel in v0.34; the
    # terminal never did.
    from ai_venture_studio.upstream import progress

    progress.set_sink(lambda line: console.print(f"[dim]{line}[/dim]"))
    try:
        result = run_autopilot(
            root, fdr_path, provider=provider, yes=yes, replan=replan,
            token_ceiling=(
                DEFAULT_TOKEN_CEILING if token_ceiling is None else token_ceiling
            ),
        )
    finally:
        progress.set_sink(None)
    if result.status == "needs_answers":
        console.print("[yellow]还需要一些信息 / A few answers needed:[/yellow]")
        for i, q in enumerate(result.assessment.questions, 1):
            console.print(f"  {i}. {q}")
        console.print(f"详见 {root / 'FDR-QUESTIONS.md'} — 补充进 FDR.md 后重新运行。")
        raise typer.Exit(code=2)
    if result.status == "awaiting_confirmation":
        console.print(result.confirmation)
        console.print(f"\n(saved to {root / 'product' / 'CONFIRMATION.md'})")
        raise typer.Exit(code=0)
    color = {"completed": "green", "halted": "yellow"}.get(result.status, "red")
    console.print(f"\n[bold {color}]{result.status}[/bold {color}]")
    if result.status == "halted":
        console.print(
            "[yellow]Stopped at the termination bound: a build consuming this "
            "much is looping, not working. Nothing was refused over money "
            "(ADR-032) — built modules are kept, and re-running continues "
            "from here. --token-ceiling raises the bound.[/yellow]"
        )
    for o in result.outcomes:
        verdict = f" · review: {o.review_verdict}" if o.review_verdict else ""
        console.print(f"  {o.task_id} {o.title}: {o.status}{verdict}")
        # A failed module names its cause here. The summary line used to stop at
        # the status, so the one question a founder actually has — why? — was
        # answerable only by opening outcomes.yaml.
        if o.status != "built" and o.detail:
            console.print(f"      [dim]why: {o.detail}[/dim]")
        # And a BUILT module names the one thing that makes `built` a lie.
        # The build ran `wireup_check` on every success and put the findings
        # on the record; no reader ever asked for them (ADR-060), so a module
        # nothing imports printed the same single green line as one the
        # product actually calls.
        if o.status == "built" and getattr(o, "wireup_issues", None):
            console.print(
                f"      [yellow]built, but nothing imports it: "
                f"{'; '.join(o.wireup_issues[:3])}[/yellow]"
            )
    console.print(f"报告 / report: {result.report_path}")
    # What it cost, unprompted. The founder signal asked to SEE this number,
    # and a figure you must know to go looking for does not answer it.
    from ai_venture_studio import spend

    cost = spend.summarize_workspace(root)
    if cost.calls:
        console.print(f"[dim]{spend.render_plain(cost, what='This workspace')}[/dim]")
    if result.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def studio(
    # Positional, like init/create — every doc writes `avs studio myteam`,
    # and the option form made that documented invocation an error.
    repo_dir: str = typer.Argument(".", help="Workspace directory"),
    # `--repo-dir` was the only way in before, and the CLI surface is a
    # versioned contract (CONTRIBUTING): it keeps working rather than
    # breaking whatever already scripted it.
    repo_dir_opt: str = typer.Option(
        None, "--repo-dir", hidden=True,
        help="Deprecated: pass the workspace positionally instead.",
    ),
    port: int = typer.Option(8433, help="Port"),
    host: str = typer.Option(
        "127.0.0.1",
        help="Bind address. Anything but loopback REQUIRES AVS_STUDIO_TOKEN "
        "(env or AVS_STUDIO_TOKEN_FILE mount) — an unauthenticated Studio "
        "never leaves the machine.",
    ),
    profile: str = typer.Option(None, help="Profile (only needed for a new workspace)"),
    lang: str = typer.Option(
        "en", help="UI language: en (English, default) | zh (bilingual "
                   "Chinese-first). Your FDR may be written in either "
                   "language whichever UI you choose."
    ),
    mode: str = typer.Option(
        None, help="UI mode: founder | engineer | enterprise. Default: "
                   "resolved from the workspace edition (.mas/edition.yaml"
                   " — solo→founder), else founder. Modes only add "
                   "read-only detail; the flow is the same in all three."
    ),
    provider: str = typer.Option(
        "anthropic",
        help="Model provider for the flow ('mock' walks clarify→plan→build→"
        "report offline with a canned product — the air-gapped evaluation "
        "path; no key, no egress)",
    ),
    entry: str = typer.Option(
        "chat",
        help="Which door the describe-step opens with: chat (default — one "
        "question at a time) | form (the whole FDR in one textarea). Both "
        "stay reachable either way; this only picks the landing page.",
    ),
):
    """Founder Studio: the browser UI for the FDR flow (localhost by
    default; non-loopback binds are token-gated)."""
    from ai_venture_studio.secrets import env_or_file
    from ai_venture_studio.studio import serve_studio
    from ai_venture_studio.studio_modes import StudioModeError
    from ai_venture_studio.upstream import init_workspace

    if host not in ("127.0.0.1", "localhost", "::1") and not env_or_file(
        "AVS_STUDIO_TOKEN"
    ):
        console.print(
            f"[red]--host {host} would expose an unauthenticated Studio. "
            "Set AVS_STUDIO_TOKEN (or AVS_STUDIO_TOKEN_FILE) first — every "
            "request will then require the token; for SSO put an OIDC "
            "reverse proxy in front.[/red]"
        )
        raise typer.Exit(code=2)

    if repo_dir_opt is not None:
        if repo_dir not in (".", repo_dir_opt):
            console.print(
                "[red]workspace given twice: "
                f"{repo_dir!r} and --repo-dir {repo_dir_opt!r}[/red]"
            )
            raise typer.Exit(code=2)
        console.print(
            "[yellow]--repo-dir is deprecated for `studio`; "
            "pass the workspace positionally: avs studio <dir>[/yellow]"
        )
        repo_dir = repo_dir_opt
    root = Path(repo_dir).resolve()
    if not (root / ".mas" / "project.yaml").exists():
        if not profile:
            from ai_venture_studio.upstream.workspace import available_profiles

            console.print(
                "[red]new workspace: pass --profile "
                f"{'|'.join(available_profiles())}[/red]"
            )
            raise typer.Exit(code=2)
        init_workspace(root, root.name, profile)
    console.print(f"Studio: http://{host}:{port}  (workspace: {root})")
    try:
        serve_studio(
            root, host=host, port=port, provider=provider, lang=lang,
            mode=mode, entry=entry,
        )
    except (StudioModeError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


@app.command()
def preview(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    port: int = typer.Option(8500, help="Port for the app"),
):
    """Run the built product locally so the founder can try it (web profile)."""
    import subprocess

    from ai_venture_studio.upstream import load_project

    root = Path(repo_dir).resolve()
    project = load_project(root)
    if project.profile == "miniprogram":
        console.print(
            "小程序预览：用微信开发者工具打开这个目录即可（工具 → 导入项目）：\n"
            f"  {root}\n"
            "Open this directory in WeChat DevTools (import project) to preview."
        )
        return
    from ai_venture_studio.upstream.provisioning import (
        PREVIEW_ENTRIES,
        preview_entry,
        preview_env,
    )

    entry = preview_entry(root)
    if entry:
        console.print(f"starting {entry} — http://127.0.0.1:{port}  (Ctrl-C stops)")
        subprocess.run(
            [sys.executable, str(root / entry)],
            cwd=root,
            env={**__import__("os").environ, "PORT": str(port), **preview_env(root)},
        )
        return
    console.print(
        "[yellow]no runnable entry found (looked for "
        f"{', '.join(PREVIEW_ENTRIES)})[/yellow]"
    )
    raise typer.Exit(code=1)


@app.command()
def add(
    fdr: str = typer.Argument(..., help="Feature FDR file (one feature per FDR)"),
    repo_dir: str = typer.Option(".", help="Existing workspace"),
    yes: bool = typer.Option(False, "--yes", help="Confirm and build the feature"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    replace: list[str] = typer.Option(
        None, "--replace",
        help="Requirement ids this feature may retire (answers a "
        "needs_decision result, e.g. --replace R-012)",
    ),
):
    """Add ONE feature to an existing product from a granular FDR. Keep each
    FDR small: one feature or change per document."""
    from ai_venture_studio.upstream.autopilot import run_feature

    result = run_feature(
        repo_dir, fdr, provider=provider, yes=yes, replace=list(replace or [])
    )
    if result.status == "needs_answers":
        for i, q in enumerate(result.assessment.questions, 1):
            console.print(f"  {i}. {q}")
        raise typer.Exit(code=2)
    if result.status == "already_satisfied":
        # Exit 0: refusing to build something twice is the system working.
        # A non-zero code here would train every wrapper to treat a correct
        # answer as a failure.
        console.print(f"[green]already satisfied[/green] — {result.blocked_reason}")
        console.print(f"see {Path(repo_dir) / 'FDR-ALREADY-BUILT.md'}")
        raise typer.Exit(code=0)
    if result.status == "needs_decision":
        console.print(f"[yellow]needs your decision[/yellow] — {result.blocked_reason}")
        console.print(f"see {Path(repo_dir) / 'FDR-DECISION.md'}")
        raise typer.Exit(code=2)
    if result.status == "awaiting_confirmation":
        console.print(result.confirmation)
        console.print("re-run with --yes to build this feature")
        raise typer.Exit(code=0)
    color = "green" if result.status == "completed" else "red"
    console.print(f"\n[bold {color}]{result.status}[/bold {color}]")
    for o in result.outcomes:
        verdict = f" · review: {o.review_verdict}" if o.review_verdict else ""
        console.print(f"  {o.task_id} {o.title}: {o.status}{verdict}")
    if result.report_path:
        console.print(f"report: {result.report_path}")
    if result.status != "completed":
        raise typer.Exit(code=1)


#: Where `avs roadmap` leaves the next step, so the founder's whole loop is
#: two commands they never have to retype: `avs roadmap`, then
#: `avs add FDR-NEXT.md --yes`. Overwritten each time on purpose — the step
#: it holds is the one to build now, and a directory of stale FDR-NEXT-3.md
#: files is a decision the founder then has to make.
NEXT_FDR = "FDR-NEXT.md"


def _render_roadmap(plan, next_) -> None:
    for step in plan.steps:
        if step.status == "done":
            mark, colour = "✓", "dim"
        elif next_ is not None and step.id == next_.id:
            mark, colour = "→", "bold green"
        else:
            mark, colour = " ", ""
        tag = f"[{colour}]" if colour else ""
        end = f"[/{colour}]" if colour else ""
        console.print(f"  {mark} {tag}{step.id}  {step.title}{end}")
        if step.note:
            console.print(f"      [dim]{step.note}[/dim]")


@app.command()
def roadmap(
    describe: str = typer.Argument(
        None,
        help="Your product in a paragraph, your own words. Leave it out to "
        "see the roadmap you already have, re-checked against what is built.",
    ),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    recheck: bool = typer.Option(
        True, "--recheck/--no-recheck",
        help="Re-read the remaining steps against what the product already "
        "promises (one model call per remaining step)",
    ),
):
    """Turn a paragraph about your product into small steps to build in
    order — then hand you the next one."""
    from ai_venture_studio.upstream import roadmap as rm

    root = Path(repo_dir)
    if describe:
        plan = rm.propose(describe, provider=provider)
        if not plan.checked:
            console.print(f"[red]no roadmap[/red] — {plan.note}")
            raise typer.Exit(code=1)
    else:
        plan = rm.load(root)
        if plan is None:
            console.print(
                "[yellow]no roadmap yet[/yellow] — describe your product:\n"
                '  avs roadmap "..."'
            )
            raise typer.Exit(code=2)
    # Re-derived on BOTH paths, including a fresh proposal: a founder who
    # re-describes their product halfway through has not un-built anything,
    # and handing them a plan that says 0/8 would walk them back into work
    # `avs add` would then correctly refuse. On an empty ledger retrieval
    # matches nothing and this costs no model call at all.
    if recheck:
        report = rm.rederive(root, plan, provider=provider)
        if report.marked_done:
            console.print(f"[dim]already built: {', '.join(report.marked_done)}[/dim]")
        if report.unchecked:
            # Said out loud rather than counted as pending: a step nobody
            # could check is not a step known to be unbuilt.
            console.print(f"[dim]not re-checked: {', '.join(report.unchecked)}[/dim]")
    rm.save(root, plan)

    next_ = rm.next_step(plan)
    console.print(f"\n[bold]{len(plan.done)}/{len(plan.steps)} built[/bold]")
    _render_roadmap(plan, next_)
    if next_ is None:
        console.print("\n[green]every step on this roadmap is built.[/green]")
        return
    path = root / NEXT_FDR
    path.write_text(
        f"# {next_.title}\n\n{next_.fdr}\n\n"
        f"<!-- avs roadmap step {next_.id} of product/roadmap.yaml -->\n",
        encoding="utf-8",
    )
    console.print(f"\nnext step written to {path}\n  avs add {NEXT_FDR} --yes")


@app.command()
def requirements(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    all_: bool = typer.Option(
        False, "--all", help="Include retired and superseded promises"
    ),
):
    """What your product promises, grouped, with what checks each one."""
    from ai_venture_studio.upstream.autopilot import checkpoints
    from ai_venture_studio.upstream.requirements import (
        LIVE_STATUSES,
        load_ledger,
        since,
        sync_ledger,
    )

    root = Path(repo_dir)
    sync_ledger(root)
    ledger = load_ledger(root)
    shown = [r for r in ledger if all_ or r.status in LIVE_STATUSES]
    if not shown:
        console.print("[yellow]this product promises nothing yet[/yellow]")
        return

    by_spec: dict[str, list] = {}
    for req in shown:
        by_spec.setdefault(req.spec_slug, []).append(req)
    live = sum(1 for r in ledger if r.status in LIVE_STATUSES)
    console.print(
        f"[bold]{live} promise(s)[/bold] across {len(by_spec)} part(s) of the product"
    )
    for slug in sorted(by_spec):
        console.print(f"\n[bold]{slug}[/bold]")
        for req in by_spec[slug]:
            colour = "green" if req.status == "built" else (
                "dim" if req.status not in LIVE_STATUSES else "yellow"
            )
            console.print(f"  [{colour}]{req.id}[/{colour}] {req.text}")
            detail = []
            if req.status != "built":
                detail.append(req.status)
            if req.verified_by:
                detail.append("checked by " + ", ".join(req.verified_by[:2]))
            if req.superseded_by:
                detail.append(f"replaced by {req.superseded_by}")
            if detail:
                console.print(f"      [dim]{' · '.join(detail)}[/dim]")

    # The newest checkpoint that actually froze a baseline. Older ones were
    # tagged before baselines existed and have nothing to compare against;
    # reporting "+0 since checkpoint 2" for one of those would measure the
    # missing file rather than the product.
    for tag in reversed(checkpoints(root)):
        delta = since(root, tag)
        if delta is None:
            continue
        parts = []
        if delta.added:
            parts.append(f"+{len(delta.added)} promise(s)")
        if delta.superseded:
            parts.append(f"{len(delta.superseded)} superseded")
        if delta.retired:
            parts.append(f"{len(delta.retired)} retired")
        if delta.restored:
            parts.append(f"{len(delta.restored)} back")
        console.print(
            f"\n[dim]since {tag}: {', '.join(parts) if parts else 'no change'}[/dim]"
        )
        break


@app.command()
def scr(
    slug: str = typer.Argument(..., help="Built spec slug that needs changing"),
    reason: str = typer.Argument(..., help="Why the spec must change"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Raise a Spec Change Request — the only legal way to change a built
    spec (ADR-U02). A human approves it with scr-approve."""
    from ai_venture_studio.upstream.spec import raise_scr

    path = raise_scr(repo_dir, slug, reason)
    console.print(f"raised: {path.name}\napprove with: avs scr-approve {path.stem.split('-')[1]}")


@app.command("scr-approve")
def scr_approve(
    number: int = typer.Argument(..., help="SCR number"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
):
    """Approve an SCR — grants exactly one regeneration of the named spec."""
    from ai_venture_studio.upstream.spec import approve_scr

    data = approve_scr(repo_dir, number)
    console.print(
        f"approved SCR-{number:03d} for spec {data['spec_slug']!r}: {data['reason']}\n"
        f"the next `avs spec`/`add` touching it may now regenerate it once"
    )


@app.command()
def ship(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    push: bool = typer.Option(
        False, "--push",
        help="Web only: actually deploy via Railway (requires railway login; "
        "this flag IS your deploy decision)",
    ),
):
    """Generate deployment artifacts + a plain-language DEPLOY.md. The
    deploy button stays yours — --push is you pressing it."""
    from ai_venture_studio.upstream.ship import push_web, ship as run_ship

    guide = run_ship(repo_dir)
    console.print(f"部署指南已生成 / deploy guide written: {guide}")
    if push:
        result = push_web(repo_dir)
        color = {"deployed": "green"}.get(result["status"], "yellow")
        console.print(f"[bold {color}]{result['status']}[/bold {color}] — {result['detail']}")
        if result["status"] == "error":
            raise typer.Exit(code=1)


def _notifier():
    from ai_venture_studio import notify as notifier

    return notifier


def _bench_notify(repo_dir: str, alert) -> None:
    """Post one finished bench run, and say plainly if that did not work.

    Forced past the repeat window on purpose. That window exists for a
    STANDING CONDITION re-reported every morning — an overdue loop stays
    overdue, and saying so six days running is how a channel becomes noise.
    A bench run is not a condition, it is an EVENT: it happened once, it
    cost hours, and a second crash in the same week is a second thing that
    happened, not a repeat of the first.

    Delivery is reported, never assumed — the same rule the cadence path
    follows. A notifier that fails quietly is worse than none, because the
    log nobody reads at least never claimed to have told anyone.
    """
    notifier = _notifier()
    try:
        result = notifier.send(
            repo_dir, alert, kind=notifier.BENCH_KIND, force=True
        )
    except notifier.NotifyError as exc:
        console.print(f"[red]alert NOT sent: {exc}[/red]")
        return
    if result.sent:
        console.print("[green]result posted to Discord[/green]")
    else:
        console.print(f"[dim]no alert: {result.reason}[/dim]")


@app.command("product-bench")
def product_bench(
    cases_dir: str = typer.Option(
        str(Path(__file__).resolve().parent.parent.parent / "benchmarks" / "products"),
        help="Labeled product cases (FDR + independent behavioral probes)",
    ),
    provider: str = typer.Option(None, help="Provider (e.g. 'mock')"),
    limit: int = typer.Option(None, help="Run only the first N cases"),
    repo_dir: str = typer.Option(".", help="Where to record the result"),
    notify: bool = typer.Option(
        False, "--notify",
        help="Post the result to Discord when the run ends, however it ends. "
        "The scheduler is not the only thing that runs this.",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Reuse cases already measured on THIS build, from the last run's "
        "checkpoints. Without it every case is measured again.",
    ),
    model: str = typer.Option(
        "claude-opus-4-8", help="Model under test (also what the preflight checks)"
    ),
    preflight: bool = typer.Option(
        True, "--preflight/--no-preflight",
        help="Spend one token confirming the account works before spending "
        "hours discovering it does not.",
    ),
):
    """Built-product quality, end to end: full autopilot per case, then
    INDEPENDENT probes against the built product (WebGen-Bench pattern)."""
    from ai_venture_studio.product_bench import (
        preflight_provider,
        run_product_bench,
        save_summary,
    )
    from ai_venture_studio.providers.base import is_simulated as _is_simulated

    # THE PREFLIGHT LIVES HERE, not in `run_product_bench`. The library
    # function is what the hermetic suite drives, and a network call inside it
    # would either break that or need a flag defaulting to off — which is the
    # ADR-051 shape exactly: a guard wired into the path nobody takes. This
    # command is the only entry point that spends money, including the one the
    # cadence agent invokes, so it is the one place the check belongs.
    if preflight:
        dead = preflight_provider(provider, model)
        if dead:
            console.print(f"[red]refusing to start: {dead}[/red]")
            console.print(
                "  Nothing was run. Run 17 (2026-08-17) found this out 3438 "
                "seconds and one full case into a five-case run."
            )
            if notify:
                _bench_notify(repo_dir, _notifier().bench_failed_alert(
                    f"preflight refused the run: {dead}",
                    workspace=Path(repo_dir).resolve().name,
                ))
            raise typer.Exit(3)
    try:
        summary = run_product_bench(
            cases_dir, provider=provider, limit=limit, repo_dir=repo_dir,
            resume=resume,
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        if notify:
            _bench_notify(repo_dir, _notifier().bench_failed_alert(
                str(exc), workspace=Path(repo_dir).resolve().name,
            ))
        raise typer.Exit(1) from exc
    table = Table(title="product bench")
    for col in ("case", "autopilot", "built", "probes passed", "clean reviews", "s"):
        table.add_column(col)
    for c in summary.cases:
        table.add_row(
            # Marked in the table for the same reason it is marked in the file:
            # a reused row is indistinguishable from a measured one otherwise,
            # and the person reading a 12-second five-case run deserves to know
            # why it was quick.
            f"{c.name} [dim](resumed)[/dim]" if c.resumed else c.name,
            c.autopilot_status,
            f"{c.tasks_built}/{c.tasks_total}",
            f"{sum(1 for p in c.probes if p.passed)}/{len(c.probes)}",
            f"{c.clean_reviews}/{c.tasks_built}",
            str(c.duration_s),
        )
    console.print(table)
    if summary.aborted:
        console.print(
            f"[red]run aborted: {summary.aborted}[/red]\n"
            "  The cases below it were never asked — they are unmeasured, not "
            "failures. Fix the cause and rerun with --resume to keep what was "
            "already measured."
        )
    scope = (
        f" (over {summary.cases_measured} of {summary.cases_total} cases)"
        if summary.unmeasured else ""
    )
    # Same idiom as the increment gate below: a rate with no denominator
    # prints as "not measured", never as 0%.
    def _rate(value: float | None) -> str:
        return (
            f"[bold]{value:.0%}[/bold]" if value is not None
            else "[bold]not measured[/bold]"
        )

    console.print(
        f"build rate {_rate(summary.build_rate)} · "
        f"probe pass {_rate(summary.probe_pass_rate)} · "
        f"clean reviews {_rate(summary.clean_review_rate)}{scope}"
    )
    # The probe rate can cover fewer cases than the other two even when
    # nothing is unmeasured, and a narrower denominator that does not say so
    # is how run 16's reading went wrong in the other direction (ADR-061).
    if summary.no_probe_reading:
        console.print(
            f"  [dim]probe pass is over "
            f"{summary.cases_measured - len(summary.no_probe_reading)} of "
            f"{summary.cases_total} cases — "
            f"{', '.join(summary.no_probe_reading)} built nothing to probe. "
            f"That failure is in the build rate, counted once.[/dim]"
        )
    # The increment axis is reported on its own line, never folded into the
    # three above: it has its own cases and its own denominator, and a
    # reader who sees one number cannot tell which question it answered.
    if summary.gate_cases_total:
        gate = (
            f"[bold]{summary.gate_rate:.0%}[/bold]"
            if summary.gate_rate is not None else "[bold]not measured[/bold]"
        )
        console.print(
            f"increment gate {gate} (over {summary.gate_cases_measured} of "
            f"{summary.gate_cases_total} increment case(s))"
        )
    # On the same screen as the rates, and deliberately on its own line below
    # them: it is not a rate and must never be read as one. Every previous run
    # reported its duration and not its cost, so "should I run this again"
    # was answerable in hours and not in dollars.
    from ai_venture_studio.product_bench import render_spend

    console.print(f"cost {render_spend(summary.spend)}")
    # Save before exiting: a run that measured three cases still measured
    # three cases, and the result is the series the kill criterion reads.
    saved = save_summary(summary, repo_dir, provider=provider)
    console.print(f"saved: {saved}")
    # Said at the moment the numbers are on screen, not left to whoever opens
    # the file later. These rates came out of a regex table; they say the
    # harness ran end to end, and nothing at all about capability (ADR-056).
    if _is_simulated(provider):
        console.print(
            f"[yellow]provider '{provider}' is simulated — these rates measure "
            f"the harness, not the system.[/yellow] Not written to "
            f"benchmarks/results/ and not read by `avs bench-criterion`."
        )
    if notify:
        from ai_venture_studio import bench_criterion

        _bench_notify(repo_dir, _notifier().bench_alert(
            summary,
            workspace=Path(repo_dir).resolve().name,
            saved=str(saved),
            # Read AFTER saving, so the series they judge includes this run.
            concern=bench_criterion.concern(repo_dir),
            movement=bench_criterion.movement(repo_dir),
            provider=provider,
        ))
    # A case that RAN and produced nothing scores a real 0.0 (ADR-035/043),
    # so it never appears in `unmeasured` and the rate is all anyone sees.
    # The rate says how badly; only this says why, and the pipeline knew.
    for case in summary.cases:
        if case.measured and case.failure_reason:
            console.print(
                f"[yellow]{case.name} produced nothing and scores 0.0 — "
                f"{case.failure_reason}[/yellow]"
            )
    if summary.unmeasured:
        # Not a capability finding — the harness broke, and nothing else
        # will say so. The rates above are honest about their scope and
        # would otherwise be the only thing anyone sees.
        console.print(
            f"[red]{len(summary.unmeasured)} case(s) never ran: "
            f"{', '.join(summary.unmeasured)}[/red]"
        )
        console.print(
            "  These are excluded from the rates above, not scored as zero — "
            "so this run measured less of the machine than it looks like."
        )
        # The measured cases are banked; say so, or the next operator repeats
        # the hours this whole mechanism exists to stop them repeating.
        banked = [c.name for c in summary.cases if c.measured and not c.resumed]
        if banked:
            console.print(
                f"  {len(banked)} measured case(s) are checkpointed "
                f"({', '.join(banked)}) — rerun with [bold]--resume[/bold] and "
                "they will not be paid for again."
            )
        raise typer.Exit(3)


@app.command()
def recover(repo_dir: str = typer.Option(".", help="Repository the reviews ran in")):
    """Continue reviews, deploy reviews, and incidents that crashed mid-run
    from their checkpoints (all three graphs share .mas/checkpoints.db)."""
    from ai_venture_studio.deploy import recover_deploy_reviews
    from ai_venture_studio.maintenance import recover_maintenance
    from ai_venture_studio.orchestrator import recover_reviews

    results = [
        {"kind": "review", "id": r["review_id"], **{k: v for k, v in r.items()
                                                    if k != "review_id"}}
        for r in recover_reviews(repo_dir)
    ]
    results += recover_deploy_reviews(repo_dir)
    results += recover_maintenance(repo_dir)
    if not results:
        console.print("nothing to recover")
        return
    for r in results:
        console.print(f"  {r['kind']} {r['id']}: {r['status']}"
                      + (f" → {r.get('verdict')}" if r.get("verdict") else ""))


@app.command()
def correct(
    complaint: str = typer.Argument(..., help="What's wrong, in your own words"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider (e.g. 'mock')"),
    yes: bool = typer.Option(
        False, "--yes", help="Build the drafted changes without asking"
    ),
):
    """M3 — 这不是我要的: repairs go through the fix path, requirement
    changes come back as a DRAFT — what would change, and what was assumed —
    and build only once you agree (your complaint IS the approval, recorded
    verbatim).

    Raise several things at once and each is routed and answered on its own
    line — none is dropped for being second."""
    from ai_venture_studio.upstream.autopilot import run_feature
    from ai_venture_studio.upstream.correction import apply_change, run_corrections

    results = run_corrections(repo_dir, complaint, provider=provider)
    if len(results) > 1:
        console.print(f"{len(results)} separate issues:")
    for result in results:
        color = {
            "fixed": "green", "change_planned": "yellow",
        }.get(result.status, "red")
        where = f"{result.spec_slug}: " if len(results) > 1 and result.spec_slug else ""
        console.print(
            f"[bold {color}]{result.status}[/bold {color}] — {where}{result.detail}"
        )
        if result.status != "change_planned" or result.plan is None:
            continue
        # The assumptions are the decisions made on your behalf. They are
        # printed BEFORE the build, not filed in the report afterwards,
        # because a wrong one is cheap to catch here and expensive to catch
        # in a built product.
        for line in result.plan.assumptions:
            console.print(f"    assumed: {line}")
        for line in result.plan.criteria:
            console.print(f"    [dim]after:[/dim] {line}")
        if not yes:
            console.print("  re-run with --yes to make this change")
            continue
        # One change at a time, in this process: `run_feature` amends the
        # spec on success, and two of them racing on one workspace is the
        # corruption the Studio's build guard exists to prevent.
        built = run_feature(
            repo_dir, apply_change(repo_dir, result.plan),
            provider=provider, yes=True,
        )
        console.print(f"  build: {built.status}")
        if built.status != "completed":
            result.status = "error"
    # Exit non-zero if ANY issue failed: a run that repaired two of three
    # and exited 0 would report success for the one it could not do.
    if any(r.status == "error" for r in results):
        raise typer.Exit(code=1)


@app.command()
def walkthrough(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
):
    """M4 — regenerate the 验收清单 (product/ACCEPTANCE.md)."""
    from ai_venture_studio.upstream.walkthrough import generate_walkthrough

    console.print(f"written: {generate_walkthrough(repo_dir, provider=provider)}")


@app.command()
def digest(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
    days: int = typer.Option(7, help="Window"),
):
    """M5 — weekly plain-language digest from the product's own telemetry;
    reconciles the hypothesis ledger with observed events."""
    from ai_venture_studio.upstream.telemetry import generate_digest

    console.print(f"written: {generate_digest(repo_dir, provider=provider, days=days)}")


@app.command("retry-task")
def retry_task(
    task_id: str = typer.Argument(..., help="Failed task id from the report"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
):
    """M7 — retry ONE failed module without rebuilding anything else."""
    from ai_venture_studio.upstream import approve_spec, run_build, run_spec_stage
    from ai_venture_studio.upstream.autopilot import review_and_repair
    from ai_venture_studio.upstream.plan import load_plan, read_fdr

    plan_result = load_plan(repo_dir)
    task = next((t for t in plan_result.tasks if t.id == task_id), None)
    if task is None:
        console.print(f"[red]no task {task_id!r} in the plan[/red]")
        raise typer.Exit(code=1)
    # The founder's document is the literal interface contract, and the
    # retry path was the one build path that never passed it — so a retried
    # module was free to invent field names its siblings had agreed on.
    contract = read_fdr(repo_dir)
    # The recorded failure is the retry's context. A retry that does not
    # know why the last attempt died repeats it — the spec writer picks the
    # same rejected phrasing, the implementer walks into the same wall.
    prior_failure = _recorded_failure(repo_dir, task_id)
    if prior_failure:
        console.print("[dim]passing the previous attempt's failure to the writer[/dim]")
    spec = run_spec_stage(
        repo_dir, f"{task.description} (task:{task.id})", provider=provider,
        source_contract=contract, prior_failure=prior_failure,
    )
    if spec.status != "proposed":
        reasons = "; ".join(spec.block_reasons) or "blocked (no reasons recorded)"
        record_retry_outcome(repo_dir, task, None, status="spec_blocked",
                             detail=reasons)
        console.print(f"[red]spec blocked: {reasons}[/red]")
        raise typer.Exit(code=1)
    approve_spec(repo_dir, spec.slug)
    result = run_build(repo_dir, spec.slug, provider=provider,
                       task_lane=task.lane, task_estimate_hours=task.estimate_hours,
                       source_contract=contract, prior_failure=prior_failure)
    # Gate 3, which this path used to skip outright: a module retried here
    # reached the founder with no reviewer having read it and an empty
    # verdict in the report, sitting next to modules `create` had reviewed.
    verdict = None
    if result.status == "built":
        console.print("[dim]checking the code that was written[/dim]")
        verdict, extra_detail, approvals, _by_voter = review_and_repair(
            Path(repo_dir).resolve(), provider=provider,
            model="claude-opus-4-8" if provider != "mock" else "mock",
            label=spec.slug, detail=result.detail,
        )
        result.detail = extra_detail
        for line in approvals:
            console.print(f"[dim]{line}[/dim]")
    # Record it. Without this a successful retry changed nothing the founder
    # can see: outcomes.yaml still said the module had failed, so the report
    # and the Studio's "modules that did not build" card kept offering to
    # retry something that was already built and committed.
    record_retry_outcome(repo_dir, task, result, verdict=verdict)
    color = "green" if result.status == "built" else "red"
    console.print(f"[bold {color}]{result.status}[/bold {color}] {result.detail}"
                  + (f" · review: {verdict}" if verdict else ""))
    if result.status != "built":
        raise typer.Exit(code=1)



def _recorded_failure(repo_dir, task_id: str) -> str:
    """The failed row for this task in product/outcomes.yaml, as retry context.

    Best-effort by design: no record just means the retry runs without prior
    context, the way it always did — never an error that blocks the retry.
    """
    import yaml as _yaml

    try:
        rows = _yaml.safe_load(
            (Path(repo_dir) / "product" / "outcomes.yaml").read_text(encoding="utf-8")
        ) or []
    except (OSError, _yaml.YAMLError):
        return ""
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("task_id") != task_id:
            continue
        if row.get("status") == "built":
            return ""
        parts = [f"previous attempt status: {row.get('status', 'unknown')}"]
        if row.get("detail"):
            parts.append(f"detail: {row['detail']}")
        if row.get("test_summary"):
            parts.append(f"test summary: {row['test_summary']}")
        return "\n".join(parts)
    return ""


def record_retry_outcome(
    repo_dir, task, result, *, status="", detail="", verdict=None
) -> None:
    """Update this task's row in product/outcomes.yaml after a retry.

    The autopilot writes outcomes as it goes; `retry-task` did not, so a
    module that a founder successfully retried stayed listed as failed
    forever — the Studio kept offering the retry button for code that was
    already committed. Best-effort: a bookkeeping failure must not turn a
    successful build into a failed command.
    """
    import yaml as _yaml

    from ai_venture_studio.upstream.autopilot import TaskOutcome

    try:
        path = Path(repo_dir) / "product" / "outcomes.yaml"
        rows = []
        if path.exists():
            rows = _yaml.safe_load(path.read_text(encoding="utf-8")) or []
        row = TaskOutcome(
            task_id=task.id,
            title=task.title,
            status=status or (result.status if result else "error"),
            review_verdict=verdict,
            detail=detail or (result.detail if result else ""),
            iterations=result.iterations if result else 0,
            files_written=list(result.files_written) if result else [],
            test_summary=result.test_summary if result else "",
        ).model_dump()
        replaced = False
        for index, existing in enumerate(rows):
            if isinstance(existing, dict) and existing.get("task_id") == task.id:
                # Keep the review verdict from the original run only when the
                # retry did not produce a new one.
                if row.get("review_verdict") is None:
                    row["review_verdict"] = existing.get("review_verdict")
                rows[index] = row
                replaced = True
                break
        if not replaced:
            rows.append(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _yaml.safe_dump(rows, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — bookkeeping never fails a retry
        return


@app.command()
def undo(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """M7 — 回到上一个版本 (a rescue branch keeps even undo undoable)."""
    from ai_venture_studio.upstream.autopilot import undo_last

    result = undo_last(Path(repo_dir).resolve())
    console.print(f"{result['status']}: {result.get('detail') or result.get('restored_to', '')}")
    if result["status"] == "error":
        raise typer.Exit(code=1)


@app.command("reconcile")
def reconcile(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    apply: bool = typer.Option(
        False, "--apply", help="Repair the flags (default: report only)"
    ),
    scan: str = typer.Option(
        "", "--scan", help="Scan every workspace one level under this directory"
    ),
):
    """Restore `built` flags a pre-v0.70 workspace lost, so a resumed run
    does not rebuild — and re-bill — modules that are already committed.

    Reports by default. Repairs only where outcomes.yaml AND a `feat(<slug>)`
    commit agree the module was built; anything they disagree on is named for
    a human and left alone.
    """
    from ai_venture_studio.upstream.plan import reconcile_built_flags

    roots = (
        sorted(d for d in Path(scan).expanduser().resolve().iterdir() if d.is_dir())
        if scan else [Path(repo_dir).resolve()]
    )
    findings = 0
    for root in roots:
        report = reconcile_built_flags(root, apply=apply)
        if report["status"] == "not_a_built_workspace":
            if not scan:
                console.print(f"{root.name}: no built product here — nothing to check")
            continue
        lost, unsupported = report["lost"], report["unsupported"]
        superseded = report["superseded"]
        if not lost and not unsupported:
            note = (
                f" ({len(superseded)} superseded spec(s) from a re-plan, "
                "harmless)" if superseded else ""
            )
            console.print(
                f"[green]ok[/green] {root.name}: every built module has its "
                f"flag{note}"
            )
            continue
        findings += len(lost) + len(unsupported)
        verb = "repaired" if apply else "LOST (report only — pass --apply to fix)"
        if lost:
            console.print(
                f"[yellow]{root.name}[/yellow]: {len(lost)} flag(s) {verb} — "
                + ", ".join(f"{e['task_id']} ({e['slug']})" for e in lost)
            )
            if not apply:
                console.print(
                    "  a resumed run would rebuild these and charge you again"
                )
        for entry in unsupported:
            console.print(
                f"[red]{root.name}[/red]: {entry['task_id']} ({entry['slug']}) is "
                "recorded built but has no commit — not repaired, look at it"
            )
    if findings and not apply:
        raise typer.Exit(code=3)  # a finding, not a failure — the exit code says "look"


@app.command()
def verify(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    provider: str = typer.Option("anthropic", help="Provider"),
):
    """Auto-verify the built product against YOUR FDR: probes are generated
    from what you asked for and run against what was built."""
    from ai_venture_studio.upstream.probegen import verify_product

    path = verify_product(repo_dir, provider=provider)
    console.print(path.read_text(encoding="utf-8"))
    console.print(f"saved: {path}")


@app.command()
def smoke(
    provider: list[str] = typer.Option(
        None, "--provider", help="Provider to check (repeatable). "
        "Default: anthropic, openai, google",
    ),
    model: str = typer.Option(None, help="Override the model for every provider"),
    repo_dir: str = typer.Option(".", help="Where to flush the spend ledger"),
):
    """Live-boundary smoke — run this before every release.

    Four real calls per configured provider, costing a fraction of a cent,
    against the boundaries the hermetic suite cannot model: a call returns
    text, a large max_tokens request streams instead of raising (the bug
    that shipped twice, unable to build anything), a capped response is
    detectable as truncated, and the spend ledger saw it all.

    An unconfigured provider is skipped, loudly, naming the variable.
    """
    from ai_venture_studio import smoke as smoke_mod
    from ai_venture_studio import spend

    console.print(
        "[dim]This makes real API calls on your own key — a few tenths of a "
        "cent — because that is the only way to check these.[/dim]"
    )
    results = smoke_mod.run_smoke(list(provider) if provider else None, model=model)
    worst = "ok"
    for result in results:
        color = {"ok": "green", "skipped": "yellow"}.get(result.status, "red")
        console.print(
            f"\n[bold {color}]{result.status}[/bold {color}] "
            f"{result.provider} · {result.model}"
        )
        for check in result.checks:
            mark = {"ok": "[green]✓[/green]", "skipped": "[yellow]—[/yellow]"}.get(
                check.status, "[red]✗[/red]"
            )
            console.print(f"  {mark} {check.name}: {check.detail}")
        if result.status == "failed":
            worst = "failed"
        elif result.status == "skipped" and worst == "ok":
            worst = "skipped"
    spend.flush(repo_dir)
    cost = spend.summarize_workspace(repo_dir)
    if cost.calls:
        console.print(f"\n[dim]{spend.render_plain(cost, what='This workspace')}[/dim]")
    if worst == "failed":
        console.print(
            "\n[red]Do not release.[/red] A failed check here is a product "
            "that cannot do its job with a real key, whatever the suite says."
        )
        raise typer.Exit(code=1)
    if worst == "skipped":
        console.print(
            "\n[yellow]Some providers were not checked[/yellow] — a skip is "
            "not a pass; set the named variables to check them."
        )


@app.command("mp-runtime")
def mp_runtime(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    timeout: int = typer.Option(120, help="Seconds to wait for DevTools"),
    cli_path: str = typer.Option(None, help="Path to the DevTools cli binary"),
):
    """小程序: open the project in WeChat DevTools and visit every registered
    page — the runtime half of the loadability gate.

    The gate in the build loop is static (does app.json name pages that
    exist); this answers whether those pages actually render. It needs the
    DevTools desktop app, `miniprogram-automator`, and DevTools' service
    port switched on by you — a missing precondition is a visible skip
    naming the remedy, never a pass.
    """
    from ai_venture_studio.lanes.miniprogram import mp_runtime_check

    report = mp_runtime_check(repo_dir, timeout_s=timeout, cli_path=cli_path)
    color = {"ok": "green", "skipped": "yellow"}.get(report.status, "red")
    console.print(f"[bold {color}]{report.status}[/bold {color}] — {report.detail}")
    for finding in report.findings:
        console.print(f"  [red]{finding.message}[/red]")
    if report.status == "failed":
        raise typer.Exit(code=1)


@app.command("setup-tests")
def setup_tests(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """小程序: install jest + miniprogram-simulate so page-level tests run
    in the build/test gates (npm required)."""
    from ai_venture_studio.upstream.ship import setup_miniprogram_tests

    result = setup_miniprogram_tests(repo_dir)
    color = {"ready": "green"}.get(result["status"], "yellow")
    console.print(f"[bold {color}]{result['status']}[/bold {color}] — {result['detail']}")


@app.command("services-cloud")
def services_cloud(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Attempt cloud AUTO-provisioning (Supabase CLI, gated on login);
    degrades to the guided SERVICES.md path when tooling is absent."""
    from ai_venture_studio.upstream import load_project
    from ai_venture_studio.upstream.provisioning import auto_provision_cloud, write_cloud_guide

    profile = load_project(repo_dir).profile
    write_cloud_guide(repo_dir, profile)
    result = auto_provision_cloud(repo_dir, profile)
    color = {"provisioned": "green"}.get(result["status"], "yellow")
    console.print(f"[bold {color}]{result['status']}[/bold {color}] — {result['detail']}")


@app.command()
def readiness(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Substrate readiness report (docs 18-19): active stages at the
    declared rung and what each missing rung would unlock."""
    from ai_venture_studio.adoption import load_substrate_profile, readiness_report

    try:
        profile = load_substrate_profile(repo_dir)
    except ValueError as exc:
        # A malformed profile is an operator mistake, not a crash: the
        # loader's message already names the file and field.
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if profile is None:
        # Not a dead end: detect what the repo already has and print a
        # starter profile prefilled from it — the what-we-found pattern.
        # Detection is conservative (pr_flow/observability can't be read
        # from the filesystem, so they start false/none and only the
        # human widens them).
        import yaml as _yaml

        root = Path(repo_dir).resolve()
        ci_files = (
            ".github/workflows", ".gitlab-ci.yml", "azure-pipelines.yml",
            "Jenkinsfile", ".circleci",
        )
        languages: list[str] = []
        map_path = root / ".mas" / "codebase-map.yaml"
        if map_path.exists():
            try:
                map_data = _yaml.safe_load(map_path.read_text(encoding="utf-8")) or {}
                languages = sorted(map_data.get("languages") or {})
            except _yaml.YAMLError:
                languages = []
        starter = {"substrate": {
            "vcs": "git" if (root / ".git").exists() else "none",
            "pr_flow": False,
            "ci": any((root / f).exists() for f in ci_files),
            "observability": ["none"],
            "progressive_delivery": False,
            "languages": languages,
        }}
        console.print(
            "No .mas/substrate-profile.yaml — the adoption ladder is not "
            "declared, so every stage runs ungated (effective S4).\n\n"
            "Detected from this repo (confirm pr_flow/observability by "
            "hand — they cannot be read from the filesystem):\n"
        )
        console.print(_yaml.safe_dump(starter, sort_keys=False).rstrip())
        console.print(
            "\nSave that as .mas/substrate-profile.yaml and re-run "
            "avs readiness for the rung-by-rung roadmap (§18.47.1)."
        )
        raise typer.Exit(code=0)
    console.print(readiness_report(profile, project_name=Path(repo_dir).resolve().name))


@app.command()
def preflight(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit 1 when anything is not ready — lets a pipeline gate on "
        "enterprise readiness instead of discovering it mid-build",
    ),
):
    """Ready to build? The enterprise-mode preflight, in the terminal —
    the same six live checks the Studio card renders (model credential,
    git identity, forge auth, governance, substrate, Studio access),
    plus the governance posture line."""
    from ai_venture_studio.studio_modes import build_preflight, governance_posture

    root = Path(repo_dir).resolve()
    rows = build_preflight(root)
    table = Table(show_lines=False)
    table.add_column("")
    table.add_column("check")
    table.add_column("found / fix")
    for row in rows:
        icon = "✅" if row["state"] == "ready" else "◻️"
        detail = row["found"]
        if row["state"] != "ready" and row["fix"]:
            detail += f"\n→ {row['fix']}"
        table.add_row(icon, row["item"], detail)
    console.print(table)

    posture = governance_posture(root)
    parts = []
    if posture["attention"]:
        parts.append("[red]attention: " + ", ".join(posture["attention"]) + "[/red]")
    if posture["measured"]:
        parts.append("measured: " + ", ".join(posture["measured"]))
    if posture["unconfigured"]:
        parts.append("[dim]not configured: "
                     + ", ".join(posture["unconfigured"]) + "[/dim]")
    console.print("posture · " + " · ".join(parts))

    ready = sum(1 for r in rows if r["state"] == "ready")
    console.print(f"{ready}/{len(rows)} ready")
    if strict and (ready < len(rows) or posture["attention"]):
        raise typer.Exit(code=1)


@app.command()
def toolchain(
    language: str = typer.Argument(..., help="Language lane: python | java | dotnet"),
    repo_dir: str = typer.Option(".", help="Repository to run the det_tools slots in"),
    manifest: str = typer.Option(
        None, help="Seeded-defect manifest (yaml) — measures catch-rate and registers"
    ),
    baseline: float = typer.Option(
        1.0, help="Reference (python-lane) catch-rate the parity margin is measured against"
    ),
):
    """Run a language's det_tools slots (ADR-U16). Skipped slots are loud —
    a missing scanner is NOT clean. With --manifest, measures the
    seeded-defect catch-rate and registers the toolchain (or labels it
    PROVISIONAL with the lagging slots named)."""
    from ai_venture_studio.adoption import (
        benchmark_toolchain,
        load_seeded_manifest,
        register_toolchain,
        run_toolchain,
    )

    report = run_toolchain(repo_dir, language)
    table = Table(show_lines=False)
    for col in ("Slot", "Status", "Detail"):
        table.add_column(col)
    for r in report.results:
        color = {"clean": "green", "findings": "yellow"}.get(r.status, "red")
        table.add_row(r.slot, f"[{color}]{r.status}[/{color}]", r.detail)
    console.print(table)

    if manifest is None:
        if report.skipped_slots:
            console.print(
                f"[red]skipped: {', '.join(report.skipped_slots)} — "
                "install or override argv in .mas/toolchains.yaml[/red]"
            )
            raise typer.Exit(code=1)
        raise typer.Exit(code=0)

    result = benchmark_toolchain(report, load_seeded_manifest(manifest))
    record = register_toolchain(repo_dir, result, baseline_rate=baseline)
    color = "green" if record.status == "registered" else "yellow"
    console.print(
        f"[bold {color}]{record.status}[/bold {color}] — catch-rate "
        f"{record.catch_rate:.0%} (baseline {record.baseline_rate:.0%}, "
        f"margin {record.parity_margin:.0%})"
    )
    if record.gaps:
        console.print(f"lagging slots: {', '.join(record.gaps)}")
    if record.status == "provisional":
        raise typer.Exit(code=1)


@app.command()
def calibrate(
    language: str = typer.Argument(..., help="Language lane: python | java | dotnet"),
    repo_dir: str = typer.Option(
        None, help="Seeded lane dir (default: the bundled tests/toolchains/seeded/<lang>)"
    ),
    manifest: str = typer.Option(
        None, help="Manifest yaml (default: <lane>/seeded.yaml)"
    ),
):
    """Calibrate a language lane's manifest patterns against the real
    scanners (run inside `make calibrate`, where the binaries exist). Writes
    a per-defect report — caught/missed plus the actual slot output for each
    miss — so hand-labeled patterns can be fixed. A miss on a slot that ran
    means the PATTERN is wrong, not the scanner; a skipped slot means the
    binary is absent."""
    from ai_venture_studio.adoption import write_calibration_report
    from ai_venture_studio.adoption.calibrate import calibration_report

    seeded = Path(__file__).resolve().parent.parent.parent / "tests" / "toolchains" / "seeded"
    lane = Path(repo_dir) if repo_dir else seeded / language
    manifest_path = Path(manifest) if manifest else lane / "seeded.yaml"
    if not manifest_path.exists():
        console.print(f"[red]no manifest at {manifest_path}[/red]")
        raise typer.Exit(code=2)

    report = calibration_report(lane, language, manifest_path)
    # Write under CWD (the container mounts it), not the lane dir which is
    # ephemeral inside the image.
    out = write_calibration_report(lane, language, manifest_path, out_base=Path.cwd())

    color = "green" if not report.needs_recalibration and not report.skipped_slots else "yellow"
    console.print(
        f"[bold {color}]{language}[/bold {color}] catch-rate "
        f"{report.catch_rate:.0%} ({report.caught}/{report.total})"
    )
    if report.skipped_slots:
        console.print(
            f"[red]skipped slots (binary absent): {', '.join(report.skipped_slots)}[/red]"
        )
    if report.misses:
        table = Table(show_lines=False, title="misses — fix the pattern or the scanner rule")
        for col in ("Defect", "Slot", "Expected pattern", "Why missed"):
            table.add_column(col)
        for m in report.misses:
            table.add_row(m.defect_id, m.slot, m.expected_pattern, m.detail)
        console.print(table)
    console.print(f"Full report (with slot output for each miss): {out}")
    if report.needs_recalibration or report.skipped_slots:
        raise typer.Exit(code=1)


@app.command("perf-calibrate")
def perf_calibrate_cmd(
    requests_per_endpoint: int = typer.Option(
        40, help="Samples per endpoint; the p95 is taken over these"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Measure and print; do not write the file"
    ),
):
    """Re-measure the perf lane against its seeded defects and record it.

    `benchmarks/perf_seeded/calibration.yaml` is committed, and `lane_status()`
    reads it to report CALIBRATED. Until this command existed, `run_perf_calibration`
    had no caller and `record_calibration` had no caller either — so the
    artifact the lane's status depends on could not be regenerated by any code
    path in the repository, only hand-edited. That is the gap this closes.

    Free and local: a loopback HTTP server over a seeded sqlite db, no
    provider, no network, no API spend. It is still a MEASUREMENT and not a
    formality — the reading it writes replaces the one on disk, and it is
    environment-dependent (`environment: loopback`, `parity: low`), so a run
    on different hardware is a different reading, not a refresh of the old
    one. Run it deliberately, the same way a bench is run deliberately.
    """
    import datetime as _dt

    from ai_venture_studio.lanes import calibrate_perf

    result = calibrate_perf.run_perf_calibration(
        requests_per_endpoint=requests_per_endpoint
    )
    for reading in result.readings:
        mark = "caught" if reading.caught else "MISSED"
        console.print(
            f"  {mark:7} {reading.defect:38} "
            f"{reading.p95_ms:8.3f}ms  vs control {reading.control_p95_ms:.3f}ms  "
            f"({reading.degradation:.2f}x)"
        )
    console.print(
        f"\n  catch rate {result.catch_rate:.0%} "
        f"at a detection factor of {result.detection_factor}"
    )
    if dry_run:
        console.print("[yellow]--dry-run: nothing written[/yellow]")
        return
    out = calibrate_perf.record_calibration(
        result, at=_dt.datetime.now().strftime("%Y-%m-%d")
    )
    console.print(f"  recorded: {out}")
    console.print(f"  lane status: {calibrate_perf.lane_status()}")


@app.command("eval-gate")
def eval_gate_cmd(
    scores: str = typer.Argument(..., help="YAML file of metric: value pairs"),
    repo_dir: str = typer.Option(".", help="Workspace with .mas/eval-baseline.yaml"),
    pin: bool = typer.Option(
        False, help="Pin these scores as the new baseline instead of gating "
        "(the file diff is the reviewable artifact — commit it via PR)"
    ),
    tolerance: float = typer.Option(0.01, help="Tolerance when pinning"),
):
    """Eval-set regression gate (§18.48.1): score deltas vs the pinned
    baseline. A pinned metric missing from the scores fails — unmeasured
    never reads as unregressed."""
    import yaml as yaml_lib

    from ai_venture_studio.adoption import eval_gate, pin_baseline

    data = yaml_lib.safe_load(Path(scores).read_text(encoding="utf-8")) or {}
    values = {str(k): float(v) for k, v in (data.get("metrics") or data).items()}
    if pin:
        path = pin_baseline(repo_dir, values, tolerance=tolerance)
        console.print(f"Baseline pinned: {path} — commit the diff via PR.")
        raise typer.Exit(code=0)
    result = eval_gate(repo_dir, values)
    table = Table(show_lines=False)
    for col in ("Metric", "Status", "Baseline", "Current", "Delta"):
        table.add_column(col)
    for v in result.verdicts:
        color = {"ok": "green", "unpinned": "yellow"}.get(v.status, "red")
        table.add_row(
            v.metric, f"[{color}]{v.status}[/{color}]",
            "" if v.baseline is None else f"{v.baseline}",
            "" if v.current is None else f"{v.current}",
            "" if v.delta is None else f"{v.delta:+}",
        )
    console.print(table)
    if not result.passed:
        raise typer.Exit(code=1)
    if result.unpinned:
        console.print(
            f"[yellow]unpinned metrics (pin to make them gate): "
            f"{', '.join(result.unpinned)}[/yellow]"
        )


@app.command()
def idempotency(
    run_a: str = typer.Argument(..., help="Output directory of the first run"),
    run_b: str = typer.Argument(..., help="Output directory of the re-run"),
):
    """Backfill idempotency check (§18.48.1): the fixture-slice re-run must
    be byte-identical. Two empty runs are an error, not a pass."""
    from ai_venture_studio.adoption import idempotency_check

    result = idempotency_check(run_a, run_b)
    if result.identical:
        console.print("[green]identical[/green] — backfill is idempotent on this slice")
        raise typer.Exit(code=0)
    for label, paths in (
        ("content differs", result.content_diffs),
        ("only in first", result.only_in_first),
        ("only in second", result.only_in_second),
    ):
        for p in paths:
            console.print(f"[red]{label}[/red]: {p}")
    raise typer.Exit(code=1)


@app.command("data-checks")
def data_checks(repo_dir: str = typer.Option(".", help="Workspace directory")):
    """Run the workspace's external data checks (dbt auto-detected;
    others declared in .mas/data-checks.yaml). Skipped is loud, never clean."""
    from ai_venture_studio.adoption import run_data_checks

    results = run_data_checks(repo_dir)
    table = Table(show_lines=False)
    for col in ("Check", "Status", "Detail"):
        table.add_column(col)
    for r in results:
        color = {"clean": "green", "findings": "yellow"}.get(r.status, "red")
        table.add_row(r.slot, f"[{color}]{r.status}[/{color}]", r.detail)
    console.print(table)
    if any(r.status in ("skipped", "error", "findings") for r in results):
        raise typer.Exit(code=1)


@app.command()
def dwell(repo_dir: str = typer.Option(".", help="Repository the reviews ran in")):
    """Approval-dwell-time report (F-18.3): how long humans actually sit on
    Gate-3 escalations before deciding. Median collapse + zero overrides
    flags the rubber-stamp pattern."""
    from ai_venture_studio.adoption import gate_dwell_report

    report = gate_dwell_report(repo_dir)
    for note in report.notes:
        style = "bold red" if report.rubber_stamp else "yellow"
        console.print(f"[{style}]{note}[/{style}]")
    if report.samples:
        console.print(
            f"{len(report.samples)} escalation(s) — median {report.median_s}s, "
            f"p90 {report.p90_s}s, override rate {report.override_rate:.0%}"
        )
        table = Table(show_lines=False)
        for col in ("Review", "Dwell (s)", "Decision"):
            table.add_column(col)
        for s in report.samples:
            table.add_row(s.review_id, f"{s.dwell_s:.0f}", s.decision)
        console.print(table)
    if report.rubber_stamp:
        raise typer.Exit(code=1)


@app.command()
def attest(
    review_id: str = typer.Argument(
        None, help="Review to chain into the ledger; omit to just verify"
    ),
    repo_dir: str = typer.Option(".", help="Repository the reviews ran in"),
):
    """Attestation ledger (§18.49): chain a review's gate/verdict records
    into the append-only hash-chained ledger, then verify the whole chain.
    Integrity only — org-key signing is a separate, deferred decision."""
    from ai_venture_studio.adoption import attest_review, verify_ledger

    if review_id is not None:
        try:
            count = attest_review(repo_dir, review_id)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        console.print(f"attested {count} record(s) from review {review_id}")

    verification = verify_ledger(repo_dir)
    if verification.ok:
        console.print(
            f"[green]chain verified[/green] — {verification.entries} entries"
        )
        raise typer.Exit(code=0)
    console.print(
        f"[bold red]LEDGER INTEGRITY FAILURE[/bold red] at seq "
        f"{verification.first_bad_seq}:"
    )
    for problem in verification.problems:
        console.print(f"  - {problem}")
    raise typer.Exit(code=1)


@app.command("cab-package")
def cab_package(
    review_id: str = typer.Argument(..., help="Review ID (directory under .mas/reviews/)"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
    change_id: str = typer.Option(None, help="CAB change record id (defaults to review id)"),
):
    """Assemble a CAB change package from a finished review: exports the
    evidence bundle, pre-fills what the audit trail knows, and runs the
    Gate-R preflight. Rollback plan and approver stay human — a fresh
    package is not eligible until a person completes it. Submission itself
    is always human."""
    from ai_venture_studio.adoption import (
        gate_r_entry,
        prepare_change_package,
        save_change_package,
    )

    try:
        package = prepare_change_package(repo_dir, review_id, change_id=change_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    path = save_change_package(repo_dir, package)
    entry = gate_r_entry(repo_dir, package)

    table = Table(show_lines=False)
    for col in ("Check", "Status", "Detail"):
        table.add_column(col)
    for r in entry.results:
        table.add_row(
            r.check.id,
            "[green]pass[/green]" if r.passed else "[red]fail[/red]",
            r.detail or r.check.description,
        )
    console.print(table)
    console.print(f"Package: {path}")
    if entry.eligible:
        console.print("[green]Gate R entry: eligible — a human submits.[/green]")
    else:
        console.print(
            "[yellow]Not yet eligible — complete the failing fields in the "
            "package file and re-run.[/yellow]"
        )
        raise typer.Exit(code=1)


@app.command("claim-lint")
def claim_lint_cmd(
    ledger: str = typer.Argument(..., help="Path to a claims/*.claim.yaml ledger"),
    kind: str = typer.Option(
        "market", help="Artifact kind: opportunity | market | prd | launch"
    ),
    mas_dir: str = typer.Option(
        ".mas", help="Workspace .mas directory (product-policy.yaml, evidence/)"
    ),
):
    """Deterministic claim-ledger lint (§20.53.3) — the outer loop's ears_lint.

    Exit 0 clean, 1 findings (JSONL on stdout), 2 malformed input."""
    import json

    from ai_venture_studio.product import lint_ledger, load_ledger, load_product_policy

    try:
        doc = load_ledger(ledger)
        policy = load_product_policy(mas_dir)
    except Exception as exc:  # malformed input is exit 2, not a stack trace
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    findings = lint_ledger(doc, kind, policy=policy)
    for finding in findings:
        print(json.dumps(finding.model_dump()))
    if findings:
        raise typer.Exit(code=1)


def _run_stage(spec, user_input: str, workspace: str, provider: str) -> None:
    """Run one product stage, persist the report, exit per outcome."""
    import yaml as _yaml
    from pathlib import Path as _Path

    from ai_venture_studio.product.stage_engine import run_product_stage

    try:
        report = run_product_stage(spec, user_input, workspace, provider=provider)
    except ValueError as exc:  # writer exhausted its revisions on the contract
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    report_path = _Path(workspace) / ".mas" / "product" / f"{spec.name}-report.yaml"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _yaml.safe_dump(report.model_dump(), sort_keys=False, allow_unicode=True)
    )
    console.print(f"[bold]{spec.name}[/bold]: {report.status} "
                  f"({len(report.voter_findings)} verified finding(s), "
                  f"{report.revisions} revision(s))")
    console.print(report.leader_summary)
    for path in report.artifacts:
        console.print(f"  wrote {path}")
    if report.status != "ok":
        for finding in report.det_findings:
            console.print(f"  [red]{finding.get('rule')}[/red]: {finding.get('message')}")
        for key, value in report.gate.items():
            if key != "passed":
                console.print(f"  gate.{key}: {value}")
        raise typer.Exit(code=1)


@app.command("opportunity")
def opportunity_cmd(
    signals: str = typer.Argument(..., help="YAML list of raw signals "
                                            "({id, source_id, text, locator})"),
    workspace: str = typer.Option(".", help="Product workspace"),
    provider: str = typer.Option("anthropic", help="LLM provider (mock for offline)"),
):
    """P0 Opportunity Sensing (§20.54): cluster real signals, draft >=3
    candidates, det-tools + five voters + verify + leader, Gate PL0."""
    import yaml as _yaml
    from pathlib import Path as _Path

    from ai_venture_studio.product import RawSignal, cluster_signals
    from ai_venture_studio.product.sources import SignalSourceError, load_signal_sources
    from ai_venture_studio.product.stages import opportunity_spec

    try:
        raw = _yaml.safe_load(_Path(signals).read_text()) or []
        parsed = [RawSignal(**s) for s in raw]
        declared = {s.id for s in load_signal_sources(_Path(workspace) / ".mas")}
        undeclared = sorted({s.source_id for s in parsed} - declared)
        if undeclared:
            raise SignalSourceError(
                f"signal sources {undeclared} not declared in "
                ".mas/signal-sources.yaml — no standing, no source (§20.54.2)"
            )
    except (SignalSourceError, ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    clusters = cluster_signals(parsed)
    # The writer must cite the signals' own locators verbatim — so the
    # signals travel with their locators, not just cluster membership
    # (found by the first real-provider smoke: a locator the prompt never
    # contained cannot be cited, and the standing check rightly blocked).
    user_input = _yaml.safe_dump(
        {"clusters": [c.model_dump() for c in clusters],
         "signals": [s.model_dump() for s in parsed]},
        sort_keys=False, allow_unicode=True,
    )
    _run_stage(opportunity_spec(workspace), user_input, workspace, provider)


@app.command("cost")
def cost_cmd(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    month: str = typer.Option(None, help="YYYY-MM (default: this month)"),
):
    """What this workspace has spent — a statement, never a verdict.

    Prices come from `.mas/cost-model.yaml` — never constants in code, because
    prices rot. A call whose model has no price is counted as UNPRICED and
    named, never as zero: a total that hides unpriced calls understates.
    Budget enforcement belongs to the provider account that does the billing
    (your key, your subscription — ADR-032); nothing here refuses work.
    """
    from ai_venture_studio.observability import load_cost_model
    from ai_venture_studio.spend import (
        current_month,
        month_report,
        priced,
        read_entries,
    )

    root = Path(repo_dir).resolve()
    window = month or current_month()
    entries = read_entries(root, month=window)
    records = priced(entries, load_cost_model(root / ".mas"))
    report = month_report(root, month=window)

    console.print(f"month {window}: {len(entries)} call(s)")
    by_model: dict[str, tuple[int, int, float, int]] = {}
    # strict: `spend.priced` returns one record per entry. A truncation
    # here would silently drop spend from a cost report.
    for entry, record in zip(entries, records, strict=True):
        calls, tokens, usd, unpriced = by_model.get(entry.model, (0, 0, 0.0, 0))
        by_model[entry.model] = (
            calls + 1,
            tokens + entry.input_tokens + entry.output_tokens,
            usd + (record.cost_usd or 0.0),
            unpriced + (1 if record.cost_usd is None else 0),
        )
    if by_model:
        table = Table("model", "calls", "tokens", "usd", "unpriced")
        for model_name, (calls, tokens, usd, unpriced) in sorted(by_model.items()):
            table.add_row(
                model_name, str(calls), f"{tokens:,}",
                f"{usd:.2f}+" if unpriced else f"{usd:.2f}",
                str(unpriced),
            )
        console.print(table)

    prefix = "≥" if report.is_floor else ""
    console.print(f"spend this month: {prefix}${report.spent_usd:.2f}")
    if report.note:
        console.print(f"[yellow]{report.note}[/yellow]")
    console.print(
        "[dim]billed to your own key or subscription — set spending limits "
        "at your provider, which is where the billing actually happens[/dim]"
    )


@app.command("prices")
def prices_cmd(
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    import_: bool = typer.Option(
        False, "--import", help="Write these prices into .mas/cost-model.yaml",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite",
        help="Replace prices already in the workspace (default: yours win)",
    ),
):
    """Published list prices, so `avs cost` can answer in dollars.

    With no prices configured every call is UNPRICED and the month's total
    is a floor — honest, but "how much will a typical month cost me?" wants
    a dollar figure. This ships the table with a source URL and a date per
    provider — list prices, not necessarily yours, and never invented.

    Where a price is a range the higher number is used, so the estimate is
    a ceiling rather than a guess. Estimation only: nothing here gates work
    (ADR-032 — billing and budget limits live at your provider).
    """
    from ai_venture_studio.prices import (
        import_into_workspace,
        load_reference_prices,
        unpriced_models,
    )

    reference = load_reference_prices()
    console.print(
        f"reference prices retrieved {reference.retrieved_at} "
        f"({reference.age_days()} days ago)"
    )
    if reference.stale():
        console.print(
            f"[yellow]stale: older than {reference.stale_after_days} days — "
            "prices rot; re-check the source pages before trusting a total "
            "built on these[/yellow]"
        )
    table = Table("model", "provider", "input $/Mtok", "output $/Mtok", "note")
    for entry in reference.entries:
        table.add_row(
            entry.model, entry.provider, f"{entry.input:g}", f"{entry.output:g}",
            (entry.note[:60] + "…") if len(entry.note) > 60 else entry.note,
        )
    console.print(table)
    for provider, url in sorted(reference.sources.items()):
        console.print(f"[dim]{provider}: {url}[/dim]")

    if not import_:
        console.print(
            "\nNothing written. `avs prices --import` writes these into "
            ".mas/cost-model.yaml, where you own and can correct them — "
            "`avs cost` and the build report then answer in dollars."
        )
        return

    result = import_into_workspace(repo_dir, overwrite=overwrite, reference=reference)
    console.print(f"\nwrote {result.path}")
    if result.models_written:
        console.print(f"  priced: {', '.join(result.models_written)}")
    if result.models_kept:
        console.print(
            f"  [dim]kept your existing price for: "
            f"{', '.join(result.models_kept)} (--overwrite to replace)[/dim]"
        )
    missing = unpriced_models(repo_dir)
    if missing:
        console.print(
            f"  [yellow]still unpriced, called by this workspace: "
            f"{', '.join(missing)} — the month's total stays a floor until "
            f"you add them[/yellow]"
        )


@app.command("mvp")
def mvp_cmd(
    slice_file: str = typer.Argument(
        ..., help="YAML describing the slice (see `avs mvp --help` for the shape)"
    ),
):
    """Check whether a first slice is minimum AND viable.

    The question this answers is not "is it small" — the tier and the budget
    already bound that — but "does this slice, on its own, tell us whether the
    thing is worth building". When the slice contains an AI-shaped capability
    it also checks the five things an AI MVP may not skip: a named simpler
    alternative, a declared cost of being wrong, a wrong-answer fallback, an
    eval set written first, and a quality metric paired to every volume metric.

    The file shape:

    \b
    hypothesis: founders cannot tell whether a long build is progressing
    increments:
      - a per-task progress panel on the building page
    success_signal: 3 of 3 reporters confirm it resolves the uncertainty
    not_now: [dark mode, shareable links]
    cheapest_test: show the 3 reporters a clickable mockup
    ai_feature:            # optional; auto-detected from your words too
      capability: summarize the ticket into one line
      why_not_deterministic: a keyword rule missed 40% of the tickets we tried
      cost_of_being_wrong: recoverable
      fallback_behavior: below confidence, show the raw ticket and say why
      autonomy_rung: suggest
      eval_cases: 25
      volume_metric: tickets summarized
      quality_metric: edit rate on the summary
    """
    import yaml as _yaml

    from ai_venture_studio.product.mvp import (
        AIFeature,
        MVPSlice,
        detect_ai_feature,
        gate_mvp_entry,
    )

    try:
        raw = _yaml.safe_load(Path(slice_file).read_text(encoding="utf-8")) or {}
        slice_ = MVPSlice(**{k: v for k, v in raw.items() if k != "ai_feature"})
        feature = AIFeature(**raw["ai_feature"]) if raw.get("ai_feature") else None
    except (OSError, _yaml.YAMLError, ValueError, TypeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    # Detected from the founder's own words, so an AI slice cannot skip the AI
    # contract just by omitting the section.
    trigger = detect_ai_feature(
        slice_.hypothesis, " ".join(slice_.increments), slice_.success_signal
    )
    if trigger and feature is None:
        console.print(
            f"[yellow]this slice reads as AI-shaped ({trigger!r}) but declares "
            "no ai_feature section — an AI slice carries five more "
            "obligations[/yellow]"
        )
        feature = AIFeature(capability=trigger)

    result = gate_mvp_entry(slice_, feature)
    for finding in result["findings"]:
        console.print(f"[red]✗ {finding['rule']}[/red]: {finding['message']}")
    if result["passed"]:
        console.print("[green]✓ minimum and viable[/green] — this slice can "
                      "answer its own question")
        return
    console.print(
        f"\n{len(result['findings'])} finding(s). An MVP that cannot be read is "
        "a demo with a deadline."
    )
    raise typer.Exit(code=1)


@app.command("map")
def map_cmd(
    repo_dir: str = typer.Argument(".", help="Repository to read"),
    write_deps: bool = typer.Option(
        False, "--write-deps",
        help="Also write .mas/deps.yaml from the imports that actually exist, "
             "as the brownfield baseline the arch check compares against",
    ),
):
    """Read an existing codebase and write .mas/codebase-map.yaml.

    Languages, entry points, modules with their sizes, the import edges that
    actually exist, the HTTP surface, and where the tests live — derived from
    the code, no LLM and no network. This is what the planner and the
    feature flow read so they stop guessing from filenames.
    """
    from ai_venture_studio.upstream.comprehend import (
        comprehend_repo,
        derive_deps,
        render_summary,
        write_map,
    )

    root = Path(repo_dir).resolve()
    result = comprehend_repo(root)
    path = write_map(result, root)
    console.print(render_summary(result))
    console.print(f"\nmap written to {path}")
    if write_deps:
        import yaml as _yaml

        deps_path = root / ".mas" / "deps.yaml"
        if deps_path.exists():
            console.print(
                f"[yellow]{deps_path} already exists — not overwriting a graph "
                "you may have tightened by hand[/yellow]"
            )
        else:
            deps_path.write_text(
                _yaml.safe_dump(derive_deps(result), sort_keys=False,
                                allow_unicode=True),
                encoding="utf-8",
            )
            console.print(
                f"deps baseline written to {deps_path} — this is what the code "
                "does today, not a design; tighten it as you learn the shape"
            )
    if not result.total_files:
        raise typer.Exit(code=3)  # nothing readable is a finding, not a pass


@app.command("probe")
def probe_cmd(
    url: str = typer.Argument(..., help="https URL of one public page"),
    repo_dir: str = typer.Option(".", help="Workspace directory"),
    method: str = typer.Option("competitor_probe", help="Evidence method tag"),
    out: str = typer.Option(
        "", help="Append the evidence entry to this YAML file "
                 "(default: print it for you to paste into --evidence)"
    ),
):
    """Fetch one public page and record it as a probe (quarantined).

    The bytes are snapshotted to .mas/evidence/ and you get a hash + locator
    back — never the page content, which is what keeps a hostile page from
    reaching a privileged session. Only locators that a source declared in
    .mas/signal-sources.yaml already has standing for can be probed, and the
    fetch is yours: no agent can call this.
    """
    from pathlib import Path as _Path

    import yaml

    from ai_venture_studio.product.market import ProbeFetchError, fetch_probe
    from ai_venture_studio.product.sources import (
        SignalSourceError,
        load_signal_sources,
    )

    mas_dir = _Path(repo_dir).resolve() / ".mas"
    try:
        sources = load_signal_sources(mas_dir)
        entry, findings = fetch_probe(
            url, sources=sources, mas_dir=mas_dir, method=method
        )
    except (ProbeFetchError, SignalSourceError, ValueError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    for finding in findings:
        console.print(f"[yellow]{finding.rule}: {finding.message}[/yellow]")
    payload = {"evidence": [entry]}
    if out:
        path = _Path(out)
        existing = yaml.safe_load(path.read_text()) if path.exists() else None
        merged = (existing or {}).get("evidence", []) + [entry]
        path.write_text(
            yaml.safe_dump({"evidence": merged}, sort_keys=False,
                           allow_unicode=True),
            encoding="utf-8",
        )
        console.print(f"appended to {path} ({len(merged)} probe(s))")
    else:
        console.print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    if findings:
        # Contaminated evidence is usable only with the flag attached, so the
        # exit code says "read this before citing it" rather than "failed".
        raise typer.Exit(code=3)


@app.command("market")
def market_cmd(
    candidate: str = typer.Argument(..., help="Candidate id or statement from "
                                              "product/opportunities.md"),
    workspace: str = typer.Option(".", help="Product workspace"),
    provider: str = typer.Option("anthropic"),
    disconfirmation_answered: bool = typer.Option(
        False, help="Set once every Disconfirmation finding has an evidence answer"),
    regulatory_triaged: bool = typer.Option(
        False, help="Set once regulatory findings are triaged"),
    evidence: str = typer.Option(
        None, help="YAML of recorded evidence the writer may cite: probe "
                   "entries from record_probe (with artifact hashes) and "
                   "owned crm://analytics figures (§20.55.3)"),
):
    """P1 Market & Viability (§20.55): bottom-up sizing, probe-derived
    facts, six voters incl. Disconfirmation, deterministic Gate PL1 entry.
    The decision itself is `market-approve`."""
    from pathlib import Path as _Path

    from ai_venture_studio.product.stages import market_spec

    context = ""
    opportunities = _Path(workspace) / "product" / "opportunities.md"
    if opportunities.exists():
        context = f"\n\n<opportunities>\n{opportunities.read_text()}\n</opportunities>"
    if evidence:
        context += (
            "\n\n<recorded_evidence>\n"
            + _Path(evidence).read_text()
            + "\n</recorded_evidence>\n"
            "Cite ONLY the recorded evidence above (verbatim locators and "
            "artifact hashes) plus clearly-labeled model_inference within the "
            "ratio ceiling. Never invent a locator."
        )
    _run_stage(
        market_spec(workspace,
                    disconfirmation_answered=disconfirmation_answered,
                    regulatory_triaged=regulatory_triaged),
        f"<candidate>\n{candidate}\n</candidate>{context}",
        workspace, provider,
    )


@app.command("market-approve")
def market_approve_cmd(
    outcome: str = typer.Option(..., help="pursue | test_first | park | reject"),
    decider: str = typer.Option(..., help="The named human deciding"),
    scope_tier: str = typer.Option("", help="Required for pursue"),
    named_test: str = typer.Option("", help="Required for test_first"),
    park_reason: str = typer.Option("", help="Required for park"),
    workspace: str = typer.Option("."),
):
    """Record the human Gate PL1 decision (§20.55.5). forbidden_autonomous:
    this gate, always."""
    import yaml as _yaml
    from pathlib import Path as _Path

    from ai_venture_studio.product import GatePL1Decision

    try:
        decision = GatePL1Decision(
            outcome=outcome, decider=decider, scope_tier=scope_tier,
            named_test=named_test, park_reason=park_reason,
        )
        decision.validate_completeness()
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    path = _Path(workspace) / ".mas" / "product" / "gate-pl1.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_yaml.safe_dump(decision.model_dump(), sort_keys=False))
    console.print(f"[green]Gate PL1 recorded[/green]: {outcome} by {decider}")


@app.command("prd")
def prd_cmd(
    workspace: str = typer.Option(".", help="Product workspace"),
    provider: str = typer.Option("anthropic"),
    metrics_dir: str = typer.Option(None, help="Metric vocabulary directory "
                                               "(default: <workspace>/metrics)"),
):
    """P2 Product Definition (§20.56): the PRD with outcomes, non-goals,
    kill criteria; prd_lint deterministic; five voters. Approval + handoff
    is `prd-approve`."""
    from pathlib import Path as _Path

    import yaml as _yaml

    from ai_venture_studio.product.stages import prd_spec

    # Gate PL1 `test_first` means "do not build yet, run this test" — the
    # canon calls it the most common correct outcome. It was a recorded string
    # with no downstream wiring: a founder could record test_first and walk
    # straight into a PRD, a handoff, and a build with nothing objecting.
    gate_pl1 = _Path(workspace) / ".mas" / "product" / "gate-pl1.yaml"
    if gate_pl1.exists():
        try:
            decision = _yaml.safe_load(gate_pl1.read_text(encoding="utf-8")) or {}
        except _yaml.YAMLError:
            decision = {}
        if str(decision.get("outcome", "")) == "test_first":
            named = str(decision.get("named_test", "")).strip() or "(unnamed)"
            console.print(
                f"[yellow]Gate PL1 recorded `test_first`, not `pursue`.[/yellow]\n"
                f"The named test was: [b]{named}[/b]\n"
                "Run that test first. If it already moved you, re-record the "
                "gate with `avs market-approve --outcome pursue "
                "--scope-tier thin --decider <you>`; a decision the evidence "
                "changed is a new decision, not an edited one."
            )
            raise typer.Exit(code=3)

    parts = []
    for rel in ("market/market.md", "product/opportunities.md"):
        path = _Path(workspace) / rel
        if path.exists():
            parts.append(f"<{path.stem}>\n{path.read_text()}\n</{path.stem}>")
    ledger = _Path(workspace) / "claims" / "market.claim.yaml"
    if ledger.exists():
        parts.append(f"<market_claims>\n{ledger.read_text()}\n</market_claims>")
    _run_stage(
        prd_spec(workspace, metrics_dir=metrics_dir),
        "\n\n".join(parts) or "No upstream artifacts found — state that and stop.",
        workspace, provider,
    )


@app.command("prd-approve")
def prd_approve_cmd(
    decider: str = typer.Option(..., help="The named human at Gate PL2"),
    workspace: str = typer.Option("."),
):
    """Record Gate PL2 (§20.56.4) and emit the machine-checked
    p2_to_stage1.yaml handoff. Cheap on purpose — the expensive judgment
    was Gate PL1's."""
    import yaml as _yaml
    from pathlib import Path as _Path

    from ai_venture_studio.product import (
        PRD,
        GatePL2Decision,
        emit_handoff,
        validate_handoff_at_dor,
        write_handoff,
    )

    root = _Path(workspace)
    try:
        prd_raw = _yaml.safe_load((root / "product" / "prd.yaml").read_text())
        prd = PRD(**prd_raw["prd"])
        prose = (root / "product" / "prd.md").read_text()
        decision = GatePL2Decision(
            acknowledged_kill_criteria=True, scope_tier=prd.scope_tier,
            decider=decider,
        )
    except (OSError, KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    handoff = emit_handoff(
        prd, prose, claim_ledger_ref="claims/prd.claim.yaml",
        outcomes_ref="product/outcomes.yaml",
    )
    path = write_handoff(handoff, root / "handoff" / "p2_to_stage1.yaml")
    validate_handoff_at_dor(path, prd_document_text=prose)  # prove it lands
    gate = root / ".mas" / "product" / "gate-pl2.yaml"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(_yaml.safe_dump(decision.model_dump(), sort_keys=False))
    # The scope tier decided here has to reach the planner, or it is a field
    # two artifacts validate and nothing acts on: `handoff.py` carries it,
    # `plan.py` reads it from project.yaml, and until now the only writer of
    # project.yaml was `init`. So a `thin` decided at PL1/PL2 had no effect on
    # any plan (this repo's own launch/gate-pl2.yaml says thin).
    project_path = root / ".mas" / "project.yaml"
    if project_path.exists():
        project = _yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        if project.get("scope_tier") != prd.scope_tier:
            project["scope_tier"] = prd.scope_tier
            project_path.write_text(
                _yaml.safe_dump(project, sort_keys=False), encoding="utf-8"
            )
            console.print(
                f"scope tier [b]{prd.scope_tier}[/b] carried into the build "
                "(the planner reads it at Gate U2)"
            )
    console.print(f"[green]Gate PL2 recorded[/green]; handoff at {path} "
                  "(validated at Discovery's DoR)")


@app.command("evidence")
def evidence_cmd(
    events: str = typer.Argument(..., help="YAML list of analytics events"),
    metric: str = typer.Option("activation_rate", help="Vocabulary metric to read"),
    cohort_field: str = typer.Option("signup_week"),
    cohort_start: str = typer.Option(..., help="ISO date the cohort window opened"),
    workspace: str = typer.Option("."),
    provider: str = typer.Option("anthropic"),
    metrics_dir: str = typer.Option(None, help="Default: <workspace>/metrics"),
):
    """P4 Product Evidence (§22.62): deterministic cohort reading through
    the privacy boundary FIRST, then the writer narrates and assigns
    verdicts against pre-stated falsifiers; Gate PL4."""
    import datetime as _dt
    import yaml as _yaml
    from pathlib import Path as _Path

    from ai_venture_studio.evidence import AnalyticsStore, cohort_calc, load_metric_vocabulary
    from ai_venture_studio.product import PRD
    from ai_venture_studio.product.stages import evidence_spec

    root = _Path(workspace)
    try:
        rows = _yaml.safe_load(_Path(events).read_text()) or []
        vocabulary = load_metric_vocabulary(metrics_dir or root / "metrics")
        definition = vocabulary[metric]
        prd = PRD(**_yaml.safe_load((root / "product" / "prd.yaml").read_text())["prd"])
        readings_list = cohort_calc(
            AnalyticsStore(rows), definition,
            cohort_field=cohort_field,
            cohort_start=_dt.date.fromisoformat(cohort_start),
            today=_dt.date.today(),
        )
    except (OSError, KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    reading = readings_list[0] if readings_list else None
    readings = {
        o.id: reading for o in prd.outcomes if o.metric == metric and reading
    }
    user_input = _yaml.safe_dump(
        {"outcomes": [o.model_dump() for o in prd.outcomes],
         "hypotheses": [h.model_dump() for h in prd.demand_hypotheses],
         "cohort_readings": {k: v.model_dump() for k, v in readings.items()}},
        sort_keys=False, allow_unicode=True,
    )
    _run_stage(
        evidence_spec(workspace, prd_outcome_ids=[o.id for o in prd.outcomes],
                      readings=readings),
        user_input, workspace, provider,
    )


@app.command("sweep")
def sweep_cmd(
    workspace: str = typer.Option(".", help="Workspace directory"),
    today: str = typer.Option(None, help="ISO date override (default: today)"),
):
    """The Sweep role (doc 29): harvest the maintenance queues the ledgers
    already keep, patch only what the rung + allowlist + contract permit,
    report the rest. SW0 (default) is report-only; a clean pass is
    recorded, never silent."""
    import datetime as _dt
    from pathlib import Path as _Path

    from ai_venture_studio.lanes.delivery import flag_lint
    from ai_venture_studio.sweep import (
        SweepConfigError,
        harvest_queues,
        load_sweep_config,
        run_sweep_pass,
    )

    root = _Path(workspace)
    day = _dt.date.fromisoformat(today) if today else _dt.date.today()
    try:
        config = load_sweep_config(root / ".mas")
    except SweepConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    flags_file = root / ".mas" / "flags.yaml"
    flag_issues = flag_lint(flags_file.read_text(), {}, today=day) if flags_file.exists() else []
    contributing = root / "CONTRIBUTING.md"
    chores = harvest_queues(
        root, today=day, flag_issues=flag_issues,
        contributing_text=contributing.read_text() if contributing.exists() else "",
    )
    digest = run_sweep_pass(root, chores, config=config, at=day.isoformat())
    console.print(f"[bold]sweep[/bold] {digest.rung}: {digest.items_inspected} "
                  f"inspected · {len(digest.actionable)} actionable · "
                  f"{len(digest.reported)} reported · "
                  f"action rate {digest.action_rate:.0%}")
    if digest.clean_pass:
        console.print(f"[green]clean pass[/green] recorded "
                      f"({digest.snapshot_hash[:18]}…)")
    for chore in digest.chores[:10]:
        marker = "→ PATCH" if chore in digest.actionable else "  report"
        console.print(f"  {marker}  [{chore.queue}] {chore.item}: {chore.detail[:70]}")


@app.command("telemetry")
def telemetry_cmd(
    action: str = typer.Argument(..., help="on | off | show"),
    workspace: str = typer.Option(".", help="Workspace directory"),
):
    """Opt-in usage telemetry (ADR-U28): default off, aggregate-only,
    schema-pinned. `show` prints the exact payload — never FDR content,
    code, prompts, outputs, repo names, or claim text. No endpoint is
    configured in this version: nothing is ever sent."""
    from ai_venture_studio.usage_telemetry import (
        render_payload,
        set_telemetry,
        telemetry_enabled,
    )

    if action == "show":
        print(render_payload(workspace))
        state = "on" if telemetry_enabled(workspace) else "off (default)"
        console.print(f"[dim]telemetry is {state}; no endpoint is configured — "
                      "nothing is sent either way[/dim]")
    elif action in ("on", "off"):
        set_telemetry(workspace, action == "on")
        console.print(f"telemetry {action} — inspect the exact payload any time "
                      "with `avs telemetry show`")
    else:
        console.print("[red]action must be on | off | show[/red]")
        raise typer.Exit(code=2)


@app.command("voter-gate")
def voter_gate_cmd(
    stage: str = typer.Argument(..., help="opportunity | market | prd | evidence | prioritization"),
    voter: str = typer.Option(None, help="One voter; default: every voter in the stage"),
    workspace: str = typer.Option("."),
    provider: str = typer.Option("anthropic", help="A REAL provider — judging an "
                                                   "LLM voter takes an LLM"),
):
    """Run the voter fixture gate (§11.19): 8 fixtures, >=87.5% to register.
    Results land in .mas/voter-registry.yaml; a failed voter stops voting."""
    from pathlib import Path as _Path

    from ai_venture_studio.product.stage_engine import load_voter_charters
    from ai_venture_studio.product.voter_gate import (
        VoterFixtureError,
        family_roots,
        record_gate_run,
        run_voter_gate,
    )

    skills_root, fixtures_root = family_roots(stage)
    charters = [
        (name, system)
        for name, system in load_voter_charters(stage, skills_root)
        if voter is None or name == voter
    ]
    if not charters:
        console.print(f"[red]no charter named {voter!r} in stage {stage!r}[/red]")
        raise typer.Exit(code=2)
    failed_any = False
    for name, system in charters:
        try:
            run = run_voter_gate(stage, name, system, provider=provider,
                                 fixtures_root=fixtures_root)
        except VoterFixtureError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        record_gate_run(_Path(workspace) / ".mas", run)
        color = "green" if run.status == "registered" else "red"
        console.print(f"[{color}]{stage}/{name}: {run.status}[/{color}] "
                      f"({run.passed}/{run.total})")
        for result in run.results:
            if not result.passed:
                console.print(f"    [red]{result.label}[/red]: {result.detail}")
        failed_any = failed_any or run.status != "registered"
    if failed_any:
        raise typer.Exit(code=1)


@app.command("automerge")
def automerge_cmd(
    review_id: str = typer.Argument(..., help="A finished review's id"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
    method: str = typer.Option("squash", help="squash | merge | rebase"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Evaluate, never act"),
):
    """Merge a reviewed PR — only if an armed .mas/automerge-policy.yaml says
    every condition holds (ADR-031). Disarmed by default; refusals are
    logged with their reasons."""
    from pathlib import Path as _Path

    import yaml as _yaml

    from ai_venture_studio import automation, forge

    finals = sorted(
        (_Path(repo_dir) / ".mas" / "reviews" / review_id).glob("[0-9]*-final.yaml")
    )
    if not finals:
        console.print(f"[red]no finished review {review_id!r}[/red]")
        raise typer.Exit(code=2)
    final = _yaml.safe_load(finals[-1].read_text(encoding="utf-8")) or {}
    target = str(final.get("target", ""))
    verdict = str(final.get("verdict", ""))
    test_report = final.get("test_report") or {}
    branch = forge.head_branch(target) or ""

    try:
        decision = automation.evaluate_merge(
            repo_dir,
            verdict=verdict,
            branch=branch,
            changed_files=list((final.get("diff") or {}).get("changed_files") or []),
            test_gate_status=test_report.get("status"),
            escalated=bool((final.get("hitl") or {}).get("issue_url")),
        )
    except automation.PolicyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if not decision.allowed:
        automation.record(repo_dir, decision, detail=f"review {review_id}")
        console.print(f"[yellow]merge refused[/yellow] for review {review_id}:")
        for reason in decision.reasons:
            console.print(f"  · {reason}")
        raise typer.Exit(code=1)
    if dry_run:
        console.print(f"[green]merge would proceed[/green] ({target}, {method})")
        return
    ok, output = forge.merge(target, method=method)
    automation.record(
        repo_dir, decision,
        detail=f"review {review_id}: {'merged' if ok else 'merge failed'} {output[:200]}",
    )
    if not ok:
        console.print(f"[red]merge failed: {output[:200]}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]merged[/green] {target}")


@app.command("deploy-execute")
def deploy_execute_cmd(
    deploy_id: str = typer.Argument(..., help="A finished deploy review's id"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Evaluate, never act"),
):
    """Run the deploy command a human wrote in .mas/deploy-exec-policy.yaml,
    only on a PROMOTE with every condition met (ADR-031). The system never
    composes the command."""
    import subprocess as _sp
    from pathlib import Path as _Path

    import yaml as _yaml

    from ai_venture_studio import automation

    finals = sorted(
        (_Path(repo_dir) / ".mas" / "deploy-reviews" / deploy_id).glob("[0-9]*-final.yaml")
    )
    if not finals:
        console.print(f"[red]no finished deploy review {deploy_id!r}[/red]")
        raise typer.Exit(code=2)
    final = _yaml.safe_load(finals[-1].read_text(encoding="utf-8")) or {}

    try:
        policy = automation.load_policy(repo_dir, automation.DEPLOY_EXEC_POLICY)
        decision = automation.evaluate_deploy(
            repo_dir,
            verdict=str(final.get("verdict", "")),
            branch=str(final.get("branch", "")),  # never defaulted (ADR-031)
            changed_files=list(final.get("deploy_files") or []),
        )
    except automation.PolicyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if not decision.allowed:
        automation.record(repo_dir, decision, detail=f"deploy-review {deploy_id}")
        console.print(f"[yellow]deploy not executed[/yellow] for {deploy_id}:")
        for reason in decision.reasons:
            console.print(f"  · {reason}")
        raise typer.Exit(code=1)
    console.print(f"[dim]$ {' '.join(policy.command)}[/dim]")
    if dry_run:
        console.print("[green]deploy would proceed[/green]")
        return
    proc = _sp.run(  # noqa: S603 — argv comes from the human-written policy
        policy.command, cwd=repo_dir, capture_output=True, text=True, timeout=1800
    )
    automation.record(
        repo_dir, decision,
        detail=f"deploy-review {deploy_id}: exit {proc.returncode} "
               f"{(proc.stdout or proc.stderr)[-200:]}",
    )
    console.print((proc.stdout or proc.stderr)[-2000:])
    if proc.returncode != 0:
        console.print(f"[red]deploy command exited {proc.returncode}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]deploy command completed[/green]")


@app.command("tenant")
def tenant_cmd(
    action: str = typer.Argument(..., help="list | add"),
    tenant_id: str = typer.Argument(None, help="Tenant id (add)"),
    workspace: str = typer.Option(None, help="That tenant's workspace root (add)"),
    webhook_secret_ref: str = typer.Option(
        "", help="secret://ENV_NAME holding this tenant's GitHub webhook secret"
    ),
    repo_dir: str = typer.Option(".", help="Where .mas/tenants.yaml lives"),
):
    """Multi-tenant server registry (ADR-030). A tenant is a token and a
    workspace; workspaces must be disjoint and tokens are stored hashed."""
    from ai_venture_studio.tenants import TenantError, add_tenant, load_tenants

    try:
        tenants = load_tenants(repo_dir)
    except TenantError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    if action == "list":
        if not tenants:
            console.print("single-tenant mode (no .mas/tenants.yaml)")
            return
        table = Table(show_lines=False)
        for col in ("id", "workspace", "enabled", "webhook secret"):
            table.add_column(col)
        for tenant in tenants:
            table.add_row(
                tenant.id, tenant.workspace,
                "yes" if tenant.enabled else "no",
                tenant.webhook_secret_ref or "[dim](none)[/dim]",
            )
        console.print(table)
        return

    if action != "add":
        console.print("[red]action must be list | add[/red]")
        raise typer.Exit(code=2)
    if not tenant_id or not workspace:
        console.print("[red]add needs a tenant id and --workspace[/red]")
        raise typer.Exit(code=2)
    try:
        tenant, token = add_tenant(
            repo_dir, tenant_id, workspace, webhook_secret_ref=webhook_secret_ref
        )
    except TenantError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]added tenant {tenant.id}[/green] → {tenant.workspace}")
    console.print(f"\n  token: [bold]{token}[/bold]\n")
    console.print("[yellow]This token is shown once and stored only as a hash — "
                  "save it now.[/yellow]")


@app.command("botfleet")
def botfleet_cmd(
    command: str = typer.Argument(..., help="The bot session command "
                                           "(emits the session protocol on stdout)"),
    repo_dir: str = typer.Option(".", help="Where to run sessions"),
    sessions: int = typer.Option(8, help="How many parallel sessions"),
    seed: int = typer.Option(1, help="Base seed; each session gets seed+i"),
    bounds: float = typer.Option(None, help="Play-area bound for out-of-bounds "
                                           "detection (±bounds per axis)"),
    net_profile: str = typer.Option("", help="Comma-separated netem profiles "
                                             "(wifi_poor,mobile_4g,intercontinental)"),
    workers: int = typer.Option(4, help="Concurrency"),
):
    """Bot playtests (doc 17 §45.2): run N sessions, triage crashes,
    softlocks, unreachable states and out-of-bounds positions, dedupe by
    signature, and print a reproduction command per finding.

    Finds crashes and stuck states — never whether the game is fun. That is
    the human playtest gate, which no bot replaces (§45.1)."""
    import shlex

    from ai_venture_studio.lanes.botfleet import run_fleet

    profiles = tuple(p.strip() for p in net_profile.split(",") if p.strip()) or None
    report = run_fleet(
        shlex.split(command), cwd=repo_dir, sessions=sessions, base_seed=seed,
        net_profiles=profiles, bounds=bounds, workers=workers,
    )
    color = {"ok": "green", "findings": "yellow", "skipped": "yellow"}.get(
        report.status, "red"
    )
    console.print(f"\n[bold {color}]{report.status}[/bold {color}] — {report.detail}")
    for finding in report.findings:
        console.print(f"\n  [yellow]{finding['kind']}[/yellow] "
                      f"({finding['sessions']} session(s)"
                      + (f", profiles: {', '.join(finding['net_profiles'])}"
                         if finding["net_profiles"] else "")
                      + f")\n    {finding['detail']}")
        console.print(f"    [dim]reproduce: {finding['reproduce']}[/dim]")
    if report.status == "findings":
        raise typer.Exit(code=1)
    if report.status == "error":
        raise typer.Exit(code=2)


@app.command("bench-criterion")
def bench_criterion_cmd(
    repo_dir: str = typer.Option(".", help="Repository holding benchmarks/results/"),
):
    """The capability kill criterion (PRD O-L2): has product-bench fallen
    below its floors for two consecutive runs? States, never decides — a
    fired criterion needs a recorded human decision at Gate PL5."""
    from ai_venture_studio.bench_criterion import evaluate

    state = evaluate(repo_dir)
    for run in state.runs_considered:
        console.print(f"  [dim]{run.summary()}[/dim]")
    # Named, not silently absent: a file the reader can see in the directory
    # and cannot find in the ledger is a reason to distrust the ledger, and
    # the reason it is out (an abort, therefore resumable) is the reason
    # someone might want to go finish it.
    for skipped in state.aborted_skipped:
        console.print(
            f"  [dim]{Path(skipped).name}: aborted attempt, not a run "
            f"of the series — resume it with `avs product-bench --resume`[/dim]"
        )
    # Same rule, different reason, and the difference is what the reader needs:
    # an abort is worth going back to finish, a simulated run is not. Nothing
    # here is resumable into a real reading (ADR-056).
    for skipped in state.simulated_skipped:
        console.print(
            f"  [dim]{Path(skipped).name}: measured against a simulated "
            f"provider, not a capability reading — it exercised the harness, "
            f"not the system[/dim]"
        )
    # The third reason, and the only one of the three that names a next step
    # the reader would actually take: the slice's cases are banked, so the run
    # that closes the suite is cheaper than this file makes it look.
    for skipped in state.truncated_skipped:
        console.print(
            f"  [dim]{Path(skipped).name}: stopped short of the suite by "
            f"--limit, not a reading of it — close it with `avs product-bench "
            f"--resume` and the cases it did measure are not re-paid[/dim]"
        )
    color = "red" if state.fires else "green"
    console.print(f"[{color}]{state.detail}[/{color}]")
    if state.fires:
        console.print(
            "\n[bold]Gate PL5 requires YOUR recorded decision[/bold] — kill, "
            "pivot, or continue — in launch/gate-pl5-evaluation.yaml. Nothing "
            "here decides it."
        )
        raise typer.Exit(code=3)


@app.command("loop")
def loop_cmd(
    root: str = typer.Option("launch", help="Cycle artifact directory "
                                           "(launch/ for this repo's own loop)"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable state"),
):
    """Where the live product loop stands, and what closes the v3.0.0 design
    gate. Reads the artifacts the stages already wrote; states, never
    decides — the gate needs a recorded human kill-or-pivot at PL5."""
    import json as _json

    from ai_venture_studio.product.cycle import read_cycle

    try:
        state = read_cycle(root)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    if json_out:
        console.print_json(_json.dumps(state.model_dump(mode="json")))
        raise typer.Exit(code=0)

    console.print(f"\n[bold]cycle[/bold] {state.root} · entry {state.entry_stage}")
    if state.entry_reason:
        console.print(f"[dim]{state.entry_reason.strip()}[/dim]")
    table = Table(show_lines=False)
    for col in ("", "stage", "artifacts"):
        table.add_column(col)
    for stage in state.stages:
        mark = "[green]✓[/green]" if stage.present else "[dim]·[/dim]"
        table.add_row(mark, f"{stage.id} {stage.label}",
                      ", ".join(stage.artifacts) or "[dim]none[/dim]")
    console.print(table)
    gates = Table(show_lines=False)
    for col in ("", "gate", "record"):
        gates.add_column(col)
    for gate in state.gates:
        mark = "[green]✓[/green]" if gate.present else "[dim]·[/dim]"
        gates.add_row(mark, f"{gate.id} {gate.label}",
                      ", ".join(gate.artifacts) or "[dim]none[/dim]")
    console.print(gates)
    console.print("[bold]v3.0.0 design gate[/bold]")
    for criterion in state.criteria:
        mark = "[green]met[/green]" if criterion.met else "[yellow]not yet[/yellow]"
        console.print(f"  {mark} {criterion.id}: {criterion.requirement}")
        console.print(f"        [dim]{criterion.detail}[/dim]")
    verdict = (
        "[bold green]design gate MET[/bold green]"
        if state.design_gate_met
        else "[bold yellow]design gate not met[/bold yellow]"
    )
    console.print(f"\n{verdict} — next: {state.next_action}")
    if state.pl5_requires_human_decision and state.pl5_decision is None:
        raise typer.Exit(code=3)  # a fired criterion is waiting on a human


@app.command("review-gate")
def review_gate_cmd(
    voter: str = typer.Option(None, help="One voter; default: all six"),
    workspace: str = typer.Option(".", help="Where .mas/voter-registry.yaml lives"),
    provider: str = typer.Option(None, help="Force one provider (e.g. 'mock')"),
):
    """Fixture-registration gate for the REVIEW voters (§11.19): 8 fixtures
    each, >=87.5% to register. A failed voter stops voting — `review` runs
    without it and says so."""
    from pathlib import Path as _Path

    from ai_venture_studio.product.voter_gate import VoterFixtureError, record_gate_run
    from ai_venture_studio.review_gate import review_voter_names, run_review_voter_gate

    names = [voter] if voter else review_voter_names()
    failed_any = False
    for name in names:
        try:
            run = run_review_voter_gate(
                name, provider_override=provider, repo_dir=workspace
            )
        except VoterFixtureError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        record_gate_run(_Path(workspace) / ".mas", run)
        color = "green" if run.status == "registered" else "red"
        console.print(f"[{color}]review/{name}: {run.status}[/{color}] "
                      f"({run.passed}/{run.total})")
        for result in run.results:
            if not result.passed:
                console.print(f"    [red]{result.label}[/red]: {result.detail}")
        failed_any = failed_any or run.status != "registered"
    if failed_any:
        raise typer.Exit(code=1)


@app.command("prd-lint")
def prd_lint_cmd(
    prd_yaml: str = typer.Argument(..., help="PRD yaml (a 'prd:' mapping, §20.56.2)"),
    prose: str = typer.Option(..., help="Path to the prd.md prose document"),
    metrics_dir: str = typer.Option("metrics", help="Metric vocabulary directory"),
    ledger: str = typer.Option(None, help="claims/*.claim.yaml the PRD cites"),
):
    """PRD lint (§20.56): EARS/module leakage, non-goals, kill criteria,
    vocabulary metrics, instrumentation-or-task. Exit 0 clean / 1 findings
    (JSONL) / 2 malformed. Generated Planning tasks print to stderr."""
    import json
    import sys as _sys
    from pathlib import Path as _Path

    import yaml as _yaml

    from ai_venture_studio.evidence import load_metric_vocabulary
    from ai_venture_studio.product import PRD, load_ledger, prd_lint

    try:
        raw = _yaml.safe_load(_Path(prd_yaml).read_text())
        prd = PRD(**(raw.get("prd") or raw))
        prose_text = _Path(prose).read_text()
        claim_ids = set()
        if ledger:
            claim_ids = {
                str(c.get("id"))
                for c in load_ledger(ledger).get("claims") or []
                if isinstance(c, dict)
            }
        vocabulary = load_metric_vocabulary(metrics_dir)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    issues, tasks = prd_lint(
        prd, prose_text, vocabulary=vocabulary,
        ledger_claim_ids=claim_ids or set(prd.evidence_refs),
    )
    for issue in issues:
        print(json.dumps(issue.model_dump()))
    for task in tasks:
        print(f"PLANNING TASK: {task.title}", file=_sys.stderr)
    if issues:
        raise typer.Exit(code=1)


@app.command("handoff-check")
def handoff_check_cmd(
    handoff: str = typer.Argument(..., help="handoff/p2_to_stage1.yaml"),
    prd_document: str = typer.Option(..., help="The exact PRD document the handoff pins"),
):
    """Discovery's DoR check (§20.56.3): a malformed handoff fails here
    with a named error rather than being interpreted. Exit 0 / 2."""
    from pathlib import Path as _Path

    from ai_venture_studio.product import HandoffError, validate_handoff_at_dor

    try:
        accepted = validate_handoff_at_dor(
            handoff, prd_document_text=_Path(prd_document).read_text()
        )
    except (HandoffError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]handoff ok[/green]: {accepted.prd_ref} "
        f"({len(accepted.hypothesis_seed)} hypothesis(es), "
        f"tier {accepted.scope_tier})"
    )


@app.command("experiment-check")
def experiment_check_cmd(
    design: str = typer.Argument(..., help="experiments/EXP-*.yaml"),
    weekly_traffic: int = typer.Option(..., help="Current eligible traffic per week"),
    max_days: int = typer.Option(60, help="Longest acceptable run window"),
):
    """Gate PL3-exp preflight (§21.61): schema, FDR plan, power, and the
    pre-registration pin if present. Exit 0 / 1 findings / 2 malformed."""
    import json
    from pathlib import Path as _Path

    from ai_venture_studio.experiment import (
        PreregistrationError,
        fdr_plan_check,
        load_design,
        power_calc,
        verify_at_analysis,
    )

    try:
        text = _Path(design).read_text()
        parsed = load_design(text)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    findings = [i.model_dump() for i in fdr_plan_check(parsed)]
    power_result = power_calc(
        baseline=parsed.power.baseline,
        mde_relative=parsed.power.mde_relative,
        arms=parsed.design_stage1.arms,
        weekly_traffic=weekly_traffic,
        max_days=max_days,
        alpha=parsed.power.alpha,
        power=parsed.power.power,
    )
    if power_result.status != "ok":
        findings.append({"rule": power_result.status, "message": power_result.detail})
    if parsed.preregistration_hash:
        try:
            verify_at_analysis(text, parsed.preregistration_hash)
        except PreregistrationError as exc:
            findings.append({"rule": "preregistration_mismatch", "message": str(exc)})
    for finding in findings:
        print(json.dumps(finding))
    if findings:
        raise typer.Exit(code=1)
    console.print(
        f"[green]design ok[/green]: n={power_result.n_per_arm}/arm, "
        f"~{power_result.expected_days} days"
    )


@app.command("preregister")
def preregister_cmd(
    design: str = typer.Argument(..., help="experiments/EXP-*.yaml to pin"),
):
    """Compute the pre-registration hash for a design (§21.61.3). Record it
    in the file's preregistration_hash field BEFORE any exposure — writing
    the pin in does not change the pin."""
    from pathlib import Path as _Path

    from ai_venture_studio.experiment import lock_preregistration

    print(lock_preregistration(_Path(design).read_text()))


@app.command("evidence-bundle")
def evidence_bundle(
    review_id: str = typer.Argument(..., help="Review ID (directory under .mas/reviews/)"),
    repo_dir: str = typer.Option(".", help="Repository the review ran in"),
):
    """Export the Gate-R evidence bundle (unsigned v0) for one review's
    audit trail — the artifact a human attaches to a CAB submission."""
    from ai_venture_studio.adoption import write_evidence_bundle

    try:
        path = write_evidence_bundle(repo_dir, review_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    console.print(f"Evidence bundle written: {path}")


@app.command("cadence")
def cadence_cmd(
    repo_dir: str = typer.Option(".", help="Workspace the recurring loops run in"),
    today: str = typer.Option(None, help="ISO date override (default: today)"),
    run_due: bool = typer.Option(
        False, "--run-due", help="Run each loop that is due",
    ),
    only: str = typer.Option(
        None, "--only", metavar="LOOP[,LOOP]",
        help="Restrict to these loops (compound, sweep, bench) — for a "
             "scheduler that owns one loop in one directory",
    ),
    install: bool = typer.Option(
        False, "--install", help="Install the daily LaunchAgent that fires --run-due"
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Remove the LaunchAgent"
    ),
    label: str = typer.Option(
        None, "--label",
        help="LaunchAgent label for --install/--uninstall. A second workspace "
             "needs its own, or installing retargets the first one",
    ),
    at: str = typer.Option("09:00", help="Schedule for --install, as HH:MM"),
    arm: bool = typer.Option(
        False, "--arm",
        help="With --install, load it into launchd now (default: write the "
             "plist and print the command for you to run)",
    ),
    notify: bool = typer.Option(
        False, "--notify",
        help="Post the alert to Discord when a loop needs a person (with "
             "--install, arm the scheduled run to do it every day)",
    ),
    force_notify: bool = typer.Option(
        False, "--force-notify",
        help="Send even if the same alert went out recently — for checking "
             "that the webhook works",
    ),
    set_webhook: str = typer.Option(
        None, "--set-webhook", metavar="URL",
        help="Save the Discord webhook URL (0600) so --notify and the daily "
             "run can find it without any environment setup",
    ),
):
    """Which recurring loop is overdue, and the trigger that keeps them from
    lapsing. Reads the artifacts compound, sweep and the product-bench
    already write; a loop that never ran is reported as never run, not as
    fresh.

    Exits 3 when a loop is overdue, so it can gate a script."""
    import datetime as _dt

    from ai_venture_studio import cadence as cad

    loops = [part.strip() for part in only.split(",")] if only else None

    if set_webhook:
        from ai_venture_studio import notify as notifier

        try:
            saved = notifier.save_webhook(set_webhook)
        except notifier.NotifyError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        # The URL is never echoed: this scrolls back in a terminal, and
        # whoever reads it can post into the channel as this app.
        console.print(f"[green]saved[/green] {saved} (readable only by you)")
        console.print(
            "  [dim]Next: [bold]avs cadence --install --notify --arm[/bold] "
            "for the daily run, or add --notify to any cadence run.[/dim]"
        )
        return

    if uninstall:
        try:
            removed = cad.uninstall_agent(label=label)
        except cad.CadenceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        console.print(
            f"[green]removed[/green] {removed['plist']}" if removed["removed"]
            else f"[dim]nothing to remove at {removed['plist']}[/dim]"
        )
        return

    if install:
        try:
            hour, _, minute = at.partition(":")
            done = cad.install_agent(
                repo_dir, hour=int(hour), minute=int(minute or 0), load=arm,
                notify=notify, only=loops, label=label,
            )
        except (ValueError, cad.CadenceError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        console.print(f"[green]wrote[/green] {done['plist']}")
        console.print(
            f"  {done['schedule']} → avs cadence --run-due"
            + (f" --only {','.join(done['loops'])}" if done.get("loops") else "")
            + (" --notify" if done.get("notify") else "")
        )
        console.print(f"  workspace: {done['workspace']}")
        console.print(f"  log: {done['log']}")
        if done["env_keys"]:
            console.print(f"  environment: {', '.join(done['env_keys'])}")
        for warning in done["warnings"]:
            console.print(f"[yellow]  warning: {warning}[/yellow]")
        if done["loaded"]:
            console.print("[green]loaded into launchd[/green] — it will fire on schedule")
        elif arm:
            console.print(f"[red]launchctl refused: {done.get('detail', '')}[/red]")
            raise typer.Exit(code=2)
        else:
            console.print(
                f"\nNot armed yet. To start it:\n  [bold]{done['command']}[/bold]"
            )
        return

    day = _dt.date.fromisoformat(today) if today else None
    try:
        report = cad.assess(repo_dir, today=day, only=loops)
    except cad.CadenceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    outcomes = None
    if run_due:
        outcomes = cad.run_due(repo_dir, today=day, only=loops)
        for outcome in outcomes:
            if not outcome.ran:
                console.print(f"[dim]{outcome.loop}: {outcome.detail}[/dim]")
                continue
            color = "green" if outcome.exit_code == 0 else "yellow"
            console.print(f"[{color}]{outcome.loop}: ran (exit {outcome.exit_code})[/{color}]")
            if outcome.detail:
                console.print(f"  [dim]{outcome.detail}[/dim]")
        report = cad.assess(repo_dir, today=day, only=loops)

    table = Table(show_lines=False)
    table.add_column("loop")
    table.add_column("last run")
    table.add_column("cadence")
    table.add_column("state")
    table.add_column("last run produced")
    for loop in report.loops:
        color = {"ok": "green", "due": "yellow"}.get(loop.state, "red")
        if loop.vacuous:
            color = "yellow"
        table.add_row(
            loop.name, loop.last_run or "—", f"{loop.cadence_days}d",
            f"[{color}]{loop.describe()}[/{color}]",
            loop.produced or "—",
        )
    console.print(table)
    console.print(report.summary())

    for loop in report.vacuous:
        # "Check that work is reaching it" was the whole advice, which leaves
        # the founder to work out WHICH of two opposite problems they have:
        # a loop watching the wrong directory, or a loop watching the right
        # one while the work is paused. The run recorded which; say it.
        hint = {
            "never_any":
                "No review has ever been written in this workspace — the "
                "loop is almost certainly pointed at the wrong --repo-dir.",
            "work_stopped":
                "Reviews exist but all of them are older than the window — "
                "the loop is fine; the work is what paused.",
        }.get(loop.empty_because,
              "Check that work is reaching it before trusting the green.")
        console.print(
            f"  [yellow]{loop.name} is on schedule but read nothing — the "
            f"cadence is being kept over an empty window.[/yellow]\n"
            f"  [dim]{hint}[/dim]"
        )

    for loop in report.stale:
        console.print(f"  [dim]{loop.command}[/dim]")

    build = cad.scheduler_build(label=label)
    if build.installed:
        console.print(f"[dim]scheduler: {build.describe()}[/dim]")
    if build.behind:
        console.print(
            f"\n[yellow]The scheduler is running v{build.scheduled_version}; "
            f"you are on v{build.running_version}.[/yellow]\n"
            f"  [dim]Publishing does not reach the daily loop — that install "
            f"has to be upgraded too.[/dim]\n"
            f"  [bold]{build.binary.replace('/avs', '/pip')} install --upgrade "
            f"ai-venture-studio[/bold]"
        )

    if notify or force_notify:
        from ai_venture_studio import notify as notifier

        # Delivery is reported, never assumed. A notifier that fails silently
        # is worse than no notifier: the log nobody reads at least did not
        # claim to have told anyone.
        try:
            alerted = notifier.notify(
                repo_dir, report, build, today=day, force=force_notify,
                outcomes=outcomes, concerns=cad.result_concerns(repo_dir),
            )
        except notifier.NotifyError as exc:
            console.print(f"[red]alert NOT sent: {exc}[/red]")
        else:
            if alerted.sent:
                console.print("[green]alert sent to Discord[/green]")
            else:
                console.print(f"[dim]no alert: {alerted.reason}[/dim]")

    if report.stale or build.behind:
        raise typer.Exit(code=3)


_WORKSPACE_PARAMS = ("repo_dir", "workspace", "repo", "root")


def _metered(callback):
    """Wrap a command so whatever it spent reaches the ledger.

    The provider adapter buffers each call in process-global state; only a
    caller that knows the workspace can persist it. Two commands did that by
    hand and the rest did not, so `compound` ran daily on a LaunchAgent and
    spent real money invisibly for as long as it existed (v0.72.1).

    Patching the callers one at a time is how that recurs: the next command
    to reach a provider is written by someone who never learned the rule.
    So the flush happens here, once, for every command — keyed off whichever
    workspace option the command already declares. An empty buffer flushes
    to nothing, so this is inert for the commands that never call out.

    ADR-032 removed the spending *cap* and kept the metering deliberately.
    This is the kept half, made structural.
    """
    import functools

    @functools.wraps(callback)
    def wrapper(**kwargs):
        try:
            return callback(**kwargs)
        finally:
            from ai_venture_studio import spend

            target = next(
                (
                    kwargs[name]
                    for name in _WORKSPACE_PARAMS
                    if isinstance(kwargs.get(name), str | Path)
                ),
                ".",
            )
            try:
                spend.flush(target)
            except OSError:
                pass  # an unwritable ledger must never mask the real result

    return wrapper


for _info in app.registered_commands:
    _info.callback = _metered(_info.callback)


def main() -> None:
    # The reader for every `_log.debug` this codebase writes. Fifteen handlers
    # skip a row, a page, or a piece of bookkeeping and say so; without this
    # they say it to a logger with no handler, which is the ADR-060 defect
    # wearing a different hat. Off by default and it must stay off: these lines
    # go to stderr, and the CLI's real output is Rich on stdout (ADR-062).
    if os.environ.get("AVS_DEBUG"):
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr,
            format="%(levelname)s %(name)s: %(message)s",
        )
        console.print(
            "[dim]AVS_DEBUG=1 — skipped rows, unreadable files and dropped "
            "bookkeeping are logged to stderr.[/dim]"
        )
    sys.exit(app())


if __name__ == "__main__":  # `python -m ai_venture_studio.cli` — the server's
    main()                  # detached workers run exactly this (PR #21 bug)
