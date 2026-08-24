"""Autopilot (`avs create`) — FDR in, working product out.

The non-technical flow. Gate philosophy: humans keep the judgments they
are BEST at (is this my intent? — asked in their own language); the
machine keeps the ones they cannot make (EARS validity, DAG soundness,
tests) — those auto-pass on their deterministic checks and every
auto-approval is recorded in the report, never silent.

Pipeline: assess FDR → (questions back to the user if not ready) →
discover → plain-language confirmation (unless --yes) → plan → for each
task in DAG order: spec → auto-approve if checks pass → build → review.
Output: product/BUILD-REPORT.md in the FDR's language.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.leader import ACTIONABLE_SEVERITIES
from ai_venture_studio.state import CLEAN_VERDICT_VALUES, Severity, Verdict

#: What makes a fix WORSE THAN THE CODE IT REPLACED, and so gets it rolled back.
#: Deliberately NARROWER than `ACTIONABLE_SEVERITIES`, and it must stay that
#: way: medium is the modal severity a review raises (89 of ~187 findings in
#: run 13), so a re-review of any real diff almost always still contains one.
#: Rolling back on medium would discard nearly every fix — turning the repair
#: pass ADR-037 just enabled back into the no-op ADR-037 exists to remove,
#: while looking like it ran.
#:
#: Two thresholds that must differ, in a codebase whose last ADR was about two
#: thresholds that must match. The test that pins them apart is
#: `test_a_fix_is_not_rolled_back_for_the_severity_that_prompted_it`.
ROLLBACK_SEVERITIES = frozenset({Severity.CRITICAL, Severity.HIGH})

if not ROLLBACK_SEVERITIES < frozenset(ACTIONABLE_SEVERITIES):
    # Checked at import, not only in the suite. The relationship between
    # these two sets is the whole repair pass: equal sets roll back every
    # fix (ADR-037's no-op, wearing the shape of a run), and a rollback set
    # that is not a subset would discard fixes for severities nothing ever
    # tried to repair. A test catches that at CI; an edit made and run
    # without the suite is the case a test cannot reach, and this file is
    # edited by the same machine it drives. Enforcement at load time is the
    # only reliable form of this control (§11.19).
    raise RuntimeError(
        "ROLLBACK_SEVERITIES must stay a STRICT subset of "
        f"ACTIONABLE_SEVERITIES; got rollback={sorted(s.value for s in ROLLBACK_SEVERITIES)} "
        f"actionable={sorted(s.value for s in ACTIONABLE_SEVERITIES)}"
    )

#: How many findings one repair pass is asked to fix, and how many files it may
#: be shown. Bounded because the fix prompt has to fit in one completion — but
#: the bound is now NAMED and its overflow is REPORTED, because silently
#: dropping the 9th finding is the difference between "the fix wasn't good
#: enough" and "the task could never clear". Run 13 hit exactly that: nine
#: copies of one issue, eight repaired, the ninth surviving by construction and
#: rejecting the re-review no matter what the fix did. The leader now folds
#: repeats of one issue into one finding, so reaching this cap means nine
#: genuinely different issues — worth saying out loud in the row.
MAX_REPAIR_FINDINGS = 8
MAX_REPAIR_FILES = 12

#: Output cap for the repair pass. Named so the refusal below can quote it —
#: an unnamed 16384 sat inline while this was the one writer stage that never
#: asked whether its response had been cut off.
_FIX_MAX_TOKENS = 16384

from ai_venture_studio.providers import get_provider, last_response_truncated
from ai_venture_studio.upstream import progress
from ai_venture_studio.upstream.discover import approve_brief, run_discovery
from ai_venture_studio.upstream.fdr import Assessment, assess_fdr
from ai_venture_studio.upstream.plan import (
    ScopeLocked,
    approve_plan,
    load_plan,
    reusable_plan,
    run_planning,
)
from ai_venture_studio.upstream.spec import (
    apply_pending_amendment,
    approve_spec,
    run_spec_stage,
)
from ai_venture_studio.upstream.build import run_build
from ai_venture_studio.yamlx import extract_mapping

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

REPORTER_MARKER = "plain-language reporter for non-technical founders"

#: Runaway backstop for one build, in tokens. NOT a budget — ADR-032 removed
#: framework-side spending caps and stands: nothing here refuses work over
#: money, and for subscription billing tokens do not map to dollars at all.
#: What this bounds is *termination*. The build loop is the one place in the
#: system where a loop can retry into a wall: `MAX_ITERATIONS` bounds
#: attempts per task and `max_tasks` bounds tasks, but a task whose context
#: grows each iteration can consume without limit inside those counts, and
#: the ledger showed nothing until the run ended.
#:
#: Set far above any healthy run, on the run's own arithmetic: 12 tasks × 3
#: build iterations ≈ 36 model rounds, plus a spec and a review each; at the
#: ~60k tokens a full-context round costs, a complete 12-task build lands
#: near 3M. Ten million is roughly 3× that — a run that crosses it is not
#: expensive, it is looping. Raise it with `token_ceiling=` for genuinely
#: large plans; `token_ceiling=0` disables the bound entirely.
DEFAULT_TOKEN_CEILING = 10_000_000


class TaskOutcome(BaseModel):
    task_id: str
    title: str
    status: str  # built | spec_blocked | build_failed | error
    review_verdict: str | None = None
    detail: str = ""
    # The diagnosis, not just the verdict. `BuildResult` has always carried
    # these; the outcome record dropped them, so every failed task in
    # outcomes.yaml, in the bench scoreboard and in the founder's report read
    # "build gate still failing after max iterations" with the cause discarded
    # one stack frame away. Optional so older outcomes.yaml files still load.
    iterations: int = 0
    files_written: list[str] = Field(default_factory=list)
    test_summary: str = ""
    #: The other two `BuildResult` fields this record dropped, found by the
    #: sweep in ADR-060 rather than by another expensive run: the comment
    #: above says "`BuildResult` has always carried these", and it was only
    #: ever made true of three of the five.
    #:
    #: `wireup_issues` is the sharp one. It is computed ONLY on a successful
    #: build, so the one outcome the record had no way to state was the one
    #: that looks best and is worst: built, tests green, nothing imports it.
    #: Every reader saw `status: built` and stopped.
    modified_existing: list[str] = Field(default_factory=list)
    wireup_issues: list[str] = Field(default_factory=list)
    #: Blocking findings per voter on the FINAL review — `{"security": 2}`.
    #: A rejection rate says how often the review said no; this says who said
    #: it, which is the difference between tightening the code and fixing a
    #: miscalibrated reviewer. Empty on a clean verdict and on older records.
    blocking_by_voter: dict[str, int] = Field(default_factory=dict)


class AutopilotResult(BaseModel):
    #: halted = stopped at the token termination bound with its work intact
    #: and resumable — distinct from `failed`, where something went wrong.
    #: already_satisfied = the request duplicates a promise the product
    #: already keeps, so nothing was built and nothing went wrong.
    #: needs_decision = it contradicts one, and only a person can choose
    #: which promise survives (ADR-046).
    status: str  # needs_answers | awaiting_confirmation | completed | failed
    #        | halted | already_satisfied | needs_decision
    assessment: Assessment | None = None
    confirmation: str = ""
    outcomes: list[TaskOutcome] = Field(default_factory=list)
    report_path: str = ""
    auto_approvals: list[str] = Field(default_factory=list)
    #: WHY a `failed` run failed, in the run's own words. `status` alone
    #: cannot tell a planner whose output would not parse from a scope lock
    #: from a model that ran out of budget, and every one of those returned
    #: the bare string "failed" — the caller then reported a case that
    #: produced nothing and could say nothing about it (ADR-043). Empty on
    #: any status that is not a failure, and on results built before it
    #: existed.
    blocked_reason: str = ""


#: Section headings for the confirmation, per language. They are shown to
#: the model as the format to follow, so they cannot be hardcoded bilingual:
#: a demonstration beats an instruction. With "会做什么 / What will be built"
#: in the prompt, an English FDR came back with Chinese headings and then a
#: Chinese body — the founder's own words were English, the UI was English,
#: and the one page they must read before spending money was not. Found by
#: driving a real live run end to end, which is what real runs are for.
_CONFIRM_HEADINGS = {
    "zh": ("会做什么 / What will be built", "这次不做 / Not in this version",
           "怎么算成功 / How we'll know it works"),
    "en": ("What will be built", "Not in this version",
           "How we'll know it works"),
}


def fdr_language(fdr_text: str) -> str:
    """"zh" when the founder wrote any CJK, else "en".

    Deterministic on purpose — a model call to decide which language to
    answer in is a model call that can get it wrong, and this is the one
    decision that makes the confirmation readable or useless.
    """
    return "zh" if any("一" <= ch <= "鿿" for ch in fdr_text) else "en"


def _confirm_system(fdr_text: str) -> str:
    built, not_now, success = _CONFIRM_HEADINGS[fdr_language(fdr_text)]
    return f"""You are the {REPORTER_MARKER}. Restate the brief below
as a short confirmation the founder reads before the build starts — in the
SAME LANGUAGE as the FDR, which is the language of the headings below.
Three sections, plain words, no tech terms:
1. {built} (from scope_now, as user-visible abilities)
2. {not_now} (scope_later + scope_never)
3. {success}
End with one line: reply `--yes` (or re-run with --yes) to start building.
Respond with the confirmation text only."""

_REPORT_SYSTEM = f"""You are the {REPORTER_MARKER}. Write BUILD-REPORT.md
for a non-technical founder, in the SAME LANGUAGE as the FDR: what was
built (per task, as user-visible abilities), what the automated reviewers
flagged (plain words: "needs attention" not verdict codes), and what the
founder should do next (try it, answer open questions). No jargon.

WHOSE FAULT A FAILURE IS — this matters more than the wording:
- `spec_blocked` is OUR failure, never the founder's. It means OUR spec
  writer produced something OUR OWN internal checks rejected (requirements
  grammar, coverage). The founder's description had nothing to do with it.
  Say so plainly — "our checks blocked this one, you can retry it" — and
  NEVER write that their requirements were unclear, vague, or insufficient.
  A founder who is told to rewrite a description that was never the problem
  will spend a day changing nothing.
- `build_failed` / `error` are also ours: the code we wrote did not pass
  the tests we wrote.
- The ONLY thing to ask a founder to clarify is a question that was
  actually put to them before the build started. If none was, there is
  nothing for them to answer.

