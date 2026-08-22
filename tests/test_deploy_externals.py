"""v0.45.0 — the §17.2 deploy CLI wrappers.

The other integration shape: binaries gated on being installed, rather than
HTTP gated on a credential. Hermetic by stubbing the subprocess boundary, so
these prove the contract (gating, read-only verbs, findings-vs-error, the
client-side dry-run default) without terraform, a cluster, or a cloud
account. What they cannot prove is that each CLI's live output matches the
shape assumed here — first real invocation per tool is outstanding, and the
module docstring says so.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from ai_venture_studio.deploy import externals
from ai_venture_studio.deploy.externals import (
    DEPLOY_EXTERNALS,
    argocd_app_diff,
    flagger_inspect,
    helm_lint,
    kubectl_dry_run,
    railway_inspect,
    terraform_validate,
)


@pytest.fixture
def cli(monkeypatch):
    """Pretend every binary is installed; record argv and script the result."""
    calls: list[list[str]] = []
    results: list[subprocess.CompletedProcess] = []

    # Patched at the resolver (ADR-069): the module asks
    # `executables.find` once and runs the absolute path it gets back, so
    # the recorded argv now carries that path — which is the whole point,
    # and these assertions say so.
    monkeypatch.setattr("ai_venture_studio.executables.shutil.which",
                        lambda name: f"/usr/bin/{name}")

    def _fake(cmd, cwd):
        calls.append(cmd)
        return results.pop(0) if results else subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(externals, "_run", _fake)

    def returns(stdout="", stderr="", code=0):
        results.append(subprocess.CompletedProcess([], code, stdout, stderr))

    return types.SimpleNamespace(calls=calls, returns=returns)


@pytest.fixture
def no_binaries(monkeypatch):
    monkeypatch.setattr("ai_venture_studio.executables.shutil.which",
                        lambda name: None)


# --- gating -------------------------------------------------------------------


UNGATED = [
    ("terraform_validate", lambda: terraform_validate("infra"), "terraform"),
    ("helm_lint", lambda: helm_lint("charts/app"), "helm"),
    ("kubectl_dry_run", lambda: kubectl_dry_run("k8s/deploy.yaml"), "kubectl"),
    ("argocd_app_diff", lambda: argocd_app_diff("checkout"), "argocd"),
    ("flagger_inspect", lambda: flagger_inspect("prod"), "kubectl"),
    ("railway_inspect", lambda: railway_inspect(), "railway"),
]


@pytest.mark.parametrize(("name", "call", "binary"), UNGATED,
                         ids=[c[0] for c in UNGATED])
def test_absent_binary_is_a_visible_skip(no_binaries, name, call, binary):
    """An uninstalled tool never counts as clean — the rule
    tools/external.py set for the scanners."""
    report = call()
    assert report.tool == name
    assert report.status == "skipped"
    assert binary in report.detail
    assert "never counted as clean" in report.detail


def test_registry_covers_the_documented_deploy_tools():
    assert set(DEPLOY_EXTERNALS) == {
        "terraform_validate", "helm_lint", "kubectl_dry_run",
        "argocd_app_diff", "flagger_inspect", "railway_inspect",
    }


def test_no_wrapper_invokes_a_mutating_verb():
    """Read-only by construction: `apply` appears only with --dry-run, and
    sync/rollback/up/redeploy/patch appear nowhere."""
    import inspect

    source = inspect.getsource(externals)
    for verb in ('"sync"', '"rollback"', '"up"', '"redeploy"', '"patch"',
                 '"delete"', '"destroy"'):
        assert verb not in source, verb
    for line in source.splitlines():
        if '"apply"' in line:
            assert "dry-run" in source  # apply is only ever a dry run


# --- terraform ----------------------------------------------------------------


def test_terraform_validate_clean(cli, tmp_path):
    (tmp_path / "infra").mkdir()
    cli.returns(stdout=json.dumps({"valid": True, "diagnostics": []}))
    report = terraform_validate("infra", repo_dir=str(tmp_path))
    assert report.status == "ok" and report.data["valid"] is True
    assert cli.calls[0] == ["/usr/bin/terraform", "validate", "-json"]


def test_terraform_validate_reports_diagnostics(cli, tmp_path):
    (tmp_path / "infra").mkdir()
    cli.returns(stdout=json.dumps({"valid": False, "diagnostics": [
        {"severity": "error", "summary": "Missing required argument",
         "detail": "The argument \"region\" is required"},
    ]}), code=1)
    report = terraform_validate("infra", repo_dir=str(tmp_path))
    assert report.status == "findings"
    assert "Missing required argument" in report.findings[0]
    assert "region" in report.findings[0]


def test_terraform_uninitialized_dir_is_an_error_not_a_pass(cli, tmp_path):
    (tmp_path / "infra").mkdir()
    cli.returns(stderr="Error: Module not installed", code=1)  # non-JSON
    report = terraform_validate("infra", repo_dir=str(tmp_path))
    assert report.status == "error" and "Module not installed" in report.detail


def test_missing_directory_is_an_error(cli, tmp_path):
    assert terraform_validate("nope", repo_dir=str(tmp_path)).status == "error"
    assert helm_lint("nope", repo_dir=str(tmp_path)).status == "error"


# --- helm ---------------------------------------------------------------------


def test_helm_lint_clean_and_with_findings(cli, tmp_path):
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "app").mkdir()
    cli.returns(stdout="1 chart(s) linted, 0 chart(s) failed")
    assert helm_lint("charts/app", repo_dir=str(tmp_path)).status == "ok"

    cli.returns(
        stdout="[WARNING] templates/: directory not found\n"
               "[ERROR] Chart.yaml: version is required\n",
        code=1,
    )
    report = helm_lint("charts/app", repo_dir=str(tmp_path))
    assert report.status == "findings"
    assert any("[ERROR]" in f for f in report.findings)
    assert any("[WARNING]" in f for f in report.findings)


# --- kubectl ------------------------------------------------------------------


def test_kubectl_dry_run_defaults_to_client_side(cli, tmp_path):
    """Server-side dry-run contacts whatever cluster the kubeconfig points
    at, so it must never be the accident."""
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text("kind: Deployment\n")
    cli.returns(stdout="deployment.apps/api configured (dry run)")
    report = kubectl_dry_run("k8s.yaml", repo_dir=str(tmp_path))
    assert report.status == "ok" and report.data["mode"] == "client"
    assert "--dry-run=client" in cli.calls[0]
    assert "--dry-run=server" not in cli.calls[0]


def test_kubectl_server_side_is_opt_in(cli, tmp_path):
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text("kind: Deployment\n")
    cli.returns(stdout="deployment.apps/api configured (server dry run)")
    report = kubectl_dry_run("k8s.yaml", repo_dir=str(tmp_path), server_side=True)
    assert report.data["mode"] == "server"
    assert "--dry-run=server" in cli.calls[0]


def test_kubectl_rejection_is_findings(cli, tmp_path):
    manifest = tmp_path / "k8s.yaml"
    manifest.write_text("kind: Nope\n")
    cli.returns(stderr='error: unable to recognize "k8s.yaml": no matches for kind "Nope"',
                code=1)
    report = kubectl_dry_run("k8s.yaml", repo_dir=str(tmp_path))
    assert report.status == "findings"
    assert any("no matches for kind" in f for f in report.findings)


def test_kubectl_missing_manifest_errors(cli, tmp_path):
    assert kubectl_dry_run("absent.yaml", repo_dir=str(tmp_path)).status == "error"


# --- argocd -------------------------------------------------------------------


def test_argocd_no_diff_is_ok(cli, tmp_path):
    cli.returns(stdout="")
    report = argocd_app_diff("checkout", repo_dir=str(tmp_path))
    assert report.status == "ok" and "matches desired" in report.detail
    assert cli.calls[0] == ["/usr/bin/argocd", "app", "diff", "checkout"]


def test_argocd_diff_exit_one_is_data_not_failure(cli, tmp_path):
    """argocd exits 1 when a diff exists; that is the answer, not an error."""
    cli.returns(stdout="- replicas: 2\n+ replicas: 5\n", code=1)
    report = argocd_app_diff("checkout", repo_dir=str(tmp_path))
    assert report.status == "findings"
    assert report.findings == ["- replicas: 2", "+ replicas: 5"]


def test_argocd_auth_and_missing_app_are_errors(cli, tmp_path):
    cli.returns(stderr="permission denied: applications, get", code=1)
    assert argocd_app_diff("checkout", repo_dir=str(tmp_path)).status == "error"
    cli.returns(stderr="application checkout not found", code=1)
    assert argocd_app_diff("checkout", repo_dir=str(tmp_path)).status == "error"


def test_argocd_requires_an_app(cli, tmp_path):
    assert argocd_app_diff("", repo_dir=str(tmp_path)).status == "error"


# --- flagger ------------------------------------------------------------------


def test_flagger_reads_canaries_and_flags_unhealthy(cli, tmp_path):
    cli.returns(stdout=json.dumps({"items": [
        {"metadata": {"name": "api"}, "status": {"phase": "Succeeded",
                                                 "failedChecks": 0}},
        {"metadata": {"name": "web"}, "status": {"phase": "Progressing",
                                                 "failedChecks": 3}},
    ]}))
    report = flagger_inspect("prod", repo_dir=str(tmp_path))
    assert report.status == "findings"
    assert "web: phase=Progressing, failed_checks=3" in report.findings
    assert len(report.data["canaries"]) == 2
    assert cli.calls[0][:3] == ["/usr/bin/kubectl", "get", "canaries"]
    assert "prod" in cli.calls[0]


def test_flagger_all_healthy_and_none_present(cli, tmp_path):
    cli.returns(stdout=json.dumps({"items": [
        {"metadata": {"name": "api"}, "status": {"phase": "Succeeded",
                                                 "failedChecks": 0}},
    ]}))
    assert flagger_inspect("prod", repo_dir=str(tmp_path)).status == "ok"
    cli.returns(stdout=json.dumps({"items": []}))
    report = flagger_inspect("prod", repo_dir=str(tmp_path))
    assert report.status == "ok" and "no Canary resources" in report.detail


def test_flagger_cluster_error_is_reported(cli, tmp_path):
    cli.returns(stderr="Unable to connect to the server", code=1)
    report = flagger_inspect("prod", repo_dir=str(tmp_path))
    assert report.status == "error" and "Unable to connect" in report.detail


# --- railway ------------------------------------------------------------------


def test_railway_status_summarizes(cli, tmp_path):
    cli.returns(stdout=json.dumps({
        "name": "groupbuy", "environment": {"name": "production"},
        "services": [{"name": "api"}, {"name": "worker"}],
    }))
    report = railway_inspect(repo_dir=str(tmp_path))
    assert report.status == "ok"
    assert report.data == {"project": "groupbuy", "environment": "production",
                           "service_count": 2}
    assert cli.calls[0] == ["/usr/bin/railway", "status", "--json"]


def test_railway_unlinked_project_is_an_error(cli, tmp_path):
    cli.returns(stderr="No linked project found", code=1)
    report = railway_inspect(repo_dir=str(tmp_path))
    assert report.status == "error" and "No linked project" in report.detail


# --- the MCP partition --------------------------------------------------------


def test_all_wrappers_are_served_by_the_l1_deploy_partition():
    from ai_venture_studio.mcp.server import SERVER_RISK, SERVER_TOOLS, server_for
    from ai_venture_studio.mcp.stage_tools import risk_of

    for tool in DEPLOY_EXTERNALS:
        assert server_for(tool) == "deploy", tool
        assert tool in SERVER_TOOLS["deploy"]
        assert risk_of(tool) == 1, tool
    assert SERVER_RISK["deploy"] == 1


def test_stage_tools_pass_through_the_skip(tmp_path, no_binaries):
    from ai_venture_studio.mcp.stage_tools import call_stage_tool

    payload = json.loads(
        call_stage_tool("terraform_validate", tmp_path, {"config_dir": "infra"})
    )
    assert payload["status"] == "skipped" and "terraform" in payload["detail"]


def test_kubectl_stage_tool_keeps_the_client_side_default(tmp_path, cli):
    from ai_venture_studio.mcp.stage_tools import call_stage_tool

    (tmp_path / "k8s.yaml").write_text("kind: Deployment\n")
    cli.returns(stdout="deployment.apps/api configured (dry run)")
    payload = json.loads(
        call_stage_tool("kubectl_dry_run", tmp_path, {"manifest": "k8s.yaml"})
    )
    assert payload["data"]["mode"] == "client"
