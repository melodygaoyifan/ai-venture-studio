"""The streaming delta (doc 27 Part 80) — contracts declared, never defaulted.

Registry defaults disagree (Confluent BACKWARD, Glue DISABLED, Apicurio
NONE), so an undeclared compatibility mode is an unknown guarantee: the
word "default" is lexically illegal in stream-contracts.yaml (ADR-U32,
invariant 14.25). Compatibility is checked field-wise; upgrade order is
derived from the declared mode; exactly-once claims are typed by mechanism
or visibly downgraded; and the enforcement tier is declared honestly —
claiming a guarantee the tier can't provide is a finding.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

COMPAT_MODES = ("BACKWARD", "FORWARD", "FULL", "NONE")
UPGRADE_ORDER = {  # derived, written into the deploy-review record (§80.1)
    "BACKWARD": "consumers deploy first",
    "FORWARD": "producers deploy first",
    "FULL": "either order (both constraints hold)",
    "NONE": "no safe order exists — coordinate a cutover",
}
ENFORCEMENT_TIERS = ("sdk_only", "broker_validated", "audit_counted")
EXACTLY_ONCE_MECHANISMS = (
    "transactional_producer_read_committed",
    "idempotent_consumer_dedupe_key",
)


class StreamContractError(RuntimeError):
    """Malformed contract file. Fails closed."""


class StreamIssue(BaseModel):
    topic: str = ""
    rule: str
    message: str


def load_stream_contracts(text: str) -> list[dict]:
    """Parse stream-contracts.yaml; 'default' is lexically rejected."""
    import yaml

    if re.search(r"\bdefault\b", text, re.I):
        raise StreamContractError(
            "the word 'default' is illegal in stream-contracts.yaml — "
            "registry defaults disagree (BACKWARD/DISABLED/NONE), so an "
            "undeclared mode is an unknown guarantee (ADR-U32)")
    raw = yaml.safe_load(text) or {}
    topics = raw.get("topics") or []
    for topic in topics:
        mode = topic.get("compatibility")
        if mode not in COMPAT_MODES:
            raise StreamContractError(
                f"topic {topic.get('name', '?')!r}: compatibility must be "
                f"declared as one of {COMPAT_MODES}")
        if topic.get("enforcement_tier") not in ENFORCEMENT_TIERS:
            raise StreamContractError(
                f"topic {topic.get('name', '?')!r}: enforcement_tier must be "
                f"declared as one of {ENFORCEMENT_TIERS} — SDK-side enforcement "
                "is bypassable and pretending otherwise is the vice this repo "
                "exists to avoid (§80.1)")
    return topics


def _fields(schema: dict) -> dict[str, dict]:
    return {str(f["name"]): f for f in schema.get("fields") or []}


def check_compatibility(
    old_schema: dict, new_schema: dict, mode: str
) -> list[StreamIssue]:
    """Field-wise compatibility: BACKWARD = new readers read old data
    (additions need defaults); FORWARD = old readers read new data
    (removals need the removed field to have had a default)."""
    old, new = _fields(old_schema), _fields(new_schema)
    added = [n for n in new if n not in old]
    removed = [n for n in old if n not in new]
    issues = []
    if mode in ("BACKWARD", "FULL"):
        for name in added:
            if "default" not in new[name]:
                issues.append(StreamIssue(
                    rule="backward_incompatible",
                    message=f"added field {name!r} without a default — new "
                            "consumers cannot read old records"))
        for name in removed:
            issues.append(StreamIssue(
                rule="backward_incompatible",
                message=f"removed field {name!r} — new consumers reading old "
                        "records would still see it; removal requires FORWARD "
                        "reasoning or a FULL plan"))
    if mode in ("FORWARD", "FULL"):
        for name in removed:
            if "default" not in old[name]:
                issues.append(StreamIssue(
                    rule="forward_incompatible",
                    message=f"removed field {name!r} had no default — old "
                            "consumers cannot read new records"))
    return issues


def stream_contract_check(
    topic: dict, old_schema: dict, new_schema: dict, producer_config: dict
) -> tuple[list[StreamIssue], str]:
    """The CI gate on any schema diff; returns (issues, upgrade_order)."""
    mode = topic["compatibility"]
    issues = check_compatibility(old_schema, new_schema, mode)
    if str(producer_config.get("auto.register.schemas", "")).lower() != "false":
        issues.append(StreamIssue(
            topic=str(topic.get("name", "?")), rule="rogue_producer_risk",
            message="auto.register.schemas must be false in production "
                    "configs — a producer that registers schemas as a side "
                    "effect bypasses the contract review"))
    for issue in issues:
        issue.topic = issue.topic or str(topic.get("name", "?"))
    return issues, UPGRADE_ORDER[mode]


class DeliveryClaim(BaseModel):
    claim: str  # exactly_once | at_least_once | at_most_once
    mechanism: str = ""
    downgraded: bool = False
    note: str = ""


def type_delivery_claim(claim: str, mechanism: str = "") -> DeliveryClaim:
    """'Exactly once' without a named mechanism downgrades, visibly (§80.3)."""
    if claim != "exactly_once":
        return DeliveryClaim(claim=claim, mechanism=mechanism)
    if mechanism in EXACTLY_ONCE_MECHANISMS:
        return DeliveryClaim(claim=claim, mechanism=mechanism,
                             note="replay verification required: reprocessing a "
                                  "bounded fixture slice must be byte-identical")
    return DeliveryClaim(
        claim="at_least_once", mechanism=mechanism, downgraded=True,
        note="exactly-once claimed with no recognized mechanism "
             f"({EXACTLY_ONCE_MECHANISMS}) — downgraded in the spec, visibly")


_UNBOUNDED_BUFFER = re.compile(
    r"\.append\(|deque\(\)|Queue\(\)|list\(\)", re.M
)
_BOUNDED_HINT = re.compile(r"maxsize\s*=|maxlen\s*=|Queue\(\s*\d")


class BackpressureFinding(BaseModel):
    rule: str
    message: str


def backpressure_scan(consumer_source: str, *, max_lag_seconds: float | None) -> list[BackpressureFinding]:
    findings = []
    if max_lag_seconds is None:
        findings.append(BackpressureFinding(
            rule="no_lag_slo",
            message="contract topic declares no max_lag_seconds — a lag SLO "
                    "is what makes the backpressure probe a gate"))
    if _UNBOUNDED_BUFFER.search(consumer_source) and not _BOUNDED_HINT.search(consumer_source):
        findings.append(BackpressureFinding(
            rule="unbounded_buffer",
            message="in-memory buffering with no declared bound in consumer "
                    "code — bounded-queue behavior is the contract (§80.3)"))
    return findings
