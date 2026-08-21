"""Checkpointed Deployment Review graph (§09.11, plan D15).

Through v0.31 the deploy MAS ran as a straight-line function — correct,
but a crash mid-vote re-paid every LLM call, and the implementation map
carried "no mid-stage resume there" as a named open item. This rebuilds
the stage as a LangGraph StateGraph on the same SqliteSaver the code
review graph checkpoints to (thread ids namespaced `deploy:<id>`), so a
crashed run continues from its last completed super-step via
`avs recover`.

Everything else is unchanged on purpose: the YAML mirror keeps its exact
step names (probes → vote → final), verdict selection stays §09.11.6
priority order, lint-only stays the ADR-U15 degraded mode that can never
PROMOTE and never feeds the track record, and the trust-tier ceiling is
untouched — this stage RECOMMENDS; production deploys stay human-executed
forever (§08.1.8).
"""

from __future__ import annotations

import functools
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypedDict

import yaml
from langgraph.graph import END, StateGraph

from ai_venture_studio import scoring, verify
from ai_venture_studio.deploy.probes import (
    canary_scan,
    detect_deploy_files,
    migration_scan,
    workflow_scan,
)
from ai_venture_studio.deploy.review import (
    DeployResult,
    DeployVerdict,
    _policy_prompt,
    _policy_violations,
    decide,
    load_policy,
)
from ai_venture_studio.diff import fetch_diff, parse_unified_diff
from ai_venture_studio.mirror import YamlMirror
from ai_venture_studio.orchestrator.checkpoint import build_saver, encryption_status
from ai_venture_studio.state import Severity, VoterFinding, VoterOutput, VoterStatus
from ai_venture_studio.voters import load_voters
from ai_venture_studio.executables import resolve


