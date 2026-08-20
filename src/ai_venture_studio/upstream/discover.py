"""Discovery stage (§13.26) — the ProductBrief and the hypothesis ledger.

Every claim in a brief is a hypothesis tagged with an evidence class:
`measured` (you have data), `sourced` (someone credible published it), or
`assumed` (be honest). Untagged claims are a deterministic failure — the
charter's no-fabricated-user-evidence rule (§13.26.7) enforced at the
schema level. Talking to real users stays human work; the ledger is what
the Maintenance stage later reconciles against production telemetry.

Gate U1 (`brief-approve`) is the human problem-selection decision — the
system prepares options, never chooses (§README scope).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from ai_venture_studio.providers import get_provider, last_response_truncated
from ai_venture_studio.upstream import progress
from ai_venture_studio.upstream.workspace import load_project
from ai_venture_studio.yamlx import extract_mapping

BRIEFWRITER_MARKER = "product brief writer in a greenfield discovery stage"

EVIDENCE_CLASSES = {"measured", "sourced", "assumed"}
# Unparseable writer output consumes a revision like any critic round, and
# CJK briefs trip YAML parsing often enough that 2 total attempts is a coin
# flip (product-bench run 4, case 02: whole case dead on this budget).
MAX_REVISIONS = 3


class Hypothesis(BaseModel):
    statement: str
    evidence: str

    @field_validator("evidence")
    @classmethod
    def _known_class(cls, value: str) -> str:
        if value not in EVIDENCE_CLASSES:
            raise ValueError(f"evidence must be one of {sorted(EVIDENCE_CLASSES)}")
        return value


class Brief(BaseModel):
    title: str
    status: str = "proposed"  # proposed | approved | blocked
    problem: str
    target_user: str
    hypotheses: list[Hypothesis]
    scope_now: list[str] = Field(min_length=1)
    scope_later: list[str] = Field(default_factory=list)
    scope_never: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(min_length=1)
    critic_issues: list[dict] = Field(default_factory=list)
    revisions: int = 0


_WRITER_SYSTEM = f"""You are the {BRIEFWRITER_MARKER}. Turn the idea into a
one-page ProductBrief the human can approve or reject.

Rules:
- Every hypothesis carries an evidence class: measured | sourced | assumed.
  Never fabricate user evidence — when in doubt, tag it `assumed`.
- scope_now is the smallest product worth shipping; be aggressive about
  pushing items to scope_later / scope_never.
- success_metrics are measurable (a number and a direction), not vibes.

Respond with ONLY YAML:
title: ...
problem: ...
target_user: ...
hypotheses:
  - statement: ...
    evidence: measured|sourced|assumed
