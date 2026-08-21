"""The realtime delta (doc 27 Part 79) — netcode as checkable claims.

The network model is declared, not discovered; determinism leaks are
scanned where they actually break (simulation code); and the testable core
is replay identity: same input stream ⇒ same state-hash sequence, every
time. A desync anywhere is an incident, never a nuisance (invariant 14.26).
"""

from __future__ import annotations

import re

from pydantic import BaseModel

NET_MODELS = ("server_authoritative", "rollback", "lockstep", "relay_lockstep")
DETERMINISTIC_MODELS = ("rollback", "lockstep", "relay_lockstep")
NETWORK_PROFILES = ("wifi_poor", "mobile_4g", "intercontinental")

# Determinism leak sources, enumerable (§79.2).
_LEAKS = (
    ("float_equality", re.compile(r"==\s*\d+\.\d+|\d+\.\d+\s*=="),
     "float equality in simulation — fixed-point/integer math expected"),
    ("float_accumulation", re.compile(r"\+=\s*\d+\.\d+"),
     "float accumulation drifts across platforms"),
    ("unseeded_rng", re.compile(r"\brandom\.(random|randint|choice|shuffle)\("),
     "non-injected RNG — the simulation's RNG must be seeded and passed in"),
    ("dict_iteration", re.compile(r"for\s+\w+\s+in\s+[\w.]+\.(keys|values|items)\(\)|for\s+\w+\s+in\s+set\("),
     "hash-order iteration in tick code — iteration order is not a contract"),
    ("wall_clock", re.compile(r"\btime\.(time|monotonic|perf_counter)\(|datetime\.now\("),
     "mid-frame wall-clock read — time enters the simulation as a tick"),
    ("tight_reconnect", re.compile(r"while\s+.*:\s*\n\s*(?:await\s+)?\w*\.?(re)?connect\(", re.M),
     "tight reconnect loop — exponential backoff with jitter required (F-27.2)"),
)


class SimScanFinding(BaseModel):
    rule: str
    line: int
    message: str


def det_sim_scan(simulation_source: str) -> list[SimScanFinding]:
    findings = []
    for lineno, line in enumerate(simulation_source.splitlines(), start=1):
        for rule, pattern, message in _LEAKS:
            if pattern.search(line):
                findings.append(SimScanFinding(rule=rule, line=lineno, message=message))
    # multi-line reconnect pattern
    for rule, pattern, message in _LEAKS:
        if rule == "tight_reconnect" and pattern.search(simulation_source):
            if not any(f.rule == "tight_reconnect" for f in findings):
                findings.append(SimScanFinding(rule=rule, line=0, message=message))
    return findings


class NetModelIssue(BaseModel):
    rule: str
    message: str


def check_net_model(design: dict) -> list[NetModelIssue]:
    """A realtime design without a declared model escalates — every
    downstream check keys off it (§79.1)."""
    issues = []
    model = design.get("net_model")
    if model not in NET_MODELS:
        issues.append(NetModelIssue(
            rule="ESCALATE_REQUIREMENT_CONFLICT",
            message=f"net_model {model!r} not declared as one of {NET_MODELS}"))
        return issues
    if not isinstance(design.get("tick_rate"), int) or design["tick_rate"] <= 0:
        issues.append(NetModelIssue(rule="missing_tick_rate",
                                    message="tick_rate (simulation Hz) required"))
    snapshot = design.get("snapshot_policy") or {}
    if model in DETERMINISTIC_MODELS and not snapshot.get("hash_every_n_ticks"):
        issues.append(NetModelIssue(
            rule="missing_hash_cadence",
            message=f"{model} requires snapshot_policy.hash_every_n_ticks — "
                    "the desync probe has no window without it"))
    return issues


class ReplayVerdict(BaseModel):
    check: str
    passed: bool
    detail: str


def replay_identity(hash_sequences: list[list[str]]) -> ReplayVerdict:
    """N replays of the same input stream ⇒ byte-identical hash sequences."""
    identical = len({tuple(s) for s in hash_sequences}) == 1 and len(hash_sequences) >= 2
    return ReplayVerdict(
        check="replay_identity", passed=identical,
        detail="identical across replays" if identical else
        "hash sequences diverged — the simulation is not deterministic; "
        "this is an incident, not a nuisance (invariant 14.26)")


def cross_build_replay(
    old_hashes: list[str], new_hashes: list[str], *, change_expected_from_tick: int | None
) -> ReplayVerdict:
    """The same stream on two builds diverges only where the diff says it
    should — silent behavior change is a finding."""
    # Deliberately the common prefix: the length mismatch is a separate
    # answer, given on the next line, and it is not "no divergence".
    first_divergence = next(
        (i for i, (a, b) in enumerate(
            zip(old_hashes, new_hashes, strict=False)) if a != b), None)
    if first_divergence is None and len(old_hashes) == len(new_hashes):
        passed = change_expected_from_tick is None
        detail = ("identical, as the diff predicts" if passed else
                  "the diff promised a behavior change and none occurred")
    elif change_expected_from_tick is not None and first_divergence is not None \
            and first_divergence >= change_expected_from_tick:
        passed, detail = True, f"diverges at tick {first_divergence}, at/after the declared change"
    elif first_divergence is None:
        # Identical over the prefix, different lengths. Reported as what it is
        # rather than as "divergence at tick None", which is what this branch
        # used to say (ADR-062): one stream ran longer, and a reader has to be
        # able to tell that from a hash that changed mid-stream.
        passed = False
        detail = (f"the streams agree for "
                  f"{min(len(old_hashes), len(new_hashes))} tick(s) and then "
                  f"one build simply stops: old {len(old_hashes)}, new "
                  f"{len(new_hashes)}")
    else:
        passed = False
        detail = (f"silent behavior change: divergence at tick {first_divergence} "
                  "with no declared cause in the diff")
    return ReplayVerdict(check="cross_build_replay", passed=passed, detail=detail)


def desync_probe(
    server_hashes: list[str], client_hashes: list[str], *, hash_every_n_ticks: int
) -> ReplayVerdict:
    """A corrupted client must be detected within the declared window."""
    # `strict=False` compares the common prefix, and the common prefix is not
    # the whole question: a client that stopped early has desynced in the most
    # complete way available, and this probe used to read that as "no
    # divergence" — a silent pass on the exact failure it exists to catch
    # (ADR-062, found by turning B905 on). The truncation is checked FIRST,
    # because a short stream has no divergence to find.
    if len(server_hashes) != len(client_hashes):
        return ReplayVerdict(
            check="desync_probe", passed=False,
            detail=f"streams are different lengths — server {len(server_hashes)} "
                   f"hash(es), client {len(client_hashes)}. A client that "
                   f"stopped producing is desynced; declared window "
                   f"{hash_every_n_ticks} ticks — file the incident (14.26)")
    first = next((i for i, (s, c) in enumerate(
        zip(server_hashes, client_hashes, strict=True)) if s != c), None)
    if first is None:
        return ReplayVerdict(check="desync_probe", passed=True, detail="no divergence")
    detected_within = hash_every_n_ticks
    return ReplayVerdict(
        check="desync_probe", passed=first is not None,
        detail=f"desync detectable at tick {first}; declared window "
               f"{detected_within} ticks — file the incident (14.26)")


def tick_budget_ok(p99_tick_ms: float, tick_rate: int) -> bool:
    """p99 per-tick cost × tick rate must fit inside one wall-clock second."""
    return p99_tick_ms * tick_rate < 1000.0
