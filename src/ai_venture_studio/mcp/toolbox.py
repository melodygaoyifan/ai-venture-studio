"""MCPToolBox — the transport switch (doc 11 §17.1's "single config switch").

`ToolBox` and `MCPToolBox` present the same surface to `voters/base.py`:
`allowed`, `budget`, `calls_made`, `remaining`, `call(tool, args)`. The
difference is where the tool body executes — in this process, or in a
partitioned subprocess.

Default stays in-process: MCP costs a subprocess spawn per server per voter
invocation, and the honest reading of the benchmark cited in doc 11 §17.1
is that the isolation is worth paying for deliberately, not by surprise.
`AUTOPRODUCT_TOOL_TRANSPORT=mcp` (or `transport="mcp"`) opts in.

The *caller's* budget stays authoritative — the per-voter call ceiling is a
cost control, and cost is counted where the voter runs, not where the tool
body happens to execute.
"""

from __future__ import annotations

import os
import pathlib

from ai_venture_studio.mcp.host import MCPHost, MCPPermissionError
from ai_venture_studio.tools.voter_tools import VOTER_TOOL_REGISTRY, ToolBudgetExceeded

TRANSPORT_ENV = "AUTOPRODUCT_TOOL_TRANSPORT"
_RESULT_CHAR_CAP = 20_000


def tool_transport(explicit: str | None = None) -> str:
    """'mcp' | 'in_process' — explicit argument wins, then the environment."""
    value = (explicit or os.environ.get(TRANSPORT_ENV) or "in_process").strip().lower()
    if value not in ("mcp", "in_process"):
        raise ValueError(
            f"{TRANSPORT_ENV}={value!r} is not a transport; use 'mcp' or 'in_process'"
        )
    return value


class MCPToolBox:
    """Drop-in for ToolBox, backed by subprocess MCP servers."""

    def __init__(
        self,
        repo_dir: str | pathlib.Path,
        allowed: list[str],
        budget: int = 10,
        *,
        voter: str = "unknown",
        timeout_s: float = 30.0,
        risk_ceiling: int = 0,
    ):
        self.root = pathlib.Path(repo_dir).resolve()
        # Voter tools stay the L0 registry; L1/L2 stage tools are reachable
        # through MCPHost directly (deploy/maintenance/test stages), never
        # by widening a voter's allowlist.
        self.allowed = set(allowed) & VOTER_TOOL_REGISTRY
        self.budget = budget
        self.calls_made = 0
        # ADR-U03 taint isolation was implemented on both sides — TaintGuard
        # and the host's authorize() branch — and never connected, so a run
        # that consumed research kept its L1+ surface in practice. One guard
        # per toolbox: the unit of taint is the run, which is what a toolbox
        # instance already scopes.
        from ai_venture_studio.harness.taint_guard import TaintGuard

        self.taint = TaintGuard(session=voter)
        self._host = MCPHost(
            self.root, sorted(self.allowed), voter=voter, timeout_s=timeout_s,
            risk_ceiling=risk_ceiling, taint=self.taint,
        )
        self._started = False

    @property
    def remaining(self) -> int:
        return self.budget - self.calls_made

    def _ensure_started(self) -> None:
        if not self._started:
            self._host.start()
            self._started = True

    def call(self, tool: str, args: dict) -> str:
        if tool not in self.allowed:
            return f"error: tool {tool!r} is not in your allowlist {sorted(self.allowed)}"
        if self.calls_made >= self.budget:
            raise ToolBudgetExceeded(f"tool budget of {self.budget} calls exhausted")
        self.calls_made += 1
        self._ensure_started()
        try:
            result = self._host.call(tool, args or {})
        except MCPPermissionError as exc:
            # Unreachable through the allowlist check above unless the
            # partition table and the registry disagree — surfaced as data
            # so the voter degrades instead of crashing the review.
            return f"error: {exc}"
        return result[:_RESULT_CHAR_CAP]

    def close(self) -> None:
        if self._started:
            self._host.close()
            self._started = False

    def __enter__(self) -> "MCPToolBox":
        self._ensure_started()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def build_toolbox(
    repo_dir: str | pathlib.Path,
    allowed: list[str],
    budget: int = 10,
    *,
    voter: str = "unknown",
    transport: str | None = None,
    risk_ceiling: int = 0,
):
    """The one place that decides which transport a voter's tools run on.

    Both branches take the same four arguments. They did not: the in-process
    branch dropped `voter` and `risk_ceiling` on the floor, so choosing the
    DEFAULT transport silently chose the unguarded one. A switch whose two
    positions differ in their security properties is not a transport switch.
    """
    if tool_transport(transport) == "mcp":
        return MCPToolBox(
            repo_dir, allowed, budget, voter=voter, risk_ceiling=risk_ceiling
        )
    from ai_venture_studio.tools.voter_tools import ToolBox

    return ToolBox(
        repo_dir, allowed, budget, voter=voter, risk_ceiling=risk_ceiling
    )
