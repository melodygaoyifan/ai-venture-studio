"""The upstream verdict vocabulary (doc 13) — typed, importable constants
so stages and routers speak one language instead of ad-hoc strings."""

from __future__ import annotations

APPROVALS = ("APPROVE_BRIEF", "APPROVE_PLAN", "APPROVE_SPEC")
BLOCKED = ("BLOCKED_MISSING_CONTEXT", "TASK_BLOCKED_MISSING_CONTEXT",
           "NEEDS_PROBE", "TASK_SCOPE_VIOLATION")
ESCALATIONS = (
    "ESCALATE_REQUIREMENT_CONFLICT",   # existed
    "ESCALATE_CONTRACT_BREAK",         # existed
    "ESCALATE_SCOPE_CREEP",
    "ESCALATE_ESTIMATE_BLOWN",
    "ESCALATE_DEPENDENCY_CYCLE",
    "ESCALATE_SPEC_GAP",               # the SCR trigger
    "ESCALATE_MIGRATION_DESTRUCTIVE",
    "ESCALATE_SECURITY_SURFACE",
    "ESCALATE_BUDGET_EXCEEDED",
    "ESCALATE_TOOL_FAILURE",
    "SPEC_DRIFT_UNDOCUMENTED",
)
ALL_VERDICTS = frozenset(APPROVALS + BLOCKED + ESCALATIONS)


def is_escalation(verdict: str) -> bool:
    return verdict in ESCALATIONS


# `is_terminal(v)` lived here and returned `v in ALL_VERDICTS` — true for
# every verdict there is, so as a filter it filtered nothing. Its docstring
# argued that all three families end a stage, which makes the body correct
# and the NAME the defect: what it actually answered was "is this a known
# verdict". Nothing called it. Deleted rather than renamed, because a
# renamed function nothing calls is still dead code, and `v in ALL_VERDICTS`
# says it in one expression at whatever call site eventually needs it.
