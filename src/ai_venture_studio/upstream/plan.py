"""Planning stage (§13.27) — the task DAG and Gate U2's scope lock.

The Planner turns an approved brief into tasks with dependencies and
lanes. `dag_check` is deterministic: unique ids, dependencies that exist,
no cycles — ~42% of MAS failures enter at specification/planning (MAST),
and a cyclic or dangling plan is the cheapest possible catch.

Gate U2 (`plan-approve`) locks scope: after the lock, the only legal way
to change the plan is a Spec Change Request — silent drift is the failure
mode the SCR back-edge exists to prevent (ADR-U02).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider, last_response_truncated
from ai_venture_studio.upstream.discover import load_brief
from ai_venture_studio.upstream.workspace import load_project
from ai_venture_studio.yamlx import extract_mapping

PLANNER_MARKER = "task planner in a greenfield product system"

MAX_REVISIONS = 2

# A 7-task plan carries a description, a lane, an estimate and file globs per
# task; 4096 output tokens is a tight fit for the wide end of that and buys
# nothing, since the planner runs once per feature.
_PLANNER_MAX_TOKENS = 8192


class Task(BaseModel):
    id: str
    title: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    lane: str = "core"
    estimate_hours: float = Field(gt=0, le=40)
    files_expected: list[str] = Field(
        default_factory=list,
        description="globs the task expects to touch — lane_check input",
    )
    external_review: str = Field(
        default="",
        pattern="^(|cab|platform)$",
        description="§18.48.2: this task ends at an external review train "
        "(CAB or app-store/mini-program review) — days of calendar latency "
        "the estimate does not capture; risk sequencing places it early",
    )


class Plan(BaseModel):
    status: str = "proposed"  # proposed | locked | blocked
    brief_title: str
    tasks: list[Task]
    dag_issues: list[str] = Field(default_factory=list)
    critic_issues: list[dict] = Field(default_factory=list)
    revisions: int = 0
    fdr_fingerprint: str = Field(
        default="",
        description="hash of the FDR this plan was decomposed from — what "
        "lets a locked plan be reused without re-deciding scope, and what "
        "detects an FDR edited after the lock. Empty on plans written "
        "before the field existed.",
    )


class ScopeLocked(RuntimeError):
    """Gate U2 held: the plan is locked and the FDR changed underneath it."""


def fdr_fingerprint(text: str) -> str:
    """Whitespace-insensitive digest of the founder's document.

    Reflowing a paragraph is not a scope change; the fingerprint should
    only move when the words do.
    """
    import hashlib

    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def read_fdr(repo_dir: str | Path) -> str:
    path = Path(repo_dir) / "FDR.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def reusable_plan(repo_dir: str | Path, fdr_text: str | None = None) -> Plan | None:
    """The locked plan, when Gate U2's lock still means what it said.

    `approve_plan` has always written `status: locked`, and every following
    `avs create` regenerated the plan anyway — so the lock was decoration,
    planning is not deterministic (the same FDR yields different task
    titles run to run), and the expensive upstream was re-paid on every
    invocation. A locked plan is now the plan.

    Returns the plan to reuse, or None when there is nothing locked to
    reuse. Raises `ScopeLocked` when the FDR has changed since the lock —
    that is a scope change after Gate U2, which belongs in an SCR or in an
    explicit, recorded `--replan`, never in a silent re-decomposition.
    """
    try:
        plan = load_plan(repo_dir)
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        # No plan, or one this version cannot read: there is nothing to
        # honor, so planning runs normally rather than failing the run.
        return None
    if plan.status != "locked" or not plan.tasks:
        return None
    current = fdr_text if fdr_text is not None else read_fdr(repo_dir)
    if not plan.fdr_fingerprint or not current:
        # Locked before fingerprints existed, or no FDR on disk to compare:
        # honor the lock, since re-planning is the behaviour that loses work.
        return plan
    if fdr_fingerprint(current) == plan.fdr_fingerprint:
        return plan
    raise ScopeLocked(
        "the plan is locked (Gate U2) and FDR.md has changed since — this is "
        "a scope change after the lock. Route it through `avs add` (a new "
        "feature FDR) or `avs scr` (a spec change request), or re-plan "
        "deliberately with --replan, which discards the locked plan."
    )


def lane_check(tasks: list[Task]) -> list[str]:
    """§13: single-writer enforced AT PLAN TIME — two tasks in DIFFERENT
    lanes declaring the same expected file is a collision waiting to
    happen. Same-lane overlap is fine (lanes serialize).

    NAMES A LEGAL REMEDY, not only the violation. It used to emit
    `lane collision: t1 (api) and t3 (orders) both expect 'app/models*.py'`
    and stop there. That sentence is true, it is precise, and a planner
    cannot act on it: it says which arrangement is forbidden and nothing
    about which arrangement is allowed. Run 18's `03-groupbuy-auto` was told
    it verbatim three times, produced a materially identical plan each time,
    exhausted `MAX_REVISIONS` and was blocked at Gate U2 — the case built no
    tasks at all and cost the run its build rate. The same product's case 01
    had already solved this exact collision by hoisting the shared file into
    its own task; nothing in the message had ever said that was available
    (ADR-041: a writer that is not told what would count as fixed cannot fix
    it; ADR-048: feedback that cannot change the outcome is not feedback).
    """
    from fnmatch import fnmatch

    # Per PAIR, not per glob pair. Three overlapping globs between two tasks
    # is one problem with one fix, and emitting it three times used a third
    # of the revision prompt restating it.
    collisions: dict[tuple[str, str], list[str]] = {}
    for i, a in enumerate(tasks):
        for b in tasks[i + 1 :]:
            if a.lane == b.lane:
                continue
            shared = [
                ga for ga in a.files_expected
                for gb in b.files_expected
                if ga == gb or fnmatch(ga, gb) or fnmatch(gb, ga)
            ]
            if shared:
                key = (a.id, b.id)
                for glob in shared:
                    if glob not in collisions.setdefault(key, []):
                        collisions[key].append(glob)

    by_id = {t.id: t for t in tasks}
    issues = []
    for (a_id, b_id), globs in collisions.items():
        a, b = by_id[a_id], by_id[b_id]
        names = ", ".join(repr(g) for g in globs)
        issues.append(
            f"lane collision: {a.id} ({a.lane}) and {b.id} ({b.lane}) both "
            f"expect {names}. FIX IT ONE OF THESE THREE WAYS, whichever "
            f"matches what the work actually is: "
            f"(1) HOIST — add a new task that owns {globs[0]!r} alone, and "
            f"make {a.id} and {b.id} depend on it and drop that glob from "
            f"their files_expected; "
            f"(2) MERGE — put {b.id} in lane {a.lane!r} (tasks in the same "
            f"lane run in sequence, so sharing a file there is allowed); "
            f"(3) SPLIT — narrow files_expected so the two no longer "
            f"overlap, e.g. one owns the model file and the other owns only "
            f"its own module. Do not re-send the same arrangement with "
            f"different wording."
        )
    return issues


def review_train_check(tasks: list[Task]) -> list[str]:
    """§18.48.2/§41.3: an external review is a train with multi-day, opaque
    latency. Work sequenced BEHIND a train stalls for days on a rejection —
    the mechanizable slice is: no task may depend on a train task."""
    train_ids = {t.id: t.external_review for t in tasks if t.external_review}
    issues = []
    for task in tasks:
        for dep in task.depends_on:
            if dep in train_ids:
                issues.append(
                    f"review-train hazard: {task.id} depends on {dep}, which "
                    f"ends at external {train_ids[dep]} review (days of "
                    "latency, rejection possible) — decouple it or move the "
                    "train to the end of the sequence"
                )
    return issues


def tier_check(tasks: list[Task], scope_tier: str) -> list[str]:
    """A thin tier that plans like a standard one is not thin.

    "EXACTLY 1-3 tasks" was planner-prompt text only, which makes it advisory:
    the model can ignore it and nothing notices. Same reasoning as the budget
    cap — the tier has to bite deterministically or it is a suggestion.
    """
    cap = _TIER_TASK_CAP.get(scope_tier)
    if cap is None or len(tasks) <= cap:
        return []
    return [
        f"scope_tier {scope_tier!r} allows at most {cap} task(s); the plan has "
        f"{len(tasks)} — cut to one working slice and add the rest with "
        "`avs add`, or plan at a wider tier"
    ]


def budget_check(tasks: list[Task], budget_hours: float) -> list[str]:
    total = sum(t.estimate_hours for t in tasks)
    if total > budget_hours:
        return [
            f"plan estimates {total:.0f}h, over the {budget_hours:.0f}h budget "
            "— cut scope or split the feature"
        ]
    return []


def record_actual(repo_dir: str | Path, lane: str, estimate_hours: float, actual_seconds: float) -> None:
    """estimate_calibrator's raw material: what builds actually cost."""
    path = Path(repo_dir) / ".mas" / "estimates.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    history = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    history = (history or [])[-200:]
    history.append(
        {"lane": lane, "estimate_hours": estimate_hours,
         "actual_seconds": round(actual_seconds, 1)}
    )
    path.write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")


