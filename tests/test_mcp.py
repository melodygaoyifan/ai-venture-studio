"""v0.37.0 — MCP as the internal tool transport (doc 11 §17).

These tests run REAL subprocesses: the point of the layer is OS-level
isolation, and a mocked transport would prove nothing about it.
"""

from __future__ import annotations

import io
import json
import os

import pytest

from ai_venture_studio.mcp import protocol
from ai_venture_studio.mcp.client import MCPClient, MCPClientError
from ai_venture_studio.mcp.host import MCPHost, MCPPermissionError, read_audit
from ai_venture_studio.mcp.server import SERVER_TOOLS, serve, server_for
from ai_venture_studio.mcp.toolbox import (
    TRANSPORT_ENV,
    MCPToolBox,
    build_toolbox,
    tool_transport,
)
from ai_venture_studio.tools.voter_tools import (
    VOTER_TOOL_REGISTRY,
    ToolBox,
    ToolBudgetExceeded,
)
from ai_venture_studio.executables import resolve


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "billing.py").write_text(
        "def invoice_total(items):\n    return sum(i.price for i in items)\n",
        encoding="utf-8",
    )
    (tmp_path / "secret_outside.txt").write_text("not reachable", encoding="utf-8")
    return tmp_path


# --- partition table ----------------------------------------------------------


def test_every_tool_is_served_by_exactly_one_partition():
    from ai_venture_studio.mcp.server import SERVER_RISK
    from ai_venture_studio.mcp.stage_tools import stage_tool_names

    served = [t for tools in SERVER_TOOLS.values() for t in tools]
    assert len(served) == len(set(served)), "a tool may live in only one partition"
    # Both surfaces are fully partitioned, and they do not overlap.
    assert set(served) == set(VOTER_TOOL_REGISTRY) | set(stage_tool_names())
    assert not set(VOTER_TOOL_REGISTRY) & set(stage_tool_names())
    for tool in [*VOTER_TOOL_REGISTRY, *stage_tool_names()]:
        assert server_for(tool) in SERVER_TOOLS
    # Every partition declares a risk tier.
    assert set(SERVER_RISK) == set(SERVER_TOOLS)


def test_risk_tiers_match_the_design_table():
    """§17.2: read-only L0, deploy/maintenance L1, test execution L2."""
    from ai_venture_studio.mcp.server import SERVER_RISK

    assert SERVER_RISK["read_only"] == SERVER_RISK["code_intel"] == 0
    assert SERVER_RISK["deploy"] == SERVER_RISK["maintenance"] == 1
    assert SERVER_RISK["test_exec"] == 2  # it executes repo code


# --- protocol -----------------------------------------------------------------


def test_protocol_roundtrip_and_malformed_line():
    line = protocol.encode(protocol.request(1, "tools/list"))
    assert line.endswith("\n") and "\n" not in line[:-1]
    assert protocol.read_message_from_line(line)["method"] == "tools/list"
    with pytest.raises(protocol.ProtocolError, match="malformed"):
        protocol.read_message_from_line("{not json")
    with pytest.raises(protocol.ProtocolError, match="must be an object"):
        protocol.read_message_from_line("[1,2]")


def test_server_loop_serves_list_and_call_over_streams(repo):
    """Drive serve() directly over string streams — the wire contract."""
    requests = "".join(
        protocol.encode(m) for m in [
            protocol.request(1, "initialize", {"protocolVersion": protocol.PROTOCOL_VERSION}),
            protocol.request(2, "tools/list"),
            protocol.request(3, "tools/call", {
                "name": "read_file", "arguments": {"path": "app/billing.py"}}),
            protocol.request(4, "tools/call", {
                "name": "symbol_refs", "arguments": {"symbol": "x"}}),  # wrong partition
            protocol.request(5, "shutdown"),
        ]
    )
    out = io.StringIO()
    serve("read_only", repo, stdin=io.StringIO(requests), stdout=out)
    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    by_id = {m["id"]: m for m in messages}
    assert by_id[1]["result"]["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert {t["name"] for t in by_id[2]["result"]["tools"]} == set(
        SERVER_TOOLS["read_only"]
    )
    assert "invoice_total" in by_id[3]["result"]["content"][0]["text"]
    # The server-side half of the triple check.
    assert by_id[4]["error"]["code"] == protocol.TOOL_NOT_PERMITTED
    assert "does not serve" in by_id[4]["error"]["message"]


