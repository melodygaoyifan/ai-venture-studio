"""Deploy-side external tools (doc 11 §17.2 `deploy_server`).

The maintenance readers (v0.43–v0.44) are HTTP APIs behind credentials.
These are the other shape: **binaries**, gated on being installed, following
the pattern `tools/external.py` set for the scanners — an absent binary is a
visible `skipped` with the install hint, never a silent pass, because
"terraform validate found nothing" and "terraform isn't installed" must not
look alike.

| tool | binary | what it does |
|---|---|---|
| `terraform_validate` | terraform | `validate -json` in a config dir |
| `helm_lint` | helm | `lint` a chart directory |
| `kubectl_dry_run` | kubectl | `apply --dry-run` over a manifest |
| `argocd_app_diff` | argocd | `app diff` for one app |
| `flagger_inspect` | kubectl | read Canary resources in a namespace |
| `railway_inspect` | railway | `status --json` for the linked project |

**Every one of these is read-only, and two of them need saying twice.**

`kubectl_dry_run` defaults to `--dry-run=client`, which never contacts a
cluster: it parses and validates locally. Server-side dry-run is real
admission-controller validation and genuinely more useful, but it talks to
whatever cluster the current kubeconfig points at, so it is opt-in per call
rather than the default. A deploy review that silently reached into a
production cluster because a context happened to be current would be the
kind of surprise this project spends its whole design budget avoiding.

`argocd_app_diff` and `railway_inspect` read live state through an
authenticated CLI. They cannot sync, promote, or roll back — the verbs are
`diff` and `status`, and this module never invokes any other.

Honest scope: exercised hermetically by stubbing the subprocess boundary.
None of these has been run against live infrastructure from this repository
(no cluster, no cloud credentials), so the first real invocation per tool is
an outstanding verification step, recorded as such in the implementation map.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

from pydantic import BaseModel, Field

from ai_venture_studio.executables import find

TIMEOUT_S = 300


class ExternalReport(BaseModel):
    tool: str
    status: str  # ok | findings | skipped | error
    detail: str = ""
    findings: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)


def _skipped(tool: str, binary: str, hint: str) -> ExternalReport:
    return ExternalReport(
        tool=tool, status="skipped",
        detail=f"{binary} not installed ({hint}) — this check did not run; a "
               "skipped tool is reported, never counted as clean",
    )


def _run(cmd: list[str], cwd: str | pathlib.Path) -> subprocess.CompletedProcess:
    # Callers pass the absolute path their availability gate returned, never a
    # bare name (ADR-069). `S607` cannot see that from here — the head is a
    # parameter — which is exactly how these six sites sat unconverted through
    # ADR-064 with both the linter and its ratchet reporting clean.
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd, capture_output=True, text=True, cwd=str(cwd), timeout=TIMEOUT_S
    )


def terraform_validate(config_dir: str, *, repo_dir: str = ".") -> ExternalReport:
    """`terraform validate -json`. Requires an initialized directory; an
    uninitialized one is reported as such rather than silently passing."""
    tool = "terraform_validate"
    binary = find("terraform")
    if not binary:
        return _skipped(tool, "terraform", "https://developer.hashicorp.com/terraform/install")
    target = pathlib.Path(repo_dir) / config_dir
    if not target.is_dir():
        return ExternalReport(tool=tool, status="error",
                              detail=f"{config_dir} is not a directory")
    proc = _run([binary, "validate", "-json"], target)
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict) or "valid" not in payload:
        # No parseable verdict — usually an uninitialized directory. Reporting
        # this as "0 diagnostics" would read like a pass; it is a non-answer.
        return ExternalReport(
            tool=tool, status="error",
            detail=(proc.stderr or proc.stdout
                    or "terraform produced no validation verdict (is the "
                       "directory initialized?)")[:300],
        )
    diagnostics = payload.get("diagnostics") or []
    findings = [
        f"{d.get('severity', 'error')}: {d.get('summary', '')}"
        f"{' — ' + d.get('detail', '') if d.get('detail') else ''}"[:300]
        for d in diagnostics
    ]
    if payload.get("valid") and not findings:
        return ExternalReport(tool=tool, status="ok",
                              detail=f"{config_dir}: configuration is valid",
                              data={"valid": True})
    return ExternalReport(
        tool=tool, status="findings",
        detail=f"{config_dir}: {len(findings)} diagnostic(s)",
        findings=findings, data={"valid": bool(payload.get("valid"))},
    )


def helm_lint(chart_dir: str, *, repo_dir: str = ".") -> ExternalReport:
    """`helm lint` one chart directory."""
    tool = "helm_lint"
    binary = find("helm")
    if not binary:
        return _skipped(tool, "helm", "https://helm.sh/docs/intro/install/")
    target = pathlib.Path(repo_dir) / chart_dir
    if not target.is_dir():
        return ExternalReport(tool=tool, status="error",
                              detail=f"{chart_dir} is not a directory")
    proc = _run([binary, "lint", str(target)], repo_dir)
    output = (proc.stdout or "") + (proc.stderr or "")
    findings = [
        line.strip() for line in output.splitlines()
        if "[ERROR]" in line or "[WARNING]" in line
    ]
    if proc.returncode == 0 and not findings:
        return ExternalReport(tool=tool, status="ok",
                              detail=f"{chart_dir}: chart lints clean")
    return ExternalReport(
        tool=tool, status="findings" if findings else "error",
        detail=f"{chart_dir}: helm lint exited {proc.returncode}",
        findings=findings[:20],
    )


def kubectl_dry_run(
    manifest: str, *, repo_dir: str = ".", server_side: bool = False
) -> ExternalReport:
    """`kubectl apply --dry-run` over a manifest.

    Client-side by default: it parses and validates locally and never
    contacts a cluster. `server_side=True` runs real admission validation
    against whatever cluster the current kubeconfig points at — more useful,
    and an explicit choice rather than an accident of which context happened
    to be current.
    """
    tool = "kubectl_dry_run"
    binary = find("kubectl")
    if not binary:
        return _skipped(tool, "kubectl", "https://kubernetes.io/docs/tasks/tools/")
    target = pathlib.Path(repo_dir) / manifest
    if not target.exists():
        return ExternalReport(tool=tool, status="error",
                              detail=f"{manifest} does not exist")
    mode = "server" if server_side else "client"
    proc = _run(
        [binary, "apply", f"--dry-run={mode}", "-f", str(target)], repo_dir
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return ExternalReport(
            tool=tool, status="ok",
            detail=f"{manifest}: valid ({mode}-side dry run)",
            data={"mode": mode, "objects": len(
                [line for line in (proc.stdout or "").splitlines() if line.strip()]
            )},
        )
    return ExternalReport(
        tool=tool, status="findings",
        detail=f"{manifest}: {mode}-side dry run rejected the manifest",
        findings=[line.strip() for line in output.splitlines() if line.strip()][:20],
        data={"mode": mode},
    )


def argocd_app_diff(app: str, *, repo_dir: str = ".") -> ExternalReport:
    """`argocd app diff` — live-vs-desired for one application.

    Read-only: this module never invokes `sync`, `rollback`, or any other
    mutating argocd verb. A non-empty diff is a finding to read at Gate 5,
    not something to reconcile automatically.
    """
    tool = "argocd_app_diff"
    binary = find("argocd")
    if not binary:
        return _skipped(tool, "argocd",
                        "https://argo-cd.readthedocs.io/en/stable/cli_installation/")
    if not str(app).strip():
        return ExternalReport(tool=tool, status="error", detail="app is required")
    proc = _run([binary, "app", "diff", str(app)], repo_dir)
    output = (proc.stdout or "") + (proc.stderr or "")
    # argocd exits 1 when a diff exists — that is data, not failure.
    if proc.returncode == 0 and not output.strip():
        return ExternalReport(tool=tool, status="ok",
                              detail=f"{app}: live state matches desired")
    if "not found" in output.lower() or "permission" in output.lower():
        return ExternalReport(tool=tool, status="error", detail=output[:300])
    lines = [line for line in output.splitlines() if line.strip()]
    return ExternalReport(
        tool=tool, status="findings",
        detail=f"{app}: {len(lines)} diff line(s) between live and desired",
        findings=lines[:40],
    )


def flagger_inspect(namespace: str = "default", *, repo_dir: str = ".") -> ExternalReport:
    """Read Flagger Canary resources via kubectl. Read-only: `get`, never
    `patch` — promoting or aborting a canary is a human's call."""
    tool = "flagger_inspect"
    binary = find("kubectl")
    if not binary:
        return _skipped(tool, "kubectl", "https://kubernetes.io/docs/tasks/tools/")
    proc = _run(
        [binary, "get", "canaries", "-n", str(namespace), "-o", "json"], repo_dir
    )
    if proc.returncode != 0:
        return ExternalReport(
            tool=tool, status="error",
            detail=(proc.stderr or "kubectl get canaries failed")[:300],
        )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ExternalReport(tool=tool, status="error",
                              detail="kubectl returned unparseable JSON")
    canaries = [
        {
            "name": (item.get("metadata") or {}).get("name", ""),
            "phase": (item.get("status") or {}).get("phase", ""),
            "failed_checks": (item.get("status") or {}).get("failedChecks", 0),
        }
        for item in (payload.get("items") or [])
    ]
    failing = [c for c in canaries if c["phase"] in ("Failed", "Terminating")
               or int(c["failed_checks"] or 0) > 0]
    if not canaries:
        return ExternalReport(tool=tool, status="ok",
                              detail=f"no Canary resources in {namespace}")
    if failing:
        return ExternalReport(
            tool=tool, status="findings",
            detail=f"{len(failing)} of {len(canaries)} canary/canaries unhealthy",
            findings=[f"{c['name']}: phase={c['phase']}, "
                      f"failed_checks={c['failed_checks']}" for c in failing],
            data={"canaries": canaries},
        )
    return ExternalReport(tool=tool, status="ok",
                          detail=f"{len(canaries)} canary/canaries healthy",
                          data={"canaries": canaries})


