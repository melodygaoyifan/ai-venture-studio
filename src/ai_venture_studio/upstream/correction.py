"""M3 — the correction loop: "这不是我要的", said after USING the product.

The founder describes what's wrong in plain words. The system maps the
complaint to the responsible built spec, decides whether it's a repair
(the build violates the spec/intent) or a scope change (the spec itself
must change), and drives the existing machinery: repairs go through the
bounded fix path; scope changes go through the SCR channel — the
founder's complaint IS the human authorization, and it is recorded on the
SCR verbatim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel

from ai_venture_studio.providers import get_provider
from ai_venture_studio.testing import _pytest_in_subprocess, combine_reports, run_js_tests
from ai_venture_studio.upstream.build import _write_files
from ai_venture_studio.upstream.spec import (
    approve_scr,
    load_spec,
    raise_scr,
    write_pending_amendment,
)
from ai_venture_studio.yamlx import extract_mapping

CORRECTION_MARKER = "correction router for a founder's plain-language complaint"

MAX_REPAIR_ITERATIONS = 3

_ROUTER_SYSTEM = f"""You are the {CORRECTION_MARKER}. A founder writes in
their own words and often raises SEVERAL separate problems in one message.
Split the complaint into every distinct issue it contains, and map each one
to the ONE feature below responsible for it.

One issue per problem the founder is reporting. Two sentences about the
same broken thing are ONE issue; two problems that live in different
features are always TWO. Never drop an issue because it is minor, and
never invent one the founder did not raise.

Classify each issue:

- kind: fix — the built product violates the feature's stated criteria or
  obvious intent (wrong text, broken behavior, missing wiring)
- kind: scope_change — the founder wants something the criteria never
  promised (the spec itself must change)

`quote` MUST be copied character-for-character from the complaint — the
founder's own words for that issue, not your summary of them. A quote that
is not a literal span of what they wrote is discarded.

Respond with ONLY YAML, issues in the order the founder raised them:
issues:
  - quote: the founder's own words for this issue, verbatim
    spec_slug: ...
    kind: fix|scope_change
    instruction: one concrete sentence for the implementer, in English
"""


CHANGE_MARKER = "change drafter for a founder's requirement change"

_CHANGE_SYSTEM = f"""You are the {CHANGE_MARKER}, writing for a
non-technical founder. They said what they want different about ONE feature
of their product. Write the detail they did not, so they only have to react
to a draft instead of specifying one.

You are given the feature's CURRENT acceptance criteria. Return the criteria
the feature should have AFTER the change — the complete list, not a diff:
carry over every criterion the change does not touch, reword the ones it
does, add what is newly promised, drop only what the founder is replacing.

Write `summary` in the founder's own language, one sentence, describing what
will be different for someone USING the product. No file names, no jargon.

