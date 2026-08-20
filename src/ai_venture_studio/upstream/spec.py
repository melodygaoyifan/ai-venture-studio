"""Spec stage (§13, built first per doc 14) — the anchor artifact.

generate → deterministic checks (ears_lint + coverage matrix) → critique
voters (Testability, Ambiguity) → bounded revision (fresh context, ≤2) →
Gate U3 (human approval). A spec that fails its deterministic checks after
revision is saved as `blocked`, never silently approved.

The spec is what `build` implements test-first and what the review stage
later verifies against — machine-checkable EARS criteria, a test skeleton
per criterion, and the domain profile's extras baked in.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider
from ai_venture_studio.providers.base import last_response_truncated
from ai_venture_studio.upstream import ears
from ai_venture_studio.upstream.workspace import Project, load_project
from ai_venture_studio.yamlx import extract_mapping

SPECWRITER_MARKER = "spec writer for a greenfield product system"

MAX_REVISIONS = 2


class GroundingError(RuntimeError):
    """Required context never reached the spec writer (§13.25.2, §11.18.3)."""


class TestSkeleton(BaseModel):
    path: str
    purpose: str
    covers: list[int] = Field(description="indices into criteria (0-based)")


class Spec(BaseModel):
    slug: str
    title: str
    status: str = "proposed"  # proposed | approved | blocked
    request: str
    profile: str
    design: str
    criteria: list[str]
    test_skeletons: list[TestSkeleton]
    lint_issues: list[dict] = Field(default_factory=list)
    critic_issues: list[dict] = Field(default_factory=list)
    block_reasons: list[str] = Field(
        default_factory=list,
        description="why status is 'blocked' — empty when proposed; a "
        "gap-blocked spec used to surface as the useless 'lint 0 issue(s)'",
    )
    revisions: int = 0
    built: bool = False
    approved_hash: str = Field(
        default="",
        description="§13.35.5: hash of the contract slice at Gate U3 approval. "
        "A later mismatch means someone edited a frozen spec outside the SCR "
        "channel, and the build refuses the unratified fork.",
    )


_WRITER_SYSTEM = f"""You are the {SPECWRITER_MARKER}. Produce a buildable
feature spec for the request, honoring the project constraints and profile
extras provided.

Rules:
- Acceptance criteria MUST use EARS syntax (The/When/While/If-then/Where
  ... shall ...) and measurable conditions — never vague words like
  "fast" or "user-friendly".
- Every criterion must be covered by at least one test skeleton (covers
  lists criterion indices, 0-based). Test paths live under tests/.
- The design section states the module layout and, where the profile
  demands it, API contracts / domains / permissions.
- Smallest spec that satisfies the request; no speculative features.
- The request's scope is law: a capability it explicitly excludes or
  defers must not appear in the spec. A profile constraint about an
  excluded capability is inapplicable — never a reason to add it.
- Test skeletons assert observable behavior (status codes, response
  bodies, rendered text, the PRESENCE of a labeled control) — never
  markup microstructure like specific id/class/attribute values.
- Where the project context includes a source_contract, that is the
  founder's LITERAL interface contract: reproduce its exact paths,
  methods, field names, and enumerated values verbatim in the design
  and criteria. Never rename, split, add to, or generalize them — a
  field the contract calls "name" stays "name"; a value it writes as
  "day5" stays the string "day5". The probes that judge the product
  are written from that contract, not from your spec.

Respond with ONLY YAML:
title: ...
design: |
  ...
criteria:
  - "When ..., the system shall ..."
test_skeletons:
  - path: tests/test_x.py
    purpose: ...
    covers: [0, 1]
