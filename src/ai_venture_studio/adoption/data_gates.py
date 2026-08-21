"""Data-pipeline det_tools core (§18.48.1, §19 G10-G12).

Three deterministic gates, all native and hermetic:

- eval_gate: score deltas vs a pinned baseline — the ML analogue of the
  mutation gate. Baseline updates are fixture updates: `pin_baseline`
  rewrites `.mas/eval-baseline.yaml` and the change rides a PR, never a
  silent re-pin.
- idempotency_check: fixture-slice re-run must produce byte-identical
  output (the backfill safety floor).
- contract_check: schema + constraint assertions over rows at a pipeline
  boundary. This is the native minimum; teams with dbt/Great Expectations
  run those suites through the toolchain wrapper instead — this checker is
  the floor, not a replacement.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

BASELINE_PATH = ".mas/eval-baseline.yaml"

_TYPES = {"int": int, "float": (int, float), "str": str, "bool": bool}


# --- eval gate -----------------------------------------------------------------

class MetricVerdict(BaseModel):
    metric: str
    status: str  # ok | regression | missing | unpinned
    baseline: float | None = None
    current: float | None = None
    delta: float | None = None


class EvalGateResult(BaseModel):
    verdicts: list[MetricVerdict]

    @property
    def passed(self) -> bool:
        return all(v.status in ("ok", "unpinned") for v in self.verdicts)

    @property
    def unpinned(self) -> list[str]:
        return [v.metric for v in self.verdicts if v.status == "unpinned"]


def pin_baseline(
    repo_dir: str | Path, scores: dict[str, float], tolerance: float = 0.01
) -> Path:
    """Pinning is deliberate: one tolerance, every metric recorded. The
    resulting file diff is the reviewable artifact."""
    if not scores:
        raise ValueError("refusing to pin an empty baseline")
    if tolerance < 0:
        raise ValueError(f"tolerance {tolerance} must be >= 0")
    path = Path(repo_dir) / BASELINE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"tolerance": tolerance,
             "metrics": {k: float(v) for k, v in sorted(scores.items())}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def eval_gate(repo_dir: str | Path, scores: dict[str, float]) -> EvalGateResult:
    """Higher is better for every pinned metric (invert error-style metrics
    before pinning). A pinned metric absent from `scores` is a failure —
    an unmeasured metric never reads as unregressed."""
    path = Path(repo_dir) / BASELINE_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"no pinned baseline at {path} — run pin_baseline first; "
            "a gate without a baseline is not a gate"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tolerance = float(data["tolerance"])
    pinned: dict[str, float] = data["metrics"]

    verdicts = []
    for metric, base in sorted(pinned.items()):
        if metric not in scores:
            verdicts.append(MetricVerdict(metric=metric, status="missing", baseline=base))
            continue
        current = float(scores[metric])
        delta = current - base
        status = "regression" if delta < -tolerance else "ok"
        verdicts.append(MetricVerdict(
            metric=metric, status=status, baseline=base,
            current=current, delta=round(delta, 6),
        ))
    for metric in sorted(set(scores) - set(pinned)):
        verdicts.append(MetricVerdict(
            metric=metric, status="unpinned", current=float(scores[metric]),
        ))
    return EvalGateResult(verdicts=verdicts)


# --- backfill idempotency --------------------------------------------------------

class IdempotencyResult(BaseModel):
    identical: bool
    only_in_first: list[str] = Field(default_factory=list)
    only_in_second: list[str] = Field(default_factory=list)
    content_diffs: list[str] = Field(default_factory=list)


def _tree_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashes


def idempotency_check(run_a: str | Path, run_b: str | Path) -> IdempotencyResult:
    """Two runs of the same backfill over the same fixture slice must be
    byte-identical (§18.48.1). Empty output directories are an error, not a
    vacuous pass — a backfill that wrote nothing proved nothing."""
    a_root, b_root = Path(run_a), Path(run_b)
    for root in (a_root, b_root):
        if not root.is_dir():
            raise FileNotFoundError(f"output directory missing: {root}")
    a, b = _tree_hashes(a_root), _tree_hashes(b_root)
    if not a and not b:
        raise ValueError("both runs produced no files — nothing was verified")
    return IdempotencyResult(
        identical=a == b,
        only_in_first=sorted(set(a) - set(b)),
        only_in_second=sorted(set(b) - set(a)),
        content_diffs=sorted(k for k in set(a) & set(b) if a[k] != b[k]),
    )


# --- data contract ---------------------------------------------------------------

class ContractViolation(BaseModel):
    row: int
    field: str
    rule: str
    detail: str


def load_contract(path: str | Path) -> list[dict]:
    """Contract yaml: fields: [{name, type: int|float|str|bool,
    required: bool, not_null: bool}]."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    fields = data.get("fields", [])
    if not fields:
        raise ValueError(f"{path}: contract declares no fields")
    for i, f in enumerate(fields):
        if "name" not in f:
            raise ValueError(f"{path}: field {i} has no name")
        if f.get("type") not in _TYPES:
            raise ValueError(
                f"{path}: field {f['name']!r} type {f.get('type')!r} "
                f"not in {sorted(_TYPES)}"
            )
    return fields


def contract_check(fields: list[dict], rows: list[dict]) -> list[ContractViolation]:
    """Assertion sweep over rows at a boundary. Empty input is a violation,
    not a pass — a boundary that saw no rows verified no contract."""
    if not rows:
        return [ContractViolation(
            row=0, field="*", rule="non_empty",
            detail="no rows reached the boundary",
        )]
    violations = []
    for i, row in enumerate(rows):
        for f in fields:
            name, expected = f["name"], _TYPES[f["type"]]
            if name not in row:
                if f.get("required", True):
                    violations.append(ContractViolation(
                        row=i, field=name, rule="required", detail="column absent",
                    ))
                continue
            value = row[name]
            if value is None:
                if f.get("not_null", False):
                    violations.append(ContractViolation(
                        row=i, field=name, rule="not_null", detail="null value",
                    ))
                continue
            if not isinstance(value, expected) or (
                f["type"] == "int" and isinstance(value, bool)
            ):
                violations.append(ContractViolation(
                    row=i, field=name, rule="type",
                    detail=f"expected {f['type']}, got {type(value).__name__}",
                ))
    return violations