`assumptions` are the decisions you had to make because they did not say —
each a flat statement they can disagree with ("deleting a task asks for
confirmation first"), never a question. Empty when they truly left nothing
open. Never invent scope: an assumption narrows what they asked for, it does
not add a feature next to it.

Respond with ONLY YAML:
summary: one plain sentence
criteria:
  - the complete post-change acceptance criteria, one per line
assumptions:
  - flat statements of what you decided for them
fdr: |
  # <feature title>
  A short feature brief for the implementer: what changes, for whom, and
  what "done" looks like. Plain prose, no headings beyond the title.
"""


class ChangePlan(BaseModel):
    """What a requirement change will do, drafted before anything is built.

    This is the object the founder confirms. It is deliberately the SAME
    object that later amends the spec: `criteria` is what they approved and
    `criteria` is what gets written, with no second model call in between
    to reinterpret it.
    """

    spec_slug: str
    summary: str
    criteria: list[str] = []
    assumptions: list[str] = []
    fdr: str = ""
    #: The founder's OWN words for this change, carried so the SCR records
    #: them rather than the model's `summary`. ADR-U02 makes the complaint
    #: the human authorization; a paraphrase filed as the authorization is
    #: a record of a decision nobody made.
    words: str = ""


class CorrectionResult(BaseModel):
    status: str  # fixed | change_planned | scr_raised | error
    spec_slug: str = ""
    kind: str = ""
    detail: str = ""
    commit: str | None = None
    #: Present only on `change_planned` — the drafted change awaiting the
    #: founder's yes. Nothing has been written to the workspace yet.
    plan: ChangePlan | None = None


class CorrectionRoute(BaseModel):
    """One issue the founder raised, routed to the feature answerable for it.

    Split out so a UI can SHOW the decision and let the founder confirm or
    reword it — "small fix, repaired directly" and "new requirement, its
    own small build" are very different promises and the founder is the
    only one who knows which they meant. There is exactly one router; this
    is it, and `run_correction` takes the same object back rather than
    classifying a second time (a second call could decide differently, and
    then what was confirmed is not what ran).

    A message carrying three problems produces three of these. One route is
    still one repair against one spec — that is what keeps a correction
    auditable — but the SPLIT happens before the repair rather than by
    quietly answering the first problem and discarding the rest.
    """

    quote: str = ""    # the founder's own words for THIS issue, verbatim
    spec_slug: str
    kind: str          # fix | scope_change
    instruction: str

    def words(self, whole: str) -> str:
        """The founder's words this route answers for.

        Falls back to the whole message when the router gave no usable
        quote, because a repair prompt and an SCR must always be able to
        show real words — never a paraphrase, and never nothing.
        """
        return self.quote or whole


class CorrectionRouteError(RuntimeError):
    """The complaint could not be routed — nothing is built yet, the model
    did not answer in the envelope, or it named a spec that does not
    exist. Raised rather than returned so a caller cannot mistake it for a
    decision."""


def _is_verbatim(quote: str, complaint: str) -> bool:
    """Is `quote` a real span of what the founder wrote?

    Same rule the intake extractor applies to a "said" value, for the same
    reason: a model asked to quote will sometimes summarise instead, and a
    summary shown back under the heading "Your words" is a lie the founder
    has no way to catch. Whitespace is normalised because YAML re-wraps
    long lines; nothing else is forgiven.
    """
    squash = " ".join(quote.split())
    return bool(squash) and squash in " ".join(complaint.split())


def route_complaint(
    repo_dir: str | Path,
    complaint: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    criterion: str = "",
    only_slug: str = "",
) -> list[CorrectionRoute]:
    """Split a plain-language complaint into its issues and route each one.

    Returns one route per distinct problem the founder raised, in the order
    they raised them — a list, always, because a founder describing three
    things wrong with their product is the normal case and answering only
    the first of them (which is what a single return value invited) loses
    the other two with nothing on screen to say so.

    `criterion` is the acceptance row the founder pressed "Wrong" on, when
    the complaint came from the Try-it page. It is passed as context, never
    merged into the complaint: the founder's words stay theirs, verbatim,
    all the way to the SCR that records them.

    `only_slug` is the feature the founder pointed AT — they pressed
    "Change this" on one card, so which feature is meant is not a question
    and the router is shown that one spec and nothing else. It cannot then
    pick a different one, which is the whole reason the card exists. What
    stays a judgment is repair-or-scope-change: pressing a button beside a
    feature says which feature, never which kind, and forcing a kind here
    would silently decide the one thing the founder did not say.
    """
    root = Path(repo_dir).resolve()
    specs = built_specs(root)
    if only_slug:
        specs = [s for s in specs if s["slug"] == only_slug]
        if not specs:
            raise CorrectionRouteError(f"{only_slug!r} is not a built feature")
    if not specs:
        raise CorrectionRouteError("nothing built yet")

    raw = get_provider(provider).complete(
        model=model,
        system=_ROUTER_SYSTEM,
        user=yaml.safe_dump(specs, sort_keys=False, allow_unicode=True)
        + (f"\n<failed_criterion>\n{criterion}\n</failed_criterion>" if criterion else "")
        + f"\n<complaint>\n{complaint}\n</complaint>",
        # Room for every issue: a truncated list parses as valid YAML that
        # is quietly short, which is the exact failure this split exists to
        # end. 512 was enough for one issue and nothing more.
        max_tokens=2048,
    )
    try:
        answer = extract_mapping(raw, ("issues", "spec_slug"))
    except ValueError as exc:
        raise CorrectionRouteError(str(exc)) from exc
    # A router that answers with a bare single issue is still answering;
    # accept it rather than failing the founder over an envelope detail.
    raw_issues = answer.get("issues")
    if not isinstance(raw_issues, list):
        raw_issues = [answer] if answer.get("spec_slug") else []
    known = {s["slug"] for s in specs}
    routes: list[CorrectionRoute] = []
    for issue in raw_issues:
        if not isinstance(issue, dict):
            continue
        slug = str(issue.get("spec_slug", ""))
        if slug not in known:
            # Loud, not partial. Keeping the routable issues and dropping
            # this one would be the original defect wearing a plural.
            raise CorrectionRouteError(f"router chose unknown spec {slug!r}")
        quote = str(issue.get("quote", ""))
        routes.append(CorrectionRoute(
            quote=quote if _is_verbatim(quote, complaint) else "",
            spec_slug=slug,
            kind=str(issue.get("kind", "fix")),
            instruction=str(issue.get("instruction", complaint)),
        ))
    if not routes:
        raise CorrectionRouteError("router returned no issues")
    return routes


def built_specs(root: Path) -> list[dict]:
    """Every feature this product actually has, as the router sees them.

    Public because the Studio renders its feature cards from this exact
    list: the cards and the routing targets are then the same objects by
    construction, so a card cannot offer to change something the router
    would refuse to route to.
    """
    specs = []
    for spec_file in sorted(root.glob("specs/*/spec.yaml")):
        data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        if data.get("built"):
            specs.append(
                {"slug": data["slug"], "title": data.get("title"),
                 "criteria": data.get("criteria", [])}
            )
    return specs


def criterion_owners(root: Path) -> dict[str, str]:
    """criterion text → the feature that owns it, for the ones that are a
    FACT rather than a guess.

    The Try-it rows come from ACCEPTANCE.md and VERIFICATION.md, and the
    acceptance walkthrough is written by a model in plain language, so most
    rows will match nothing here. That is the intended outcome: a row with
    no entry is routed by the router, which is good at it. A WRONG
    pre-scope would be worse than none, because it skips the router exactly
    when it was needed — so a criterion claimed by two features is dropped
    rather than assigned to the first one found.
    """
    owners: dict[str, str] = {}
    for spec in built_specs(root):
        slug = str(spec["slug"])
        for criterion in spec.get("criteria") or []:
            key = " ".join(str(criterion).split())
            if not key:
                continue
            # "" marks a criterion two features both claim. Repeating it
            # within ONE spec is not a conflict — that is still one owner.
            owners[key] = slug if owners.get(key, slug) == slug else ""
    return {text: slug for text, slug in owners.items() if slug}


def draft_change(
    repo_dir: str | Path,
    route: CorrectionRoute,
    complaint: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
) -> ChangePlan:
    """Turn "make the cart remember things" into something buildable.

    Pure: reads the spec, calls the model, returns. Nothing is written and
    no SCR is raised, so a founder who reads the draft and closes the tab
    leaves the workspace exactly as they found it.
    """
    root = Path(repo_dir).resolve()
    spec = load_spec(root, route.spec_slug)
    raw = get_provider(provider).complete(
        model=model,
        system=_CHANGE_SYSTEM,
        user=yaml.safe_dump(
            {"feature": spec.title, "current_criteria": list(spec.criteria)},
            sort_keys=False, allow_unicode=True,
        )
        + f"\n<founder_words>\n{route.words(complaint)}\n</founder_words>"
        + f"\n<instruction>\n{route.instruction}\n</instruction>",
        max_tokens=4096,
    )
    try:
        data = extract_mapping(raw, ("summary",))
    except ValueError as exc:
        raise CorrectionRouteError(f"could not draft the change: {exc}") from exc
    criteria = [str(c).strip() for c in (data.get("criteria") or []) if str(c).strip()]
    # A draft that would erase the contract is not a draft. Falling back to
    # the spec's existing criteria keeps the change buildable and the
    # acceptance list intact; the FDR still carries what must change.
    if not criteria:
        criteria = list(spec.criteria)
    return ChangePlan(
        spec_slug=route.spec_slug,
        summary=str(data.get("summary", "")).strip() or route.instruction,
        criteria=criteria,
        assumptions=[
            str(a).strip() for a in (data.get("assumptions") or []) if str(a).strip()
        ],
        fdr=str(data.get("fdr", "")).strip()
        or f"# {spec.title}\n\n{route.words(complaint)}\n\n{route.instruction}\n",
        words=route.words(complaint),
    )


def apply_change(repo_dir: str | Path, plan: ChangePlan) -> Path:
    """The founder said go. Authorize the spec change and stage the build.

    Three things, in this order, because the order is what makes it safe:

    1. The SCR is raised and approved HERE, not at classification time. The
       founder's own words are the human authorization, recorded verbatim
       (ADR-U02) — `plan.words`, never `plan.summary`, because a paraphrase
       filed as the authorization is a record of a decision nobody made —
       and the grant only exists once they have actually agreed.
    2. The amendment is PARKED, not applied. `run_feature` spends it only if
       the build succeeds — a spec promising behaviour that failed to build
       would be worse than the stale spec this whole path exists to fix.
    3. The FDR is written last, so the file the builder consumes never
       exists without the authorization behind it.

    Returns the FDR path for `run_feature`.
    """
    root = Path(repo_dir).resolve()
    scr_path = raise_scr(root, plan.spec_slug, f"founder correction: {plan.words}")
    approve_scr(root, int(scr_path.stem.split("-")[1]))
    write_pending_amendment(root, plan.spec_slug, plan.criteria, plan.fdr)
    fdr_path = root / ".mas" / "pending-change.md"
    fdr_path.parent.mkdir(parents=True, exist_ok=True)
    fdr_path.write_text(plan.fdr, encoding="utf-8")
    return fdr_path


def run_correction(
    repo_dir: str | Path,
    complaint: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    criterion: str = "",
    route: CorrectionRoute | None = None,
) -> CorrectionResult:
    """Act on ONE routed issue — routing the complaint first if needed.

    `route` is how a confirmed classification executes: what the founder
    saw and agreed to is what runs, rather than a second router call that
    might land somewhere else.

    One call repairs one issue against one spec, which is what makes a
    correction auditable. When no route is given and the complaint turns
    out to hold several issues, this REFUSES rather than picking one —
    `run_corrections` is the entry point that handles all of them.
    """
    root = Path(repo_dir).resolve()
    if route is None:
        try:
            routes = route_complaint(
                root, complaint, provider=provider, model=model,
                criterion=criterion,
            )
        except CorrectionRouteError as exc:
            return CorrectionResult(status="error", detail=str(exc))
        if len(routes) > 1:
            return CorrectionResult(
                status="error",
                detail=f"complaint holds {len(routes)} separate issues "
                f"({', '.join(r.spec_slug for r in routes)}) — "
                "use run_corrections so none is dropped",
            )
        route = routes[0]
    slug = route.spec_slug
    kind = route.kind
    instruction = route.instruction
    # From here on the founder's words for THIS issue, not the whole
    # message: an implementer handed two unrelated problems it has no
    # sources for makes changes nobody asked it for, and an SCR is a
    # record of one decision.
    complaint = route.words(complaint)
    if slug not in {s["slug"] for s in built_specs(root)}:
        return CorrectionResult(
            status="error", detail=f"router chose unknown spec {slug!r}"
        )

    if kind == "scope_change":
        # A scope change is a BUILD, not a note. It used to raise an SCR,
        # approve it, and hand the founder "re-run `avs add`/`spec` for
        # 'cart' to regenerate" — a terminal instruction, in a browser, to
        # someone who has never opened a terminal. Nothing was built, the
        # page said it worked, and the same complaint came back.
        #
        # No SCR is raised here. Raising one at classification time left an
        # approved, unconsumed grant behind whenever the founder looked at
        # the plan and walked away — a free pass to edit a frozen spec,
        # banked by someone who decided NOT to change anything. The grant is
        # raised in `apply_change`, when they say go.
        plan = draft_change(
            root, route, complaint, provider=provider, model=model,
        )
        return CorrectionResult(
            status="change_planned", spec_slug=slug, kind=kind,
            detail=plan.summary, plan=plan,
        )

    # Repair path: complaint + spec + implicated sources → smallest change.
    # Bounded ITERATION against the suite (same discipline as build, found
    # necessary by the linkly shakedown): failure output feeds the next
    # attempt; the workspace reverts only after the attempts are exhausted.
    spec = load_spec(root, slug)
    from ai_venture_studio.upstream.build import _related_sources  # reuse mention parser

    related = _related_sources(root, spec)
    base_user = (
        f"<complaint>\n{complaint}\n</complaint>\n\n"
        + (f"<failed_criterion>\n{criterion}\n</failed_criterion>\n\n"
           if criterion else "")
        + f"<instruction>\n{instruction}\n</instruction>\n\n"
        f"<spec>\n{yaml.safe_dump(spec.model_dump(include={'title', 'design', 'criteria'}), sort_keys=False, allow_unicode=True)}</spec>\n\n"
        + related
    )
    allowed_tests = {s.path for s in spec.test_skeletons}
    feedback = ""
    written: list[str] = []
    for iteration in range(1, MAX_REPAIR_ITERATIONS + 1):
        current = ""
        if written:
            current = "\n\n".join(
                f'<current_file path="{rel}">\n'
                + (root / rel).read_text(encoding="utf-8", errors="replace")
                + "\n</current_file>"
                for rel in written
                if (root / rel).is_file()
            )
        raw_fix = get_provider(provider).complete(
            model=model,
            system="You are the single-writer implementer in a greenfield product "
            "system, repairing ONE founder complaint. Smallest change; complete "
            "file contents back; never touch tests you did not author.\n\n"
            "Respond with ONLY YAML:\nfiles:\n  - path: ...\n    new_content: |\n      ...",
            user=base_user
            + (f"\n\n{current}" if current else "")
            + (f"\n\n<test_failure attempt=\"{iteration - 1}\">\n{feedback}\n</test_failure>" if feedback else ""),
            max_tokens=16384,
        )
        try:
            data = extract_mapping(raw_fix, ("files",))
            batch, kept = _write_files(root, data.get("files") or [], allowed_test_paths=allowed_tests)
        except ValueError as exc:
            feedback = f"your previous response failed: {exc}"
            continue
        if not batch:
            feedback = (
                "your files were discarded as weakened skeleton tests: " + "; ".join(kept)
                if kept
                else "you returned no files; return the corrected file contents"
            )
            continue
        written = sorted(set(written) | set(batch))
        report = combine_reports(_pytest_in_subprocess(root), run_js_tests(root))
        if report.status not in ("failed", "error"):
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, timeout=60)
            committed = subprocess.run(
                ["git", "-c", "user.email=autoproduct@local", "-c", "user.name=autoproduct",
                 "commit", "-qm", f"fix({slug}): founder correction — {complaint[:60]}"],
                cwd=root, capture_output=True, text=True,
            )
            if committed.returncode != 0:
                return CorrectionResult(status="error", spec_slug=slug, kind=kind,
                                        detail="no effective change produced")
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                                 capture_output=True, timeout=60, text=True).stdout.strip()
            return CorrectionResult(
                status="fixed", spec_slug=slug, kind=kind, commit=sha,
                detail=f"repaired in {iteration} attempt(s); files: {', '.join(written)}",
            )
        feedback = report.detail or report.summary
    subprocess.run(["git", "checkout", "--", "."], cwd=root, capture_output=True, timeout=60)
    subprocess.run(["git", "clean", "-fdq", "--exclude=.mas", "--exclude=data"],
                   cwd=root, capture_output=True, timeout=60)
    return CorrectionResult(
        status="error", spec_slug=slug, kind=kind,
        detail=f"repair still broke the suite after {MAX_REPAIR_ITERATIONS} "
        f"attempt(s) ({feedback[:120]}); workspace reverted",
    )


def run_corrections(
    repo_dir: str | Path,
    complaint: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-opus-4-8",
    criterion: str = "",
    routes: list[CorrectionRoute] | None = None,
) -> list[CorrectionResult]:
    """Act on every issue in the complaint, and report on every one.

    One result per issue, in the founder's order, whatever happened to it.
    Issues are independent — one that cannot be repaired must not stop the
    ones after it, or a founder who raised three problems would be back to
    getting one answered.
    """
    root = Path(repo_dir).resolve()
    if routes is None:
        try:
            routes = route_complaint(
                root, complaint, provider=provider, model=model,
                criterion=criterion,
            )
        except CorrectionRouteError as exc:
            return [CorrectionResult(status="error", detail=str(exc))]
    results = []
    for route in routes:
        try:
            results.append(run_correction(
                root, complaint, provider=provider, model=model,
                criterion=criterion, route=route,
            ))
        except Exception as exc:  # noqa: BLE001 — a result, never a lost issue
            results.append(CorrectionResult(
                status="error", spec_slug=route.spec_slug, kind=route.kind,
                detail=f"{type(exc).__name__}: {exc}",
            ))
    return results
