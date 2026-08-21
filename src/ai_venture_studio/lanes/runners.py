"""Availability-gated execution wrappers for the docs 26-28 lanes.

The standing rule (tools/base.py): an absent external reports `skipped` —
visible in the record, never silently missing, because silent absence
reads as "checked and clean". The deterministic contracts live in
perf.py/streaming.py; these wrappers only execute.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field

from ai_venture_studio.executables import find

from ai_venture_studio.lanes.perf import PERF_AC


class RunnerReport(BaseModel):
    runner: str
    status: str  # ok | skipped | error
    detail: str = ""
    data: dict = Field(default_factory=dict)


# --- k6 -----------------------------------------------------------------------


def k6_script_from_ac(criterion: str, *, url: str) -> str:
    """Compile a perf AC into a k6 script whose thresholds ARE the AC —
    breach means non-zero exit, a deterministic gate with no glue (§77.2).
    Pure and hermetically testable; execution is the gated part."""
    match = PERF_AC.match(criterion.strip())
    if not match:
        raise ValueError(f"not a perf AC: {criterion!r}")
    metric = match.group("metric")
    op, value = match.group("op"), match.group("value")
    unit = match.group("unit") or ""
    pct = match.group("pct")
    duration = match.group("dur") or "60s"
    shape = match.group("shape")
    rps = None
    rps_match = __import__("re").search(r"(\d+)\s*rps", shape, __import__("re").I)
    if rps_match:
        rps = int(rps_match.group(1))
    threshold_value = value if unit != "s" else str(float(value) * 1000)
    expr = (f"p({pct})<{threshold_value}" if pct and metric.endswith("duration")
            else f"rate{op}{float(value) / 100 if unit == '%' else value}"
            if "rate" in metric else f"{'p(95)' if pct is None else f'p({pct})'}"
            f"{op}{threshold_value}")
    executor = (
        f"""executor: 'constant-arrival-rate', rate: {rps}, timeUnit: '1s',
      duration: '{duration}', preAllocatedVUs: {max(10, (rps or 10) * 2)},"""
        if rps else
        f"executor: 'constant-vus', vus: 50, duration: '{duration}',")
    return f"""import http from 'k6/http';
