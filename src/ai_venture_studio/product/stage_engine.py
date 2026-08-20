"""The shared product-stage engine (§12.24.1, applied to P-stages).

generate → deterministic tools → critique-vote → verify → leader → gate,
with deterministic control flow throughout: the LLM writes and judges,
Python decides what runs next (CLAUDE.md architecture invariant). Voters
are loaded from their skills/product/<stage>/ charters, run independently
with no cross-visibility, and every finding passes a fresh verify seat
before the Leader sees it. Deterministic tool findings feed the writer's
revision loop directly — a machine-checkable failure is feedback, not a
report.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field
from typing import Callable

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.providers import get_provider
from ai_venture_studio.yamlx import extract_mapping

PRODUCT_VOTER_MARKER = "PRODUCT-STAGE VOTER"
PRODUCT_VERIFIER_MARKER = "PRODUCT-STAGE VERIFIER"
PRODUCT_LEADER_MARKER = "PRODUCT-STAGE LEADER"

MAX_REVISIONS = 2

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

_VOTER_CONTRACT = """

Respond with ONLY YAML:
findings:
  - severity: major|minor
    problem: one sentence
    evidence: verbatim quote from the artifact that grounds the finding
"""

_VERIFIER_SYSTEM = f"""You are the {PRODUCT_VERIFIER_MARKER}: a fresh agent
re-deriving one voter finding from the artifact alone. Verify it only if the
quoted evidence appears and supports the problem stated; refute plausible-
but-wrong findings without mercy.

Respond with ONLY YAML:
verdict: verified|refuted
reason: one sentence
"""

_LEADER_SYSTEM = f"""You are the {PRODUCT_LEADER_MARKER}: synthesize the
verified findings into the gate surface. Never add findings of your own;
never soften a major.

Respond with ONLY YAML:
summary: two sentences for the human at the gate
"""


class VoterFinding(BaseModel):
    voter: str
    severity: str
    problem: str
    evidence: str = ""
    verified: bool = False


class RosterReport(BaseModel):
    """What a charter-voter critique pass produces, stage-agnostic."""

    stage: str
    voter_findings: list[VoterFinding] = Field(default_factory=list)
    excluded_voters: list[str] = Field(default_factory=list)  # failed gate
    unregistered_voters: list[str] = Field(default_factory=list)  # no gate run
    leader_summary: str = ""

    def as_issues(self) -> list[dict]:
        """The upstream stages' critic_issues dict shape (lens = voter)."""
        return [
            {"severity": f.severity, "lens": f.voter, "problem": f.problem,
             "evidence": f.evidence}
            for f in self.voter_findings
        ]


class StageReport(BaseModel):
    stage: str
    status: str  # ok | gate_blocked | needs_revision
    revisions: int
    det_findings: list[dict] = Field(default_factory=list)
    voter_findings: list[VoterFinding] = Field(default_factory=list)
    excluded_voters: list[str] = Field(default_factory=list)  # failed gate
    unregistered_voters: list[str] = Field(default_factory=list)  # no gate run
    leader_summary: str = ""
    gate: dict = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)


@dataclass
class StageSpec:
    """Everything that differs between P-stages; the engine is the same."""

    name: str
    writer_system: str
    expected_keys: tuple[str, ...]
    skills_subdir: str  # under skills/product/
    # dict from the writer → (artifact object, artifact_text for voters).
    # Raises ValueError on schema violations (feeds the revision loop).
    parse: Callable[[dict], tuple[object, str]]
    # deterministic checks: artifact → list of {rule, message} dicts.
    det_tools: Callable[[object], list[dict]]
    # gate: artifact → {"passed": bool, ...surface}
    gate: Callable[[object], dict]
    # persist artifact; returns written paths.
    persist: Callable[[object, str], list[str]] = field(
        default=lambda artifact, workspace: []
    )
    # what the voters (and verifiers) read; defaults to the writer's raw
    # output. Stages whose det tools DERIVE material a voter must see
    # (generated planning tasks, computed sizing) enrich it here — a voter
    # flagging a gap the machinery already closed is noise.
    voter_context: Callable[[object, str], str] | None = None


def _default_skills_root() -> pathlib.Path:
    from ai_venture_studio.paths import skills_root

    return skills_root() / "product"


def load_voter_charters(
    stage_subdir: str, skills_root: pathlib.Path | None = None
) -> list[tuple[str, str]]:
    """(name, system_prompt) per charter file; the frontmatter contract is
    honored for identity, the body becomes the seat's system prompt.

    Kept for callers that only need the prompt. `load_voter_charter_specs`
    below also carries the declared tools, which is what lets a charter seat
    actually look at the repo instead of being told to and given nothing.
    """
    return [(c.name, c.system) for c in load_voter_charter_specs(
        stage_subdir, skills_root)]


class CharterSeat(BaseModel):
    """A charter voter's identity, prompt, and declared tool contract."""

    name: str
    system: str
    tools: list[str] = Field(default_factory=list)
    tool_budget: int = 6


