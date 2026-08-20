"""MCPHost — mounts only what a voter declared, audits every call.

The triple check of doc 11 §17.3, made real:

1. **spec** — the voter's frontmatter allowlist (already validated by
   `harness/spec_validator.py`) decides which tools exist for it.
2. **host** — this class mounts only the *servers* those tools live in. A
   voter allowlisting `read_file` never gets a `code_intel` connection, so
   `symbol_refs` is not merely refused, it is unreachable.
3. **server** — the subprocess refuses any tool outside its partition,
   independently (`mcp/server.py`).

Any one layer's bug therefore fails closed rather than escalating. Every
call — permitted or refused — appends a record to `.mas/mcp-audit.jsonl`,
which is the `mcp-audit` artifact the implementation map has carried as an
open item since v0.8.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import time

from ai_venture_studio.mcp.client import MCPClient, MCPClientError
from ai_venture_studio.mcp.server import SERVER_RISK, SERVER_TOOLS, server_for

# Imported at runtime, not under TYPE_CHECKING: the annotation on `taint`
# named a class nothing imported, so the type on this security boundary was
# checked by no one. A TYPE_CHECKING import would silence the linter and
# leave `get_type_hints()` raising the same NameError — the defect intact,
# only quieter. `taint_guard` imports nothing but stdlib, so there is no
# cycle and no startup cost to pay for resolving it here.
from ai_venture_studio.harness.taint_guard import TaintGuard

AUDIT_FILE = "mcp-audit.jsonl"


class MCPPermissionError(PermissionError):
    """A tool call outside the voter's mounted surface (audit-logged)."""


class MCPHost:
    """One host per voter invocation: mounts servers, routes calls, audits."""

    def __init__(
        self,
        repo_dir: str | pathlib.Path,
        allowed_tools: list[str],
        *,
        voter: str = "unknown",
        timeout_s: float = 30.0,
        audit_dir: str | pathlib.Path | None = None,
        risk_ceiling: int = 0,
        taint: TaintGuard | None = None,
    ):
        self.root = pathlib.Path(repo_dir).resolve()
        self.voter = voter
        self.timeout_s = timeout_s
        self.risk_ceiling = risk_ceiling
        # §13.31.2 / ADR-U03: a run that consumed research keeps its declared
        # ceiling on paper but loses L1+ in practice. Mounting is decided
        # from the DECLARED ceiling so the surface does not silently change
        # mid-run; the per-call authorize() is what refuses.
        self.taint = taint
        self.audit_path = (
            pathlib.Path(audit_dir) if audit_dir else self.root / ".mas"
        ) / AUDIT_FILE
        routable = [t for t in allowed_tools if server_for(t)]
        # Risk-tier RBAC (§17.2): a tool whose partition sits above the
        # caller's declared ceiling is refused HERE, where the connection
        # would be made — not in a prompt the model could talk around.
        self.over_ceiling_tools = sorted(
            t for t in routable
            if SERVER_RISK.get(server_for(t), 99) > risk_ceiling
        )
        # Layer 2: which servers does this allowlist actually require?
        self.allowed_tools = [t for t in routable if t not in self.over_ceiling_tools]
        self.mounted_servers = sorted(
            {server_for(t) for t in self.allowed_tools} - {None}
        )
        self.unroutable_tools = sorted(set(allowed_tools) - set(routable))
        self._clients: dict[str, MCPClient] = {}

    # --- lifecycle ------------------------------------------------------
    def start(self) -> "MCPHost":
        for server in self.mounted_servers:
            self._clients[server] = MCPClient(
                server, self.root, timeout_s=self.timeout_s
            ).start()
        return self

    def close(self) -> None:
        while self._clients:
            _name, client = self._clients.popitem()
            client.close()

    def __enter__(self) -> "MCPHost":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- discovery / calls ----------------------------------------------
    def available_tools(self) -> list[str]:
        """Tools reachable through the mounted servers ∩ the allowlist —
        constant-cost context (doc 11 §17.1): a voter never sees the global
        registry, only its own surface."""
        reachable = []
        for server, client in self._clients.items():
            for tool in client.list_tools():
                name = tool.get("name")
                if name in self.allowed_tools and name in SERVER_TOOLS[server]:
                    reachable.append(name)
        return sorted(reachable)

    def call(self, tool: str, arguments: dict) -> str:
        started = time.monotonic()
        server = server_for(tool)
        if self.taint is not None and self.taint.tainted:
            from ai_venture_studio.harness.taint_guard import ToolDenied

            try:
                self.taint.authorize(tool, SERVER_RISK.get(server))
            except ToolDenied as exc:
                self._audit(tool, server, arguments, "refused",
                            time.monotonic() - started,
                            detail=f"tainted session: {exc}"[:200])
                raise
        if tool in self.over_ceiling_tools:
            self._audit(tool, server, arguments, "refused",
                        time.monotonic() - started,
                        detail=f"risk tier {SERVER_RISK.get(server)} exceeds the "
                               f"caller's ceiling {self.risk_ceiling}")
            raise MCPPermissionError(
                f"voter {self.voter!r} may not call {tool!r}: its partition "
                f"{server!r} is risk L{SERVER_RISK.get(server)}, above the "
                f"declared ceiling L{self.risk_ceiling}"
            )
        if tool not in self.allowed_tools or server not in self._clients:
            self._audit(tool, server, arguments, "refused",
                        time.monotonic() - started,
                        detail="outside the voter's mounted surface")
            raise MCPPermissionError(
                f"voter {self.voter!r} attempted unauthorized tool call {tool!r}; "
                f"mounted servers: {self.mounted_servers}"
            )
        try:
            text = self._clients[server].call_tool(tool, arguments)
        except MCPClientError as exc:
            self._audit(tool, server, arguments, "transport_error",
                        time.monotonic() - started, detail=str(exc)[:200])
            # Transport failure is data for the voter, which then degrades to
            # BLOCKED_TOOL_FAILURE rather than guessing.
            return f"error: {exc}"
        if self.taint is not None:
            # Taint on evidence: research-wrapped content in ANY tool result
            # taints the run, so the next L1+ reach is denied.
            self.taint.observe_tool_result(text, source_id=f"{server}.{tool}")
        self._audit(tool, server, arguments, "ok", time.monotonic() - started,
                    detail=f"{len(text)} chars")
        return text

    # --- audit ----------------------------------------------------------
    def _audit(
        self, tool: str, server: str | None, arguments: dict, outcome: str,
        duration_s: float, *, detail: str = "",
    ) -> None:
        record = {
            "at": datetime.datetime.now(datetime.UTC).isoformat(),
            "voter": self.voter,
            "server": server,
            "tool": tool,
            # Arguments can carry repo paths and regexes; a digest keeps the
            # ledger useful for "what did it ask for, how often" without
            # copying searched content into a second place.
            "args_digest": hashlib.sha256(
                json.dumps(arguments, sort_keys=True, default=str).encode()
            ).hexdigest()[:16],
            "outcome": outcome,
            "duration_s": round(duration_s, 4),
            "detail": detail,
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_audit(repo_dir: str | pathlib.Path) -> list[dict]:
    """The mcp-audit ledger, oldest first (empty when nothing ran)."""
    path = pathlib.Path(repo_dir) / ".mas" / AUDIT_FILE
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn final line never invalidates the ledger
    return records