def calibration_note(repo_dir: str | Path, tasks: list[Task]) -> str:
    """Advisory (n>=5 per doc 13): compare plan estimates against the
    recorded reality for the same lanes."""
    path = Path(repo_dir) / ".mas" / "estimates.yaml"
    if not path.exists():
        return ""
    history = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if len(history) < 5:
        return ""
    import statistics

    median_s = statistics.median(e["actual_seconds"] for e in history)
    return (
        f"calibration: {len(history)} recorded build(s), median actual "
        f"{median_s:.0f}s per task — treat hour-estimates as relative sizing"
    )


def blast_radius(repo_dir: str | Path, text: str, cap: int = 20) -> list[str]:
    """Files the change will plausibly touch — token overlap between the
    FDR/brief text and existing file paths/symbols. Advisory planner
    context (the full repo-graph version arrives with code_intel)."""
    from ai_venture_studio.maintenance.correlate import _tokens

    tokens = _tokens(text)
    root = Path(repo_dir)
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".js", ".ts", ".wxml", ".json"):
            continue
        rel = path.relative_to(root)
        if any(part in (".git", ".mas", "node_modules", "__pycache__", "specs") for part in rel.parts):
            continue
        stem_tokens = _tokens(str(rel).replace("/", " ").replace("_", " ").replace(".", " "))
        if stem_tokens & tokens:
            hits.append(str(rel))
        if len(hits) >= cap:
            break
    return hits


