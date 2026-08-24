"""Structured domain-profile schema (doc 17 §41.1) — profiles as
machine-readable, ADD-ONLY deltas that compose, never fork.

Extends the prose profiles (constraints/spec_extras) with the structured
fields the design names: det_tools_add, voter_deltas, artifact_add,
gates_add, forbidden_autonomous_add, done_vocabulary, nfr_vocabulary,
paths. The validator is the same posture as edition_lint and the channel
loader: a profile may only add; removal-shaped keys are refused, and
composition of several profiles is the union of their additions.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

STRUCTURED_ADD_KEYS = (
    "det_tools_add", "voter_deltas", "artifact_add", "gates_add",
    "forbidden_autonomous_add", "done_vocabulary", "nfr_vocabulary", "paths",
)
_PROSE_KEYS = ("name", "description", "constraints", "spec_extras", "checks",
               "stack_hint")
_REMOVAL_SHAPED = ("det_tools_remove", "voter_remove", "gates_remove",
                   "checks_remove", "forbidden_autonomous_remove",
                   "skip_stages", "disable_checks")


class ProfileSchemaError(RuntimeError):
    """A profile that widens or that the harness cannot vouch for."""


class StructuredProfile(BaseModel):
    name: str
    det_tools_add: list[str] = Field(default_factory=list)
    voter_deltas: list[str] = Field(default_factory=list)
    artifact_add: list[str] = Field(default_factory=list)
    gates_add: list[str] = Field(default_factory=list)
    forbidden_autonomous_add: list[str] = Field(default_factory=list)
    done_vocabulary: list[str] = Field(default_factory=list)
    nfr_vocabulary: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)  # globs the delta applies to


def validate_profile(raw: dict) -> StructuredProfile:
    if not isinstance(raw, dict) or not raw.get("name"):
        raise ProfileSchemaError("profile must be a mapping with a name")
    removals = [k for k in raw if k in _REMOVAL_SHAPED]
    if removals:
        raise ProfileSchemaError(
            f"{raw.get('name')}: removal-shaped keys {removals} — profiles are "
            "deltas that may only ADD (§41.1, ADR-U12)")
    unknown = set(raw) - set(STRUCTURED_ADD_KEYS) - set(_PROSE_KEYS)
    if unknown:
        raise ProfileSchemaError(
            f"{raw.get('name')}: unknown profile keys {sorted(unknown)} — the "
            "harness refuses what it cannot vouch for")
    return StructuredProfile(
        name=str(raw["name"]),
        **{k: [str(x) for x in raw.get(k) or []] for k in STRUCTURED_ADD_KEYS})


def compose_profiles(profiles: list[StructuredProfile]) -> StructuredProfile:
    """Multi-profile composition (§41.1): the union of additions. Nothing
    a later profile says can subtract what an earlier one added."""
    merged: dict[str, list[str]] = {k: [] for k in STRUCTURED_ADD_KEYS}
    for profile in profiles:
        for key in STRUCTURED_ADD_KEYS:
            for value in getattr(profile, key):
                if value not in merged[key]:
                    merged[key].append(value)
    return StructuredProfile(
        name="+".join(p.name for p in profiles) or "empty", **merged)