Do not state counts or totals — a tally is appended to your text
mechanically. Describe, do not add up.
Respond with the markdown only."""


def run_autopilot(
    workspace: str | Path,
    fdr_path: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    yes: bool = False,
    max_tasks: int = 12,
    parallel: bool = False,
    replan: bool = False,
    token_ceiling: int = DEFAULT_TOKEN_CEILING,
) -> AutopilotResult:
    root = Path(workspace).resolve()
    fdr_text = Path(fdr_path).read_text(encoding="utf-8")
    provider_impl = get_provider(provider)
    # Stamped before the first model call so "what did THIS run cost" is
    # answerable from the ledger without threading a label through every seat.
    import datetime as _dt

    run_started_at = _dt.datetime.now(_dt.UTC).isoformat()

    auto_approvals: list[str] = []
    # Gate U2, honored. `approve_plan` has always written `status: locked`,
    # and every later `avs create` re-decomposed the brief anyway: assess,
    # discovery, four charter voters with a verify pass, a leader, then
    # planning — minutes and real money — to arrive at a DIFFERENT plan,
    # because planning is not deterministic. That is what made resume unable
    # to recognize its own work (t4 was 结算页 in one run and 购物车 in the
    # next). A locked plan is now the plan.
    try:
        locked = None if replan else reusable_plan(root, fdr_text)
    except ScopeLocked as exc:
        return AutopilotResult(status="failed", confirmation=str(exc),
                               blocked_reason=f"scope locked: {exc}")
    if replan:
        auto_approvals.append(
            "Gate U2: --replan given — the locked plan (if any) is discarded "
            "and scope is decided again"
        )
    assessment: Assessment | None = None
    if locked is not None:
        progress.step(root, progress.SETUP, "plan",
                      "reusing the plan you already approved")
        auto_approvals.append(
            f"Gate U2 (scope lock): reused the locked plan "
            f"({len(locked.tasks)} task(s)) — FDR.md is unchanged since it "
            "was approved, so discovery and planning did not re-run "
            "(`--replan` re-decomposes deliberately)"
        )
        confirmation = _locked_confirmation(root, locked)
        if not yes:
            return AutopilotResult(
                status="awaiting_confirmation", confirmation=confirmation
            )
        _provision(root, auto_approvals)
        brief_title = locked.brief_title
        return _build_plan(
            root, locked,
            fdr_text=fdr_text, brief_title=brief_title, confirmation=confirmation,
            assessment=assessment, auto_approvals=auto_approvals,
            provider=provider, model=model, max_tasks=max_tasks,
            parallel=parallel, run_started_at=run_started_at,
            token_ceiling=token_ceiling,
        )

    # The pre-task phases are the longest silent stretch in a run — assess,
    # brief, four charter voters with a verify pass, leader, then planning —
    # and until tasks exist the Studio has nothing to show but "planning…".
    # Same journal the per-task steps use; SETUP is the task id meaning "the
    # run itself, before it has tasks".
    progress.step(root, progress.SETUP, "plan", "reading your requirements")
    assessment = assess_fdr(fdr_text, provider=provider, model=model)
    if not assessment.ready:
        questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(assessment.questions))
        (root / "FDR-QUESTIONS.md").write_text(
            f"# 请回答这些问题 / Please answer these questions\n\n"
            f"{assessment.summary}\n\n{questions}\n\n"
            f"把答案补充进 FDR.md 后重新运行 `avs create`。\n"
            f"Add your answers to FDR.md and run `avs create` again.\n",
            encoding="utf-8",
        )
        return AutopilotResult(status="needs_answers", assessment=assessment)

    progress.step(root, progress.SETUP, "plan",
                  "working out what to build — the longest step")
    brief = run_discovery(root, fdr_text, provider=provider)
    estimate = estimate_hint(root, len(brief.scope_now))
    progress.step(root, progress.SETUP, "plan",
                  "writing the plan back to you in plain language")
    confirmation = provider_impl.complete(
        model=model,
        system=_confirm_system(fdr_text),
        user=yaml.safe_dump(
            brief.model_dump(include={"title", "scope_now", "scope_later", "scope_never", "success_metrics"}),
            sort_keys=False, allow_unicode=True,
        )
        + f"\n预计/estimate: {estimate}\n"
        + f"\n<fdr_language_sample>\n{fdr_text[:400]}\n</fdr_language_sample>",
        max_tokens=1024,
    )
    confirmation += f"\n\n---\n{estimate}\n"
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "CONFIRMATION.md").write_text(confirmation, encoding="utf-8")
    if not yes:
        return AutopilotResult(
            status="awaiting_confirmation",
            assessment=assessment,
            confirmation=confirmation,
        )

    approve_brief(root)
    auto_approvals.append("Gate U1 (brief): confirmed via --yes on the plain-language summary")

    _provision(root, auto_approvals)
    progress.step(root, progress.SETUP, "plan",
                  "breaking it into modules to build")
    plan = run_planning(root, provider=provider, fdr_text=fdr_text, replan=replan)
    if plan.status == "blocked":
        return AutopilotResult(
            status="failed", assessment=assessment,
            confirmation=confirmation, auto_approvals=auto_approvals,
            # `dag_check` wrote down what was wrong and this line threw it
            # away, so a run that spent six minutes and produced no product
            # reported the single word "failed". The reason was on disk in
            # `product/plan.yaml` the whole time.
            blocked_reason="planning blocked: "
            + ("; ".join(plan.dag_issues) or "dag_check gave no reason"),
        )
    approve_plan(root)
    auto_approvals.append("Gate U2 (scope lock): auto — dag_check passed")
    return _build_plan(
        root, plan,
        fdr_text=fdr_text, brief_title=brief.title, confirmation=confirmation,
        assessment=assessment, auto_approvals=auto_approvals,
        provider=provider, model=model, max_tasks=max_tasks,
        parallel=parallel, run_started_at=run_started_at,
        token_ceiling=token_ceiling,
    )


def _locked_confirmation(root: Path, plan) -> str:
    """What the founder sees when the plan is the one they already approved.

    The stored CONFIRMATION.md if it survives — it is the text they read the
    first time — and otherwise the task list itself. Deliberately not a
    model call: reusing a locked plan should cost nothing.
    """
    stored = root / "product" / "CONFIRMATION.md"
    if stored.exists():
        text = stored.read_text(encoding="utf-8").strip()
        if text:
            return (
                text
                + "\n\n---\n这是你上次确认过的计划，需求没有改动，所以直接沿用。"
                "/ This is the plan you already approved; FDR.md has not "
                "changed, so it is reused as-is.\n"
            )
    rows = "\n".join(f"- {t.title}" for t in plan.tasks)
    return (
        "沿用你已确认的计划 / Reusing the plan you already approved "
        f"({len(plan.tasks)} 个模块 / modules):\n\n{rows}\n\n"
        "加 `--yes` 开始建 / add `--yes` to start building.\n"
    )


def _provision(root: Path, auto_approvals: list[str]) -> None:
    from ai_venture_studio.upstream.provisioning import provision_local, write_cloud_guide
    from ai_venture_studio.upstream.workspace import load_project as _lp

    provision_local(root)
    write_cloud_guide(root, _lp(root).profile)
    auto_approvals.append(
        "services: local SQLite provisioned (data/app.db); cloud options in SERVICES.md"
    )


def _build_plan(
    root: Path,
    plan,
    *,
    fdr_text: str,
    brief_title: str,
    confirmation: str,
    assessment,
    auto_approvals: list[str],
    provider: str,
    model: str,
    max_tasks: int,
    parallel: bool,
    run_started_at: str,
    token_ceiling: int = DEFAULT_TOKEN_CEILING,
) -> AutopilotResult:
    """Spec → build → review every task in the plan, then report.

    Shared by both entry paths: a freshly decomposed plan and a locked one
    reused from an earlier run take exactly the same route from here, so
    reuse can never mean a weaker build.
    """
    from ai_venture_studio import spend

    provider_impl = get_provider(provider)

    # Task-level resume (plan item 15, upstream half). The expensive unit
    # here is a TASK — spec + build + review, minutes and real money each —
    # so outcomes are persisted as they complete and a re-run skips what is
    # already built rather than re-paying it. This is task-granular, not
    # super-step-granular like the review graph: a task interrupted halfway
    # restarts that task, and the ones before it stay done.
    outcomes: list[TaskOutcome] = _resume_outcomes(root)
    plan_topo = _topo_order(load_plan(root).tasks)[:max_tasks]
    ordered, resumed = tasks_to_build(outcomes, plan_topo)
    if resumed:
        auto_approvals.append(
            f"resumed: {len(resumed)} task(s) already built "
            f"({', '.join(sorted(resumed))}) — not rebuilt"
        )
    # A re-run's first attempt at a previously-failed task must know why it
    # failed last time. The failure context used to reach only same-run
    # retries, so pressing "continue the build" after an interrupted or
    # failed run re-attempted every failed task BLIND — same inputs, same
    # writer, same wall, which is precisely the error-recurrence this system
    # promises not to have. Captured before any attempt because
    # record_outcome replaces the row the moment the new attempt lands.
    prior_failures = {
        o.task_id: _failure_context(o)
        for o in outcomes
        if o.status in _AUTO_RETRYABLE
    }
    if parallel:
        auto_approvals.append("parallel lanes: wave scheduling (one task per lane per wave)")
        for wave in schedule_waves(ordered):
            for wave_outcome in _build_wave_parallel(
                root, wave, provider=provider, model=model,
                auto_approvals=auto_approvals, fdr_text=fdr_text,
                prior_failures=prior_failures,
            ):
                record_outcome(outcomes, wave_outcome)
        ordered = []
    halted_at = 0
    for task in ordered:
        # The stop rule, checked BETWEEN tasks rather than inside one. A task
        # interrupted halfway leaves a half-written workspace and has to be
        # rebuilt from scratch; a task never started costs nothing and is
        # already resumable, because outcomes are on disk and `tasks_to_build`
        # skips what is done. So the bound stops the loop at the one boundary
        # where stopping is free.
        if token_ceiling:
            used = spend.tokens_since(root, run_started_at)
            if used > token_ceiling:
                halted_at = used
                auto_approvals.append(
                    f"HALTED: this run passed the {token_ceiling:,}-token "
                    f"termination bound at {used:,} tokens with "
                    f"{len(ordered) - len(resumed)} task(s) still unbuilt. "
                    "Nothing was refused over money — a build that consumes "
                    "this much is looping, not working. Re-run `avs create` "
                    "to continue where it stopped (built tasks are not "
                    "rebuilt), or raise the bound with --token-ceiling."
                )
                break
        outcome = _attempt_task(
            root, task, provider=provider, model=model, fdr_text=fdr_text,
            auto_approvals=auto_approvals,
            prior_failure=prior_failures.get(task.id, ""),
        )
        record_outcome(outcomes, outcome)
        # Persist immediately: a crash after this point must not lose the
        # task that just cost minutes to build.
        _write_outcomes(root, outcomes)
        # Also flush the ledger, so the bound above reads a real number rather
        # than only what is still in the buffer.
        spend.flush(root)

    # The machine retries its own failures before asking a human to. Every
    # failed task used to end the story with a retry button the founder had
    # to press — and the bench record shows that button usually worked
    # (t1/t2 recovered on the second pass, t5/t9 built on retry). A retry a
    # human would perform mechanically is the machine's job; judgment gates
    # stay human.
    # Not when halted: the bound exists because this run was consuming without
    # converging, and a retry pass is more of exactly that.
    if not halted_at:
        _retry_failed_tasks(
            root, plan_topo, outcomes, provider=provider, model=model,
            fdr_text=fdr_text, auto_approvals=auto_approvals,
        )

    report = provider_impl.complete(
        model=model,
        system=_REPORT_SYSTEM,
        user=yaml.safe_dump(
            {
                "fdr_language_sample": fdr_text[:400],
                "brief_title": brief_title,
                "outcomes": [o.model_dump() for o in outcomes],
                "auto_approvals": auto_approvals,
            },
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=2048,
    )
    report_path = root / "product" / "BUILD-REPORT.md"
    # The tally, as arithmetic rather than prose. The reporter wrote "five of
    # nine" for a run that built six, and separately blamed three
    # spec_blocked tasks on the founder's requirements when they were our own
    # lint rejecting our own writer. A count is not a thing to narrate.
    report_lang = fdr_language(fdr_text)
    tally_block = _outcome_tally(outcomes, report_lang)
    # What it cost, in the report the founder actually reads. The signal this
    # answers asked to SEE the number — "how much will a typical month of
    # builds cost me? I'm scared to leave autopilot running" — and a figure
    # you have to know to go looking for does not answer it. Appended rather
    # than prompted, so the number is arithmetic and never model prose.
    from ai_venture_studio import spend

    spend.flush(root)
    cost_line = spend.render_plain(
        spend.summarize_workspace(root, since=run_started_at), what="This build"
    )
    shape = spend.typical_and_projected(root)
    cost_block = (
        f"\n\n---\n\n## {_TALLY_TEXT[report_lang]['cost_heading']}\n\n"
        f"{cost_line}\n"
    )
    if shape.get("runs_seen", 0) > 1:
        cost_block += (
            f"\nTypical run so far: ${shape['typical_run_usd']:.2f}; "
            f"the most expensive one was ${shape['worst_run_usd']:.2f}. "
            f"This month: ${shape['month_to_date_usd']:.2f}.\n"
        )
    cost_block += (
        "\nThis is billed to your own API key — the framework never spends "
        "money on your behalf.\n"
    )
    report_path.write_text(
        report + tally_block + _wireup_block(outcomes, report_lang) + cost_block,
        encoding="utf-8",
    )

    built_count = sum(1 for o in outcomes if o.status == "built")
    if halted_at:
        # Its own status, not "failed": nothing failed. The run stopped at a
        # bound with its work intact, and re-running continues it.
        status = "halted"
    else:
        status = "completed" if built_count == len(outcomes) and outcomes else "failed"
    _post_build_artifacts(
        root, provider=provider, model=model, fdr_text=fdr_text,
        outcomes=outcomes, status=status,
    )
    return AutopilotResult(
        status=status,
        assessment=assessment,
        confirmation=confirmation,
        outcomes=outcomes,
        report_path=str(report_path),
        auto_approvals=auto_approvals,
    )


#: Statuses that mean the SYSTEM failed, not the founder. `spec_blocked` is
#: our spec writer failing our own requirements-grammar and coverage checks;
#: the founder's description is not the thing being checked.
_OURS = {"spec_blocked": "our checks blocked it",
         "build_failed": "our code did not pass our tests",
         "error": "the run hit an error",
         "merge_conflict": "two parallel lanes collided"}


#: The appended blocks, per language. The reporter's prose already follows
#: the FDR, but the tally and the cost line are assembled in Python — and
#: they were hardcoded bilingual, so a report an English founder read ended
#: in "## 结果清单", "个模块建成" and "建好了，检查有意见". Same defect as the
#: confirmation headings, one file over: text a founder reads has to be in
#: the language they wrote in.
_TALLY_TEXT = {
    "zh": {
        "heading": "结果清单 / What happened, counted",
        "built_fmt": "**{built} / {total}** 个模块建成 / modules built.",
        "clean": "已通过检查 / clean",
        "notes": "建好了，检查有意见 / built, review had notes",
        "changes": "建好了，但检查要求改动 / built, but the review asked for changes",
        "ours": "（这不是你的需求写得不好，是我们这边没过；可以单独重试）"
                " / not your requirements — ours; retry it on its own",
        "cost_heading": "花了多少 / What this cost",
        "wireup_heading": "建好了但没接上 / Built, but not wired up",
        "wireup_lead": "下面这些模块建成、测试也过了，但没有任何代码引用它们。"
                       "测试通过不等于接进了产品。"
                       " / These modules built and their tests pass, but "
                       "nothing in the product imports them. A green test "
                       "suite is not the same as being reachable.",
        "modified_heading": "改到了原有文件 / Pre-existing files changed",
        "modified_lead": "这些不是新建的文件，是原来就有、被改动过的。"
                         " / These files already existed and were modified, "
                         "not created.",
    },
    "en": {
        "heading": "What happened, counted",
        "built_fmt": "**{built} / {total}** modules built.",
        "clean": "clean",
        "notes": "built, review had notes",
        "changes": "built, but the review asked for changes",
        "ours": " — not your requirements, ours; you can retry it on its own",
        "cost_heading": "What this cost",
        "wireup_heading": "Built, but not wired up",
        "wireup_lead": "These modules built and their tests pass, but nothing "
                       "in the product imports them. A green test suite is not "
                       "the same as being reachable.",
        "modified_heading": "Pre-existing files changed",
        "modified_lead": "These files already existed and were modified, not "
                         "created.",
    },
}


def _wireup_block(outcomes, lang: str = "zh") -> str:
    """What the build already knew about a SUCCESSFUL task, and never said.

    `wireup_check` runs at the end of every build that reaches `built`, and
    its findings went onto `BuildResult.wireup_issues` — where, until ADR-060,
    nothing read them. The failure mode that hid there is the expensive one:
    a task whose tests pass and whose code nothing calls reports `built`, and
    a founder reading the report has no way to tell it from a task that
    actually shipped.

    Deterministic, for the reason `_outcome_tally` is: the reporter is an LLM
    and this is a list of facts. `modified_existing` rides here too — the
    field's own description promised "visible, reviewed, never silent" and it
    had no reader to be visible to.
    """
    words = _TALLY_TEXT[lang]
    unwired = [o for o in outcomes if o.status == "built" and o.wireup_issues]
    touched = [o for o in outcomes if o.modified_existing]
    if not unwired and not touched:
        return ""
    block = ""
    if unwired:
        block += f"\n\n---\n\n## {words['wireup_heading']}\n\n{words['wireup_lead']}\n\n"
        for outcome in unwired:
            block += f"- **{outcome.task_id} {outcome.title}**\n"
            for issue in outcome.wireup_issues:
                block += f"  - {issue}\n"
    if touched:
        block += (
            f"\n\n---\n\n## {words['modified_heading']}\n\n"
            f"{words['modified_lead']}\n\n"
        )
        for outcome in touched:
            files = ", ".join(f"`{f}`" for f in outcome.modified_existing)
            block += f"- **{outcome.task_id}**: {files}\n"
    return block


def _outcome_tally(outcomes, lang: str = "zh") -> str:
    """The counted truth, appended under the reporter's prose.

    Deterministic on purpose: the model narrates, arithmetic counts. It also
    labels every failure as ours, because every status in `_OURS` is.
    """
    words = _TALLY_TEXT[lang]
    built = [o for o in outcomes if o.status == "built"]
    lines = [
        f"\n\n---\n\n## {words['heading']}\n",
        words["built_fmt"].format(built=len(built), total=len(outcomes)) + "\n",
    ]
    for outcome in outcomes:
        if outcome.status == "built":
            # Three states, not two. A REQUEST_CHANGES task used to print the
            # same "review had notes" as an APPROVE_WITH_NOTES one, so the
            # founder could not tell work the reviewer signed off on from work
            # it refused to — the two rows read identically. That mattered
            # little while medium-only rejections were also reasonless; now
            # that ADR-037 makes them say what they objected to, the tally has
            # to stop flattening them into the approvals.
            if outcome.review_verdict == Verdict.APPROVE.value:
                note = words["clean"]
            elif outcome.review_verdict in CLEAN_VERDICT_VALUES:
                note = words["notes"]
            else:
                note = words["changes"]
            lines.append(f"- ✅ **{outcome.title}** — {note}")
        else:
            lines.append(
                f"- ❌ **{outcome.title}** — {_OURS.get(outcome.status, outcome.status)}"
                + words["ours"]
            )
    return "\n".join(lines) + "\n"


def record_outcome(outcomes: list, outcome: TaskOutcome) -> None:
    """One row per task, always: replace the previous attempt's row.

    Appending was a bug with a delayed fuse. `_resume_outcomes` keeps failed
    rows (so a re-run knows what to re-attempt), and the build loop appended
    a fresh row for the re-attempt — leaving both. The tally then counted
    the ghost: a workspace whose failed task was rebuilt on the next run
    reported "3/4 modules built" and status `failed` for a product that was
    entirely built, and the founder was asked to retry work that was already
    done. A run that says failed when it succeeded is a manufactured
    stop-and-ask.

    Keyed on task_id alone, deliberately: ids are positional and the plan is
    authoritative about what t4 *is now*, so a stale row about what t4 used
    to be must not survive alongside the new one. (Skipping-as-built is the
    opposite problem and stays keyed on (id, title) in `tasks_to_build`.)
    """
    for index, existing in enumerate(outcomes):
        if existing.task_id == outcome.task_id:
            outcomes[index] = outcome
            return
    outcomes.append(outcome)


def _attempt_task(
    root: Path,
    task,
    *,
    provider: str,
    model: str,
    fdr_text: str,
    auto_approvals: list[str],
    prior_failure: str = "",
    marker: str | None = None,
) -> TaskOutcome:
    """One task, end to end: spec → Gate U3 → build → review → repair.

    Extracted so the first attempt, the auto-retry, and the feature flow run
    the SAME code — the retry paths used to be hand-copied variants, which is
    how `retry-task` shipped without the review gate and the feature loop
    without progress narration. `prior_failure` is the previous attempt's
    diagnosis; both the spec writer and the implementer see it, so a retry is
    a different attempt rather than a replay.
    """
    marker = marker or task.id
    progress.step(root, task.id, "spec", f"working out how to build: {task.title}")
    spec = run_spec_stage(
        root, f"{task.description} (task:{marker})", provider=provider,
        source_contract=fdr_text, prior_failure=prior_failure,
    )
    if spec.status != "proposed":
        reasons = "; ".join(spec.block_reasons) or "blocked (no reasons recorded)"
        progress.step(root, task.id, "spec", f"blocked: {reasons[:160]}")
        return TaskOutcome(
            task_id=task.id, title=task.title, status="spec_blocked", detail=reasons
        )
    approve_spec(root, spec.slug)
    auto_approvals.append(f"Gate U3 ({spec.slug}): auto — ears_lint + coverage passed")
    built = run_build(
        root, spec.slug, provider=provider, model=model,
        task_lane=task.lane, task_estimate_hours=task.estimate_hours,
        source_contract=fdr_text, prior_failure=prior_failure,
    )
    verdict = None
    detail = built.detail
    by_voter: dict[str, int] = {}
    if built.status == "built":
        verdict, detail, approvals, by_voter = review_and_repair(
            root, provider=provider, model=model, label=spec.slug,
            task_id=task.id, detail=detail,
        )
        auto_approvals.extend(approvals)
    return TaskOutcome(
        task_id=task.id, title=task.title,
        status=built.status, review_verdict=verdict, detail=detail,
        iterations=built.iterations,
        files_written=built.files_written,
        test_summary=built.test_summary,
        modified_existing=built.modified_existing,
        wireup_issues=built.wireup_issues,
        blocking_by_voter=by_voter,
    )


#: What the machine may retry on its own: every status in `_OURS` — our spec
#: writer, our build gate, our lane collision. A retry of these is mechanical
#: (the founder's retry button did exactly this); nothing here overrides a
#: human judgment gate.
_AUTO_RETRYABLE = frozenset({"spec_blocked", "build_failed", "error", "merge_conflict"})


def _failure_context(outcome: TaskOutcome) -> str:
    """The previous attempt's diagnosis, as the next attempt's context."""
    parts = [f"previous attempt status: {outcome.status}"]
    if outcome.detail:
        parts.append(f"detail: {outcome.detail}")
    if outcome.test_summary:
        parts.append(f"test summary: {outcome.test_summary}")
    return "\n".join(parts)


def _retry_failed_tasks(
    root: Path,
    plan_tasks,
    outcomes: list,
    *,
    provider: str,
    model: str,
    fdr_text: str,
    auto_approvals: list[str],
    marker_for=None,
    persist: bool = True,
) -> None:
    """One bounded retry pass over this run's own failures.

    The founder's retry button recovers real tasks — the bench record shows
    it — and pressing it requires no judgment, only patience. So the run
    presses it itself, once per failed task, in dependency order (a task
    that failed because its dependency failed retries AFTER the dependency
    recovered), with the failure handed to the writer as context. Bounded on
    purpose: one pass, never recursive.

    Every retry and its result land in auto_approvals — the report says what
    the machine did on the founder's behalf, never silently.
    """
    failed = {o.task_id: o for o in outcomes if o.status in _AUTO_RETRYABLE}
    if not failed:
        return
    recovered: list[str] = []
    for task in plan_tasks:
        prior = failed.get(task.id)
        if prior is None:
            continue
        progress.step(
            root, task.id, "retry",
            f"first attempt failed — retrying with its failure as context: {task.title}",
        )
        outcome = _attempt_task(
            root, task, provider=provider, model=model, fdr_text=fdr_text,
            auto_approvals=auto_approvals,
            prior_failure=_failure_context(prior),
            marker=marker_for(task) if marker_for else None,
        )
        if outcome.status == "built":
            outcome.detail = (
                (outcome.detail + " " if outcome.detail else "")
                + "(recovered on auto-retry)"
            )
            recovered.append(task.id)
        else:
            # Both diagnoses travel: a later human retry starts from the
            # accumulated history, not from the last symptom alone.
            outcome.detail = (
                (outcome.detail + " " if outcome.detail else "")
                + f"(auto-retry also failed; first attempt: {prior.status}"
                + (f" — {prior.detail[:160]}" if prior.detail else "")
                + ")"
            )
        record_outcome(outcomes, outcome)
        if persist:
            _write_outcomes(root, outcomes)
    auto_approvals.append(
        f"auto-retry: {len(failed)} failed task(s) retried once with their "
        f"failure as context; {len(recovered)} recovered"
        + (f" ({', '.join(recovered)})" if recovered else "")
    )


def tasks_to_build(outcomes, tasks) -> tuple[list, list[str]]:
    """Split the plan into (still to build, already built).

    A task is skipped ONLY when a built outcome shares both its id and its
    title. Matching on the id alone was a silent-work-loss bug: ids are
    positional (t1..tN) and the plan is regenerated on every `avs create`,
    so the same id routinely names different work between runs —

        run 1   t4  结算页与下单记录界面     (built)
        run 2   t4  购物车与结算UI          (never built, skipped as done)

    and the report would announce "resumed: 1 task already built" for a
    module that does not exist. Skipping work is only safe when we can say
    what work it was.
    """
    built = {
        (o.task_id, o.title) for o in outcomes if o.status == "built"
    }
    todo = [t for t in tasks if (t.id, t.title) not in built]
    skipped = [t.id for t in tasks if (t.id, t.title) in built]
    return todo, skipped


def estimate_hint(root: Path, item_count: int) -> str:
    """M7: honest expectation-setting from recorded actuals when they
    exist, defaults when they don't."""
    per_task_min = 10
    estimates_path = root / ".mas" / "estimates.yaml"
    if estimates_path.exists():
        history = yaml.safe_load(estimates_path.read_text(encoding="utf-8")) or []
        if len(history) >= 3:
            import statistics

            per_task_min = max(3, int(statistics.median(
                e["actual_seconds"] for e in history) / 60) or per_task_min)
    n = max(item_count, 1)
    return (
        f"预计约 {n}–{n + 3} 个模块，每个 {per_task_min}–{per_task_min * 3} 分钟，"
        f"部分模块可能失败并可单独重试。"
        f" / Roughly {n}–{n + 3} modules at {per_task_min}–{per_task_min * 3} min "
        "each; individual modules may fail and can be retried alone."
    )


