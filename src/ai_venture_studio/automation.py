"""Policy-armed automation (ADR-031 — the hardest reversal, kept narrow).

"Auto-merge to main. Auto-deploy to production." was out of scope in every
prior edition, and §08.1.8 called the ceiling architectural. This module
reverses that with the only structure that keeps it honest: **the
capability exists, and it is disarmed until a human writes a policy file
saying otherwise, per repository, with conditions the machine then checks
mechanically.**

The distinction that makes this defensible: the system still never decides
*whether* automation is acceptable — a human does, in advance, in writing,
in a file they own. What the system does is refuse to act unless every
declared condition holds. That is the difference between delegation and
autonomy, and the ceiling that actually mattered (no agent talks itself
into a merge) is intact.

Five properties, each with teeth in `evaluate_*` below:

1. **Disarmed by default.** No policy file, no automation, and an absent
   file is never "allow" (`enabled: false` is also the default *inside* a
   present file).
2. **Narrow by construction.** Branch allowlists are exact strings; `"*"`
   is refused. A policy cannot arm what it does not name.
3. **Earned, not asserted.** A minimum track record of correct
   recommendations is required before the first automated action, read from
   the same ledger the deploy trust tiers use.
4. **Every precondition is a machine check**, not a prompt: verdict class,
   gate outcomes, test-gate status, changed-path exclusions, and a
   human-set expiry after which the policy is dead until renewed.
5. **Refusals and actions are both recorded** in `.mas/automation-log.jsonl`
   with the exact reason, so "why did it merge" and "why didn't it" have
   the same quality of answer.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import pathlib

import yaml
from pydantic import BaseModel, Field

AUTOMERGE_POLICY = "automerge-policy.yaml"
DEPLOY_EXEC_POLICY = "deploy-exec-policy.yaml"
AUTOMATION_LOG = "automation-log.jsonl"

# Verdicts that may ever precede an automated merge. REQUEST_CHANGES,
# every ESCALATE_*, and anything unknown are excluded by omission.
MERGEABLE_VERDICTS = {"APPROVE", "APPROVE_WITH_NOTES"}
# Paths whose change always demands a human, whatever the policy says.
ALWAYS_HUMAN_PATHS = (
    "**/migrations/**", "**/*.tf", "**/Dockerfile*", ".github/workflows/**",
    "**/charts/**", "**/k8s/**", "CLAUDE.md", ".mas/**",
    "**/automerge-policy.yaml", "**/deploy-exec-policy.yaml",
)


class PolicyError(RuntimeError):
    """A policy file that cannot be honored — never a silent disarm."""


class Decision(BaseModel):
    """Why the machine did or did not act. Both directions get a reason."""

    action: str  # merge | deploy
    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    policy_path: str = ""


class AutomationPolicy(BaseModel):
    enabled: bool = False
    branches: list[str] = Field(default_factory=list)
    min_track_record: int = 10
    require_test_gate_pass: bool = True
    expires_at: str = ""  # YYYY-MM-DD; a policy without one is refused
    armed_by: str = ""  # the human who armed it, for the record
    exclude_paths: list[str] = Field(default_factory=list)
    # deploy only: the exact argv the human authorizes. No shell, ever.
    command: list[str] = Field(default_factory=list)


def _policy_path(repo_dir: str | pathlib.Path, filename: str) -> pathlib.Path:
    return pathlib.Path(repo_dir) / ".mas" / filename


def load_policy(
    repo_dir: str | pathlib.Path, filename: str, *, today: str | None = None
) -> AutomationPolicy:
    """Absent file → disarmed. Present but malformed → PolicyError."""
    path = _policy_path(repo_dir, filename)
    if not path.exists():
        return AutomationPolicy()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path} is not parseable YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{path} must be a mapping")
    try:
        policy = AutomationPolicy.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — surfaced as PolicyError
        raise PolicyError(f"{path}: invalid policy: {exc}") from exc
    if not policy.enabled:
        return policy  # disarmed on purpose; the rest need not validate

    if "*" in policy.branches or any(
        "*" in branch or "?" in branch for branch in policy.branches
    ):
        raise PolicyError(
            f"{path}: branch patterns are refused — list exact branch names. A "
            "policy cannot arm what it does not name."
        )
    if not policy.branches:
        raise PolicyError(f"{path}: an armed policy must name at least one branch")
    if not policy.armed_by.strip():
        raise PolicyError(
            f"{path}: `armed_by` must name the human who armed this — an "
            "unattributed automation policy is not a decision"
        )
    if not policy.expires_at:
        raise PolicyError(
            f"{path}: `expires_at` (YYYY-MM-DD) is required — standing "
            "permission that never lapses is how this stops being a decision"
        )
    try:
        expiry = datetime.date.fromisoformat(policy.expires_at)
    except ValueError as exc:
        raise PolicyError(f"{path}: expires_at must be YYYY-MM-DD: {exc}") from exc
    now = (
        datetime.date.fromisoformat(today)
        if today
        else datetime.datetime.now(datetime.UTC).date()
    )
    if expiry < now:
        raise PolicyError(
            f"{path}: policy expired on {policy.expires_at} — renew it "
            "deliberately or delete it; an expired policy never silently "
            "keeps working"
        )
    if policy.min_track_record < 1:
        raise PolicyError(
            f"{path}: min_track_record must be >= 1 — the first automated "
            "action is never the first action"
        )
    return policy


def _path_blocked(changed_files: list[str], policy: AutomationPolicy) -> list[str]:
    patterns = [*ALWAYS_HUMAN_PATHS, *policy.exclude_paths]
    blocked = []
    for path in changed_files:
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(f"**/{path}", pattern):
                blocked.append(f"{path} (matches {pattern})")
                break
    return blocked


def evaluate_merge(
    repo_dir: str | pathlib.Path,
    *,
    verdict: str,
    branch: str,
    changed_files: list[str],
    test_gate_status: str | None,
    escalated: bool = False,
    today: str | None = None,
) -> Decision:
    """May this reviewed PR be merged without a human? Default: no."""
    path = _policy_path(repo_dir, AUTOMERGE_POLICY)
    policy = load_policy(repo_dir, AUTOMERGE_POLICY, today=today)
    reasons: list[str] = []
    if not policy.enabled:
        reasons.append(
            "no armed automerge policy (.mas/automerge-policy.yaml absent or "
            "enabled: false) — merging stays human work by default"
        )
        return Decision(action="merge", allowed=False, reasons=reasons,
                        policy_path=str(path))
    if verdict not in MERGEABLE_VERDICTS:
        reasons.append(f"verdict {verdict!r} is not in {sorted(MERGEABLE_VERDICTS)}")
    if escalated:
        reasons.append("the review escalated to a human — that decision stands")
    if not branch:
        # Same rule as deploy: an unknown branch is a refusal, never an
        # assumption. `gh pr view` failing must not become "probably main".
        reasons.append(
            "the PR's branch could not be determined — refusing rather than "
            "assuming a default"
        )
    elif branch not in policy.branches:
        reasons.append(f"branch {branch!r} is not in the armed list {policy.branches}")
    if policy.require_test_gate_pass and test_gate_status != "passed":
        reasons.append(f"test gate status is {test_gate_status!r}, not 'passed'")
    blocked = _path_blocked(changed_files, policy)
    if blocked:
        reasons.append(f"changed paths always require a human: {blocked[:5]}")

    from ai_venture_studio.deploy import track_record

    ready = track_record.readiness(repo_dir, needed=policy.min_track_record)
    if not ready.eligible:
        reasons.append(
            f"track record {ready.streak}/{ready.needed} correct — automation "
            "is earned, not asserted"
        )
    return Decision(action="merge", allowed=not reasons, reasons=reasons,
                    policy_path=str(path))


def evaluate_deploy(
    repo_dir: str | pathlib.Path,
    *,
    verdict: str,
    branch: str,
    changed_files: list[str],
    today: str | None = None,
) -> Decision:
    """May this PROMOTE recommendation be executed without a human? No, by
    default — and never for a verdict that is not PROMOTE."""
    path = _policy_path(repo_dir, DEPLOY_EXEC_POLICY)
    policy = load_policy(repo_dir, DEPLOY_EXEC_POLICY, today=today)
    reasons: list[str] = []
    if not policy.enabled:
        reasons.append(
            "no armed deploy-exec policy (.mas/deploy-exec-policy.yaml absent "
            "or enabled: false) — the button stays yours by default"
        )
        return Decision(action="deploy", allowed=False, reasons=reasons,
                        policy_path=str(path))
    if not policy.command:
        reasons.append(
            "policy names no `command:` argv — the system executes only the "
            "exact command a human wrote, never one it composes"
        )
    if verdict != "PROMOTE":
        reasons.append(f"deploy verdict is {verdict!r}, not 'PROMOTE'")
    if not branch:
        # An unresolvable branch (detached HEAD, no gh, no remote) must never
        # fall back to a default: "assume main" is how an armed policy acts
        # on work it was never armed for.
        reasons.append(
            "the review's branch could not be determined — refusing rather "
            "than assuming a default"
        )
    elif branch not in policy.branches:
        reasons.append(f"branch {branch!r} is not in the armed list {policy.branches}")
    blocked = _path_blocked(changed_files, policy)
    if blocked:
        reasons.append(f"changed paths always require a human: {blocked[:5]}")

    from ai_venture_studio.deploy import track_record

    ready = track_record.readiness(repo_dir, needed=policy.min_track_record)
    if not ready.eligible:
        reasons.append(
            f"track record {ready.streak}/{ready.needed} correct PROMOTEs — "
            "automation is earned, not asserted"
        )
    return Decision(action="deploy", allowed=not reasons, reasons=reasons,
                    policy_path=str(path))


def record(repo_dir: str | pathlib.Path, decision: Decision, detail: str = "") -> None:
    """Append to `.mas/automation-log.jsonl` — refusals included, because
    "why didn't it" deserves the same answer quality as "why did it"."""
    path = pathlib.Path(repo_dir) / ".mas" / AUTOMATION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "action": decision.action,
        "allowed": decision.allowed,
        "reasons": decision.reasons,
        # WHICH policy file said yes. `Decision` has carried this since the
        # field was added and the log — the only durable record of a merge or
        # a deploy the machine performed — dropped it (ADR-060). These policies
        # expire, get re-armed by a named human, and are the entire authority
        # for the action; an audit line that omits the authorizing document
        # answers "did it?" and not "on whose say-so?".
        "policy_path": decision.policy_path,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(repo_dir: str | pathlib.Path) -> list[dict]:
    path = pathlib.Path(repo_dir) / ".mas" / AUTOMATION_LOG
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