scope_now: [...]
scope_later: [...]
scope_never: [...]
success_metrics: [...]
"""

def run_discovery(
    repo_dir: str | Path,
    idea: str,
    *,
    provider: str = "anthropic",
    writer_model: str = "claude-opus-4-8",
    critic_model: str = "claude-sonnet-5",
) -> Brief:
    project = load_project(repo_dir)
    provider_impl = get_provider(provider)
    context = yaml.safe_dump(
        {"project": project.name, "profile": project.profile,
         "constraints": project.profile_data.get("constraints", [])},
        sort_keys=False, allow_unicode=True,
    )

    feedback = ""
    brief: Brief | None = None
    critics: list[dict] = []
    # The best brief seen so far. A revision that comes back as malformed
    # YAML used to discard EVERY earlier attempt — including one that had
    # already passed schema and been through the four charter voters — so a
    # botched final polish killed a run that had a perfectly usable brief in
    # hand. Observed live: attempt 3 parsed and was critiqued, attempt 4 came
    # back unparseable, and the run died with "failed schema after 4
    # attempts".
    best: Brief | None = None
    best_critics: list[dict] = []
    for revision in range(MAX_REVISIONS + 1):
        progress.step(
            repo_dir, progress.SETUP, "plan",
            "writing the brief" if revision == 0
            else f"revising the brief (attempt {revision + 1})",
        )
        raw = provider_impl.complete(
            model=writer_model,
            system=_WRITER_SYSTEM,
            user=f"<project>\n{context}</project>\n\n<idea>\n{idea}\n</idea>"
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            # A dense FDR produces a dense brief, and CJK costs far more
            # tokens per character than English. At 4096 a 4-5KB Chinese FDR
            # ran out of output budget mid-YAML on every attempt, so all four
            # tries failed identically and the founder got "brief failed
            # schema after 4 attempts" for what was really a size problem.
            max_tokens=8192,
        )
        if last_response_truncated():
            # Distinct from unparseable: the model was not confused, it ran
            # out of room. Saying "not a parseable YAML mapping" here sends
            # the next attempt to fix quoting, which changes nothing, which
            # is exactly how this burned all four revisions.
            feedback = (
                "YOUR LAST ANSWER WAS CUT OFF at the output limit, so none of "
                "it could be used. Be substantially more concise: at most 5 "
                "hypotheses and 6 scope_now items, one short sentence each. "
                "Summarise the source material rather than restating it."
            )
            brief = None
            continue
        try:
            data = extract_mapping(raw, ("hypotheses", "title"))
        except ValueError:
            # Non-parsing output (common with non-English content: unquoted
            # colons break YAML) is revision feedback, not a crash.
            feedback = (
                "Your previous response was not a parseable YAML mapping. "
                "Respond with ONLY the YAML schema given, and double-quote "
                "every string value."
            )
            brief = None
            continue
        try:
            brief = Brief(
                title=str(data.get("title", idea))[:120],
                problem=str(data.get("problem", "")),
                target_user=str(data.get("target_user", "")),
                hypotheses=[Hypothesis.model_validate(h) for h in data.get("hypotheses", [])],
                scope_now=[str(s) for s in data.get("scope_now", [])],
                scope_later=[str(s) for s in data.get("scope_later", [])],
                scope_never=[str(s) for s in data.get("scope_never", [])],
                success_metrics=[str(m) for m in data.get("success_metrics", [])],
                revisions=revision,
            )
        except Exception as exc:  # noqa: BLE001 — schema failure feeds revision
            feedback = f"schema violation: {exc}"
            brief = None
            continue
        # Charter roster (doc 13 §25.1): Desirability, Feasibility,
        # Viability, ScopeDiscipline — each a registered voter with its own
        # fixture gate, findings verified before they count. The single
        # "brief critic panel" prompt retired here (plan phase D13).
        from ai_venture_studio.product.stage_engine import run_critique_roster
        from ai_venture_studio.product.voter_gate import family_roots

        skills_root, _ = family_roots("discovery")
        progress.step(
            repo_dir, progress.SETUP, "plan",
            "four reviewers checking the brief — desirability, feasibility, "
            "viability, scope",
        )
        roster = run_critique_roster(
            "discovery", "discovery",
            yaml.safe_dump(brief.model_dump(exclude={"critic_issues"}),
                           sort_keys=False, allow_unicode=True),
            str(repo_dir),
            provider_impl=provider_impl,
            voter_model=critic_model,
            leader_model=writer_model,
            skills_root=skills_root,
        )
        critics = roster.as_issues()[:10]
        best, best_critics = brief, critics
        majors = [c for c in critics if c.get("severity") == "major"]
        if not majors:
            break
        feedback = yaml.safe_dump({"critic_majors": majors}, sort_keys=False, allow_unicode=True)

    if brief is None and best is not None:
        # Keep the good one, and say so rather than quietly presenting it as
        # the finished article — the critics' majors were never addressed.
        brief, critics = best, list(best_critics)
        critics.append({
            "severity": "minor", "lens": "discovery",
            "problem": "the final revision came back unparseable; this is the "
                       "last brief that passed schema and review, so any major "
                       "findings above may still be open",
            "evidence": feedback[:200],
        })
    if brief is None:
        raise ValueError(f"brief failed schema after {MAX_REVISIONS + 1} attempts: {feedback}")
    brief.critic_issues = critics
    _save(repo_dir, brief)
    _append_ledger(repo_dir, brief)
    return brief


def _save(repo_dir: str | Path, brief: Brief) -> None:
    directory = Path(repo_dir) / "product"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "brief.yaml").write_text(
        yaml.safe_dump(brief.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    hypotheses = "\n".join(
        f"- ({h.evidence}) {h.statement}" for h in brief.hypotheses
    )
    (directory / "brief.md").write_text(
        f"# {brief.title}\n\nstatus: **{brief.status}**\n\n## Problem\n\n"
        f"{brief.problem}\n\n## Target user\n\n{brief.target_user}\n\n"
        f"## Hypotheses (evidence-tagged)\n\n{hypotheses}\n\n"
        f"## Scope now\n\n" + "\n".join(f"- {s}" for s in brief.scope_now)
        + "\n\n## Later / Never\n\n"
        + "\n".join(f"- later: {s}" for s in brief.scope_later)
        + "\n" + "\n".join(f"- never: {s}" for s in brief.scope_never)
        + "\n\n## Success metrics\n\n"
        + "\n".join(f"- {m}" for m in brief.success_metrics)
        + "\n\nApprove with: `avs brief-approve` (Gate U1)\n",
        encoding="utf-8",
    )


def _append_ledger(repo_dir: str | Path, brief: Brief) -> None:
    """The hypothesis ledger — what Maintenance reconciles after launch."""
    path = Path(repo_dir) / ".mas" / "hypotheses.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else []
    existing = existing or []
    known = {e["statement"] for e in existing}
    for h in brief.hypotheses:
        if h.statement not in known:
            existing.append(
                {"statement": h.statement, "evidence": h.evidence, "verified": None}
            )
    path.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_brief(repo_dir: str | Path) -> Brief:
    path = Path(repo_dir) / "product" / "brief.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no brief under {repo_dir}/product (run `avs discover`)")
    return Brief.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def approve_brief(repo_dir: str | Path) -> Brief:
    """Gate U1 — the human problem-selection decision."""
    brief = load_brief(repo_dir)
    brief.status = "approved"
    _save(repo_dir, brief)
    return brief