def load_voter_charter_specs(
    stage_subdir: str, skills_root: pathlib.Path | None = None
) -> list[CharterSeat]:
    """Charter seats with their `tools:` frontmatter honored.

    This used to read the frontmatter only for `name` and drop everything
    else, so ~40 product and upstream seats declared `tools: [read_file,
    grep]`, were instructed to go read things, and were handed nothing. An
    unknown tool name is dropped with the seat recorded rather than silently
    accepted — the review-side SpecValidator aborts startup on one, but a
    charter roster must still be able to vote.
    """
    from ai_venture_studio.tools.voter_tools import VOTER_TOOL_REGISTRY

    root = (skills_root or _default_skills_root()) / stage_subdir
    seats: list[CharterSeat] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text()
        match = _FRONTMATTER.match(text)
        name = path.stem
        declared: list[str] = []
        budget = 6
        if match:
            meta = yaml.safe_load(match.group(1)) or {}
            name = str(meta.get("name", name))
            raw_tools = meta.get("tools") or []
            if isinstance(raw_tools, list):
                declared = [
                    str(t) for t in raw_tools if str(t) in VOTER_TOOL_REGISTRY
                ]
            budget = int(meta.get("tool_budget", 6))
            text = text[match.end():]
        seats.append(CharterSeat(
            name=name,
            system=f"You are a {PRODUCT_VOTER_MARKER}.\n\n{text}{_VOTER_CONTRACT}",
            tools=declared,
            tool_budget=max(1, min(budget, 25)),
        ))
    return seats


_TOOL_PREAMBLE = """

You may inspect the workspace before judging. To use a tool, reply with ONLY:
tool_request:
  tool: <{tools}>
  args: {{...}}
You will receive a <tool_result> and may request again until your budget of
{budget} calls runs out. When you are ready — or out of budget — reply with the
findings YAML. Ground every finding in what you actually read: an unverifiable
finding is dropped by the verifier seat anyway.
"""


def _charter_vote(
    seat: CharterSeat,
    context_text: str,
    workspace: str,
    *,
    provider_impl,
    voter_model: str,
) -> str:
    """One charter seat's raw response, after any tool turns it asks for.

    Seats that declare no tools take the original single-shot path
    unchanged. Seats that do declare tools get a real investigation loop with
    the budget enforced at the ToolBox boundary — the same protocol the
    review voters use, deliberately not a second dialect.
    """
    if not seat.tools:
        return provider_impl.complete(
            model=voter_model, system=seat.system, user=context_text,
            max_tokens=2048,
        )

    from ai_venture_studio.tools.voter_tools import ToolBox, ToolBudgetExceeded
    from ai_venture_studio.voters.base import Voter

    toolbox = ToolBox(workspace, seat.tools, budget=seat.tool_budget)
    system = seat.system + _TOOL_PREAMBLE.format(
        tools="|".join(seat.tools), budget=seat.tool_budget
    )
    transcript = context_text
    for _ in range(seat.tool_budget + 1):
        raw = provider_impl.complete(
            model=voter_model, system=system, user=transcript, max_tokens=2048
        )
        request = Voter._tool_request(raw)
        if request is None:
            return raw
        try:
            result = toolbox.call(
                str(request.get("tool", "")), request.get("args") or {}
            )
        except ToolBudgetExceeded:
            transcript += (
                "\n\nTool budget exhausted. Reply with your findings YAML now, "
                "based on what you have read."
            )
            return provider_impl.complete(
                model=voter_model, system=system, user=transcript,
                max_tokens=2048,
            )
        transcript += (
            f"\n\n<tool_result tool={request.get('tool')} "
            f"remaining_calls={toolbox.remaining}>\n{result}\n</tool_result>"
        )
    # Budget spent without a verdict: ask once more, plainly.
    return provider_impl.complete(
        model=voter_model, system=system,
        user=transcript + "\n\nReply with the findings YAML now.",
        max_tokens=2048,
    )