def test_unknown_server_name_refuses_to_start(repo):
    with pytest.raises(SystemExit, match="unknown server"):
        serve("nope", repo, stdin=io.StringIO(""), stdout=io.StringIO())


# --- client over a real subprocess -------------------------------------------


def test_client_talks_to_a_real_subprocess(repo):
    with MCPClient("read_only", repo) as client:
        names = {t["name"] for t in client.list_tools()}
        assert names == set(SERVER_TOOLS["read_only"])
        text = client.call_tool("read_file", {"path": "app/billing.py"})
        assert "invoice_total" in text
        # Path traversal is refused inside the subprocess, not the harness.
        escaped = client.call_tool("read_file", {"path": "../secret_outside.txt"})
        assert "escapes the repository root" in escaped


def test_client_surfaces_server_side_refusal(repo):
    with MCPClient("code_intel", repo) as client:
        with pytest.raises(MCPClientError, match="does not serve"):
            client.call_tool("grep", {"pattern": "x"})


def test_calls_before_start_error_cleanly(repo):
    client = MCPClient("read_only", repo)
    with pytest.raises(MCPClientError, match="not started"):
        client.list_tools()


# --- host: mounting, triple check, audit --------------------------------------


def test_host_mounts_only_the_servers_the_allowlist_needs(repo):
    with MCPHost(repo, ["read_file", "grep"], voter="security") as host:
        assert host.mounted_servers == ["read_only"]
        assert host.available_tools() == ["grep", "read_file"]
        # code_intel was never mounted: symbol_refs is unreachable, not merely
        # refused (doc 11 §17.3 layer 2).
        with pytest.raises(MCPPermissionError, match="unauthorized tool call"):
            host.call("symbol_refs", {"symbol": "invoice_total"})


def test_host_mounts_both_partitions_when_needed(repo):
    with MCPHost(repo, ["read_file", "symbol_refs"], voter="correctness") as host:
        assert host.mounted_servers == ["code_intel", "read_only"]
        assert host.available_tools() == ["read_file", "symbol_refs"]
        assert "invoice_total" in host.call("read_file", {"path": "app/billing.py"})
        assert host.call("symbol_refs", {"symbol": "invoice_total"})


def test_audit_ledger_records_permitted_and_refused_calls(repo):
    with MCPHost(repo, ["read_file"], voter="style") as host:
        host.call("read_file", {"path": "app/billing.py"})
        with pytest.raises(MCPPermissionError):
            host.call("grep", {"pattern": "x"})
    records = read_audit(repo)
    assert [r["outcome"] for r in records] == ["ok", "refused"]
    assert {r["voter"] for r in records} == {"style"}
    assert records[0]["tool"] == "read_file" and records[0]["server"] == "read_only"
    assert records[1]["tool"] == "grep"
    assert "outside the voter's mounted surface" in records[1]["detail"]
    # Arguments are digested, not copied — the ledger says what was asked
    # for and how often without duplicating searched content.
    assert "billing.py" not in json.dumps(records)
    assert len(records[0]["args_digest"]) == 16


def test_unroutable_allowlist_entries_are_reported_not_silently_dropped(repo):
    # lsp_references is named in §17.2 but has no MCP registration yet: a
    # tool no partition serves is REPORTED, not quietly dropped.
    host = MCPHost(repo, ["read_file", "lsp_references"], voter="test")
    assert host.unroutable_tools == ["lsp_references"]
    assert host.mounted_servers == ["read_only"]


# --- the transport switch -----------------------------------------------------


def test_transport_defaults_to_in_process_and_validates(monkeypatch):
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    assert tool_transport() == "in_process"
    monkeypatch.setenv(TRANSPORT_ENV, "mcp")
    assert tool_transport() == "mcp"
    assert tool_transport("in_process") == "in_process"  # explicit wins
    monkeypatch.setenv(TRANSPORT_ENV, "carrier-pigeon")
    with pytest.raises(ValueError, match="not a transport"):
        tool_transport()