def railway_inspect(*, repo_dir: str = ".") -> ExternalReport:
    """`railway status --json` for the linked project. Read-only: this
    module never invokes `up`, `down`, or `redeploy`."""
    tool = "railway_inspect"
    binary = find("railway")
    if not binary:
        return _skipped(tool, "railway", "https://docs.railway.com/guides/cli")
    proc = _run([binary, "status", "--json"], repo_dir)
    if proc.returncode != 0:
        return ExternalReport(
            tool=tool, status="error",
            detail=(proc.stderr or "railway status failed — is the project "
                    "linked and the CLI logged in?")[:300],
        )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ExternalReport(tool=tool, status="error",
                              detail="railway returned unparseable JSON")
    summary = {
        "project": str(payload.get("name", ""))[:200],
        "environment": str(
            ((payload.get("environment") or {}) or {}).get("name", "")
        )[:100],
        "service_count": len(payload.get("services") or []),
    }
    return ExternalReport(
        tool=tool, status="ok",
        detail=f"{summary['project'] or 'project'}: "
               f"{summary['service_count']} service(s) in "
               f"{summary['environment'] or 'unknown'} environment",
        data=summary,
    )


DEPLOY_EXTERNALS = {
    "terraform_validate": terraform_validate,
    "helm_lint": helm_lint,
    "kubectl_dry_run": kubectl_dry_run,
    "argocd_app_diff": argocd_app_diff,
    "flagger_inspect": flagger_inspect,
    "railway_inspect": railway_inspect,
}