def dag_check(tasks: list[Task]) -> list[str]:
    issues = []
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        issues.append("duplicate task ids")
    known = set(ids)
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known:
                issues.append(f"task {task.id!r} depends on unknown {dep!r}")
            if dep == task.id:
                issues.append(f"task {task.id!r} depends on itself")
    # Kahn's algorithm: anything left over sits on a cycle.
    remaining = {t.id: set(t.depends_on) & known for t in tasks}
    while True:
        ready = [tid for tid, deps in remaining.items() if not deps]
        if not ready:
            break
        for tid in ready:
            remaining.pop(tid)
            for deps in remaining.values():
                deps.discard(tid)
    if remaining:
        issues.append(f"dependency cycle involving {sorted(remaining)}")
    return issues


# Scope tiers (SCOPE_TIERS in product/prd.py) reach the builder here. The
# vocabulary existed in the PRD and at the outer→inner handoff and then went
# nowhere: a human could decide "thin" at Gate PL1 and the planner would
# still decompose into a dozen tasks. thin NARROWS standard — same stages,
# same gates, fewer tasks — never a different pipeline.
_TIER_RULES = {
    "thin": (
        "- EXACTLY 1-3 tasks. This is a THIN slice: the goal is one thing "
        "working end to end today, not a complete product.\n"
        "- Task t1 MUST be a single user-visible action working end to end "
        "(storage + endpoint + the minimal screen to exercise it), so the "
        "product boots and does one real thing when t1 lands.\n"
        "- Everything else the brief asks for is deferred: name it in the "
        "task descriptions as out of scope for now. The founder adds the "
        "next slice with `avs add` once they have seen this one run.\n"
    ),
    "standard": "- 3-12 tasks; each is one `avs spec` + `build` cycle (<= 2 days).\n",
    "deep": (
        "- 3-12 tasks; each is one `avs spec` + `build` cycle (<= 2 days).\n"
        "- Sequence the riskiest and most architecturally load-bearing task "
        "first, so a wrong assumption surfaces while it is still cheap.\n"
    ),
}
# A thin slice that estimates like a full build is not thin. The tier caps
# the budget so budget_check bites instead of the guidance being advisory.
_TIER_BUDGET_CAP = {"thin": 10.0}
# ...and a thin tier that plans twelve tasks is not thin either.
_TIER_TASK_CAP = {"thin": 3}