def test_build_toolbox_honors_the_switch(repo, monkeypatch):
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    assert isinstance(build_toolbox(repo, ["read_file"]), ToolBox)
    monkeypatch.setenv(TRANSPORT_ENV, "mcp")
    box = build_toolbox(repo, ["read_file"], voter="security")
    assert isinstance(box, MCPToolBox)
    box.close()


def test_mcp_toolbox_matches_the_in_process_surface(repo):
    """Same contract: allowlist refusal as data, budget as an exception,
    identical tool output."""
    plain = ToolBox(repo, ["read_file"], budget=2)
    with MCPToolBox(repo, ["read_file"], budget=2, voter="security") as mcp_box:
        assert mcp_box.remaining == plain.remaining == 2
        assert mcp_box.call("read_file", {"path": "app/billing.py"}) == plain.call(
            "read_file", {"path": "app/billing.py"}
        )
        assert mcp_box.calls_made == plain.calls_made == 1

        # An allowlist refusal is data and costs no budget, on both.
        refusal = mcp_box.call("grep", {"pattern": "x"})
        assert refusal.startswith("error: tool 'grep' is not in your allowlist")
        assert refusal == plain.call("grep", {"pattern": "x"})
        assert mcp_box.calls_made == plain.calls_made == 1

        # Budget is the caller's: the second call spends it, the third raises.
        mcp_box.call("read_file", {"path": "app/billing.py"})
        plain.call("read_file", {"path": "app/billing.py"})
        assert mcp_box.remaining == plain.remaining == 0
        with pytest.raises(ToolBudgetExceeded):
            mcp_box.call("read_file", {"path": "app/billing.py"})
        with pytest.raises(ToolBudgetExceeded):
            plain.call("read_file", {"path": "app/billing.py"})


def test_voter_investigation_runs_over_mcp_end_to_end(repo, monkeypatch):
    """The whole point: a voter's tool loop works over the partitioned
    transport, and the audit ledger proves the calls left the process."""
    from ai_venture_studio.paths import skills_root
    from ai_venture_studio.voters import load_voters

    monkeypatch.setenv(TRANSPORT_ENV, "mcp")
    diff = (
        "diff --git a/app/billing.py b/app/billing.py\n"
        "--- a/app/billing.py\n+++ b/app/billing.py\n@@ -1,1 +1,1 @@\n"
        "+    q = f\"SELECT * FROM t WHERE a = '{a}'\"\n"
    )
    voters = [
        v for v in load_voters(skills_root(), provider_override="mock")
        if v.spec.name == "security"
    ]
    output = voters[0].run(diff, context="", repo_dir=str(repo))
    assert output.status.value == "OK"
    assert any("SQL" in f.title for f in output.findings)


def test_in_process_transport_writes_no_audit(repo, monkeypatch):
    """The ledger is an MCP artifact; the in-process path must not fake one."""
    monkeypatch.delenv(TRANSPORT_ENV, raising=False)
    box = build_toolbox(repo, ["read_file"], voter="style")
    box.call("read_file", {"path": "app/billing.py"})
    assert read_audit(repo) == []


