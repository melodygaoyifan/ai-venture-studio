"""debt_server tools (doc 11 §19): radon complexity, jscpd clones, vulture
dead code — availability-gated wrappers; absent binaries skip visibly.
Their outputs feed the Sweep queues (doc 29 §84.2) and the checkpoint
debt trend (F-28.1)."""

from __future__ import annotations

import json
import subprocess

from pydantic import BaseModel, Field

from ai_venture_studio.executables import find


class DebtReport(BaseModel):
    tool: str
    status: str  # ok | skipped | error
    detail: str = ""
    items: list[dict] = Field(default_factory=list)


def _run(tool: str, argv: list[str], parse) -> DebtReport:
    # One lookup, and it is the one that runs (ADR-069) — `shutil.which`
    # here answered the question and then let PATH answer it again at exec.
    found = find(argv[0])
    if found is None:
        return DebtReport(tool=tool, status="skipped",
                          detail=f"{argv[0]} not installed — debt metric "
                                 "absent VISIBLY, never assumed clean")
    result = subprocess.run([found, *argv[1:]], capture_output=True, text=True,
                            timeout=300)
    try:
        return DebtReport(tool=tool, status="ok", items=parse(result.stdout))
    except (ValueError, json.JSONDecodeError) as exc:
        return DebtReport(tool=tool, status="error", detail=str(exc)[:200])


def radon_complexity(path: str = "src") -> DebtReport:
    return _run("radon", ["radon", "cc", "-j", path],
                lambda out: [{"file": f, "blocks": len(blocks)}
                             for f, blocks in json.loads(out or "{}").items()])


def jscpd_clones(path: str = "src") -> DebtReport:
    return _run("jscpd", ["jscpd", "--reporters", "json", "--silent", path],
                lambda out: json.loads(out or "{}").get("duplicates", []))


def vulture_dead_code(path: str = "src", *, min_confidence: int = 90) -> DebtReport:
    """min_confidence high on purpose (F-29.2): dead-code deletion is the
    sweep chore most likely to remove dynamically-referenced code."""
    def parse(out: str) -> list[dict]:
        return [{"line": line} for line in out.splitlines() if line.strip()]
    return _run("vulture",
                ["vulture", path, f"--min-confidence={min_confidence}"], parse)
