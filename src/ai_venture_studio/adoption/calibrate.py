"""Scanner-calibration report (§19 G7, R-G3).

The seeded-lane manifest `pattern`s are hand-labels — substrings a slot's
output is *expected* to contain when it catches a planted defect. They only
become trustworthy after a run against the real scanners, and they must be
re-checked on every scanner version bump. This module produces the report
that makes that calibration loop fast: per defect, caught or missed, and —
crucially for a miss — the actual slot output the operator needs to pick the
right pattern.

It runs no scanners itself; it drives `run_toolchain`, so a slot whose binary
is absent shows up as `skipped` (loud, never "clean"), exactly as everywhere
else. `make calibrate` provides the container where the binaries exist.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ai_venture_studio.adoption.toolchains import (
    benchmark_toolchain,
    load_seeded_manifest,
    run_toolchain,
)

_OUTPUT_SNIPPET_CHARS = 4000


class DefectCalibration(BaseModel):
    defect_id: str
    slot: str
    expected_pattern: str
    caught: bool
    detail: str = ""
    note: str = ""


class SlotOutput(BaseModel):
    slot: str
    status: str
    detail: str = ""
    output: str = ""


class CalibrationReport(BaseModel):
    language: str
    catch_rate: float
    total: int
    caught: int
    skipped_slots: list[str] = Field(default_factory=list)
    misses: list[DefectCalibration] = Field(default_factory=list)
    hits: list[DefectCalibration] = Field(default_factory=list)
    slot_outputs: list[SlotOutput] = Field(default_factory=list)

    @property
    def needs_recalibration(self) -> bool:
        """Any miss on a slot that actually ran is a hand-label that needs
        fixing (as opposed to a genuinely uncaught defect, which is rarer and
        worth a scanner-rule change instead)."""
        ran = {s.slot for s in self.slot_outputs if s.status in ("clean", "findings")}
        return any(m.slot in ran for m in self.misses)


def calibration_report(
    repo_dir: str | Path, language: str, manifest_path: str | Path
) -> CalibrationReport:
    defects = load_seeded_manifest(manifest_path)
    report = run_toolchain(repo_dir, language)
    result = benchmark_toolchain(report, defects)

    by_id = {d["id"]: d for d in defects}

    hits, misses = [], []
    for outcome in result.outcomes:
        defect = by_id[outcome.defect_id]
        row = DefectCalibration(
            defect_id=outcome.defect_id,
            slot=outcome.slot,
            expected_pattern=defect["pattern"],
            caught=outcome.caught,
            detail=outcome.detail,
            note=str(defect.get("note", "")),
        )
        (hits if outcome.caught else misses).append(row)

    slot_outputs = [
        SlotOutput(
            slot=s.slot, status=s.status, detail=s.detail,
            output=s.output[:_OUTPUT_SNIPPET_CHARS],
        )
        for s in report.results
    ]

    return CalibrationReport(
        language=language,
        catch_rate=round(result.catch_rate, 4),
        total=len(result.outcomes),
        caught=sum(1 for o in result.outcomes if o.caught),
        skipped_slots=report.skipped_slots,
        misses=misses,
        hits=hits,
        slot_outputs=slot_outputs,
    )


def write_calibration_report(
    repo_dir: str | Path,
    language: str,
    manifest_path: str | Path,
    out_base: str | Path | None = None,
) -> Path:
    """Run the lane in `repo_dir` but write the report under `out_base`
    (default: `repo_dir`). The container run sets out_base to the mounted
    working directory so reports survive the container, not the lane dir."""
    report = calibration_report(repo_dir, language, manifest_path)
    out_dir = Path(out_base if out_base is not None else repo_dir) / ".mas" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{language}.yaml"
    path.write_text(
        yaml.safe_dump(report.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
