"""The metric vocabulary (§22.62.3) — the outer loop's EARS grammar.

Every metric has a definition file in metrics/*.md (human-owned) or it is
not a metric: an outcome citing an undefined metric fails the same way an
AC citing "fast" dies at quantifier_scan. A definition change is a
breaking change (§22.62.4): it records changed_at and RESETS the baseline
— comparing across it is a finding, because silent redefinition is the
outer loop's cheapest form of self-deception (FMEA F-22.1).
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re

import yaml
from pydantic import BaseModel, Field

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)


class MetricDefinition(BaseModel):
    id: str
    definition: str
    numerator_event: str = ""
    denominator: str = ""
    window_days: int = 0
    cohort_basis: str = ""
    exclusions: list[str] = Field(default_factory=list)
    owner: str = "human"
    changed_at: str = ""  # last definition change; baseline resets here


class MetricIssue(BaseModel):
    metric_id: str
    rule: str
    message: str


class MetricVocabularyError(RuntimeError):
    """A metrics/*.md file that does not parse fails loading outright."""


def load_metric_vocabulary(
    metrics_dir: str | pathlib.Path,
) -> dict[str, MetricDefinition]:
    vocabulary = {}
    root = pathlib.Path(metrics_dir)
    if not root.is_dir():
        return vocabulary
    for path in sorted(root.glob("*.md")):
        match = _FRONTMATTER.match(path.read_text())
        if not match:
            raise MetricVocabularyError(f"{path.name}: no YAML front-matter")
        try:
            raw = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise MetricVocabularyError(f"{path.name}: {exc}") from exc
        spec = raw.get("metric")
        if not isinstance(spec, dict) or not spec.get("id"):
            raise MetricVocabularyError(f"{path.name}: front-matter lacks metric.id")
        definition = MetricDefinition(**spec)
        vocabulary[definition.id] = definition
    return vocabulary


def metric_definition_check(
    cited_metrics: list[str], vocabulary: dict[str, MetricDefinition]
) -> list[MetricIssue]:
    """A metric cited without a definition file is not a metric."""
    return [
        MetricIssue(
            metric_id=metric_id,
            rule="undefined_metric",
            message=f"{metric_id!r} has no definition file in metrics/ — "
            "define it or do not cite it",
        )
        for metric_id in cited_metrics
        if metric_id not in vocabulary
    ]


def baseline_comparable(
    metric: MetricDefinition, earlier: dt.date, later: dt.date
) -> bool:
    """False when the two readings straddle a definition change — the old
    series is preserved and labeled, never silently continued."""
    if not metric.changed_at:
        return True
    changed = dt.date.fromisoformat(str(metric.changed_at))
    return not (earlier < changed <= later)


def comparison_issues(
    metric: MetricDefinition, earlier: dt.date, later: dt.date
) -> list[MetricIssue]:
    if baseline_comparable(metric, earlier, later):
        return []
    return [
        MetricIssue(
            metric_id=metric.id,
            rule="definition_change_break",
            message=f"readings {earlier} and {later} straddle the "
            f"{metric.changed_at} definition change — the baseline reset there; "
            "restate the series with the break marked (F-22.1)",
        )
    ]