def planner_system(scope_tier: str = "standard") -> str:
    return _PLANNER_SYSTEM_TEMPLATE.format(
        tier_rules=_TIER_RULES.get(scope_tier, _TIER_RULES["standard"]).rstrip("\n")
    )


_PLANNER_SYSTEM_TEMPLATE = f"""You are the {PLANNER_MARKER}. Decompose the approved
brief's scope_now into a task DAG a solo developer can execute.

Rules:
{{tier_rules}}
- depends_on lists task ids that must land first; no cycles.
- lane groups tasks that touch the same surface (e.g. api, ui, infra) —
  tasks in one lane run serially, lanes run in parallel.
- Only scope_now. scope_later items do NOT get tasks.
- <ruled_out> is what the founder has said NOT to build. Plan nothing that
  delivers one of those lines, however natural a companion to the scope it
  looks — they wrote it down precisely because it looks natural.
- Every task is a USER-VISIBLE feature slice specifiable as acceptance
  criteria. NO meta-tasks: no "test skeleton", "contract tests",
  "performance benchmark", "E2E suite", or "infrastructure" tasks — tests
  ship inside each feature task's own spec, never as a separate task.

Respond with ONLY YAML:
tasks:
  - id: t1
    title: ...
    description: one sentence, phrased as a spec request
    depends_on: []
    lane: api
    estimate_hours: 4
    files_expected: ["app/orders*.py"]   # globs this task will touch
"""

def release_lock_if_fdr_changed(repo_dir: str | Path, fdr_text: str) -> bool:
    """Rewriting the FDR in the Studio IS the deliberate re-decision.

    The refusal in `reusable_plan` exists to stop a CLI re-run from
    silently re-decomposing approved scope. A founder who opens the
    requirements form and submits different words has just done the
    opposite of that: asked for the scope to change, in the one place the
    product offers for asking. So the lock is released here, at the moment
    the new document is written, rather than surfacing as an error two
    screens later on a page that cannot explain it.

    Returns True when a lock was actually released (the text differed).
    """
    try:
        plan = load_plan(repo_dir)
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        return False
    if plan.status != "locked" or not plan.fdr_fingerprint:
        return False
    if fdr_fingerprint(fdr_text) == plan.fdr_fingerprint:
        return False
    plan.status = "proposed"
    plan.critic_issues = [
        *plan.critic_issues,
        {"severity": "minor", "lens": "scope",
         "problem": "scope lock released: the FDR was rewritten by the "
                    "founder, so this plan is re-decided"},
    ]
    _save(repo_dir, plan)
    return True