def _write_outcomes(root: Path, outcomes) -> None:
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(
        yaml.safe_dump([o.model_dump() for o in outcomes], sort_keys=False,
                       allow_unicode=True),
        encoding="utf-8",
    )
    # Outcomes are written on every autopilot exit path, which makes this the
    # one place guaranteed to run after the spending stops.
    from ai_venture_studio import spend

    spend.flush(root)


def _resume_outcomes(root: Path) -> list[TaskOutcome]:
    """Prior outcomes, trusted only where the workspace agrees.

    An outcome claiming `built` is honored only if the spec for that task is
    actually built on disk (`built_task_ids`) — a stale outcomes.yaml must
    never let a run skip work that is not there. Anything else is dropped so
    the task is attempted again.
    """
    path = root / "product" / "outcomes.yaml"
    if not path.exists():
        return []
    try:
        rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError:
        return []  # an unreadable record is no record; redo the work
    from ai_venture_studio.upstream.plan import built_task_ids

    on_disk = built_task_ids(root)
    kept = []
    for row in rows if isinstance(rows, list) else []:
        try:
            outcome = TaskOutcome.model_validate(row)
        except Exception as exc:  # noqa: BLE001 — a bad row is not a built task
            _log.debug("resume: dropping an outcome row that will not "
                       "validate, so its task is redone: %s", exc)
            continue
        if outcome.status == "built" and outcome.task_id not in on_disk:
            continue  # claimed built, is not: redo it
        kept.append(outcome)
    return kept