def run_critique_roster(
    stage_name: str,
    skills_subdir: str,
    context_text: str,
    workspace: str,
    *,
    provider_impl,
    voter_model: str = "claude-sonnet-5",
    leader_model: str = "claude-opus-4-8",
    skills_root: pathlib.Path | None = None,
    det_findings: list[dict] | None = None,
) -> RosterReport:
    """Charter voters (no cross-visibility) → per-finding verify → leader.

    The one critique machine every generative stage shares (doc 13 §25.3):
    P-stages call it through run_product_stage; the upstream stages call
    it directly with skills_root pointing at skills/upstream/ — the
    single-panel critic prompts retired in v0.32 lived exactly here.
    """
    voter_findings: list[VoterFinding] = []
    excluded: list[str] = []
    unregistered: list[str] = []
    from ai_venture_studio.product.voter_gate import registry_status

    mas_dir = pathlib.Path(workspace) / ".mas"
    for seat in load_voter_charter_specs(skills_subdir, skills_root):
        voter_name = seat.name
        status = registry_status(mas_dir, stage_name, voter_name)
        if status == "failed":
            # §11.19: no agent registers without passing its fixture gate —
            # a voter with a FAILED gate run does not vote, full stop.
            excluded.append(voter_name)
            continue
        if status == "unregistered":
            unregistered.append(voter_name)  # loads, but the report says so
        raw = _charter_vote(
            seat, context_text, workspace,
            provider_impl=provider_impl, voter_model=voter_model,
        )
        try:
            found = extract_mapping(raw, ("findings",)).get("findings") or []
        except ValueError:
            continue  # a voter that cannot emit the contract contributes nothing
        for entry in found[:5]:
            if not isinstance(entry, dict) or not entry.get("problem"):
                continue
            finding = VoterFinding(
                voter=voter_name,
                severity=str(entry.get("severity", "minor")),
                problem=str(entry["problem"]),
                evidence=str(entry.get("evidence", "")),
            )
            raw_verdict = provider_impl.complete(
                model=voter_model,
                system=_VERIFIER_SYSTEM,
                user=yaml.safe_dump(
                    {"finding": finding.model_dump(exclude={"verified"}),
                     "artifact": context_text},
                    sort_keys=False, allow_unicode=True,
                ),
                max_tokens=512,
            )
            try:
                verdict = extract_mapping(raw_verdict, ("verdict",))
            except ValueError:
                verdict = {}
            finding.verified = verdict.get("verdict") == "verified"
            if finding.verified:
                voter_findings.append(finding)

    raw_summary = provider_impl.complete(
        model=leader_model,
        system=_LEADER_SYSTEM,
        user=yaml.safe_dump(
            {"stage": stage_name,
             "verified_findings": [f.model_dump() for f in voter_findings],
             "det_findings": det_findings or []},
            sort_keys=False, allow_unicode=True,
        ),
        max_tokens=1024,
    )
    try:
        leader_summary = str(extract_mapping(raw_summary, ("summary",))["summary"])
    except (ValueError, KeyError):
        leader_summary = f"{len(voter_findings)} verified finding(s); see report."

    return RosterReport(
        stage=stage_name,
        voter_findings=voter_findings,
        excluded_voters=excluded,
        unregistered_voters=unregistered,
        leader_summary=leader_summary,
    )


def run_product_stage(
    spec: StageSpec,
    user_input: str,
    workspace: str,
    *,
    provider: str = "anthropic",
    writer_model: str = "claude-opus-4-8",
    voter_model: str = "claude-sonnet-5",
    skills_root: pathlib.Path | None = None,
) -> StageReport:
    provider_impl = get_provider(provider)

    artifact: object | None = None
    artifact_text = ""
    det_findings: list[dict] = []
    feedback = ""
    revision = 0
    for revision in range(MAX_REVISIONS + 1):
        raw = provider_impl.complete(
            model=writer_model,
            system=spec.writer_system,
            user=user_input
            + (f"\n\n<revision_feedback>\n{feedback}\n</revision_feedback>" if feedback else ""),
            max_tokens=8192,
        )
        try:
            data = extract_mapping(raw, spec.expected_keys)
        except ValueError:
            feedback = (
                "Your previous response was not a parseable YAML mapping. "
                "Respond with ONLY the YAML schema given, double-quoting "
                "every string value."
            )
            artifact = None
            continue
        try:
            artifact, artifact_text = spec.parse(data)
        except ValueError as exc:
            feedback = f"schema violation: {exc}"
            artifact = None
            continue
        det_findings = spec.det_tools(artifact)
        if not det_findings:
            break
        # Deterministic failures are revision feedback (ADR-U05): the
        # writer gets the machine's findings verbatim, once per revision.
        feedback = yaml.safe_dump({"deterministic_findings": det_findings},
                                  sort_keys=False, allow_unicode=True)
    if artifact is None:
        raise ValueError(
            f"{spec.name}: writer failed schema after {MAX_REVISIONS + 1} "
            f"attempts: {feedback}"
        )

    # Voters: independent seats, no cross-visibility, then a fresh verify
    # pass per finding — plausible-but-wrong findings die here.
    context_text = (
        spec.voter_context(artifact, artifact_text)
        if spec.voter_context
        else artifact_text
    )
    roster = run_critique_roster(
        spec.name,
        spec.skills_subdir,
        context_text,
        workspace,
        provider_impl=provider_impl,
        voter_model=voter_model,
        leader_model=writer_model,
        skills_root=skills_root,
        det_findings=det_findings,
    )
    voter_findings = roster.voter_findings

    gate = spec.gate(artifact)
    majors = [f for f in voter_findings if f.severity == "major"]
    if det_findings or not gate.get("passed"):
        status = "gate_blocked"
    elif majors:
        status = "needs_revision"
    else:
        status = "ok"
    return StageReport(
        stage=spec.name,
        status=status,
        revisions=revision,
        det_findings=det_findings,
        voter_findings=voter_findings,
        excluded_voters=roster.excluded_voters,
        unregistered_voters=roster.unregistered_voters,
        leader_summary=roster.leader_summary,
        gate=gate,
        artifacts=spec.persist(artifact, workspace),
    )