export const options = {{
  scenarios: {{ shape: {{ {executor} }} }},
  thresholds: {{ '{metric}': ['{expr}'] }},
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)'],
}};
export default function () {{ http.get('{url}'); }}
"""


def run_k6(criterion: str, *, url: str, timeout_s: int = 900) -> RunnerReport:
    k6 = find("k6")
    if k6 is None:
        return RunnerReport(
            runner="k6", status="skipped",
            detail="k6 binary not found — install k6 to execute; the script "
                   "this run would use is deterministic and in the record",
            data={"script": k6_script_from_ac(criterion, url=url)})
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(k6_script_from_ac(criterion, url=url))
        script = handle.name
    result = subprocess.run(
        [k6, "run", "--summary-export", script + ".json", script],
        capture_output=True, timeout=timeout_s, text=True)
    summary = {}
    try:
        summary = json.loads(Path(script + ".json").read_text(encoding="utf-8"))
    except OSError:
        pass
    return RunnerReport(
        runner="k6", status="ok" if result.returncode == 0 else "error",
        detail=f"exit {result.returncode} (non-zero = threshold breach, by design)",
        data={"summary": summary})


# --- netem ---------------------------------------------------------------------

# Declared network-condition profiles (§79.4) — delay/jitter/loss.
NETEM_PROFILES = {
    "wifi_poor": {"delay_ms": 40, "jitter_ms": 20, "loss_pct": 1.0},
    "mobile_4g": {"delay_ms": 80, "jitter_ms": 30, "loss_pct": 0.5},
    "intercontinental": {"delay_ms": 180, "jitter_ms": 10, "loss_pct": 0.1},
}


def netem_command(profile: str, *, interface: str = "eth0") -> list[str]:
    """The exact tc invocation a Linux host would run — pure, recorded."""
    spec = NETEM_PROFILES[profile]
    return ["tc", "qdisc", "add", "dev", interface, "root", "netem",
            "delay", f"{spec['delay_ms']}ms", f"{spec['jitter_ms']}ms",
            "loss", f"{spec['loss_pct']}%"]


def apply_netem(profile: str, *, interface: str = "eth0") -> RunnerReport:
    if profile not in NETEM_PROFILES:
        return RunnerReport(runner="netem", status="error",
                            detail=f"unknown profile {profile!r}")
    tc = find("tc")
    if platform.system() != "Linux" or tc is None:
        return RunnerReport(
            runner="netem", status="skipped",
            detail="netem needs Linux tc — bot playtests under this profile "
                   "are skipped VISIBLY, never assumed to have run",
            data={"command": netem_command(profile, interface=interface)})
    # `netem_command` stays pure and bare — it is the RECORD of what a Linux
    # host would run, asserted as `["tc", ...]` and shown in skip reports.
    # What actually reaches the kernel is the resolved binary (ADR-064).
    argv = netem_command(profile, interface=interface)
    result = subprocess.run([tc, *argv[1:]],
                            capture_output=True, text=True, timeout=30)
    return RunnerReport(runner="netem",
                        status="ok" if result.returncode == 0 else "error",
                        detail=result.stderr.strip()[:200])


# --- schema registry -------------------------------------------------------------


def registry_compat_check(
    subject: str, schema_json: str, *, registry_url: str | None = None
) -> RunnerReport:
    """Defer to a live Schema Registry when configured; the deterministic
    field-wise check (streaming.check_compatibility) runs regardless."""
    url = registry_url or os.environ.get("SCHEMA_REGISTRY_URL")
    if not url:
        return RunnerReport(
            runner="schema_registry", status="skipped",
            detail="SCHEMA_REGISTRY_URL not set — the in-repo field-wise "
                   "compatibility check still gates; the registry adds the "
                   "authoritative second opinion when configured")
    # `urlopen` honours whatever scheme it is handed, and `SCHEMA_REGISTRY_URL`
    # is an environment variable — `file:///etc/passwd` there would be read and
    # its bytes handed to `json.loads`, and the check would report on it. The
    # variable is operator-set rather than attacker-set, so this is a guard and
    # not an incident; it costs one comparison and it says no in words a
    # misconfigured operator can act on (ADR-062, S310).
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        return RunnerReport(
            runner="schema_registry", status="error",
            detail=f"SCHEMA_REGISTRY_URL must be http or https; got "
                   f"{scheme or 'no scheme'!r}. A registry is a service over "
                   f"the network, not a path on this disk.")
    request = urllib.request.Request(  # noqa: S310 — scheme checked above
        f"{url.rstrip('/')}/compatibility/subjects/{subject}/versions/latest",
        data=json.dumps({"schema": schema_json}).encode(),
        headers={"Content-Type": "application/vnd.schemaregistry.v1+json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            body = json.loads(response.read())
    except OSError as exc:
        return RunnerReport(runner="schema_registry", status="error",
                            detail=str(exc)[:200])
    return RunnerReport(runner="schema_registry",
                        status="ok" if body.get("is_compatible") else "error",
                        detail=f"registry says is_compatible={body.get('is_compatible')}",
                        data=body)


def reconcile_contracts(
    declared: dict[str, str], registry_modes: dict[str, str]
) -> list[dict]:
    """F-27.4's nightly job, as a pure function: contract file vs registry
    state; drift is a finding, in either direction."""
    findings = []
    for topic, mode in sorted(declared.items()):
        actual = registry_modes.get(topic)
        if actual is None:
            findings.append({"topic": topic, "rule": "not_in_registry",
                             "message": "declared in the contract file, absent "
                                        "from the registry"})
        elif actual != mode:
            findings.append({"topic": topic, "rule": "mode_drift",
                             "message": f"file declares {mode}, registry has "
                                        f"{actual} — reconcile via PR"})
    for topic in sorted(set(registry_modes) - set(declared)):
        findings.append({"topic": topic, "rule": "undeclared_topic",
                         "message": "in the registry but not in the contract "
                                    "file — no standing, no topic"})
    return findings
