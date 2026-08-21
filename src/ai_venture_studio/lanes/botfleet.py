"""Bot playtest fleet (doc 17 §45.2, doc 27 §79).

The game profile's one unbuilt check: *"scripted + agent-driven sessions
overnight"* hunting crashes, softlocks, unreachable states, and collision
anomalies — the failure classes a human finds by accident and a unit test
never reaches.

The design decision that makes this shippable without an engine: **the fleet
is defined by a session protocol, not by a game.** A bot session — Unity,
Unreal, a browser, or a fifty-line Python sim — emits newline-delimited JSON
events. Everything valuable here is a deterministic function over that
stream:

    {"t": 0, "kind": "tick", "state_hash": "a1", "pos": [0, 0], "reachable": 12}
    {"t": 1, "kind": "tick", "state_hash": "a1", "pos": [0, 0], "reachable": 12}
    ...                       ^ unchanged state + unchanged position = softlock

So the detectors are testable now, against real sessions of a real (if tiny)
deterministic simulation, rather than stubbed against an engine nobody here
has. Wiring a commercial engine is then an **adapter** that emits this
protocol — a day of integration work per engine, not a redesign.

What the fleet does NOT do, per §45.1: judge whether the game is any good.
It finds crashes and stuck states. `forbidden_autonomous` still covers
balance-constant changes, and the human playtest gate remains the release
gate no bot replaces.
"""

from __future__ import annotations

import logging
import collections
import json
import pathlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from ai_venture_studio.lanes.realtime import NETWORK_PROFILES

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

# A run of identical (state_hash, pos) ticks this long is a softlock: the
# simulation is advancing time without advancing anything else.
SOFTLOCK_TICKS = 30
# Sessions per fleet run, and the wall-clock ceiling per session.
DEFAULT_SESSIONS = 8
SESSION_TIMEOUT_S = 120


class SessionEvent(BaseModel):
    t: int = 0
    kind: str = "tick"  # tick | error | crash | goal | note
    state_hash: str = ""
    pos: list[float] = Field(default_factory=list)
    reachable: int | None = None
    message: str = ""


class Anomaly(BaseModel):
    kind: str  # crash | softlock | unreachable_regression | out_of_bounds | error
    tick: int
    detail: str
    # Sessions differing only by seed hit the same bug; this is what dedupes.
    signature: str


class SessionResult(BaseModel):
    session_id: str
    seed: int
    net_profile: str = ""
    ticks: int = 0
    exit_code: int | None = None
    anomalies: list[Anomaly] = Field(default_factory=list)
    note: str = ""


class FleetReport(BaseModel):
    status: str  # ok | findings | skipped | error
    detail: str = ""
    sessions: list[SessionResult] = Field(default_factory=list)
    # One entry per distinct signature, with the sessions that reproduced it.
    findings: list[dict] = Field(default_factory=list)


