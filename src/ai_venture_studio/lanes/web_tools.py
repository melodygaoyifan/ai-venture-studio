"""Web det-tool runners (doc 17 §42.1) — axe-core, Lighthouse budgets,
size-limit — availability-gated; absent binaries skip visibly."""

from __future__ import annotations

import json
import subprocess

from pydantic import BaseModel, Field

from ai_venture_studio.executables import find


class WebToolReport(BaseModel):
    tool: str
    status: str  # ok | skipped | error | findings
    detail: str = ""
    findings: list[dict] = Field(default_factory=list)


def _gated(tool: str, binary: str, argv: list[str], parse) -> WebToolReport:
    # The gate's answer IS the executable (ADR-069). This used to ask
    # `shutil.which(binary)`, throw the absolute path away, and exec `argv`
    # with its bare head — two independent PATH lookups, so the binary that
    # was checked and the binary that runs need not be the same one.
    found = find(binary)
    if found is None:
        return WebToolReport(tool=tool, status="skipped",
                             detail=f"{binary} not installed — a11y/perf budget "
                                    "unverified VISIBLY, never assumed clean")
    result = subprocess.run([found, *argv[1:]], capture_output=True, text=True,
                            timeout=600)
    try:
        findings = parse(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        return WebToolReport(tool=tool, status="error", detail=str(exc)[:200])
    return WebToolReport(tool=tool, status="findings" if findings else "ok",
                         findings=findings)


def axe_scan(url: str) -> WebToolReport:
    return _gated("axe_scan", "axe", ["axe", url, "--stdout"],
                  lambda out: [{"rule": v.get("id"), "impact": v.get("impact"),
                                "nodes": len(v.get("nodes", []))}
                               for v in (json.loads(out or "[]")[0]
                                         .get("violations", []) if out.strip() else [])])


def lighthouse_budget(url: str, budget_path: str) -> WebToolReport:
    return _gated(
        "lighthouse_budget", "lighthouse",
        ["lighthouse", url, f"--budget-path={budget_path}",
         "--output=json", "--quiet", "--chrome-flags=--headless"],
        lambda out: [
            {"resource": r.get("resourceType"), "over_by": r.get("sizeOverBudget")}
            for r in (json.loads(out).get("audits", {})
                      .get("resource-budget", {}).get("details", {})
                      .get("items", []))
            if r.get("sizeOverBudget")])


def size_limit_check(package_dir: str = ".") -> WebToolReport:
    return _gated("size_limit", "size-limit",
                  ["size-limit", "--json"],
                  lambda out: [e for e in json.loads(out or "[]")
                               if not e.get("passed", True)])
