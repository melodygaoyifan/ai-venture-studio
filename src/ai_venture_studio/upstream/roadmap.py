"""The roadmap (ADR-048) — the founder describes the product, the system
proposes the increments.

The granularity rule this system runs on is "one FDR = one thing", and
until now it was enforced on the FOUNDER. They arrive with a paragraph —
"I want the thing my building's group-buy runs on" — and the product's
answer is: split that into twelve small documents yourself, in the right
order, and get each one small enough. That is unpaid labour, it is the
part a non-technical person is worst at, and it is the part a machine can
actually do. Everything downstream of an FDR is automated; the one step
left manual is the one requiring judgment the founder does not have.

So: paragraph in, an ordered list of small steps out, each one a request
`avs add` can take as written. The founder approves by tapping a sequence.

Two design calls keep it from becoming fiction:

**The roadmap is a proposal, not a contract.** A twelve-item roadmap
written on day one is stale by item three — the build teaches things the
paragraph did not know. `rederive` re-reads the remainder against the
requirement ledger after each increment lands, so a step the product now
satisfies is marked done rather than being built twice. A stale roadmap
the system still believes is worse than no roadmap at all: it has an
authoritative shape and a wrong answer inside it.

**A step is only "done" when something says so.** Re-derivation reuses the
reconciler (ADR-046), which returns `checked=False` rather than a clean
verdict when it could not judge. An unchecked step stays pending. Marking
work done because a check failed to run is the exact shape of defect
ADR-041 removed from the spec stage, and here it would silently drop a
feature out of the founder's plan.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider
from ai_venture_studio.providers.base import last_response_truncated
from ai_venture_studio.yamlx import extract_mapping

ROADMAP_MARKER = "product roadmapper for non-technical founders"

#: The most steps a proposal may hold. A roadmap longer than this is not a
#: plan, it is a backlog, and the founder cannot read it — and `rederive`
#: would spend a model call per step on every run.
MAX_STEPS = 12

#: How many pending steps one re-derivation will check against the ledger.
#: Each is a model call; a bound that drops work says so (ADR-039) and
#: `RederiveReport.unchecked` carries the count out.
RECHECK_CAP = 8

_SYSTEM = f"""You are the {ROADMAP_MARKER}. The founder has described a
product in one paragraph, in their own words. Break it into the SMALLEST
increments that can be built one at a time, in the order they should be
built.

Rules:
- Each step is ONE thing: one user-visible capability, buildable on its
  own, usable on its own. If a step needs the word "and" to describe it,
  it is two steps.
- Step 1 must be usable by itself — the thinnest version of the product
  that a real person could open and get value from. Never "set up the
  database" or "build the data model": those are not things a founder can
  look at, and the system builds its own scaffolding.
- 3 to {MAX_STEPS} steps. Stop at what the paragraph actually asked for; do
  not invent accounts, payments, analytics or admin panels the founder did
  not mention.
- `fdr` is the step written as a REQUEST, in the founder's own language
  and vocabulary — the words they would type if they were asking for just
  this one thing. Two or three sentences. Never technical.
- depends_on lists the ids of steps that must exist first. Most steps
  depend on the one before; a step that genuinely does not, depends on
  nothing. No cycles.

Respond with ONLY YAML:
steps:
  - id: S-001
    title: short name, in the founder's language
    fdr: |
      the request, two or three sentences
    depends_on: []