def parse_session(stream: str) -> list[SessionEvent]:
    """Parse a session's event stream. A malformed line is skipped rather
    than fatal — a crashing game often truncates its last line, and losing
    the whole session to that would hide the crash we came for."""
    events = []
    for line in (stream or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            try:
                events.append(SessionEvent.model_validate(payload))
            except Exception as exc:  # noqa: BLE001 — a bad event is not a bad session
                _log.debug("dropping a session event that will not "
                           "validate: %s", exc)
                continue
    return events


def detect(events: list[SessionEvent], *, bounds: float | None = None) -> list[Anomaly]:
    """Every anomaly class the profile names, as one deterministic pass.

    Signatures are deliberately coarse — the anomaly kind plus the *place*
    it happened (state hash or message shape), never the tick number — so
    the same bug found by twelve seeds dedupes to one finding instead of
    twelve.
    """
    anomalies: list[Anomaly] = []
    run_key: tuple[str, tuple] | None = None
    run_len = 0
    run_start = 0
    peak_reachable: int | None = None
    # A CONTINUING condition is one bug, not one per tick: a bot that walks
    # out of the world stays out for the rest of the session. Without this,
    # the first real fleet run over the toy sim produced 44 findings for one
    # escaping bot.
    seen: set[str] = set()

    def record(anomaly: Anomaly) -> None:
        if anomaly.signature in seen:
            return
        seen.add(anomaly.signature)
        anomalies.append(anomaly)

    for event in events:
        if event.kind == "crash":
            record(Anomaly(
                kind="crash", tick=event.t,
                detail=event.message or "session reported a crash",
                signature=f"crash:{_shape(event.message)}",
            ))
            continue
        if event.kind == "error":
            record(Anomaly(
                kind="error", tick=event.t,
                detail=event.message or "session reported an error",
                signature=f"error:{_shape(event.message)}",
            ))
            continue
        if event.kind != "tick":
            continue

        # Out of bounds: a position outside the declared play area is the
        # clipping/fall-through-the-world class.
        if bounds is not None and event.pos:
            breached = [
                (i, c) for i, c in enumerate(float(c) for c in event.pos)
                if abs(c) > bounds
            ]
            if breached:
                # Signature names WHICH axis and side left the play area, not
                # how far along it the bot got: walking further out is the
                # same bug, and a per-position signature made one escape look
                # like forty.
                axes = ",".join(
                    f"axis{i}{'+' if value > 0 else '-'}" for i, value in breached
                )
                record(Anomaly(
                    kind="out_of_bounds", tick=event.t,
                    detail=f"position {event.pos} outside ±{bounds} "
                           f"(first breach at t={event.t})",
                    signature=f"out_of_bounds:{axes}",
                ))

        # Unreachable regression: the reachable-state count must never shrink
        # within a session — a shrinking frontier means the bot walked into
        # somewhere it cannot leave.
        if event.reachable is not None:
            if peak_reachable is not None and event.reachable < peak_reachable:
                record(Anomaly(
                    kind="unreachable_regression", tick=event.t,
                    detail=f"reachable states fell {peak_reachable} → "
                           f"{event.reachable}",
                    signature=f"unreachable:{event.state_hash or 'unknown'}",
                ))
            peak_reachable = max(peak_reachable or 0, event.reachable)

        # Softlock: time advances, nothing else does.
        key = (event.state_hash, tuple(event.pos))
        if key == run_key:
            run_len += 1
            if run_len == SOFTLOCK_TICKS:
                record(Anomaly(
                    kind="softlock", tick=run_start,
                    detail=f"state and position unchanged for {SOFTLOCK_TICKS} "
                           f"ticks from t={run_start}",
                    signature=f"softlock:{event.state_hash or 'unknown'}",
                ))
        else:
            run_key, run_len, run_start = key, 1, event.t
    return anomalies


def _shape(message: str) -> str:
    """A message's shape, not its text: digits and hex collapse so that
    'index 41 out of range' and 'index 7 out of range' dedupe together."""
    import re

    shape = re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", "N", str(message or ""))
    return " ".join(shape.split())[:120]


def run_session(
    command: list[str],
    *,
    seed: int,
    session_id: str,
    cwd: str | pathlib.Path = ".",
    net_profile: str = "",
    bounds: float | None = None,
    timeout_s: float = SESSION_TIMEOUT_S,
) -> SessionResult:
    """One bot session. The seed is passed through the environment so a
    reproduction is `AUTOPRODUCT_BOT_SEED=<n> <command>` — a bug a fleet run
    found must be replayable by hand, or it is not actionable."""
    import os

    env = {**os.environ, "AUTOPRODUCT_BOT_SEED": str(seed)}
    if net_profile:
        env["AUTOPRODUCT_NET_PROFILE"] = net_profile
    try:
        proc = subprocess.run(  # noqa: S603 — argv from the caller's config
            command, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return SessionResult(
            session_id=session_id, seed=seed, net_profile=net_profile,
            exit_code=None,
            anomalies=[Anomaly(kind="crash", tick=-1,
                               detail=f"session exceeded {timeout_s}s",
                               signature="crash:timeout")],
            note="timed out",
        )
    except (OSError, ValueError) as exc:
        return SessionResult(
            session_id=session_id, seed=seed, net_profile=net_profile,
            exit_code=None, note=f"could not start: {exc}"[:200],
        )

    events = parse_session(proc.stdout)
    anomalies = detect(events, bounds=bounds)
    # A non-zero exit with no reported crash event is still a crash: the
    # process died without getting to say so.
    if proc.returncode not in (0, None) and not any(
        a.kind == "crash" for a in anomalies
    ):
        anomalies.append(Anomaly(
            kind="crash", tick=events[-1].t if events else -1,
            detail=f"exit code {proc.returncode}: "
                   f"{(proc.stderr or '').strip()[:160]}",
            signature=f"crash:exit-{proc.returncode}",
        ))
    return SessionResult(
        session_id=session_id, seed=seed, net_profile=net_profile,
        ticks=sum(1 for e in events if e.kind == "tick"),
        exit_code=proc.returncode, anomalies=anomalies,
    )


def run_fleet(
    command: list[str],
    *,
    cwd: str | pathlib.Path = ".",
    sessions: int = DEFAULT_SESSIONS,
    base_seed: int = 1,
    net_profiles: tuple[str, ...] | None = None,
    bounds: float | None = None,
    workers: int = 4,
    timeout_s: float = SESSION_TIMEOUT_S,
) -> FleetReport:
    """Run N sessions in parallel across the declared network profiles and
    triage what they hit.

    Availability-gated like every other external: no runnable command means
    a visible skip, because "the fleet found nothing" and "the fleet never
    ran" must not look alike.
    """
    if not command:
        return FleetReport(
            status="skipped",
            detail="no bot session command configured — set one in the game "
                   "profile (checks.bot_playtest.command); a skipped fleet is "
                   "reported, never counted as a clean overnight run",
        )
    import shutil

    if not (pathlib.Path(cwd) / command[0]).exists() and not shutil.which(command[0]):
        return FleetReport(
            status="skipped",
            detail=f"{command[0]!r} is not executable here — the fleet did not "
                   "run; this is not a clean result",
        )

    profiles = tuple(net_profiles or ("",))
    for profile in profiles:
        if profile and profile not in NETWORK_PROFILES:
            return FleetReport(
                status="error",
                detail=f"unknown network profile {profile!r}; declared profiles "
                       f"are {list(NETWORK_PROFILES)} (doc 27 §79)",
            )

    plan = [
        (base_seed + i, profiles[i % len(profiles)])
        for i in range(max(1, sessions))
    ]
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(plan)))) as pool:
        results = list(pool.map(
            lambda spec: run_session(
                command, seed=spec[0], session_id=f"s{spec[0]}", cwd=cwd,
                net_profile=spec[1], bounds=bounds, timeout_s=timeout_s,
            ),
            plan,
        ))

    grouped: dict[str, list[SessionResult]] = collections.defaultdict(list)
    for result in results:
        for anomaly in result.anomalies:
            grouped[anomaly.signature].append(result)
    findings = []
    for signature, hits in sorted(grouped.items()):
        example = next(
            a for a in hits[0].anomalies if a.signature == signature
        )
        findings.append({
            "signature": signature,
            "kind": example.kind,
            "detail": example.detail,
            "sessions": len(hits),
            # The reproduction command, not just the fact of the bug.
            "reproduce": f"AUTOPRODUCT_BOT_SEED={hits[0].seed} "
                         + (f"AUTOPRODUCT_NET_PROFILE={hits[0].net_profile} "
                            if hits[0].net_profile else "")
                         + " ".join(command),
            "net_profiles": sorted({h.net_profile for h in hits if h.net_profile}),
        })

    total_ticks = sum(r.ticks for r in results)
    if findings:
        return FleetReport(
            status="findings",
            detail=f"{len(findings)} distinct anomaly/anomalies across "
                   f"{len(results)} session(s), {total_ticks} tick(s)",
            sessions=results, findings=findings,
        )
    return FleetReport(
        status="ok",
        detail=f"{len(results)} session(s), {total_ticks} tick(s), no anomalies "
               "— crashes and stuck states only; whether the game is FUN is the "
               "human playtest gate's question (§45.1)",
        sessions=results,
    )