def resolve_branch(target: str, repo_dir: str) -> str:
    """The branch this review covers: a PR's head branch, or the checked-out
    branch for a local range. Empty when it cannot be determined — callers
    must refuse rather than assume (ADR-031 §mechanism)."""
    from ai_venture_studio import forge

    if forge.is_change_request(target):
        return forge.head_branch(target) or ""
    proc = subprocess.run(  # noqa: S603 — fixed argv
        [resolve("git"), "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_dir, capture_output=True, text=True, timeout=30,
    )
    branch = proc.stdout.strip() if proc.returncode == 0 else ""
    return "" if branch in ("", "HEAD") else branch  # detached HEAD is unknown


class DeployState(TypedDict, total=False):
    deploy_id: str
    target: str
    branch: str
    repo_dir: str
    skills_dir: str
    provider_override: str | None
    lint_only: bool
    started_at: float  # wall clock — survives resume, unlike monotonic
    diff_raw: str
    policy: dict
    deploy_files: list[str]
    det_findings: list[dict]
    voter_outputs: list[dict]
    kept_findings: list[dict]
    blocked_voters: list[str]
    result: dict


def probes_node(state: DeployState, *, mirror: YamlMirror) -> dict[str, Any]:
    """Deterministic slice first (ADR-U05): scans + Policy-as-Prompt's
    code-enforced `forbidden` list."""
    repo_dir = state["repo_dir"]
    diff = parse_unified_diff(state["diff_raw"])
    policy = load_policy(repo_dir)
    reports = [
        migration_scan(diff, repo_dir),
        workflow_scan(diff, repo_dir),
        canary_scan(diff, repo_dir),
    ]
    findings: list[VoterFinding] = [f for r in reports for f in r.findings]
    findings += _policy_violations(diff, policy)
    mirror.write(
        "probes",
        {"reports": [r.model_dump(mode="json") for r in reports],
         "policy_violations": sum(1 for f in findings if f.taxonomy_hint == "deploy:policy")},
    )
    return {
        "policy": policy,
        "det_findings": [f.model_dump(mode="json") for f in findings],
        "deploy_files": detect_deploy_files(diff.changed_files),
    }


def vote_node(state: DeployState, *, mirror: YamlMirror) -> dict[str, Any]:
    if state.get("lint_only"):
        mirror.write("vote", {"skipped": "lint_only degraded mode — no voters ran"})
        return {"voter_outputs": []}
    voters = load_voters(
        state["skills_dir"], provider_override=state.get("provider_override")
    )
    context = _policy_prompt(state["policy"])
    with ThreadPoolExecutor(max_workers=len(voters)) as pool:
        outputs = list(
            pool.map(
                lambda v: v.run(
                    state["diff_raw"], context=context, repo_dir=state["repo_dir"]
                ),
                voters,
            )
        )
    dumped = [o.model_dump(mode="json") for o in outputs]
    mirror.write("vote", {"voter_outputs": dumped})
    return {"voter_outputs": dumped}


def score_node(state: DeployState) -> dict[str, Any]:
    """Fresh-agent verification + scoring — the expensive super-step a
    resume must never re-pay when it already completed."""
    det = [VoterFinding.model_validate(f) for f in state["det_findings"]]
    if state.get("lint_only"):
        det.sort(key=lambda f: list(Severity).index(f.severity))
        return {
            "kept_findings": [f.model_dump(mode="json") for f in det],
            "blocked_voters": [],
        }
    outputs = [VoterOutput.model_validate(o) for o in state["voter_outputs"]]
    voter_findings = [f for o in outputs for f in o.findings]
    skills = {
        v.spec.name: v.skill
        for v in load_voters(
            state["skills_dir"], provider_override=state.get("provider_override")
        )
    }
    todo = [f for f in voter_findings if f.verification is None]
    provider_override = state.get("provider_override")

    def check(finding: VoterFinding) -> None:
        provider, model, fallback = verify.verifier_config_for(skills[finding.voter])
        if provider_override:
            provider, fallback = provider_override, None
        finding.verification = verify.verify_finding(
            finding, state["diff_raw"], provider=provider, model=model, fallback=fallback
        )

    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as pool:
            list(pool.map(check, todo))
        everything = det + voter_findings
        for finding in todo:
            finding.score = scoring.score_finding(finding, everything)

    kept = det + [
        f
        for f in voter_findings
        if f.verification != "NOT_REPRODUCIBLE" and scoring.passes_threshold(f)
    ]
    kept.sort(key=lambda f: list(Severity).index(f.severity))
    blocked = [o.voter for o in outputs if o.status is not VoterStatus.OK]
    return {
        "kept_findings": [f.model_dump(mode="json") for f in kept],
        "blocked_voters": blocked,
    }


def finalize_node(state: DeployState, *, mirror: YamlMirror) -> dict[str, Any]:
    kept = [VoterFinding.model_validate(f) for f in state["kept_findings"]]
    policy = state["policy"]
    blocked = state["blocked_voters"]
    elapsed = time.time() - state["started_at"]
    verdict = decide(kept, blocked)

    if state.get("lint_only"):
        if verdict is DeployVerdict.PROMOTE:
            verdict = DeployVerdict.HOLD_FOR_HUMAN
        result = DeployResult(
            verdict=verdict,
            tier=policy["tier"],
            summary=(
                f"{verdict.value} — DEGRADED config-lint-only (substrate below "
                f"S4): {len(kept)} deterministic finding(s), voters did NOT "
                "run, so this is never a promotion recommendation; "
                f"{len(state['deploy_files'])} deploy-relevant file(s), "
                f"{elapsed:.0f}s"
            ),
            findings=kept,
            deploy_files=state["deploy_files"],
            artifacts_dir=str(mirror.dir),
            branch=state.get("branch", ""),
        )
        mirror.write("final", result.model_dump(mode="json"))
        return {"result": result.model_dump(mode="json")}

    from ai_venture_studio.deploy import track_record

    track_record.record_review(state["repo_dir"], state["deploy_id"], verdict.value)
    ready = track_record.readiness(
        state["repo_dir"], needed=int(policy.get("promotion_track_record", 10))
    )
    tier_note = ""
    if policy["tier"] == "insight" and ready.eligible:
        tier_note = (
            f"; track record {ready.streak}/{ready.needed} correct PROMOTEs — "
            "eligible for assistive tier (human edits .mas/deploy-policy.yaml)"
        )
    result = DeployResult(
        verdict=verdict,
        tier=policy["tier"],
        summary=(
            f"{verdict.value} (tier: {policy['tier']}; recommendation only) — "
            f"{len(kept)} finding(s), {len(blocked)} blocked voter(s), "
            f"{len(state['deploy_files'])} deploy-relevant file(s)"
            f"{tier_note}, {elapsed:.0f}s"
        ),
        findings=kept,
        blocked_voters=blocked,
        deploy_files=state["deploy_files"],
        artifacts_dir=str(mirror.dir),
        branch=state.get("branch", ""),
    )
    mirror.write("final", result.model_dump(mode="json"))
    return {"result": result.model_dump(mode="json")}


def build_deploy_graph(*, repo_dir: str = ".", deploy_id: str | None = None):
    deploy_id = deploy_id or uuid.uuid4().hex[:12]
    mirror = YamlMirror(Path(repo_dir) / ".mas" / "deploy-reviews", deploy_id)

    graph = StateGraph(DeployState)
    graph.add_node("probes", functools.partial(probes_node, mirror=mirror))
    graph.add_node("vote", functools.partial(vote_node, mirror=mirror))
    graph.add_node("score", score_node)
    graph.add_node("finalize", functools.partial(finalize_node, mirror=mirror))
    graph.set_entry_point("probes")
    graph.add_edge("probes", "vote")
    graph.add_edge("vote", "score")
    graph.add_edge("score", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=build_saver(repo_dir)), deploy_id


def _thread(deploy_id: str) -> dict:
    return {"configurable": {"thread_id": f"deploy:{deploy_id}"}}


def run_deploy_review(
    target: str,
    *,
    repo_dir: str = ".",
    skills_dir: str = "skills/deploy",
    provider_override: str | None = None,
    diff_text: str | None = None,
    lint_only: bool = False,
) -> DeployResult:
    """`lint_only` is the substrate ladder's degraded mode (ADR-U15): only
    the deterministic slice runs (probes + policy scan) — no voters, no
    verification. Because the voter dimension never ran, a lint-only run can
    escalate or hold but never PROMOTE, and it never feeds the promotion
    track record."""
    diff = (
        parse_unified_diff(diff_text)
        if diff_text is not None
        else fetch_diff(target, repo_dir=repo_dir)
    )
    app, deploy_id = build_deploy_graph(repo_dir=repo_dir)
    branch = resolve_branch(target, repo_dir)
    meta = {
        "target": target,
        "branch": branch,
        "repo_dir": repo_dir,
        "skills_dir": skills_dir,
        "provider_override": provider_override,
        "lint_only": lint_only,
        "checkpoint_encryption": encryption_status(),
    }
    meta_path = Path(repo_dir) / ".mas" / "deploy-reviews" / deploy_id / "meta.yaml"
    meta_path.write_text(yaml.safe_dump(meta), encoding="utf-8")

    initial: DeployState = {
        "deploy_id": deploy_id,
        "target": target,
        "branch": branch,
        "repo_dir": repo_dir,
        "skills_dir": skills_dir,
        "provider_override": provider_override,
        "lint_only": lint_only,
        "started_at": time.time(),
        "diff_raw": diff.raw,
    }
    final = app.invoke(initial, config=_thread(deploy_id))
    return DeployResult.model_validate(final["result"])


def recover_deploy_reviews(repo_dir: str = ".") -> list[dict]:
    """Deploy runs with a meta.yaml but no final mirror step continue from
    their SQLite checkpoint — same recovery contract as code review."""
    base = Path(repo_dir) / ".mas" / "deploy-reviews"
    results: list[dict] = []
    if not base.is_dir():
        return results
    for run_dir in sorted(base.iterdir()):
        meta_path = run_dir / "meta.yaml"
        if not meta_path.exists() or list(run_dir.glob("[0-9]*-final.yaml")):
            continue
        deploy_id = run_dir.name
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        try:
            app, _ = build_deploy_graph(
                repo_dir=meta["repo_dir"], deploy_id=deploy_id
            )
            config = _thread(deploy_id)
            snapshot = app.get_state(config)
            if not snapshot.values:
                results.append(
                    {"kind": "deploy", "id": deploy_id, "status": "no_checkpoint"}
                )
                continue
            final = app.invoke(None, config=config)
            results.append(
                {
                    "kind": "deploy",
                    "id": deploy_id,
                    "status": "recovered",
                    "verdict": (final.get("result") or {}).get("verdict"),
                }
            )
        except Exception as exc:  # noqa: BLE001 — one broken run never blocks the rest
            results.append(
                {"kind": "deploy", "id": deploy_id, "status": "error",
                 "detail": str(exc)[:200]}
            )
    return results
