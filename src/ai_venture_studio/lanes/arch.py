"""Architecture evolution (doc 28 Part 81) — the design as a fitness function.

`.mas/deps.yaml` is the allowed module graph; `arch_contract_check` is its
compiled form (Python lane: import scanning). The graph mutates only
through the SCR-class channel (ADR-U34, invariant 14.27) — the checker
carries a provenance hash of the graph it was compiled from, so a
hand-edited checker is drift by definition. Brownfield adoption uses the
checkpoint pattern: existing violations become a baseline that must only
shrink; new violations fail immediately.
"""

from __future__ import annotations

import hashlib
import pathlib
import re

import yaml
from pydantic import BaseModel


class DepsGraphError(RuntimeError):
    """Malformed module graph. The largest spec fails loudly."""


class ArchViolation(BaseModel):
    module: str
    imports: str
    file: str
    line: int
    baseline: bool = False  # known debt vs new violation


def load_deps(text: str) -> dict[str, dict]:
    raw = yaml.safe_load(text) or {}
    modules = raw.get("modules")
    if not isinstance(modules, dict) or not modules:
        raise DepsGraphError("deps.yaml must declare a non-empty modules mapping")
    for name, spec in modules.items():
        if not isinstance(spec, dict):
            raise DepsGraphError(f"module {name!r} spec must be a mapping")
        for target in spec.get("may_import") or []:
            root = str(target).split(".")[0]
            if root not in modules and root != "shared":
                raise DepsGraphError(
                    f"module {name!r} may_import {target!r} which is not a "
                    "declared module — the graph is closed")
    return modules


def graph_fingerprint(text: str) -> str:
    """The provenance hash the compiled checker must carry (invariant 14.27)."""
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)


def arch_contract_check(
    deps_text: str,
    sources: dict[str, str],  # relative path -> source text
    *,
    package_root: str = "modules",
    baseline: set[tuple[str, str]] = frozenset(),
) -> list[ArchViolation]:
    """Python-lane fitness function: every cross-module import must be an
    allowed edge, and only via the target's public surface."""
    modules = load_deps(deps_text)
    violations = []
    for path, source in sorted(sources.items()):
        parts = pathlib.PurePosixPath(path).parts
        if package_root not in parts:
            continue
        owner = parts[parts.index(package_root) + 1]
        if owner not in modules:
            continue
        allowed = {str(t) for t in modules[owner].get("may_import") or []}
        publics = {p for spec in modules.values() for p in spec.get("public") or []}
        for lineno, line in enumerate(source.splitlines(), start=1):
            match = _IMPORT.match(line)
            if not match:
                continue
            target = (match.group(1) or match.group(2) or "")
            if not target.startswith(f"{package_root}."):
                continue
            dotted = target[len(package_root) + 1:]
            target_module = dotted.split(".")[0]
            if target_module == owner:
                continue
            edge_ok = (
                dotted in allowed
                or target_module in allowed
                or (dotted in publics and any(a.split(".")[0] == target_module
                                              for a in allowed))
            )
            internal_reach = "." in dotted and dotted not in publics and dotted not in allowed
            if edge_ok and not internal_reach:
                continue
            violation = ArchViolation(
                module=owner, imports=dotted, file=path, line=lineno,
                baseline=(owner, dotted) in baseline)
            violations.append(violation)
    return violations


class CheckpointResult(BaseModel):
    new_violations: list[ArchViolation]
    remaining_debt: int
    debt_delta: int  # vs the recorded baseline size


def checkpoint_check(
    violations: list[ArchViolation], baseline: set[tuple[str, str]]
) -> CheckpointResult:
    """Brownfield mode: new violations fail immediately; old ones are debt
    with a visible count that must trend down (F-28.1)."""
    new = [v for v in violations if not v.baseline]
    present = {(v.module, v.imports) for v in violations if v.baseline}
    return CheckpointResult(
        new_violations=new,
        remaining_debt=len(present),
        debt_delta=len(present) - len(baseline),
    )


class ApiSurfaceIssue(BaseModel):
    rule: str
    message: str


def api_surface_check(
    declared: list[str], current: list[str], *, deprecated: dict[str, str] = {}
) -> list[ApiSurfaceIssue]:
    """§81.3 — the built product's API gets the §74.2 treatment: removing a
    declared surface without a deprecation window is a contract break."""
    issues = []
    for route in declared:
        if route not in current and route not in deprecated:
            issues.append(ApiSurfaceIssue(
                rule="ESCALATE_CONTRACT_BREAK",
                message=f"declared surface {route!r} removed with no deprecation "
                        "window (>=1 minor version, loud warning)"))
    return issues
