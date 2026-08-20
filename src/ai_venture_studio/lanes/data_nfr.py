"""Data NFR vocabulary + lineage (doc 18 §48.1).

The data profile's non-functional grammar, perf.py-style: freshness,
row-count tolerance, eval floors, cost-per-run become lintable; "fresh
enough" dies like "fast" did. Lineage is declared per dataset; a change
touching a dataset with undeclared consumers is a finding — the 3 a.m.
consumer is the party the check protects.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

DATA_NFR = re.compile(
    r"^(?P<dataset>[\w.]+)\s+SHALL\s+"
    r"(?P<metric>freshness|row_count_delta|eval_score|cost_per_run)\s*"
    r"(?P<op><=|<|>=|>)\s*(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>h|m|%|usd)?\s*(?:AT\s+p(?P<pct>50|95|99))?\s*$",
    re.I,
)
VAGUE_DATA = re.compile(
    r"\b(fresh enough|reasonably fresh|roughly complete|about right|"
    r"good quality data|cheap to run|low drift)\b", re.I)


class DataNfrIssue(BaseModel):
    index: int
    criterion: str
    problem: str


def lint_data_nfr(criteria: list[str]) -> list[DataNfrIssue]:
    issues = []
    for i, criterion in enumerate(criteria):
        text = criterion.strip()
        vague = VAGUE_DATA.search(text)
        if vague:
            issues.append(DataNfrIssue(index=i, criterion=criterion,
                                       problem=f"vague data term {vague.group(0)!r} — "
                                               "use <dataset> SHALL <metric> <op> <value>"))
        elif not DATA_NFR.match(text):
            issues.append(DataNfrIssue(index=i, criterion=criterion,
                                       problem="does not match: <dataset> SHALL "
                                               "freshness|row_count_delta|eval_score|"
                                               "cost_per_run <op> <value> [AT pXX]"))
    return issues


class LineageIssue(BaseModel):
    dataset: str
    rule: str
    message: str


def lineage_impact_check(
    lineage: dict[str, dict], changed_datasets: list[str]
) -> list[LineageIssue]:
    """lineage: {dataset: {consumers: [...], owner: str}}. A changed
    dataset must declare its consumers — an empty declared list is a
    statement; an ABSENT dataset is the finding."""
    issues = []
    for dataset in changed_datasets:
        entry = lineage.get(dataset)
        if entry is None:
            issues.append(LineageIssue(
                dataset=dataset, rule="undeclared_lineage",
                message="changed dataset has no lineage declaration — its "
                        "consumers are unknown, which is not the same as none"))
            continue
        consumers = entry.get("consumers")
        if consumers is None:
            issues.append(LineageIssue(
                dataset=dataset, rule="consumers_unstated",
                message="lineage entry lacks a consumers list (empty list = "
                        "declared leaf; missing = unknown)"))
        elif consumers:
            issues.append(LineageIssue(
                dataset=dataset, rule="impact_review",
                message=f"{len(consumers)} declared consumer(s) "
                        f"({', '.join(map(str, consumers[:5]))}) — impact "
                        "review required before merge"))
    return issues