def test_mcp_server_module_is_runnable_as_a_subprocess_entry_point(repo):
    """`python -m ai_venture_studio.mcp.server` is the documented spawn path."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "ai_venture_studio.mcp.server", "read_only",
         "--root", str(repo)],
        input=protocol.encode(protocol.request(1, "tools/list")),
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
    )
    assert proc.returncode == 0, proc.stderr
    message = json.loads(proc.stdout.splitlines()[0])
    assert {t["name"] for t in message["result"]["tools"]} == set(
        SERVER_TOOLS["read_only"]
    )


# --- L1/L2 partitions and risk-tier RBAC (v0.40.0) ----------------------------


DEPLOY_DIFF = (
    "diff --git a/migrations/0044_drop.sql b/migrations/0044_drop.sql\n"
    "--- a/migrations/0044_drop.sql\n+++ b/migrations/0044_drop.sql\n"
    "@@ -1,0 +1,1 @@\n+DROP TABLE legacy_orders;\n"
)


def test_risk_ceiling_blocks_mounting_a_higher_tier_partition(repo):
    """A voter declaring risk_ceiling 0 cannot reach L1/L2 even if its
    allowlist names one of their tools — refused where the connection would
    be made, not in a prompt."""
    host = MCPHost(repo, ["read_file", "migration_scan", "run_tests"],
                   voter="security", risk_ceiling=0)
    assert host.over_ceiling_tools == ["migration_scan", "run_tests"]
    assert host.mounted_servers == ["read_only"]
    with host:
        with pytest.raises(MCPPermissionError, match="above the declared ceiling"):
            host.call("migration_scan", {"diff_text": DEPLOY_DIFF})
    refusal = read_audit(repo)[-1]
    assert refusal["outcome"] == "refused"
    assert "exceeds the caller's ceiling" in refusal["detail"]


def test_l1_deploy_partition_serves_the_probes_in_a_subprocess(repo):
    # run_tests is allowlisted here so the refusal below is about the risk
    # tier, not about the allowlist.
    with MCPHost(repo, ["migration_scan", "canary_scan", "run_tests"],
                 voter="deploy_config", risk_ceiling=1) as host:
        assert host.mounted_servers == ["deploy"]  # test_exec never mounted
        assert host.over_ceiling_tools == ["run_tests"]
        payload = json.loads(host.call("migration_scan", {"diff_text": DEPLOY_DIFF}))
        assert payload["tool"] == "migration_scan"
        assert any(f["severity"] == "critical" for f in payload["findings"])
        # L2 stays out of reach at ceiling 1, even though it is allowlisted.
        with pytest.raises(MCPPermissionError, match="above the declared ceiling"):
            host.call("run_tests", {})


def test_l1_maintenance_partition_reads_git_history(repo):
    import subprocess
    import sys as _sys

    subprocess.run([resolve("git"), "init", "-q"], cwd=repo, check=True)
    subprocess.run([resolve("git"), "add", "."], cwd=repo, check=True)
    subprocess.run(
        [resolve("git"), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "billing: invoice_total over items"],
        cwd=repo, check=True,
    )
    with MCPHost(repo, ["recent_commits", "correlate"], voter="rootcause",
                 risk_ceiling=1) as host:
        assert host.mounted_servers == ["maintenance"]
        commits = json.loads(host.call("recent_commits", {"days": 30}))
        assert commits and "invoice_total" in commits[0]["subject"]
        suspects = json.loads(host.call(
            "correlate", {"incident_text": "TypeError in invoice_total", "days": 30}
        ))
        assert suspects and suspects[0]["score"] > 0
    assert _sys.executable  # sanity: the subprocess path was used


def test_l2_test_exec_partition_requires_the_top_ceiling(repo, monkeypatch):
    """run_tests executes repo code, so only a ceiling-2 caller may mount it."""
    import ai_venture_studio.testing as testing_mod

    monkeypatch.setattr(testing_mod, "docker_available", lambda: False)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    with MCPHost(repo, ["run_tests"], voter="test_gate", risk_ceiling=2) as host:
        assert host.mounted_servers == ["test_exec"]
        payload = json.loads(host.call("run_tests", {"diff_text": "", "mode": "fast"}))
        assert "status" in payload
    assert [r["server"] for r in read_audit(repo)] == ["test_exec"]


def test_stage_tools_return_errors_as_data(repo):
    from ai_venture_studio.mcp.stage_tools import call_stage_tool, risk_of, stage_tool_names

    assert risk_of("migration_scan") == 1 and risk_of("run_tests") == 2
    assert risk_of("read_file") is None  # L0 lives in the ToolBox, not here
    assert "error: unknown stage tool" in call_stage_tool("nope", repo, {})
    assert "bad arguments" in call_stage_tool("migration_scan", repo, {"wrong": 1})
    from ai_venture_studio.deploy.externals import DEPLOY_EXTERNALS
    from ai_venture_studio.maintenance.signals import READERS

    assert set(stage_tool_names()) == {
        "migration_scan", "workflow_scan", "canary_scan",
        "recent_commits", "correlate", "run_tests",
        *READERS, *DEPLOY_EXTERNALS,
    }


def test_voter_allowlists_cannot_reach_stage_tools_through_the_toolbox(repo):
    """MCPToolBox intersects with the L0 registry: a skill naming an L1 tool
    gets it dropped at construction, not merely refused at call time."""
    box = MCPToolBox(repo, ["read_file", "migration_scan"], voter="security")
    assert box.allowed == {"read_file"}
    assert box.call("migration_scan", {}).startswith("error: tool 'migration_scan'")
    box.close()
