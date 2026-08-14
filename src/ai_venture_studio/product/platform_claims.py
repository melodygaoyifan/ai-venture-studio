"""ADR-U29 — the platform's own claims pass its own linter, in CI.

README.md and the published benchmark page are parsed; every quantitative
token (percentages, n=, confidence intervals) must resolve against
claims/platform.yaml — the platform's own claim ledger, typed per §20.53 —
and no unmeasured superlative may appear at all. A README edit that
asserts beyond the ledger fails the suite. The 2026-07-18 session's
defensible-claims list was manual and manual lists rot; this mechanizes
the same discipline with the tool built to keep P3 honest.
"""

from __future__ import annotations

import pathlib
import re

from pydantic import BaseModel

from ai_venture_studio.product.claims import SOURCE_TYPES
from ai_venture_studio.superlatives import compile_gate

_URL = re.compile(r"https?://\S+|\]\([^)]*\)")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)%")
_N_EQUALS = re.compile(r"n=(\d+)")
_CI = re.compile(r"CI \[?([0-9., –-]+)\]?")
# The vocabulary is shared with the marketing gate (ADR-039) — the two lists
# were maintained by hand and had drifted. Both carve-outs below are
# documented in `superlatives`: "cheapest test" is the framework's own term of
# art (§20.54.3), and the specific comparatives are listed instead of a broad
# `most \w+` because the README legitimately says "at most once".
_SUPERLATIVE = compile_gate(
    (r"cheapest(?!\s+test)", "most accurate", "most reliable", "most complete")
)
_COUNT = re.compile(r"\b(\d{2,})\s+(?:hermetic tests|labeled cases|tests\b|fixtures\b)")


class PlatformClaimFinding(BaseModel):
    rule: str  # uncovered_number | unmeasured_superlative | bad_ledger_entry
    line: str
    detail: str


def _ledger_corpus(ledger: dict) -> tuple[str, list[PlatformClaimFinding]]:
    findings = []
    texts = []
    for claim in ledger.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if claim.get("source_type") not in SOURCE_TYPES:
            findings.append(
                PlatformClaimFinding(
                    rule="bad_ledger_entry", line=str(claim.get("id", "?")),
                    detail="ledger entry lacks an admissible source_type",
                )
            )
        if claim.get("source_type") != "model_inference" and not claim.get("evidence"):
            findings.append(
                PlatformClaimFinding(
                    rule="bad_ledger_entry", line=str(claim.get("id", "?")),
                    detail="sourced entry lacks evidence",
                )
            )
        texts.append(str(claim.get("text", "")))
    return " \n ".join(texts), findings


def check_platform_claims(
    document_text: str, ledger: dict
) -> list[PlatformClaimFinding]:
    corpus, findings = _ledger_corpus(ledger)

    for raw_line in document_text.splitlines():
        line = _URL.sub(" ", raw_line)  # badge/link URLs are not claims
        tokens: list[str] = []
        tokens += [m.group(1) for m in _PERCENT.finditer(line)]
        tokens += [m.group(1) for m in _N_EQUALS.finditer(line)]
        tokens += [m.group(1) for m in _COUNT.finditer(line)]
        for ci in _CI.finditer(line):
            tokens += re.findall(r"\d+(?:\.\d+)?", ci.group(1))
        for token in tokens:
            if token not in corpus:
                findings.append(
                    PlatformClaimFinding(
                        rule="uncovered_number",
                        line=raw_line.strip()[:90],
                        detail=f"{token!r} resolves to no entry in "
                        "claims/platform.yaml — add the typed claim or drop "
                        "the number (ADR-U29)",
                    )
                )
        superlative = _SUPERLATIVE.search(line)
        if superlative:
            findings.append(
                PlatformClaimFinding(
                    rule="unmeasured_superlative",
                    line=raw_line.strip()[:90],
                    detail=f"{superlative.group(0)!r} — comparative claims "
                    "about others require measurements of others; delete it "
                    "(§68.1, §74.1)",
                )
            )
    return findings


def check_repo(repo_root: str | pathlib.Path) -> list[PlatformClaimFinding]:
    """The CI entry point: README + benchmark page vs the platform ledger."""
    import yaml

    root = pathlib.Path(repo_root)
    ledger = yaml.safe_load((root / "claims" / "platform.yaml").read_text()) or {}
    findings = []
    for rel in ("README.md", "docs/benchmark.md"):
        path = root / rel
        if path.exists():
            findings += check_platform_claims(path.read_text(), ledger)
    return findings
