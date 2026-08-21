"""Gate PL3 (§21.61.5) — human, per artifact class, scoped approvals only.

The gate presents the exact artifact as it will appear, the substantiation
map (sentence → claim → evidence), the disclosure block, the preflight
summary, and the diff vs the last approved version.

The scoping rule is the operational heart of the stage: approvals never
generalize. The data model has NO representation for an unscoped approval
— ApprovalScope requires one artifact hash, one channel, one time window,
and rejects wildcards. Approving one post is not approving a campaign; a
batch is approved only as an enumerated list of hashes, every artifact
presented. This is the strongest available form of the rule, and this
module records approvals — nothing in it publishes (§21.57).
"""

from __future__ import annotations

import difflib
import hashlib

from pydantic import BaseModel, Field, field_validator

from ai_venture_studio.marketing.artifacts import Draft
from ai_venture_studio.marketing.register import ReleaseContract
from ai_venture_studio.marketing.substantiation import (
    _match_register,
    split_sentences,
)


class GateBlockedError(RuntimeError):
    """Gate PL3 entry conditions not met."""


class ApprovalScope(BaseModel):
    """One artifact, one channel, one window. There is no wider shape."""

    artifact_hash: str
    channel: str
    window_start: str
    window_end: str

    @field_validator("artifact_hash", "channel", "window_start", "window_end")
    @classmethod
    def _no_wildcards(cls, value: str) -> str:
        if not value.strip() or value.strip() in {"*", "all", "any"}:
            raise ValueError(
                "approvals are scoped: one artifact, one channel, one window — "
                "wildcards have no representation (§21.61.5)"
            )
        return value


class SubstantiationMapEntry(BaseModel):
    sentence: str
    claim_id: str = ""  # empty = the sentence asserts nothing checkable
    evidence_locators: list[str] = Field(default_factory=list)


class PreflightSummary(BaseModel):
    check: str
    findings: int
    hard_fails: int = 0


class GatePL3Packet(BaseModel):
    artifact_id: str
    artifact_text: str  # the exact artifact as it will appear
    artifact_hash: str
    channel: str
    substantiation_map: list[SubstantiationMapEntry]
    disclosure_block: str
    preflight: list[PreflightSummary]
    diff_vs_last_approved: str
    rubric: tuple[str, ...] = (
        "Is every capability sentence backed by a claim I can open?",
        "Would I be comfortable if this were quoted back in a complaint?",
        "Does this respect the community/channel I'm about to enter?",
        "Is the cadence a ceiling I'm approaching, or a target I'm chasing?",
    )


class ApprovalRecord(BaseModel):
    scope: ApprovalScope
    approver: str  # a named human; the button is theirs
    decision: str  # approve | revise | reject

    @field_validator("approver")
    @classmethod
    def _named_human(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("an approval requires a named human approver")
        return value

    @field_validator("decision")
    @classmethod
    def _legal_outcome(cls, value: str) -> str:
        if value not in {"approve", "revise", "reject"}:
            raise ValueError("outcome is approve | revise | reject")
        return value


def artifact_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def build_substantiation_map(
    draft: Draft, register: ReleaseContract
) -> list[SubstantiationMapEntry]:
    entries = []
    for sentence in split_sentences(draft.text):
        hit = _match_register(sentence, register)
        entries.append(
            SubstantiationMapEntry(
                sentence=sentence,
                claim_id=hit.id if hit else "",
                evidence_locators=[
                    str(e.get("locator", "")) for e in (hit.evidence if hit else [])
                ],
            )
        )
    return entries


def assemble_gate_packet(
    draft: Draft,
    register: ReleaseContract,
    release_instrumentation_verified: bool,
    preflight_reports: dict[str, list],
    *,
    disclosure_block: str = "",
    last_approved_text: str = "",
) -> GatePL3Packet:
    """Assemble the approval surface. Entry conditions are preconditions:
    instrumentation verified and every backstop green, or the packet is not
    built at all — a human is never asked to approve past a red check."""
    if not release_instrumentation_verified:
        raise GateBlockedError(
            "release.instrumentation_verified is false — BLOCKED (§21.57.4); "
            "an unmeasurable campaign cannot be approved into existence"
        )
    dirty = {name: reports for name, reports in preflight_reports.items() if reports}
    if dirty:
        # Which of them have no override path. Every preflight scanner sets
        # `hard_fail` on the findings that cannot be argued down — consent,
        # suppression, a missing disclosure — and until ADR-060 no reader
        # anywhere consulted the flag, so the block message flattened "fix the
        # wording" and "this may never ship" into one list of check names.
        # The gate refuses on ANY finding either way; what changes is that the
        # human reading the refusal can now tell which kind of work is in
        # front of them.
        unoverridable = sorted(
            name for name, reports in dirty.items()
            if any(getattr(r, "hard_fail", False) for r in reports)
        )
        detail = ""
        if unoverridable:
            detail = (
                f"; {unoverridable} raised HARD failures — those have no "
                "override path and are not a wording problem"
            )
        raise GateBlockedError(
            f"backstops not green: {sorted(dirty)} — Gate PL3 entry requires "
            f"every deterministic check clean (§21.61.5){detail}"
        )
    return GatePL3Packet(
        artifact_id=draft.id,
        artifact_text=draft.text,
        artifact_hash=artifact_hash(draft.text),
        channel=draft.channel,
        substantiation_map=build_substantiation_map(draft, register),
        disclosure_block=disclosure_block,
        # Counted, not asserted. Both numbers were literals — `findings=0`
        # written in, `hard_fails` never written at all — which is true today
        # only because the entry condition above refuses any non-empty report.
        # A packet that states a fact it did not measure is a packet that will
        # keep stating it after the condition that made it true changes.
        preflight=[
            PreflightSummary(
                check=name,
                findings=len(reports),
                hard_fails=sum(
                    1 for r in reports if getattr(r, "hard_fail", False)
                ),
            )
            for name, reports in preflight_reports.items()
        ],
        diff_vs_last_approved="\n".join(
            difflib.unified_diff(
                last_approved_text.splitlines(),
                draft.text.splitlines(),
                fromfile="last-approved",
                tofile="draft",
                lineterm="",
            )
        ),
    )


def record_approval(
    packet: GatePL3Packet, scope: ApprovalScope, approver: str, decision: str
) -> ApprovalRecord:
    """Record a human decision. The scope must match the packet exactly —
    an approval minted for one artifact cannot be replayed onto another."""
    if scope.artifact_hash != packet.artifact_hash:
        raise ValueError(
            "approval scope hash does not match the presented artifact — "
            "approvals never generalize"
        )
    if scope.channel != packet.channel:
        raise ValueError("approval scope channel does not match the artifact")
    return ApprovalRecord(scope=scope, approver=approver, decision=decision)
