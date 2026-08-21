"""The analytics boundary (§22.64, invariant 14.16) — enforced at the
query layer, not by instructing the agent.

Person-level data never leaves the analytics boundary. The store refuses
to return person-level rows — there is no method that does — applies the
k-anonymity cohort floor server-side, and redacts free text through
pii_scan before returning it. An agent that asks for individual rows
receives an error, which is the only reliable form of this control
(§11.19's no-degraded-mode principle applied to data).

The operator's real analytics system is the runtime behind this interface
— wrapped, never vendored (§23 Appendix N). The in-memory implementation
is the reference and the test double at once: whatever the backend, the
egress surface is exactly this.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from ai_venture_studio.product.taint import MIN_COHORT_FLOOR

# Field names that identify a person. Extendable upward by config, never
# shrinkable — same posture as the taint classes they implement.
PERSON_LEVEL_FIELDS = frozenset(
    {"user_id", "email", "ip", "device_id", "phone", "name", "account_id"}
)

_PII_PATTERNS = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[email redacted]"),
    (
        re.compile(
            r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}\b"
        ),
        "[phone redacted]",
    ),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn redacted]"),
)


class PersonLevelQueryError(RuntimeError):
    """An agent asked for person-level data. There is no override."""


class CohortTooSmallError(RuntimeError):
    """The cohort is under the k-anonymity floor; aggregation refused."""


class CohortAggregate(BaseModel):
    group: dict[str, str]
    n: int
    numerator: int
    value: float


def pii_scan(text: str) -> str:
    """Redact person-identifying strings from free text before egress."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class AnalyticsStore:
    """Read-only, aggregate-scoped access to event rows.

    The public surface is cohort_aggregate() and free_text() — there is
    deliberately no row-returning method to misuse.
    """

    def __init__(
        self, events: list[dict], *, min_cohort_size: int = MIN_COHORT_FLOOR
    ) -> None:
        if min_cohort_size < MIN_COHORT_FLOOR:
            raise PersonLevelQueryError(
                f"cohort floor {min_cohort_size} below the taint-class floor "
                f"{MIN_COHORT_FLOOR} — configurable upward only (§22.64)"
            )
        self._events = list(events)
        self._floor = min_cohort_size

    def cohort_aggregate(
        self,
        *,
        group_by: list[str],
        numerator_event: str,
        distinct_field: str = "unit",
        where: dict[str, str] | None = None,
    ) -> list[CohortAggregate]:
        """Aggregate counts per cohort. Grouping or filtering by a
        person-level field is refused; cohorts under the floor are refused."""
        for field in [*group_by, *(where or {})]:
            if field in PERSON_LEVEL_FIELDS:
                raise PersonLevelQueryError(
                    f"field {field!r} is person-level — person-level data never "
                    "leaves the analytics boundary (invariant 14.16)"
                )
        if distinct_field in PERSON_LEVEL_FIELDS:
            raise PersonLevelQueryError(
                f"distinct_field {distinct_field!r} is person-level; use the "
                "pseudonymous cohort unit"
            )

        groups: dict[tuple, dict] = {}
        for event in self._events:
            if where and any(event.get(k) != v for k, v in where.items()):
                continue
            key = tuple(str(event.get(g, "")) for g in group_by)
            bucket = groups.setdefault(key, {"units": set(), "hits": set()})
            unit = event.get(distinct_field)
            if unit is None:
                continue
            bucket["units"].add(unit)
            if event.get("event") == numerator_event:
                bucket["hits"].add(unit)

        results = []
        for key, bucket in sorted(groups.items()):
            n = len(bucket["units"])
            if n < self._floor:
                raise CohortTooSmallError(
                    f"cohort {dict(zip(group_by, key, strict=True))} has n={n}, below the "
                    f"k-anonymity floor {self._floor} — refused, not rounded"
                )
            hits = len(bucket["hits"])
            results.append(
                CohortAggregate(
                    # strict: `key` is built column-by-column from
                    # `group_by`. A mismatch would mislabel a cohort, which
                    # is worse than raising in an aggregation nobody reads.
                    group=dict(zip(group_by, key, strict=True)),
                    n=n,
                    numerator=hits,
                    value=hits / n,
                )
            )
        return results

    def free_text(self, *, kind: str, limit: int = 50) -> list[str]:
        """Free-text fields (churn reasons, survey answers) — PII-redacted
        before they leave the boundary, never attributed."""
        texts = [
            pii_scan(str(e.get("text", "")))
            for e in self._events
            if e.get("kind") == kind and e.get("text")
        ]
        return texts[:limit]