"""


class Step(BaseModel):
    id: str
    title: str
    fdr: str
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"
    note: str = Field(
        default="",
        description="why it is no longer pending — which requirement ids "
        "the product already satisfies it with, or which FDR built it.",
    )


class Roadmap(BaseModel):
    #: Whether the proposal actually ran and produced a readable answer.
    #: Same reason as `Reconciliation.checked`: an empty step list with
    #: `checked=True` says "this product needs nothing built", which is
    #: never true and is exactly what a parse failure looks like.
    checked: bool = False
    described: str = ""
    steps: list[Step] = Field(default_factory=list)
    note: str = ""

    @property
    def pending(self) -> list[Step]:
        return [s for s in self.steps if s.status == "pending"]

    @property
    def done(self) -> list[Step]:
        return [s for s in self.steps if s.status == "done"]


class RederiveReport(BaseModel):
    """What one re-derivation learned. `unchecked` is the honest half: the
    steps nobody looked at, because the cap was reached or because the
    reconciler could not read its own answer."""

    marked_done: list[str] = Field(default_factory=list)
    still_pending: list[str] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)


def roadmap_path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / "product" / "roadmap.yaml"


def load(repo_dir: str | Path) -> Roadmap | None:
    path = roadmap_path(repo_dir)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Roadmap.model_validate(data)


def save(repo_dir: str | Path, roadmap: Roadmap) -> Path:
    path = roadmap_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(roadmap.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _ordered(steps: list[Step]) -> list[Step] | None:
    """Steps in an order that respects depends_on, or None on a cycle.

    A stable topological sort rather than a refusal: a model that lists a
    prerequisite second has got the ORDER wrong, not the plan, and the
    order is the one part of this that code can fix without guessing. A
    cycle is different — there is no order that satisfies it, and picking
    one would hand the founder a sequence that cannot be built.
    """
    remaining = list(steps)
    placed: list[Step] = []
    done: set[str] = set()
    while remaining:
        ready = [s for s in remaining if all(d in done for d in s.depends_on)]
        if not ready:
            return None
        for step in ready:
            placed.append(step)
            done.add(step.id)
        remaining = [s for s in remaining if s.id not in done]
    return placed


def propose(
    described: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> Roadmap:
    """An ordered list of small, individually buildable steps.

    Returns `checked=False` — never an empty plan presented as a finished
    one — when the answer will not parse, was cut off mid-list, named no
    step, or describes a dependency cycle.
    """
    if not described.strip():
        return Roadmap(checked=False, note="nothing was described")
    raw = get_provider(provider).complete(
        model=model,
        system=_SYSTEM,
        user=f"<product>\n{described.strip()}\n</product>",
        max_tokens=4096,
    )
    if last_response_truncated():
        return Roadmap(
            checked=False, described=described,
            note="the roadmap was cut off at the output limit — a partial "
            "list of steps is not a plan for the product",
        )
    try:
        data = extract_mapping(raw, ("steps",))
    except ValueError as exc:
        return Roadmap(
            checked=False, described=described,
            note=f"roadmap output did not parse: {exc}",
        )
    steps: list[Step] = []
    for index, entry in enumerate(data.get("steps") or [], start=1):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        fdr = str(entry.get("fdr", "")).strip()
        if not title or not fdr:
            # A step with no request in it is a heading, and `avs add`
            # cannot be handed a heading.
            continue
        steps.append(
            Step(
                id=f"S-{index:03d}", title=title, fdr=fdr,
                depends_on=[str(d).strip() for d in (entry.get("depends_on") or [])],
            )
        )
        if len(steps) >= MAX_STEPS:
            break
    if not steps:
        return Roadmap(
            checked=False, described=described,
            note="the roadmap named no buildable step",
        )
    known = {s.id for s in steps}
    for step in steps:
        # An edge to a step that was dropped or never existed points at
        # nothing; keeping it would deadlock `next_step` forever.
        step.depends_on = [d for d in step.depends_on if d in known and d != step.id]
    ordered = _ordered(steps)
    if ordered is None:
        return Roadmap(
            checked=False, described=described,
            note="the proposed steps depend on each other in a loop, so no "
            "order builds them",
        )
    return Roadmap(checked=True, described=described, steps=ordered)


def next_step(roadmap: Roadmap) -> Step | None:
    """The step to build now: the first pending one whose prerequisites are
    already done."""
    done = {s.id for s in roadmap.steps if s.status == "done"}
    for step in roadmap.steps:
        if step.status == "pending" and all(d in done for d in step.depends_on):
            return step
    return None


def rederive(
    repo_dir: str | Path,
    roadmap: Roadmap,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    cap: int = RECHECK_CAP,
) -> RederiveReport:
    """Re-read the remainder against what the product now promises.

    Mutates `roadmap` in place and returns what it learned. A step the
    ledger already satisfies — the reconciler calls it a duplicate — is
    marked done, with the requirement ids in its note. Anything the
    reconciler could not judge stays pending and is named in `unchecked`:
    the founder is better served by building a step twice than by having
    it silently vanish from their plan.
    """
    from ai_venture_studio.upstream import reconcile as _rec
    from ai_venture_studio.upstream.requirements import relevant, sync_ledger

    # The ledger is derived, and re-deriving the roadmap against a stale one
    # is the exact failure this function exists to prevent: every step would
    # read as unbuilt because nothing had written down that they were.
    sync_ledger(repo_dir)
    report = RederiveReport()
    pending = roadmap.pending
    for step in pending[:cap]:
        slice_ = relevant(repo_dir, step.fdr)
        if not slice_.shown:
            report.still_pending.append(step.id)
            continue
        verdict = _rec.reconcile(step.fdr, slice_, provider=provider, model=model)
        if not verdict.checked:
            report.unchecked.append(step.id)
            continue
        duplicates = verdict.duplicates
        if duplicates:
            step.status = "done"
            step.note = "already promised by " + ", ".join(
                r.requirement_id for r in duplicates
            )
            report.marked_done.append(step.id)
        else:
            report.still_pending.append(step.id)
    report.unchecked.extend(s.id for s in pending[cap:])
    return report