def run_planning(
    repo_dir: str | Path,
    *,
    provider: str = "anthropic",
    planner_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
    fdr_text: str | None = None,
    replan: bool = False,
) -> Plan:
    # Gate U2 first: a locked plan is reused, not re-decomposed. `--replan`
    # is the deliberate way past it and is recorded in the plan it writes.
    if not replan:
        reuse = reusable_plan(repo_dir, fdr_text)
        if reuse is not None:
            return reuse
    load_project(repo_dir)
    brief = load_brief(repo_dir)
    if brief.status != "approved":
        raise ValueError(
            f"brief status is {brief.status!r} — Gate U1 requires "
            "`avs brief-approve` before planning"
        )
    provider_impl = get_provider(provider)
    project_raw = yaml.safe_load(
        (Path(repo_dir) / ".mas" / "project.yaml").read_text(encoding="utf-8")
    ) or {}
    scope_tier = str(project_raw.get("scope_tier", "standard"))
    brief_yaml = yaml.safe_dump(
        brief.model_dump(include={"title", "problem", "scope_now", "success_metrics"}),
        sort_keys=False, allow_unicode=True,
    )

    # Section 4 of the FDR — the founder's "not for now" list — has always
    # been written and never read (ADR-047). The brief carries scope_now
    # and scope_later; neither is "do not build this", and the difference
    # matters most here, where a planner is deciding what a first version
    # contains.
    from ai_venture_studio.upstream import constitution as _con

    _con.sync_constitution(repo_dir, fdr_text or read_fdr(repo_dir), "FDR.md")
    ruled_out = _con.render_for_planner(_con.live(repo_dir))

    feedback = ""
    tasks: list[Task] = []
    dag_issues: list[str] = []
    critics: list[dict] = []
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=planner_model,
            system=planner_system(scope_tier),
            user=f"<brief>\n{brief_yaml}</brief>\n\n"
            f"<ruled_out>\n{ruled_out}\n</ruled_out>"
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=_PLANNER_MAX_TOKENS,
        )
        if last_response_truncated():
            # A plan cut off at the output cap loses whole tasks silently: the
            # YAML still parses, the missing tasks simply are not there, and
            # nothing downstream can tell a 3-task plan from a 7-task plan that
            # lost 4. dag_check cannot catch it either — the truncated plan is
            # internally consistent.
            feedback = (
                "YOUR LAST RESPONSE WAS CUT OFF at the output limit, so the plan "
                "was incomplete and was discarded. Return the SAME plan with "
                "shorter descriptions — every task, fewer words each."
            )
            tasks, dag_issues, critics = [], ["planner response truncated at the output cap"], []
            continue
        try:
            data = extract_mapping(raw, ("tasks",))
            tasks = [Task.model_validate(t) for t in data.get("tasks", [])]
        except Exception as exc:  # noqa: BLE001 — parse/schema failure feeds revision
            # Keep the opening of what the model actually said: "unparseable
            # planner output" alone cannot distinguish a schema slip from a
            # model that answered in prose, and the plan is where ~42% of MAS
            # failures enter.
            opening = " ".join(str(raw).split())[:160]
            # THE PARSER SAID WHERE IT BROKE AND WE KEPT ONLY THE WORD FOR
            # WHAT KIND OF BREAK IT WAS. A yaml ScannerError carries "line 3,
            # column 9: expected alphabetic or numeric character but found
            # '*'"; `type(exc).__name__` reduces that to "ScannerError", and
            # the revision prompt then asked the model to fix something it
            # was never shown. Run 16's case 02 revised twice on this
            # feedback, failed to parse both times, and cost the run a whole
            # product — the same shape as ADR-041 (the writer never told what
            # was wrong with its answer) and ADR-042 (a cause recorded as
            # decoration instead of a fact).
            why = " ".join(str(exc).split())[:200]
            feedback = (
                f"Your previous response failed to parse. {type(exc).__name__}: "
                f"{why}. Fix that exact problem. Respond with ONLY the YAML "
                "schema given, double-quoting every string value."
            )
            tasks, dag_issues, critics = [], [
                f"unparseable planner output ({type(exc).__name__}: {why}); "
                f"response began: {opening!r}"
            ], []
            continue
        for task in tasks:
            if not task.files_expected:
                # lane_check only bites when globs exist; derive a fallback
                # from blast radius when the planner omits them.
                task.files_expected = blast_radius(
                    repo_dir, f"{task.title} {task.description}", cap=3
                )
        budget = float(project_raw.get("budget_hours", 60))
        cap = _TIER_BUDGET_CAP.get(scope_tier)
        if cap is not None:
            # Tiers narrow, never widen: a thin slice cannot buy itself more
            # hours than the workspace budget already allows.
            budget = min(budget, cap)
        dag_issues = (
            dag_check(tasks) + lane_check(tasks)
            + budget_check(tasks, budget) + review_train_check(tasks)
            + tier_check(tasks, scope_tier)
        )
        # Charter roster (doc 13 §25.1): Completeness, DependencyRealism,
        # RiskSequencing, ParallelizationSafety, EstimateSanity — the
        # single "plan critic panel" prompt retired here (plan phase D13).
        from ai_venture_studio.product.stage_engine import run_critique_roster
        from ai_venture_studio.product.voter_gate import family_roots

        skills_root, _ = family_roots("planning")
        roster = run_critique_roster(
            "planning", "planning",
            f"<brief>\n{brief_yaml}</brief>\n\n<tasks>\n"
            + yaml.safe_dump([t.model_dump() for t in tasks],
                             sort_keys=False, allow_unicode=True)
            + "</tasks>",
            str(repo_dir),
            provider_impl=provider_impl,
            voter_model=critic_model,
            leader_model=planner_model,
            skills_root=skills_root,
            det_findings=[{"rule": "dag", "message": m} for m in dag_issues],
        )
        critics = roster.as_issues()[:10]
        majors = [c for c in critics if c.get("severity") == "major"]
        if not dag_issues and not majors:
            break
        feedback = yaml.safe_dump(
            {"dag_issues": dag_issues, "critic_majors": majors},
            sort_keys=False, allow_unicode=True,
        )

    source_fdr = fdr_text if fdr_text is not None else read_fdr(repo_dir)
    plan = Plan(
        status="proposed" if not dag_issues else "blocked",
        brief_title=brief.title,
        tasks=tasks,
        dag_issues=dag_issues,
        critic_issues=critics,
        revisions=revision,
        fdr_fingerprint=fdr_fingerprint(source_fdr) if source_fdr else "",
    )
    note = calibration_note(repo_dir, tasks)
    if note:
        plan.critic_issues.append({"severity": "minor", "lens": "estimates", "problem": note})
    _save(repo_dir, plan)
    return plan