def refresh_evidence(
    root: Path, *, provider: str, model: str, fdr_text: str
) -> None:
    """Everything derived FROM the built product: the acceptance walkthrough,
    the verification checklist, the screenshots.

    Split out of `_post_build_artifacts` because a FEATURE build must run
    it too. It used to be reachable only from the first build, so after a
    change the Studio kept serving the pictures and the verification rows
    of the product as it was BEFORE the founder changed it — with a green
    success message above them. While a change was a no-op that staleness
    was accidentally honest; the moment changes really rebuild, it is the
    Studio lying about the founder's own product.

    What is NOT here is deliberate. `outcomes.yaml` is the FIRST build's
    per-module record, and feature outcomes are kept out of it on purpose
    (`persist=False` below) — rewriting it with a feature's two tasks would
    erase the main build's failures and turn the home page green. Telemetry
    installation is idempotent and profile-level, not product-derived.

    Best-effort by contract: evidence never fails a build that succeeded.
    """
    from ai_venture_studio.upstream.workspace import load_project

    try:
        profile = load_project(root).profile
    except Exception:  # noqa: BLE001 — no project, nothing to derive
        return
    try:
        from ai_venture_studio.upstream.walkthrough import generate_walkthrough

        generate_walkthrough(
            root, provider=provider, model=model, language_sample=fdr_text
        )
        # Auto-verification from the founder's own PRD — probes generated
        # against what was actually built, never hand-setup fixtures.
        has_entry = any(
            (root / e).exists() for e in ("app/main.py", "main.py", "app.py")
        )
        if profile == "web" and has_entry:
            from ai_venture_studio.upstream.probegen import verify_product

            verify_product(root, provider=provider, model=model)
        from ai_venture_studio.upstream.screenshots import capture

        shots = capture(root, profile)
        if shots.captured or shots.note:
            (root / "product" / "screenshots.yaml").write_text(
                yaml.safe_dump(shots.model_dump(), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
    except Exception as exc:  # noqa: BLE001 — artifacts never fail the build
        _log.debug("verification/screenshot artifacts skipped for this "
                   "build: %s", exc)


def _post_build_artifacts(
    root: Path, *, provider: str, model: str, fdr_text: str, outcomes, status: str
) -> None:
    """M2/M4/M5/M7 wiring: outcomes record (retry UX), screenshots,
    acceptance walkthrough, telemetry module, undo checkpoint."""
    (root / "product").mkdir(exist_ok=True)
    (root / "product" / "outcomes.yaml").write_text(
        yaml.safe_dump([o.model_dump() for o in outcomes], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    try:
        from ai_venture_studio.upstream.telemetry import install_telemetry
        from ai_venture_studio.upstream.workspace import load_project

        install_telemetry(root, load_project(root).profile)
    except Exception as exc:  # noqa: BLE001 — artifacts never fail the build
        _log.debug("telemetry was not installed into the product — its "
                   "success metrics will have no events to read: %s", exc)
    refresh_evidence(root, provider=provider, model=model, fdr_text=fdr_text)
    if status == "completed":
        tag_checkpoint(root)


def tag_checkpoint(root: Path) -> str:
    """M7 undo: every completed build/feature gets a checkpoint tag.

    The tag also freezes what the product promised at that moment
    (ADR-048), so `avs requirements` can answer "what did this build change
    about my promises" instead of only "what does the product promise now".
    The baseline is written AFTER the tag exists and its failure is not
    fatal: a checkpoint that cannot be undone because a bookkeeping file
    would not write is a far worse outcome than a missing delta line.
    """
    import subprocess

    existing = subprocess.run(
        [resolve("git"), "tag", "--list", "ap-checkpoint-*"], cwd=root,
        capture_output=True, timeout=60, text=True,
    ).stdout.split()
    name = f"ap-checkpoint-{len(existing) + 1:03d}"
    subprocess.run([resolve("git"), "tag", name], cwd=root, capture_output=True, timeout=60)
    try:
        from ai_venture_studio.upstream.requirements import write_baseline

        write_baseline(root, name)
    except OSError:
        pass
    return name


def checkpoints(root: Path) -> list[str]:
    """Every checkpoint tag, oldest first."""
    import subprocess

    return subprocess.run(
        [resolve("git"), "tag", "--list", "ap-checkpoint-*", "--sort=version:refname"],
        cwd=root, capture_output=True, timeout=60, text=True,
    ).stdout.split()


def checkpoint_log(root: Path) -> list[dict]:
    """The change list, NEWEST FIRST, with what going back past each one
    would really cost.

    The history model here is a straight line of checkpoints: returning to
    a point means `git reset --hard` to it, so everything after it goes
    too. That is the honest shape, and `later_changes` / `undoes_commits`
    exist so a UI can SAY it instead of implying that one change in the
    middle can be lifted out on its own. (It cannot: that would be
    `git revert` and a conflict resolution no founder should be handed.)
    """
    import subprocess

    tags = checkpoints(root)
    entries: list[dict] = []
    for index, tag in enumerate(tags):
        subject = subprocess.run(
            [resolve("git"), "log", "-1", "--format=%s", tag],
            cwd=root, capture_output=True, timeout=60, text=True,
        ).stdout.strip()
        when = subprocess.run(
            [resolve("git"), "log", "-1", "--format=%ci", tag],
            cwd=root, capture_output=True, timeout=60, text=True,
        ).stdout.strip()[:16]
        previous = tags[index - 1] if index else ""
        commits = 0
        if previous:
            counted = subprocess.run(
                [resolve("git"), "rev-list", "--count", f"{previous}..HEAD"],
                cwd=root, capture_output=True, timeout=60, text=True,
            ).stdout.strip()
            commits = int(counted) if counted.isdigit() else 0
        entries.append({
            "tag": tag,
            "subject": subject,
            "when": when,
            "previous": previous,
            # Checkpoints newer than this one — every one of them is undone
            # as well, because the reset lands before this one.
            "later_changes": len(tags) - 1 - index,
            "undoes_commits": commits,
        })
    return list(reversed(entries))


def undo_to_before(root: Path, tag: str) -> dict:
    """Reset to the checkpoint BEFORE `tag`, saving a rescue branch first.

    Linear, and deliberately not disguised: every checkpoint after `tag`
    goes with it. The caller must have told the founder how many that is —
    see `checkpoint_log`.
    """
    import subprocess
    import time as _time

    tags = checkpoints(root)
    if tag not in tags:
        return {"status": "unknown_checkpoint", "detail": tag}
    index = tags.index(tag)
    if index == 0:
        return {"status": "nothing_to_undo",
                "detail": "只有一个版本，暂时没有可回退的更早版本。"
                          " / no earlier version to return to."}
    rescue = f"rescue/{int(_time.time())}"
    subprocess.run([resolve("git"), "branch", rescue], cwd=root, capture_output=True, timeout=60)
    target = tags[index - 1]
    reset = subprocess.run(
        [resolve("git"), "reset", "--hard", target], cwd=root, capture_output=True, timeout=60, text=True
    )
    if reset.returncode != 0:
        return {"status": "error", "detail": reset.stderr[:200]}
    dropped = tags[index:]
    for stale in dropped:
        subprocess.run([resolve("git"), "tag", "-d", stale], cwd=root, capture_output=True, timeout=60)
    return {"status": "undone", "restored_to": target, "rescue_branch": rescue,
            "dropped": dropped}


def undo_last(root: Path) -> dict:
    """M7: 回到上一个版本 — resets to the previous checkpoint after saving
    a rescue branch, so even undo is undoable."""
    tags = checkpoints(root)
    if len(tags) < 2:
        return {"status": "nothing_to_undo",
                "detail": "只有一个版本，暂时没有可回退的更早版本。"}
    return undo_to_before(root, tags[-1])


def review_and_repair(
    root: Path,
    *,
    provider: str,
    model: str,
    label: str,
    task_id: str = "",
    detail: str = "",
) -> tuple[str | None, str, list[str], dict[str, int]]:
    """Gate 3 for one just-built module: review, one bounded repair pass on
    critical/high findings, re-review, roll back if it did not clear.

    Shared so that every path which builds a module also reviews it.
    `retry-task` used to skip this entirely — it ran spec + build and
    stopped — so a founder who retried four failed modules ended up with
    four modules that no reviewer had ever looked at, each carrying an
    empty verdict in the report while the modules built by `create` beside
    them carried a real one. A retry is not a lesser build.

    Returns (verdict, detail, auto-approval lines, blocking findings by voter).
    """
    approvals: list[str] = []
    if task_id:
        progress.step(root, task_id, "review", "checking the code that was written")
    review = _review_head(root, provider)
    verdict = review.verdict.value if review else None
    if not review:
        # A review that did not run is not a review that found nothing, and
        # until now the row could not tell them apart. Say it in the words
        # the gate used, so the reader is not left inferring a silence.
        detail = (detail + " " if detail else "") + (
            f"[the review did not run: {getattr(review, 'why', 'no verdict was produced')}]"
        )
    # Fix loop: ONE bounded repair iteration — recorded, re-reviewed, never
    # silent. The set is imported from the leader rather than re-listed here,
    # because this list USED to read ("critical", "high") while the leader
    # blocked on {CRITICAL, HIGH, MEDIUM}. A medium-only finding therefore
    # produced REQUEST_CHANGES that no fix was ever attempted on and that no
    # later attempt could clear — permanently unclean by construction, not by
    # quality. Bench run 14 scored clean-review 38% with build and probes both
    # at 100%, and 7 of its 11 rejections were exactly this shape.
    # Blocking on a severity you will not try to repair is not a strict
    # reviewer, it is a dead end.
    serious = [
        f for f in (review.findings if review else [])
        if f.severity in ACTIONABLE_SEVERITIES
    ]
    if serious and task_id:
        progress.step(
            root, task_id, "fix",
            f"fixing {len(serious)} issue(s) the review found",
        )
    final_review = review
    if serious:
        landed, after, why_not = _fix_iteration(root, provider, model, serious)
        if landed and after:
            verdict = after.verdict.value
            # The verdict comes from the re-review, so the reasons must too.
            # Reporting the PRE-fix findings next to a POST-fix verdict would
            # name issues that were just repaired.
            final_review = after
        if landed:
            approvals.append(
                f"fix iteration ({label}): {len(serious)} review "
                "finding(s) repaired; suite re-passed and the re-review "
                "found nothing serious"
            )
            detail = (detail + " " if detail else "") + "(after fix iteration)"
        else:
            # THE VERDICT DESCRIBES THE CODE THAT SURVIVED. When a repair is
            # rolled back, `git reset --hard` puts the PRE-fix tree back — and
            # this branch used to keep the post-fix review anyway, so the row
            # reported findings about a diff that no longer existed and hid
            # the ones that described what shipped. Run 16's `04-t3` was
            # recorded ESCALATE_SECURITY_RISK for *"input validation removed
            # for non-integer candidate IDs"*: the repair had removed it, the
            # rollback had put it back, and the guard was in the delivered
            # code the whole time the scoreboard said it was gone.
            #
            # The discarded attempt is still evidence — about the repair pass,
            # not about the product — so it rides in the reason, not in the
            # verdict.
            detail = (detail + " " if detail else "") + (
                f"(repair attempted, not applied: {why_not})" if why_not
                else "(repair attempted, not applied)"
            )
        # SAY WHAT THE CAPS DROPPED. A repair pass shown 8 of 11 findings
        # cannot clear a re-review that checks all 11, and until now that read
        # in the row as an ordinary rejection. A bound nobody can see is
        # indistinguishable from a fix that wasn't good enough.
        _repairing, _sites, omitted_sites = _repair_scope(serious)
        capped = []
        if len(serious) > MAX_REPAIR_FINDINGS:
            capped.append(
                f"{MAX_REPAIR_FINDINGS} of {len(serious)} findings — "
                f"{len(serious) - MAX_REPAIR_FINDINGS} were never shown to it"
            )
        if omitted_sites:
            capped.append(
                f"{len(omitted_sites)} file(s) a finding names were not "
                "shown either"
            )
        if capped:
            detail = (detail + " " if detail else "") + (
                "(repair pass saw " + "; ".join(capped) + ")"
            )
    # A non-clean verdict must say what made it non-clean. It did not: `detail`
    # was written ONLY when a fix iteration ran, so every medium-only rejection
    # reached the scoreboard as REQUEST_CHANGES with an empty reason — 11 of
    # run 14's 17 rows. The run could report that it rejected the work and not
    # what it objected to, which is ADR-036's evidence-deletion failure one
    # stage over: a reviewer rejecting everything correctly and one rejecting
    # everything spuriously produce byte-identical records.
    if verdict not in CLEAN_VERDICT_VALUES:
        # GATE 2 FIRST, because when it fired it is the whole answer. The test
        # gate downgrades an APPROVE deterministically and writes why into
        # `leader.summary` — where nothing read it. So the one rejection in
        # the system that already knows its exact cause was the one that
        # reached the row with no cause at all, and the notes beside it
        # blamed whatever else was lying around (ADR-042: a cause recorded as
        # decoration instead of as a fact).
        gate2 = _gate2_note(final_review)
        why = " ".join(filter(None, (
            gate2,
            _blocked_voter_note(final_review, gate2_blocked=bool(gate2)),
            _findings_summary(final_review.findings if final_review else []),
        )))
        if why:
            detail = (detail + " " if detail else "") + why
    return verdict, detail, approvals, _blocking_by_voter(final_review)


def _blocking_by_voter(review) -> dict[str, int]:
    """WHICH REVIEWER IS REJECTING THE WORK, recorded per task.

    Run 13's rejections were diagnosable only by hand-reading preserved
    review YAML — and the answer, once read, was that one deterministic
    tool raised 60% of every blocking finding in the run. A scoreboard that
    reports a rejection rate without saying who rejected cannot tell a
    strict reviewer from a miscalibrated one, which is the same shape as
    ADR-036: two very different failures with byte-identical records.
    """
    import collections

    counts = collections.Counter(
        f.voter
        for f in (review.findings if review else [])
        if f.severity in ACTIONABLE_SEVERITIES
    )
    return dict(counts.most_common())


def _gate2_note(review) -> str:
    """`[Gate 2 blocked — <reason>]`, or "" if the test gate did not fire.

    Read from `leader.summary`, which is where `test_gate_node` has always put
    it. The marker is a shared constant so the writer and this reader cannot
    drift apart.
    """
    from ai_venture_studio.orchestrator.graph import gate2_reason

    reason = gate2_reason(str(getattr(review, "summary", "") or ""))
    return f"[Gate 2 blocked — {reason}]" if reason else ""


def _blocked_voter_note(review, *, gate2_blocked: bool = False) -> str:
    """A REVIEWER THAT NEVER ANSWERED IS A REASON, AND THE ROW MUST SAY SO.

    `leader.synthesize` rejects on two triggers, not one: a finding at an
    actionable severity, **or** `len(blocked) == 2` — two voters whose
    status came back anything but OK. Nothing downstream knew about the
    second one. So a task whose every finding was LOW — a severity that
    cannot block on its own — reached the scoreboard as REQUEST_CHANGES
    with `[review: 1 low — B310: blacklist]` beside it, naming the finding
    that did not reject it and omitting the two reviewers that did.

    Run 16 had three of those in eleven rejections: three tasks that would
    otherwise have been clean, charged to the products. The tell was there
    to read — all three rows carry no `blocking_by_voter` at all — but the
    sentence a person actually reads said something else. That is the exact
    failure ADR-037's detail line was added to end, so this is that fix
    finished rather than a new one.
    """
    blocked = list(getattr(review, "blocked_voters", None) or [])
    if not blocked:
        return ""
    # Whether these voters merely contributed to the rejection or ARE the
    # rejection is knowable, so state which. Without the distinction a
    # reader has to re-derive the leader's trigger order from the findings.
    # ...and "decisive" is a claim about the LEADER's trigger order, so it is
    # false whenever something downstream of the leader made the call. Gate 2
    # downgrades an APPROVE after synthesis; a row that said "this is what
    # rejected the task" about a blocked voter, when the suite had failed, was
    # confidently naming the wrong cause. `01-groupbuy-api t3` in run 18 is
    # that row: one blocked voter, zero findings, and no path through
    # `synthesize` that rejects on either.
    decisive = not gate2_blocked and not any(
        f.severity in ACTIONABLE_SEVERITIES for f in (review.findings or [])
    )
    return (
        f"[{len(blocked)} voter(s) returned no verdict: {', '.join(blocked)}"
        + (" — this is what rejected the task, not the findings below]"
           if decisive else "]")
    )


def _findings_summary(findings, limit: int = 3, width: int = 240) -> str:
    """`2 medium, 1 high — title; title; title`, bounded.

    Bounded on purpose: this rides in the bench row for every task, and the
    row is the durable record (the workspace it points at is gitignored).
    Enough to tell two different rejections apart without turning the
    scoreboard into a review dump.
    """
    if not findings:
        return ""
    import collections

    counts = collections.Counter(f.severity.value for f in findings)
    order = ("critical", "high", "medium", "low", "minor", "info")
    tally = ", ".join(
        f"{counts[s]} {s}" for s in order if counts.get(s)
    )
    titles = "; ".join(str(f.title) for f in findings[:limit])
    more = f" (+{len(findings) - limit} more)" if len(findings) > limit else ""
    return f"[review: {tally} — {titles}{more}]"[:width]


class ReviewDidNotRun:
    """Falsy stand-in for a review the graph refused to start.

    `run_review` returns `(None, state)` whenever no leader node ran — the
    DoR gate refusing is the common way — and `_review_head` unpacked that
    tuple as `review, _`, throwing the state away. So "the reviewer read the
    code and would not sign it off" and "the reviewer was never allowed to
    look" arrived at the caller as the identical `None`, and the task was
    recorded with an empty verdict and an EMPTY detail while every other
    rejected row in the run explained itself. The reason existed twice over
    — in `state["dor_reasons"]` and on disk in `02-dor_fail.yaml` — and was
    written down neither time. ADR-042's shape: the one piece of evidence
    that explains the row, discarded one stack frame away.

    Falsy on purpose. Every existing caller tests `if review` / `if after`
    and every one of them must keep behaving exactly as it did for `None`;
    `findings` is empty for the same reason. What changes is only that a
    caller may now ASK why, and the bench does.
    """

    def __init__(self, reasons: list[str] | None = None) -> None:
        self.reasons = [str(r) for r in (reasons or [])]
        self.findings: list = []

    def __bool__(self) -> bool:
        return False

    @property
    def why(self) -> str:
        return "; ".join(self.reasons) or "no verdict was produced"


def _review_head(root: Path, provider: str):
    import os

    from ai_venture_studio.orchestrator import run_review
    from ai_venture_studio.upstream.workspace import load_project

    from ai_venture_studio.paths import skills_root

    skills = skills_root()
    skills_dir = str(skills)
    try:
        if load_project(root).profile == "data":
            # §18.48.1 voter deltas ride along for data workspaces
            skills_dir = os.pathsep.join([skills_dir, str(skills / "data")])
    except FileNotFoundError:
        pass  # not an avs workspace — core roster only
    review, state = run_review(
        # Committed range only — the working tree carries uncommitted
        # bookkeeping from later tasks mid-autopilot (Gate 2 apply
        # conflicts otherwise; found by the product bench).
        "HEAD~1..HEAD", repo_dir=str(root), skills_dir=skills_dir,
        provider_override=provider if provider == "mock" else None,
    )
    if review is None:
        return ReviewDidNotRun((state or {}).get("dor_reasons") or [])
    return review


def _repair_scope(findings) -> tuple[list, list[str], list[str]]:
    """What one repair pass can be shown: `(findings, file sites, omitted)`.

    ONE definition of the bound, read twice — by the pass that applies it and
    by the row that has to report it. Computing it in both places is how the
    first bound came to be an unnamed `[:8]` sitting in two expressions with
    nothing saying it had dropped anything.
    """
    repairing = list(findings[:MAX_REPAIR_FINDINGS])
    wanted: list[str] = []
    for f in repairing:
        for rel in [f.file_path, *(getattr(f, "also_in", None) or [])]:
            if rel not in wanted:
                wanted.append(rel)
    return repairing, wanted[:MAX_REPAIR_FILES], wanted[MAX_REPAIR_FILES:]


def _fix_iteration(root: Path, provider: str, model: str, findings):
    """One bounded repair pass: findings → implementer → suite must pass
    → commit → RE-REVIEW, rolled back if it made things worse.

    Returns (landed, review_after, why). `review_after` is handed back so the
    caller records the post-fix verdict without paying for a second review.

    `why` exists because this function can fail in five different ways and
    every one of them used to reach the scoreboard as the same sentence —
    *"a fix was attempted and rolled back — it did not clear the review"* —
    which names the only one of the five that involves a review at all. Six
    of bench run 16's eleven rejections carried that sentence, so "the
    reviewer got stricter" and "the repair pass stopped working" were
    indistinguishable in the record, and the clean rate fell from 55% to 31%
    with nothing able to say which had happened. A pass that can fail five
    ways and report one is not a repair pass with a bug, it is an
    unobservable one.
    """
    import subprocess

    from ai_venture_studio.testing import _pytest_in_subprocess
    from ai_venture_studio.upstream.build import IMPLEMENTER_MARKER  # noqa: F401
    from ai_venture_studio.upstream.build import _write_files

    repairing, sites, _omitted = _repair_scope(findings)
    listing = yaml.safe_dump(
        [
            {"file": f.file_path, "line": f.line_start, "severity": f.severity.value,
             "title": f.title, "explanation": f.explanation[:300],
             # A folded finding carries its other sites. Without them the fix
             # pass would repair the one file the leader kept and leave the
             # other eight — and the re-review would raise the same issue again
             # at the sites nobody was shown.
             **({"also_in": list(f.also_in)} if getattr(f, "also_in", None) else {}),
             **({"occurrences": f.occurrences}
                if getattr(f, "occurrences", 1) > 1 else {})}
            for f in repairing
        ],
        sort_keys=False, allow_unicode=True,
    )
    sources = {}
    for rel in sites:
        path = root / rel
        if path.is_file():
            sources[rel] = path.read_text(encoding="utf-8", errors="replace")
    file_blocks = "\n\n".join(
        f"<file path=\"{p}\">\n{t}\n</file>" for p, t in sources.items()
    )
    raw = get_provider(provider).complete(
        model=model,
        system=f"You are the {IMPLEMENTER_MARKER}. Fix ONLY the review "
        "findings below in the provided files — smallest change, complete "
        "file contents back, no drive-by edits.\n\nRespond with ONLY YAML:\n"
        "files:\n  - path: ...\n    new_content: |\n      ...",
        user=f"<review_findings>\n{listing}</review_findings>\n\n{file_blocks}",
        max_tokens=_FIX_MAX_TOKENS,
    )
    if last_response_truncated():
        # Every other writer stage refuses a truncated batch (ADR-041); this
        # one silently handed the partial YAML to the parser and reported
        # whatever fell out as a failed review.
        return False, None, (
            f"the repair model's response was cut off at the "
            f"{_FIX_MAX_TOKENS}-token output cap; nothing was written"
        )
    try:
        data = extract_mapping(raw, ("files",))
        written, kept = _write_files(root, data.get("files") or [])
    except ValueError as exc:
        return False, None, (
            f"the repair model's response did not parse "
            f"({' '.join(str(exc).split())[:160]}); nothing was written"
        )
    if not written:
        return False, None, "the repair wrote no files"
    if kept:
        # A PARTLY-APPLIED REPAIR IS NOT A REPAIR. `kept` names the files the
        # write-guard dropped, and it used to be discarded on this line — the
        # single fact that explains the outcome, thrown away at the moment it
        # was produced. What survived was the rest of the batch: in run 16
        # that meant the new shared helper landed while the call sites that
        # were supposed to use it did not, the suite still passed, the
        # half-change was committed, and the re-review flagged the orphan the
        # repair pass had just created. Undo it and say which files were
        # refused, so the next reviewer sees the original code and the row
        # names the wall instead of blaming the product.
        subprocess.run([resolve("git"), "checkout", "--", "."], cwd=root,
                       capture_output=True, timeout=60)
        subprocess.run([resolve("git"), "clean", "-qfd", "--", *written],
                       cwd=root, capture_output=True, timeout=60)
        return False, None, (
            f"{len(kept)} file(s) of the repair were refused by the "
            f"write-guard, so it was applied to none: {'; '.join(kept[:2])}"
        )
    # The SAME gate the build loop uses. This used to run bare pytest, which
    # on a 小程序 returns "no_tests" — so a fix iteration on a JS product
    # committed without the JavaScript suite ever executing. One did exactly
    # that: `fix: address serious review findings` deleted an `onAdd` handler,
    # cart.page.test.js started failing, and because the build gate runs the
    # whole suite and existing tests are read-only walls to later
    # implementers, the next FOUR tasks could not be built. One unchecked fix
    # cost four modules.
    from ai_venture_studio.testing import combine_reports, run_js_tests

    verdict = combine_reports(_pytest_in_subprocess(root), run_js_tests(root))
    if verdict.status not in ("passed", "no_tests", "skipped"):
        subprocess.run([resolve("git"), "checkout", "--", "."], cwd=root, capture_output=True, timeout=60)
        # checkout restores tracked files only — NEW files the fix wrote
        # would stay as untracked residue (run-4 workspace contamination).
        subprocess.run(
            [resolve("git"), "clean", "-qfd", "--", *written],
            cwd=root, capture_output=True, timeout=60,
        )
        return False, None, (
            f"the repair broke the suite ({verdict.status}) and was discarded"
        )
    before = subprocess.run(
        [resolve("git"), "rev-parse", "HEAD"], cwd=root, capture_output=True,
        timeout=60, text=True,
    ).stdout.strip()
    subprocess.run([resolve("git"), "add", "-A"], cwd=root, capture_output=True, timeout=60)
    committed = subprocess.run(
        [resolve("git"), "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
         "commit", "-qm", "fix: address serious review findings"],
        cwd=root, capture_output=True, timeout=60, text=True,
    )
    if committed.returncode != 0:
        return False, None, (
            "the repair could not be committed: "
            + " ".join((committed.stderr or committed.stdout or "").split())[:160]
        )

    # THE FIX IS REVIEWED BEFORE IT STANDS. This review used to run in the
    # caller, after the commit, purely to record a verdict — so a "fix" that
    # made the product worse was written into history and the bad news went
    # into the report. It happened: told that an onAdd handler was never
    # wired to a control, the fix deleted the handler instead of adding the
    # control. The next review said so at critical/certain, score 100,
    # "breaking core feature" — and nothing acted on it. Four later tasks
    # then could not build against the broken suite.
    #
    # Same number of review calls as before; the difference is that this one
    # can say no.
    after = _review_head(root, provider)
    if _should_roll_back(after):
        subprocess.run(
            [resolve("git"), "reset", "--hard", before or "HEAD~1"],
            cwd=root, capture_output=True, timeout=60,
        )
        worse = ", ".join(sorted({
            f.severity.value for f in (after.findings if after else [])
            if f.severity in ROLLBACK_SEVERITIES
        }))
        return False, after, (
            f"the repair was reviewed, found {worse} findings of its own, "
            "and was discarded"
        )
    return True, after, ""


def _should_roll_back(after) -> bool:
    """Did the fix leave something bad enough to be worse than not fixing?

    A seam, so the threshold can be tested without driving git and a model.
    Reads `ROLLBACK_SEVERITIES`, which is narrower than the set that prompted
    the fix — see the comment on that constant for why it must stay narrower.
    """
    return any(
        f.severity in ROLLBACK_SEVERITIES for f in (after.findings if after else [])
    )


from ai_venture_studio.upstream.plan import PLANNER_MARKER as _PLANNER_MARKER
from ai_venture_studio.executables import resolve

_FEATURE_PLANNER_SYSTEM = f"""You are the {_PLANNER_MARKER},
planning ONE FEATURE CHANGE against an existing product.

Rules:
- 1-6 tasks; each is one spec+build cycle. Small features are ONE task.
- You see the existing file tree and prior features: plan integration with
  what exists — never plan rebuilding existing surfaces.
- <existing_requirements> lists promises this product ALREADY makes, with
  ids. Plan around them: never plan a task that re-delivers one, and where
  a task changes what an existing requirement promises, say which id in the
  task description ("changes R-012: ..."). If the list is truncated, the
  requirements you were not shown still exist.
- <conflicts_to_plan_around> names requirements this request clashes with.
  A task touching one must CHANGE it in place, never add a second rule
  beside it — two live rules that contradict each other is the state this
  section exists to prevent.
- <ruled_out> is what the founder has said NOT to build. Plan nothing that
  delivers one of those lines. The exception is the request in front of
  you: if THIS feature asks for a thing that was ruled out, the founder
  has changed their mind and the request wins — plan it, and say which
  line it reverses in the task description ("reverses C-003: ...").
- depends_on only within this feature's tasks; no cycles.

Respond with ONLY YAML:
tasks:
  - id: f1
    title: ...
    description: one sentence, phrased as a spec request
    depends_on: []
    lane: api
    estimate_hours: 3
"""


def _requirement_lines(root: Path, relations) -> str:
    """The clash in the founder's terms: the promise, and the test that
    proves the product keeps it. An id alone is not something a
    non-technical person can act on."""
    from ai_venture_studio.upstream.requirements import load_ledger

    ledger = {r.id: r for r in load_ledger(root)}
    lines = []
    for rel in relations:
        req = ledger.get(rel.requirement_id)
        if req is None:
            continue
        proof = f" (checked by {req.verified_by[0]})" if req.verified_by else ""
        lines.append(f"- **{req.id}** — {req.text}{proof}\n  {rel.reason}")
    return "\n".join(lines)


def _write_already_built(root: Path, feature_dir: Path, duplicates) -> None:
    (root / "FDR-ALREADY-BUILT.md").write_text(
        "# 这个功能已经有了 / You already have this\n\n"
        "系统没有重复建造，因为产品已经承诺了下面这些：\n"
        "Nothing was built, because the product already promises this:\n\n"
        f"{_requirement_lines(root, duplicates)}\n\n"
        "如果你想要的不一样，请把区别写清楚再试一次。\n"
        "If you wanted something different, say what is different and "
        "try again.\n",
        encoding="utf-8",
    )
    (feature_dir / "REPORT.md").write_text(
        "# Already satisfied\n\nThis feature was not built: it duplicates "
        f"{', '.join(d.requirement_id for d in duplicates)}.\n",
        encoding="utf-8",
    )


def _write_decision(root: Path, conflicts) -> None:
    ids = " ".join(c.requirement_id for c in conflicts)
    (root / "FDR-DECISION.md").write_text(
        "# 需要你决定 / One decision needed\n\n"
        "这个新需求和你之前提过的要求冲突了。两个不能同时成立：\n"
        "This request contradicts something you asked for earlier. Both "
        "cannot be true at once:\n\n"
        f"{_requirement_lines(root, conflicts)}\n\n"
        "**用新的替换旧的** / **Replace the old with the new**:\n\n"
        f"    avs add <fdr> --replace {ids} --yes\n\n"
        "**保留旧的** / **Keep the old one**: 改写你的需求文档再试一次 / "
        "reword the request and try again.\n",
        encoding="utf-8",
    )


def _propose_conflict_scrs(root: Path, conflicts) -> list[Path]:
    """Record the clash against each affected spec, unapproved.

    Raised but never approved: `--yes` means the founder does not want to
    be asked about the BUILD, and reading it as consent to retire an
    earlier promise would put words in their mouth (ADR-U02).
    """
    from ai_venture_studio.upstream.requirements import load_ledger
    from ai_venture_studio.upstream.spec import raise_scr

    ledger = {r.id: r for r in load_ledger(root)}
    raised = []
    for rel in conflicts:
        req = ledger.get(rel.requirement_id)
        if req is None:
            continue
        raised.append(raise_scr(
            root, req.spec_slug,
            f"unapproved: a later feature contradicts {req.id} "
            f"({rel.reason or 'no reason given'})",
        ))
    return raised


def run_feature(
    workspace: str | Path,
    fdr_path: str | Path,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    yes: bool = False,
    replace: list[str] | None = None,
) -> AutopilotResult:
    """Per-feature FDR on an existing product (granularity contract: one
    FDR = one feature change). Same gates, feature-scoped artifacts under
    product/features/.

    `replace` carries the founder's answer to a `needs_decision` result:
    the requirement ids they agreed this feature may retire. Nothing is
    retired without it, and nothing is retired until the build lands.
    """
    from ai_venture_studio.upstream.build import _file_tree
    from ai_venture_studio.upstream.plan import Task, dag_check

    root = Path(workspace).resolve()
    fdr_text = Path(fdr_path).read_text(encoding="utf-8")
    provider_impl = get_provider(provider)

    # WHAT ALREADY EXISTS, handed to the assessor. Without it the intake bar
    # is the first-FDR bar — "does this document establish users, actions and
    # scope" — asked of a request whose entire job is to change one thing
    # about a product that established all of those already. Run 18's three
    # follow-up FDRs each came back `needs_answers` and returned right here,
    # so the reconciliation gate this function exists to run never ran, and
    # the increment axis recorded 0% for a gate that had not been asked
    # anything (ADR-058).
    from ai_venture_studio.upstream.requirements import (
        load_ledger as _load_ledger,
        relevant as _relevant,
        render_slice as _render_slice,
        sync_ledger as _sync_ledger,
    )

    try:
        # SYNCED FIRST, the same order the planner's own read below obeys: a
        # slice read before the sync misses a spec built moments earlier, and
        # an assessor shown a stale ledger can ask for something the product
        # already promises. `sync_ledger` is idempotent, so the later call
        # stands unchanged.
        _sync_ledger(root)
        existing = (
            _render_slice(_relevant(root, fdr_text)) if _load_ledger(root) else ""
        )
    except (OSError, ValueError):
        # A product with no readable ledger is assessed as a first FDR — the
        # stricter bar. Degrading toward MORE questions is the safe direction.
        existing = ""

    assessment = assess_fdr(
        fdr_text, provider=provider, model=model, product_context=existing
    )
    if not assessment.ready:
        questions = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(assessment.questions))
        if not yes:
            (root / "FDR-QUESTIONS.md").write_text(
                f"# 请回答这些问题 / Please answer\n\n{assessment.summary}\n\n{questions}\n",
                encoding="utf-8",
            )
            return AutopilotResult(status="needs_answers", assessment=assessment)
        # `--yes` is a NON-INTERACTIVE run: nobody is there to answer, so
        # stopping here parks the request forever — bench run 19's third
        # increment ("让住户给维修打个分") died at intake on three questions a
        # one-line founder request never answers ("1-5 stars or 1-10?").
        # On an ESTABLISHED product the intake bar is advisory: the planner
        # and spec writers choose sensible defaults for exactly these gaps.
        # The questions are still recorded — visibly, with the fact that the
        # build went ahead — so the founder can answer them and re-run if
        # the defaults miss. First-FDR intake (run_autopilot) keeps the
        # strict bar: with no established users, actions or scope there is
        # nothing to default FROM.
        (root / "FDR-QUESTIONS.md").write_text(
            f"# 我们先按常规做法继续了 / We proceeded with sensible defaults\n\n"
            f"{assessment.summary}\n\n"
            "这次运行带了 `--yes`（无人值守），所以没有停下来等回答。下面这些问题，"
            "系统按常见做法自行选择了；如果和你想的不一样，回答后重新运行即可。\n"
            "This run used `--yes` (unattended), so it did not stop to ask. "
            "The system chose common-sense defaults for the questions below; "
            "answer them and re-run if they miss.\n\n"
            f"{questions}\n",
            encoding="utf-8",
        )

    features_dir = root / "product" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)
    # Idempotent per FDR: an unbuilt feature dir with identical FDR content
    # is resumed (confirm → build is two calls on the same feature).
    feature_dir = None
    for existing in sorted(features_dir.iterdir()):
        fdr_file = existing / "fdr.md"
        if (
            fdr_file.exists()
            and fdr_file.read_text(encoding="utf-8") == fdr_text
            and not (existing / "REPORT.md").exists()
        ):
            feature_dir = existing
            break
    if feature_dir is None:
        slug = f"{len(list(features_dir.iterdir())) + 1:02d}-" + "".join(
            c if c.isalnum() else "-" for c in assessment.summary[:32].lower()
        ).strip("-")
        feature_dir = features_dir / slug
        feature_dir.mkdir(exist_ok=True)
    slug = feature_dir.name
    (feature_dir / "fdr.md").write_text(fdr_text, encoding="utf-8")

    prior = "\n".join(f"- {d.name}" for d in sorted(features_dir.iterdir()) if d != feature_dir)
    from ai_venture_studio.upstream.plan import blast_radius

    radius = blast_radius(root, fdr_text)
    # The planner used to see prior features as a list of DIRECTORY NAMES,
    # which tells it that seven things happened and nothing about what the
    # product promises. The ledger is synced first so a spec built by an
    # earlier feature in this same session is already in it (ADR-045).
    from ai_venture_studio.upstream.requirements import (
        attribute_origin,
        load_ledger,
        relevant,
        render_slice,
        sync_ledger,
    )

    sync_ledger(root)
    requirements_before = {r.id for r in load_ledger(root)}
    req_slice = relevant(root, fdr_text, radius=radius)

    # Reconcile BEFORE planning (ADR-046). A request that duplicates a
    # promise the product already keeps must not reach a planner at all —
    # planning it produces a task, a spec and a build for work that is
    # already done and already passing.
    from ai_venture_studio.upstream import reconcile as _rec

    reconciliation = _rec.reconcile(
        fdr_text, req_slice, provider=provider, model=model
    )
    _rec.save(feature_dir, reconciliation)
    settled = set(replace or [])
    duplicates = reconciliation.duplicates
    conflicts = [c for c in reconciliation.conflicts if c.requirement_id not in settled]
    if duplicates:
        _write_already_built(root, feature_dir, duplicates)
        return AutopilotResult(
            status="already_satisfied", assessment=assessment,
            blocked_reason="already promised by "
            + ", ".join(d.requirement_id for d in duplicates),
        )
    if conflicts and not yes:
        _write_decision(root, conflicts)
        return AutopilotResult(
            status="needs_decision", assessment=assessment,
            blocked_reason="conflicts with "
            + ", ".join(c.requirement_id for c in conflicts),
        )
    if conflicts:
        # `yes` authorizes the BUILD, not the retirement of a promise the
        # founder made earlier. The clash is recorded as a PROPOSED SCR —
        # visible, auditable, and unapproved. Approving it here would forge
        # the human authorization ADR-U02 exists to require.
        _propose_conflict_scrs(root, conflicts)
    # A requirement the founder agreed to replace is superseded only if the
    # build lands, for the reason `apply_pending_amendment` is deferred: a
    # product with the old promise retired and the new one unbuilt promises
    # less than it did before the founder asked for more.
    to_supersede = [
        c.requirement_id for c in reconciliation.conflicts
        if c.requirement_id in settled
    ]

    # What the founder has ruled out (ADR-047). Synced against THIS FDR's
    # own section 4 first, so a feature that says "still no logins" adds
    # its line before the planner is shown the list, and scoped to this
    # document's origin so it cannot repeal what FDR.md said.
    from ai_venture_studio.upstream import constitution as _con

    _con.sync_constitution(
        root, fdr_text, str((feature_dir / "fdr.md").relative_to(root))
    )
    invariants = _con.render_for_planner(_con.live(root))

    from ai_venture_studio.upstream.blocks import catalog_summary
    from ai_venture_studio.upstream.workspace import load_project as _lp2

    blocks_note = catalog_summary(_lp2(root).profile)
    # The planner used to see a 200-path file list and filename-token matches,
    # which is not enough to integrate with a product: it could not tell which
    # module owns what, what the HTTP surface already is, or where the tests
    # live. The derived map answers all three from the code itself.
    from ai_venture_studio.upstream.comprehend import comprehend_repo, render_summary

    codebase = render_summary(comprehend_repo(root))
    raw = provider_impl.complete(
        model=model,
        system=_FEATURE_PLANNER_SYSTEM,
        user=(f"<blocks>\n{blocks_note}\n</blocks>\n\n" if blocks_note else "")
        + f"<codebase_map>\n{codebase}\n</codebase_map>\n\n"
        + f"<existing_tree>\n{_file_tree(root)}\n</existing_tree>\n\n"
        f"<likely_touched_files>\n"
        + ("\n".join(f"- {p}" for p in radius) or "(none matched)")
        + "\n</likely_touched_files>\n\n"
        f"<prior_features>\n{prior or '(first feature)'}\n</prior_features>\n\n"
        f"<existing_requirements>\n{render_slice(req_slice)}\n"
        "</existing_requirements>\n\n"
        f"<conflicts_to_plan_around>\n{_rec.render_for_planner(reconciliation)}\n"
        "</conflicts_to_plan_around>\n\n"
        f"<ruled_out>\n{invariants}\n</ruled_out>\n\n"
        f"<feature_fdr>\n{fdr_text}\n</feature_fdr>",
        max_tokens=2048,
    )
    data = extract_mapping(raw, ("tasks",))
    tasks = [Task.model_validate(t) for t in data.get("tasks", [])]
    if not tasks:
        # A deliberate `tasks: []` is the planner's only channel for "there
        # is nothing to build here" — it has read the codebase map and the
        # existing tree, and found every acceptance criterion already met.
        # Bench run 19: a reworded duplicate slipped past the reconciler
        # (empty retrieval slice), the planner planned zero tasks — doing
        # the reconciler's job by accident — and the empty outcome list fell
        # through the completion check below as `failed`. "Nothing to do"
        # reported as a failure is a false alarm on the founder's desk.
        (feature_dir / "plan.yaml").write_text("[]\n", encoding="utf-8")
        (feature_dir / "REPORT.md").write_text(
            "# Already satisfied\n\nThis feature was not built: the planner "
            "examined the existing code and found no work to do — the "
            "product already appears to provide what this FDR asks for.\n\n"
            "如果你想要的和现有功能不一样，请把区别写清楚再试一次。\n"
            "If you wanted something different, say what is different and "
            "try again.\n",
            encoding="utf-8",
        )
        return AutopilotResult(
            status="already_satisfied", assessment=assessment,
            blocked_reason="the feature planner found no tasks to build — "
            "the product already appears to provide this",
        )
    for task in tasks:
        if not task.files_expected:
            task.files_expected = blast_radius(
                root, f"{task.title} {task.description}", cap=3
            )
    issues = dag_check(tasks)
    if issues:
        return AutopilotResult(status="failed", assessment=assessment,
                               confirmation=f"plan dag_check failed: {issues}",
                               blocked_reason=f"planning blocked: {issues}")
    (feature_dir / "plan.yaml").write_text(
        yaml.safe_dump([t.model_dump() for t in tasks], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    confirmation = provider_impl.complete(
        model=model,
        system=_confirm_system(fdr_text),
        user=yaml.safe_dump(
            {"feature": assessment.summary,
             "tasks": [t.title for t in tasks]},
            sort_keys=False, allow_unicode=True,
        )
        + f"\n<fdr_language_sample>\n{fdr_text[:400]}\n</fdr_language_sample>",
        max_tokens=1024,
    )
    (feature_dir / "CONFIRMATION.md").write_text(confirmation, encoding="utf-8")
    if not yes:
        return AutopilotResult(
            status="awaiting_confirmation", assessment=assessment, confirmation=confirmation
        )

    auto_approvals = [f"feature plan ({slug}): auto — dag_check passed"]
    outcomes: list[TaskOutcome] = []
    feature_topo = _topo_order(tasks)
    for task in feature_topo:
        record_outcome(outcomes, _attempt_task(
            root, task, provider=provider, model=model, fdr_text=fdr_text,
            auto_approvals=auto_approvals, marker=f"{slug}-{task.id}",
        ))
    # Feature builds fail for the same mechanical reasons full builds do, and
    # the founder's recourse was the same retry button. Same pass, same
    # bounds; feature outcomes stay in the feature dir, so no product-level
    # persistence.
    _retry_failed_tasks(
        root, feature_topo, outcomes, provider=provider, model=model,
        fdr_text=fdr_text, auto_approvals=auto_approvals,
        marker_for=lambda t: f"{slug}-{t.id}", persist=False,
    )

    # Provenance is recorded HERE because this is the only point that knows
    # both halves: which requirements are new since planning, and which FDR
    # asked for them (ADR-045).
    # The ARCHIVED fdr is the origin, not the path the caller passed: a
    # correction arrives as `.mas/pending-change.md`, which is overwritten
    # by the next one, and a provenance link to a file that will hold
    # something else next week is worse than none.
    sync_ledger(root)
    origin = str((feature_dir / "fdr.md").relative_to(root))
    attribute_origin(root, requirements_before, origin)
    if to_supersede and outcomes and all(o.status == "built" for o in outcomes):
        from ai_venture_studio.upstream.requirements import supersede

        supersede(root, to_supersede, origin)

    report = provider_impl.complete(
        model=model,
        system=_REPORT_SYSTEM,
        user=yaml.safe_dump(
            {"fdr_language_sample": fdr_text[:400], "feature": slug,
             "outcomes": [o.model_dump() for o in outcomes],
             "auto_approvals": auto_approvals},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=2048,
    )
    (feature_dir / "REPORT.md").write_text(report, encoding="utf-8")
    built_count = sum(1 for o in outcomes if o.status == "built")
    if outcomes and built_count == len(outcomes):
        # The spec is amended BEFORE the evidence is derived: the walkthrough
        # and the verification probes are generated from the criteria, so
        # refreshing them first would re-derive the founder's acceptance list
        # from the contract they just changed away from.
        apply_pending_amendment(root, fdr_text)
        tag_checkpoint(root)
        refresh_evidence(root, provider=provider, model=model, fdr_text=fdr_text)
    return AutopilotResult(
        status="completed" if outcomes and built_count == len(outcomes) else "failed",
        assessment=assessment, confirmation=confirmation, outcomes=outcomes,
        report_path=str(feature_dir / "REPORT.md"), auto_approvals=auto_approvals,
    )


def schedule_waves(tasks) -> list[list]:
    """Dependency waves with at most ONE task per lane per wave — lane_check
    guarantees cross-lane tasks don't share files, so a wave's builds can
    run in parallel worktrees and merge cleanly."""
    done: set[str] = set()
    remaining = list(tasks)
    waves: list[list] = []
    while remaining:
        ready = [t for t in remaining if set(t.depends_on) <= done]
        if not ready:
            break  # cycle — dag_check blocks these upstream
        wave, lanes_used = [], set()
        for task in ready:
            if task.lane in lanes_used:
                continue
            wave.append(task)
            lanes_used.add(task.lane)
        for task in wave:
            done.add(task.id)
            remaining.remove(task)
        waves.append(wave)
    return waves


def _build_wave_parallel(
    root, wave, *, provider, model, auto_approvals, fdr_text="",
    prior_failures=None,
):
    """Each task of the wave builds in its own worktree branch; merges,
    bookkeeping, review and repair are applied serially afterwards.
    `prior_failures` maps task_id → the recorded failure of a previous run's
    attempt, so a resumed parallel run is no blinder than a sequential one.

    Only the BUILD is parallel. Everything after the merge runs one task at a
    time and through the same `review_and_repair` the sequential path uses:
    what `--parallel` buys is wall-clock on the writers, never a weaker gate.
    """
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    from ai_venture_studio.upstream.build import finalize_build_bookkeeping

    prior_failures = prior_failures or {}

    def build_one(item):
        task, spec_slug = item
        return task, run_build(
            root, spec_slug, provider=provider, model=model, in_branch=True,
            task_lane=task.lane, task_estimate_hours=task.estimate_hours,
            source_contract=fdr_text,
            prior_failure=prior_failures.get(task.id, ""),
        )

    prepared = []
    outcomes = []
    for task in wave:
        spec = run_spec_stage(
            root, f"{task.description} (task:{task.id})", provider=provider,
            source_contract=fdr_text,
            prior_failure=prior_failures.get(task.id, ""),
        )
        if spec.status != "proposed":
            outcomes.append(
                TaskOutcome(
                    task_id=task.id, title=task.title, status="spec_blocked",
                    detail="; ".join(spec.block_reasons) or "blocked (no reasons recorded)",
                )
            )
            continue
        approve_spec(root, spec.slug)
        auto_approvals.append(f"Gate U3 ({spec.slug}): auto — ears_lint + coverage passed")
        # Commit the spec in main BEFORE branching: lane worktrees see it in
        # HEAD, and the merge back can't collide with untracked spec files.
        subprocess.run([resolve("git"), "add", f"specs/{spec.slug}"], cwd=root, capture_output=True, timeout=60)
        subprocess.run(
            [resolve("git"), "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "commit", "-qm", f"spec({spec.slug}): approved"],
            cwd=root, capture_output=True,
        )
        prepared.append((task, spec.slug))

    with ThreadPoolExecutor(max_workers=max(1, len(prepared))) as pool:
        built = list(pool.map(build_one, prepared))

    for task, result in built:
        if result.status != "built":
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title,
                            status=result.status, detail=result.detail)
            )
            continue
        # `--no-commit`, so the bookkeeping lands INSIDE the merge commit.
        # build.py learned this one commit earlier ("BEFORE the commit, not
        # after"): `built: true`, the changelog fragment and the ledger sync
        # are exactly the files `git checkout -- .` throws away, and the
        # review below can reach that path through a rolled-back repair.
        # Uncommitted bookkeeping underneath a review is bookkeeping waiting
        # to be deleted — and the cost is a resumed run re-paying for modules
        # it already built.
        merged = subprocess.run(
            [resolve("git"), "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "merge", "--no-ff", "--no-commit", f"build/{result.slug}"],
            cwd=root, capture_output=True, timeout=60, text=True,
        )
        if merged.returncode != 0:
            subprocess.run([resolve("git"), "merge", "--abort"], cwd=root, capture_output=True, timeout=60)
            subprocess.run([resolve("git"), "branch", "-D", f"build/{result.slug}"],
                           cwd=root, capture_output=True, timeout=60)
            outcomes.append(
                TaskOutcome(task_id=task.id, title=task.title, status="merge_conflict",
                            detail=merged.stderr[:200] or merged.stdout[:200])
            )
            continue
        finalize_build_bookkeeping(root, result.slug, result.files_written)
        subprocess.run([resolve("git"), "add", "-A"], cwd=root, capture_output=True, timeout=60)
        committed = subprocess.run(
            [resolve("git"), "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
             "commit", "-qm", f"merge build/{result.slug}"],
            cwd=root, capture_output=True, timeout=60, text=True,
        )
        subprocess.run([resolve("git"), "branch", "-D", f"build/{result.slug}"],
                       cwd=root, capture_output=True, timeout=60)
        if committed.returncode != 0:
            subprocess.run([resolve("git"), "merge", "--abort"], cwd=root, capture_output=True, timeout=60)
            outcomes.append(
                TaskOutcome(
                    task_id=task.id, title=task.title, status="error",
                    detail="the merge could not be committed: "
                    + " ".join((committed.stderr or committed.stdout or "").split())[:160],
                )
            )
            continue
        # A PARALLEL BUILD IS NOT A LESSER BUILD. This path used to record
        # `status="built"` with `review_verdict=None` and stop — so a founder
        # who passed `--parallel` got modules no reviewer had ever looked at,
        # sitting in the report beside sequentially-built ones that carried a
        # real verdict. That is the same hole `retry-task` shipped with and
        # that `review_and_repair` was extracted to close; it survived here
        # because the wave loop was hand-written rather than routed through
        # `_attempt_task`. Serial on purpose: review and repair drive the
        # working tree, and the merges are already serialized.
        verdict, review_detail, approvals, by_voter = review_and_repair(
            root, provider=provider, model=model, label=result.slug,
            task_id=task.id, detail=f"parallel lane {task.lane}",
        )
        auto_approvals.extend(approvals)
        outcomes.append(
            TaskOutcome(
                task_id=task.id, title=task.title, status="built",
                review_verdict=verdict, detail=review_detail,
                # Carried for the same reason: the report's iteration count,
                # file list and test summary were blank for every parallel
                # task, so `--parallel` looked like a thinner build in the
                # record as well as being one.
                iterations=result.iterations,
                files_written=result.files_written,
                test_summary=result.test_summary,
                modified_existing=result.modified_existing,
                wireup_issues=result.wireup_issues,
                blocking_by_voter=by_voter,
            )
        )
    return outcomes


def _topo_order(tasks):
    done: set[str] = set()
    ordered = []
    remaining = list(tasks)
    while remaining:
        ready = [t for t in remaining if set(t.depends_on) <= done]
        if not ready:
            break  # cycle — dag_check should have blocked upstream
        for task in ready:
            ordered.append(task)
            done.add(task.id)
            remaining.remove(task)
    return ordered