"""

def contract_hash(spec: "Spec") -> str:
    """Hash of what Gate U3 actually approved: the contract a human read.

    Deliberately excludes bookkeeping (status, built, revisions, critic
    notes) — those move for legitimate reasons and would make every build
    look like a fork.
    """
    import hashlib

    payload = yaml.safe_dump(
        {
            "title": spec.title,
            "design": spec.design,
            "criteria": list(spec.criteria),
            "test_skeletons": [s.model_dump() for s in spec.test_skeletons],
        },
        sort_keys=True, allow_unicode=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hard_constraints(repo_dir: str | Path) -> str:
    """CLAUDE.md verbatim — the constraints every spec is bound by."""
    path = Path(repo_dir) / "CLAUDE.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _module_invariants(repo_dir: str | Path) -> str:
    from ai_venture_studio.upstream.build import _module_spec_context

    return _module_spec_context(Path(repo_dir))


def _spec_grounding_gate(repo_dir: str | Path, request: str, prompt: str) -> None:
    """Refuse to write a spec blind to its hard constraints (§13.25.2).

    Raises GroundingError rather than returning a blocked Spec: there is no
    artifact yet to attach block_reasons to, and a spec authored without its
    constraints is not a weak spec, it is one nobody can trust.
    """
    from ai_venture_studio.upstream.context_assembler import (
        assemble,
        verify_prompt_grounding,
    )

    slug = _slugify(str(request))
    manifest = assemble(
        repo_dir, slug, task_id=slug,
        # Pre-spec: constraints and module invariants are required; the spec
        # being written is not.
        required_kinds={"constraints", "module_spec"},
    )
    violations = verify_prompt_grounding(manifest, prompt)
    if violations:
        detail = "; ".join(f"{v.path} ({v.rule})" for v in violations[:4])
        raise GroundingError(
            f"spec generation refused: required context missing from the "
            f"writer's prompt — {detail}. A spec authored blind to its own "
            "hard constraints is not a weak spec, it is an untrustworthy one."
        )


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "feature"


def _foreign_skeletons(spec_data: dict, profile: str) -> list[str]:
    """Test skeletons in a language the product cannot run.

    The spec is where this has to be caught: a `tests/test_catalog_page.py`
    skeleton in a 小程序 spec makes the build gate demand a passing pytest
    run against a project that has no Python in it, and the task dies three
    iterations later on "pytest collected no tests" — a true sentence about
    the wrong thing. The build boundary refuses these too, but by then the
    approved spec is already asking for the impossible.
    """
    from ai_venture_studio.upstream.build import foreign_language_issue

    issues = []
    for skeleton in spec_data.get("test_skeletons") or []:
        path = str((skeleton or {}).get("path", "")) if isinstance(skeleton, dict) else ""
        if not path:
            continue
        issue = foreign_language_issue(profile, path)
        if issue:
            issues.append(f"test skeleton in the wrong language: {issue}")
    return issues


def _coverage_gaps(spec_data: dict) -> list[int]:
    covered = {
        i
        for skeleton in spec_data.get("test_skeletons", [])
        for i in skeleton.get("covers", [])
    }
    return [i for i in range(len(spec_data.get("criteria", []))) if i not in covered]


def _undelivered(spec_data: dict) -> list[str]:
    """What the writer failed to deliver AT ALL, as revision feedback.

    Emptiness is the worst outcome of this stage and — before this existed —
    also the quietest, because it passes every quality check by having
    nothing to check: `ears.lint_criteria([])` is clean, `_coverage_gaps`
    finds no uncovered criterion among zero criteria, and
    `_foreign_skeletons` finds no wrong-language skeleton among zero
    skeletons. All three came back empty, the revision loop's "good enough"
    break fired on a spec containing nothing, and the feedback sent to the
    writer never mentioned the one thing that was actually wrong. Two cases
    of bench run 15 blocked this way with `criteria: 0, skeletons: 0`.
    """
    if not spec_data.get("criteria"):
        return [
            "You returned NO acceptance criteria. The criteria list IS the "
            "spec — a response without it is not a partial answer, it is no "
            "answer. Return the whole YAML schema with a populated "
            "`criteria:` list."
        ]
    if not spec_data.get("test_skeletons"):
        return [
            "You returned criteria but NO test skeletons. Every criterion "
            "must be covered by at least one skeleton under tests/."
        ]
    return []


def run_spec_stage(
    repo_dir: str | Path,
    request: str,
    *,
    provider: str = "anthropic",
    writer_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
    source_contract: str = "",
    prior_failure: str = "",
) -> Spec:
    project: Project = load_project(repo_dir)
    if not source_contract:
        # The founder's FDR, when the workspace carries one, IS the
        # interface contract. Without it the writer only ever sees the
        # planner's paraphrase and re-invents field names and enums the
        # probes then reject (product-bench run 5, case 04: FDR said
        # {"name"} and "day5"; the built API demanded "direction" and
        # integer rounds — every probe 400'd).
        fdr_file = Path(repo_dir) / "FDR.md"
        if fdr_file.exists():
            source_contract = fdr_file.read_text(encoding="utf-8")
    provider_impl = get_provider(provider)
    profile = project.profile_data
    design_memory = ""
    design_path = Path(repo_dir) / "product" / "design.md"
    if design_path.exists():
        design_memory = design_path.read_text(encoding="utf-8")[-4000:]
    # CLAUDE.md and the module invariants belong in the SPEC writer's prompt
    # too, not only the implementer's: a criterion that contradicts a module
    # invariant becomes a build that cannot satisfy both, and Code Review
    # then flags SPEC_DRIFT_UNDOCUMENTED against work nobody could have got
    # right. The grounding gate below is what surfaced this.
    hard_constraints = _hard_constraints(repo_dir)
    module_block = _module_invariants(repo_dir)
    context = yaml.safe_dump(
        {
            "project": project.name,
            "profile": project.profile,
            "constraints": profile.get("constraints", []),
            "spec_extras": profile.get("spec_extras", []),
            "stack_hint": profile.get("stack_hint", ""),
        },
        sort_keys=False, allow_unicode=True,
    ) + (
        "\nproject_hard_constraints: |\n"
        + "".join(f"  {line}\n" for line in hard_constraints.splitlines())
        if hard_constraints.strip() else ""
    ) + (
        "\nmodule_invariants: |\n"
        + "".join(f"  {line}\n" for line in module_block.splitlines())
        if module_block.strip() else ""
    ) + (
        f"\nexisting_architecture: |\n  (extend this — do not re-derive)\n{design_memory}"
        if design_memory
        else ""
    ) + (
        "\nsource_contract: |\n  (the founder's literal interface contract — "
        "reproduce exact paths, methods, field names, and values verbatim)\n"
        + "".join(f"  {line}\n" for line in source_contract[:3000].splitlines())
        if source_contract.strip()
        else ""
    )

    # Grounding (§13.25.2): the constraints and invariants this spec must not
    # violate have to actually BE in the prompt. The spec itself is not
    # required reading here — it is what we are about to write.
    _spec_grounding_gate(repo_dir, request, context)

    # A retry that does not know why the last attempt died repeats it. The
    # observed loop: EARS rejects a phrasing → the task blocks → the retry
    # regenerates from the same inputs → the writer picks the same phrasing.
    # Handing the writer the previous failure is what makes the second
    # attempt a different attempt (product-bench: retries recovered t1/t2 and
    # t5/t9 only after the underlying rule changed — context is the cheap way
    # to change what the writer does without changing the rules).
    failure_block = (
        "\n\n<previous_attempt_failed note=\"an earlier attempt at this exact "
        "task failed for the reason below — understand the cause and take a "
        "different approach; repeating it will fail the same way\">\n"
        + prior_failure.strip()[:1500]
        + "\n</previous_attempt_failed>"
        if prior_failure.strip()
        else ""
    )

    feedback = ""
    spec_data: dict = {}
    lint: list = []
    critics: list[dict] = []
    truncated = False
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=writer_model,
            system=_WRITER_SYSTEM,
            user=f"<project>\n{context}</project>\n\n<request>\n{request}\n</request>"
            + failure_block
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=4096,
        )
        # A response that hit the output cap is a PARTIAL spec wearing the
        # shape of a whole one, and truncated YAML usually still parses — a
        # response cut off after `title:` is a valid mapping with zero
        # criteria. Every other writer stage asks this question (plan.py,
        # build.py, discover.py); this one did not, so a delivery failure
        # was reported as a content judgment ("no acceptance criteria").
        if last_response_truncated():
            truncated = True
            feedback = (
                "YOUR LAST RESPONSE WAS CUT OFF at the output limit, so the "
                "spec was incomplete and was discarded. Return the SAME spec "
                "with a shorter design section and terser criteria — every "
                "criterion, fewer words each."
            )
            spec_data, lint, critics = {}, [], []
            continue
        truncated = False
        try:
            spec_data = extract_mapping(raw, ("criteria", "title"))
        except ValueError:
            feedback = (
                "Your previous response was not a parseable YAML mapping. "
                "Respond with ONLY the YAML schema given, and double-quote "
                "every string value."
            )
            spec_data = {}
            lint, critics = [], []
            continue
        lint = ears.lint_criteria([str(c) for c in spec_data.get("criteria", [])])
        gaps = _coverage_gaps(spec_data)
        foreign = _foreign_skeletons(spec_data, project.profile)
        undelivered = _undelivered(spec_data)
        # Charter roster (doc 13 §25.1): Testability, Consistency,
        # Completeness, Ambiguity, InterfaceImpact — the two ad-hoc critic
        # prompts retired here (plan phase D13). The roster sees the same
        # slice the old critics did, plus the contract InterfaceImpact
        # judges fidelity against.
        from ai_venture_studio.product.stage_engine import run_critique_roster
        from ai_venture_studio.product.voter_gate import family_roots

        skills_root, _ = family_roots("spec")
        roster = run_critique_roster(
            "spec", "spec",
            yaml.safe_dump(
                {"criteria": spec_data.get("criteria", []),
                 "test_skeletons": spec_data.get("test_skeletons", []),
                 "design": str(spec_data.get("design", "")),
                 "source_contract": source_contract[:3000]},
                sort_keys=False, allow_unicode=True,
            ),
            str(repo_dir),
            provider_impl=provider_impl,
            voter_model=critic_model,
            leader_model=writer_model,
            skills_root=skills_root,
            det_findings=[i.model_dump() for i in lint],
        )
        critics = roster.as_issues()[:10]
        majors = [c for c in critics if c.get("severity") == "major"]
        if not undelivered and not lint and not gaps and not majors and not foreign:
            break
        feedback = yaml.safe_dump(
            {
                # First, and named so it cannot be read as one nit among
                # several: nothing else in this blob matters if the spec is
                # empty, and everything else in it is silent when it is.
                "you_returned_nothing": undelivered,
                "ears_lint": [i.model_dump() for i in lint],
                "uncovered_criteria_indices": gaps,
                "critic_majors": majors,
                "wrong_language_skeletons": foreign,
            },
            sort_keys=False, allow_unicode=True,
        )
    else:
        pass

    gaps = _coverage_gaps(spec_data)
    foreign = _foreign_skeletons(spec_data, project.profile)
    has_criteria = bool(spec_data.get("criteria"))
    status = (
        "proposed"
        if has_criteria and not lint and not gaps and not foreign
        else "blocked"
    )
    block_reasons: list[str] = []
    if status == "blocked":
        if not has_criteria:
            # Same empty spec, two different diagnoses. "no acceptance
            # criteria" reads as a judgment on what the writer wrote; when
            # the last response was cut off, nothing was written at all and
            # saying so is the difference between raising the cap and
            # rewriting the prompt.
            block_reasons.append(
                "spec writer response cut off at the output cap — no "
                "acceptance criteria were received"
                if truncated
                else "no acceptance criteria"
            )
        if foreign:
            block_reasons.append("; ".join(foreign))
        if lint:
            block_reasons.append(
                f"{len(lint)} EARS lint issue(s): "
                + "; ".join(str(i.problem)[:80] for i in lint[:3])
            )
        if gaps:
            block_reasons.append(
                f"criteria {', '.join(str(g) for g in gaps)} covered by no "
                "test skeleton"
            )
    slug = _slugify(str(spec_data.get("title") or request))

    # SCR guard (ADR-U02): overwriting a spec that has been BUILT is the
    # drift this system kills — the only legal channel is an approved SCR,
    # and each approval grants exactly one regeneration.
    existing_path = _spec_dir(repo_dir, slug) / "spec.yaml"
    if existing_path.exists():
        existing = yaml.safe_load(existing_path.read_text(encoding="utf-8")) or {}
        if existing.get("built") and not _scr_grant(repo_dir, slug):
            raise PermissionError(
                f"spec {slug!r} is built and frozen; changing it requires an "
                f"approved SCR: avs scr {slug} \"<reason>\" then "
                "avs scr-approve <n>"
            )
    spec = Spec(
        slug=slug,
        title=str(spec_data.get("title", request))[:120],
        status=status,
        request=request,
        profile=project.profile,
        design=str(spec_data.get("design", "")),
        criteria=[str(c) for c in spec_data.get("criteria", [])],
        test_skeletons=[
            TestSkeleton.model_validate(s) for s in spec_data.get("test_skeletons", [])
        ],
        lint_issues=[i.model_dump() for i in lint],
        critic_issues=critics,
        block_reasons=block_reasons,
        revisions=revision,
    )
    _save(repo_dir, spec)
    return spec


def _spec_dir(repo_dir: str | Path, slug: str) -> Path:
    return Path(repo_dir) / "specs" / slug


def _save(repo_dir: str | Path, spec: Spec) -> None:
    directory = _spec_dir(repo_dir, spec.slug)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "spec.yaml").write_text(
        yaml.safe_dump(spec.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    criteria = "\n".join(f"{i}. {c}" for i, c in enumerate(spec.criteria))
    skeletons = "\n".join(
        f"- `{s.path}` — {s.purpose} (covers {s.covers})" for s in spec.test_skeletons
    )
    (directory / "spec.md").write_text(
        f"# {spec.title}\n\nstatus: **{spec.status}** · profile: {spec.profile} · "
        f"revisions: {spec.revisions}\n\n## Design\n\n{spec.design}\n\n"
        f"## Acceptance criteria (EARS)\n\n{criteria}\n\n"
        f"## Test skeletons\n\n{skeletons}\n\n"
        f"Approve with: `avs spec-approve {spec.slug}` (Gate U3)\n",
        encoding="utf-8",
    )


def load_spec(repo_dir: str | Path, slug: str) -> Spec:
    path = _spec_dir(repo_dir, slug) / "spec.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no spec {slug!r} under {repo_dir}/specs")
    return Spec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _scr_dir(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".mas" / "scr"


def _scr_grant(repo_dir: str | Path, slug: str) -> bool:
    """An approved, unconsumed SCR for this spec grants one regeneration."""
    for path in sorted(_scr_dir(repo_dir).glob("SCR-*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("spec_slug") == slug and data.get("status") == "approved":
            data["status"] = "consumed"
            path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
            return True
    return False


def raise_scr(repo_dir: str | Path, slug: str, reason: str) -> Path:
    """ADR-U02: the only legal drift channel after a spec is built."""
    directory = _scr_dir(repo_dir)
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob("SCR-*.yaml"))) + 1
    path = directory / f"SCR-{number:03d}.yaml"
    path.write_text(
        yaml.safe_dump(
            {"number": number, "spec_slug": slug, "reason": reason, "status": "proposed"},
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def approve_scr(repo_dir: str | Path, number: int) -> dict:
    """The human half of the SCR channel."""
    path = _scr_dir(repo_dir) / f"SCR-{number:03d}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no SCR-{number:03d}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["status"] = "approved"
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return data


def approve_spec(repo_dir: str | Path, slug: str) -> Spec:
    """Gate U3 — the human acknowledgement that makes a spec buildable."""
    spec = load_spec(repo_dir, slug)
    if spec.status == "blocked":
        raise ValueError(
            f"spec {slug!r} is blocked by deterministic checks "
            f"(lint: {len(spec.lint_issues)}); fix and regenerate before approving"
        )
    spec.status = "approved"
    # Pin what this approval covered (§13.35.5). Editing the spec afterwards
    # is legal — it just stops being approved until an SCR ratifies it.
    spec.approved_hash = contract_hash(spec)
    _save(repo_dir, spec)
    return spec


def amend_spec_criteria(
    repo_dir: str | Path, slug: str, criteria: list[str]
) -> Spec:
    """Rewrite a built spec's acceptance criteria, spending one approved SCR.

    ADR-U02's channel finally has a consumer. `raise_scr` + `approve_scr`
    existed on one side and `_scr_grant` on the other, but the only thing
    that ever spent a grant was a full spec regeneration — so a founder
    correction classified as a scope change raised an SCR, approved it, and
    left it sitting there while the spec kept describing the behaviour they
    had just changed away from.

    Criteria ONLY. `test_skeletons` belong to the build that authored the
    tests now on disk; regenerating them here would orphan those files, and
    the feature build has already written its own tests against the new
    behaviour.

    The amendment re-stamps `approved_hash`, because re-stamping is what
    ratification MEANS. `contract_hash` covers criteria, so leaving the old
    stamp would make `_approval_drift` refuse every later build of this slug
    as "an unratified fork" (build.py) — the founder's approved change would
    quietly destroy the product's ability to build at all.
    """
    cleaned = [str(c).strip() for c in criteria if str(c).strip()]
    if not cleaned:
        # An empty amendment is never what anyone meant, and it would erase
        # the contract silently — the acceptance list is derived from it.
        raise ValueError(f"refusing to amend spec {slug!r} to zero criteria")
    spec = load_spec(repo_dir, slug)
    if spec.built and not _scr_grant(repo_dir, slug):
        raise PermissionError(
            f"spec {slug!r} is built and frozen; amending it requires an "
            f"approved SCR: avs scr {slug} \"<reason>\" then "
            "avs scr-approve <n>"
        )
    spec.criteria = cleaned
    spec.revisions += 1
    spec.approved_hash = contract_hash(spec)
    _save(repo_dir, spec)
    return spec


def _amendment_path(repo_dir: str | Path) -> Path:
    return Path(repo_dir) / ".mas" / "pending-amendment.yaml"


def _fdr_digest(fdr_text: str) -> str:
    import hashlib

    return hashlib.sha256(" ".join(fdr_text.split()).encode("utf-8")).hexdigest()


def write_pending_amendment(
    repo_dir: str | Path, slug: str, criteria: list[str], fdr_text: str
) -> Path:
    """Park an amendment until the build that earns it succeeds.

    The Studio confirms a change in one process and builds it in a detached
    subprocess minutes later, so the amendment cannot simply be applied at
    confirmation time: a build that fails would leave the spec promising
    behaviour that was never built, which is worse than the stale spec this
    fixes. Parking it on disk also means it survives the Studio being closed.

    Bound to the FDR by digest so a stale record can never attach itself to
    an unrelated build that happens to finish next.
    """
    path = _amendment_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"spec_slug": slug, "criteria": [str(c) for c in criteria],
             "fdr_digest": _fdr_digest(fdr_text)},
            sort_keys=False, allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def apply_pending_amendment(repo_dir: str | Path, fdr_text: str) -> Spec | None:
    """Spend a parked amendment iff THIS build is the one it was parked for.

    Returns the amended spec, or None when there is nothing to apply. The
    record is removed either way once it matches: a grant is spent once.
    """
    path = _amendment_path(repo_dir)
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        path.unlink(missing_ok=True)
        return None
    if data.get("fdr_digest") != _fdr_digest(fdr_text):
        return None
    path.unlink(missing_ok=True)
    try:
        return amend_spec_criteria(
            repo_dir, str(data.get("spec_slug", "")), data.get("criteria") or []
        )
    except (FileNotFoundError, PermissionError, ValueError):
        # The build succeeded; refusing to record it is not a reason to
        # report the founder's change as failed. The spec stays stale and
        # the SCR stays unspent — both visible, neither destructive.
        return None