def _save(repo_dir: str | Path, plan: Plan) -> None:
    directory = Path(repo_dir) / "product"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "plan.yaml").write_text(
        yaml.safe_dump(plan.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {t.id} | {t.title} | {', '.join(t.depends_on) or '—'} | {t.lane} | {t.estimate_hours}h |"
        for t in plan.tasks
    )
    (directory / "plan.md").write_text(
        f"# Plan — {plan.brief_title}\n\nstatus: **{plan.status}** · "
        f"{len(plan.tasks)} task(s) · revisions: {plan.revisions}\n\n"
        f"| id | task | depends on | lane | est |\n|---|---|---|---|---|\n{rows}\n\n"
        + (
            "Scope is **locked** (Gate U2): later runs reuse this plan "
            "instead of re-deciding it. Changes go through `avs add` or an "
            "SCR; `--replan` discards the lock deliberately.\n"
            if plan.status == "locked"
            else "Lock scope with: `avs plan-approve` (Gate U2). After the "
            "lock, scope changes go through an SCR, never silently.\n"
        ),
        encoding="utf-8",
    )


def load_plan(repo_dir: str | Path) -> Plan:
    path = Path(repo_dir) / "product" / "plan.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no plan under {repo_dir}/product (run `avs plan`)")
    return Plan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def approve_plan(repo_dir: str | Path) -> Plan:
    """Gate U2 — scope lock."""
    plan = load_plan(repo_dir)
    if plan.status == "blocked":
        raise ValueError(f"plan is blocked by dag_check: {plan.dag_issues}")
    plan.status = "locked"
    _save(repo_dir, plan)
    return plan


TASK_MARKER = re.compile(r"\(task:([\w-]+)\)")


def built_task_ids(repo_dir: str | Path) -> set[str]:
    """Task ids whose spec exists and is built.

    The link is the `(task:<id>)` marker autopilot/retry-task put in the
    spec request — Spec has no `task_id` field, so the earlier lookup for
    one silently matched nothing and every task always looked unbuilt.
    """
    done: set[str] = set()
    for spec_file in (Path(repo_dir) / "specs").glob("*/spec.yaml"):
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if not data.get("built"):
            continue
        marker = TASK_MARKER.search(str(data.get("request", "")))
        if marker:
            done.add(marker.group(1))
    return done


def reconcile_built_flags(repo_dir: str | Path, *, apply: bool = False) -> dict:
    """Find — and optionally repair — specs whose `built` flag the workspace
    lost, using evidence the workspace already holds.

    Until v0.70 `built: true` was written AFTER the task's own commit, so it
    sat in the working tree, where two recovery paths discard uncommitted
    changes wholesale: `git checkout -- .` when a fix iteration is rolled
    back, and `_reset_workspace` after a failed build. A real run committed
    six modules and kept the flag on two.

    That flag is not decoration: `built_task_ids` is what a resumed run reads
    to decide what it may skip, so a lost flag makes the next `avs create`
    rebuild — and re-bill — work that is already built and committed.

    A TASK is the unit, never a spec file. Planning is not deterministic, so
    re-running leaves SUPERSEDED specs behind: the same task id gets a second
    spec directory under a slightly different slug, and the older one keeps
    `built: false` forever. Four such pairs sit in one real workspace
    (`per-product-review-area-text-star-rating` beside
    `per-product-review-area-with-text-and-star-rat`). Those are not lost
    flags and repairing them would resurrect a spec a later plan replaced —
    the task already has a built spec, `built_task_ids` already returns it,
    and nothing will be rebuilt. A first version of this check reported all
    four as damage, which is why the rule is stated on tasks.

    So a task is a lost flag only when NO spec of its own carries the flag,
    while both of these say it was built:
      * outcomes.yaml records it as `built` — the run's own verdict, and
      * a commit exists naming that spec (`feat(<slug>)`) or its title, which
        the build's own commit subject embeds.
    A task the two disagree on is named for a human and left alone. This
    function never decides that something was built; it restores a record of
    a decision two independent artifacts already agree on.

    Read-only unless `apply=True`.
    """
    import subprocess

    root = Path(repo_dir).resolve()
    result: dict = {
        "workspace": str(root), "lost": [], "repaired": [],
        "unsupported": [], "superseded": [], "status": "clean",
    }
    outcomes_path = root / "product" / "outcomes.yaml"
    if not (root / "specs").is_dir() or not outcomes_path.exists():
        result["status"] = "not_a_built_workspace"
        return result

    recorded_built = {
        o["task_id"] for o in yaml.safe_load(
            outcomes_path.read_text(encoding="utf-8")
        ) or [] if o.get("status") == "built" and o.get("task_id")
    }
    already_flagged = built_task_ids(root)  # one definition of built, shared
    log = subprocess.run(
        ["git", "log", "--pretty=%s"], cwd=root, capture_output=True,
        text=True, timeout=60,
    ).stdout

    # task id → its unbuilt specs, so the decision is made per task
    unbuilt: dict[str, list[tuple[Path, dict]]] = {}
    for spec_file in sorted((root / "specs").glob("*/spec.yaml")):
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if data.get("built"):
            continue
        marker = TASK_MARKER.search(str(data.get("request", "")))
        if marker:
            unbuilt.setdefault(marker.group(1), []).append((spec_file, data))

    for task_id, specs in sorted(unbuilt.items()):
        if task_id not in recorded_built:
            continue  # genuinely not built — nothing to restore
        if task_id in already_flagged:
            # Another spec for this task carries the flag: these are the
            # leftovers of a re-plan, and the task is not at risk.
            result["superseded"].extend(
                {"slug": f.parent.name, "task_id": task_id} for f, _ in specs
            )
            continue
        # Repair the spec the commits actually name; with several candidates
        # and no commit naming any of them, repair nothing and say so.
        supported = [
            (f, d) for f, d in specs
            if f"feat({f.parent.name})" in log
            or (str(d.get("title") or "").strip() and str(d["title"]).strip() in log)
        ]
        if not supported:
            result["unsupported"].append(
                {"slug": specs[0][0].parent.name, "task_id": task_id}
            )
            continue
        spec_file = supported[0][0]
        entry = {"slug": spec_file.parent.name, "task_id": task_id}
        result["lost"].append(entry)
        if apply:
            text = spec_file.read_text(encoding="utf-8")
            if "built: false" in text:
                text = text.replace("built: false", "built: true", 1)
            else:  # key absent entirely — append rather than rewrite the YAML
                text = text.rstrip("\n") + "\nbuilt: true\n"
            spec_file.write_text(text, encoding="utf-8")
            result["repaired"].append(entry)

    if result["unsupported"]:
        result["status"] = "needs_a_human"
    elif result["repaired"]:
        result["status"] = "repaired"
    elif result["lost"]:
        result["status"] = "lost_flags_found"
    return result


def next_tasks(repo_dir: str | Path) -> list[Task]:
    """Tasks not yet built whose dependencies all are — the work queue view."""
    plan = load_plan(repo_dir)
    done = built_task_ids(repo_dir)
    return [
        t for t in plan.tasks
        if t.id not in done and all(d in done for d in t.depends_on)
    ]
