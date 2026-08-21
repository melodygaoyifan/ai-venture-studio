"""No new fact may be written into a record that nothing reads.

ADR-058 found six instances of one defect — a component knew something and
the record dropped it before it reached the reader that needed it — and
found every one of them by hand, after a $67 bench run had already paid to
expose the symptom. ADR-060 asked the same question mechanically instead,
and got five more in an afternoon for nothing, including two fields on
`BuildResult` that ADR-058's own fix walked straight past while adding three
of their neighbours.

This test is the part that keeps it from coming back. `write_without_reader`
does the audit; `KNOWN_UNREAD` below is where the judgment lives. Every entry
is a field that is written and not read AND has a reason to stay that way,
written down. Anything else fails.

Deliberately a subset check, not an equality check. A field that GAINS a
reader should not break an unrelated change — it just drops out of the audit
and leaves a harmless stale entry. What must fail is the direction that
loses information: a new field nobody reads. `test_the_allowlist_has_not
_rotted` catches entries for fields that no longer exist at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from write_without_reader import declared_fields, unread_fields

REPO = Path(__file__).resolve().parent.parent


#: field -> why it is legitimately written without an in-repo reader.
#: A reason is required. "It might be useful later" is not one of these.
KNOWN_UNREAD: dict[str, str] = {
    # --- public library surface -------------------------------------------
    # These are exported from a package `__init__` and have no in-repo caller
    # at all. The field is the payload; the reader is the importer.
    "exposed_n": "HoldoutComparison is exported; compare_holdout has no in-repo caller",
    "exposed_rate": "the exposed arm's rate on that same exported comparison",
    "exposed_ci": "the exposed arm's interval on that same exported comparison",
    "holdout_n": "the holdout arm's size on that same exported comparison",
    "holdout_rate": "the holdout arm's rate on that same exported comparison",
    "holdout_ci": "the holdout arm's interval on that same exported comparison",
    "p_value": "BHResult is the returned record of benjamini_hochberg; callers pick",
    "total_n": "PowerResult is a returned record; the caller chooses what to use",
    "in_flight": "WipCheck echoes its own input so the record is self-describing",
    # --- the subject of a finding -----------------------------------------
    # An id saying WHICH thing a finding is about. Nothing dispatches on it
    # because nothing needs to; dropping it would make the finding unusable.
    "asset_id": "names the asset a UtmFinding is about",
    "signal_id": "names the signal a Routing is about",
    "candidate_id": "names the candidate a GatePL0Finding is about",
    "artifact_id": "names the artifact a GatePL3Packet is about",
    "session_id": "names the session a SessionResult is about",
    "evidence_ref": "points at the evidence behind a HypothesisVerdict",
    # --- read by a person, out of a file ----------------------------------
    # Append-only history and approval surfaces. The reader is a human with
    # the YAML open, which is the reader these records were built for.
    "hypotheses_falsified": "kill-registry.yaml is read by a person deciding to revisit",
    "signal_refs": "provenance on an OpportunityCandidate, for the human reviewing it",
    "changelog_refs": "release-contract provenance, read at review time",
    "disclosure_block": "the exact disclosure text a human approves at Gate PL3",
    "hard_fails": "counted onto the packet a human approves; ADR-060 made it a count",
    "review_flag": "marks a finding needing human judgment on a surface a human reads",
    "modified_at": "page metadata carried through to the rendered artifact",
    # --- declared, not yet enforced ---------------------------------------
    # The `escalate_on` shape (ADR-060): a knob that reads like it controls
    # something and does not. Kept, named, and NOT quietly deleted, because
    # the schema is a published contract. Both are open items.
    "allowed_error_classes": (
        "declared in the .spec.yaml schema; spec_drift_check does not yet "
        "enforce it — an open item, named here rather than forgotten"
    ),
    "complaint_rate_target": (
        "an operating target beside the enforced complaint_rate_ceiling; "
        "aspirational by design, for the human reading deliverability.yaml"
    ),
}


@pytest.fixture(scope="module")
def unread() -> dict[str, list[str]]:
    return unread_fields(REPO)


def test_every_written_fact_has_a_reader_or_a_written_reason(unread):
    surprises = {f: sites for f, sites in unread.items() if f not in KNOWN_UNREAD}
    assert not surprises, (
        "These fields are written and nothing in the repo reads them:\n\n"
        + "\n".join(f"  {f}: {'; '.join(sites)}" for f, sites in surprises.items())
        + "\n\nThis is ADR-058's defect: the run knows something and the record "
        "does not carry it to anyone. Either give the field a reader, or add it "
        "to KNOWN_UNREAD in this file WITH the reason it is legitimately "
        "unread. A field with neither is a fact the system does not have."
    )


def test_the_allowlist_has_not_rotted():
    """Every excused field still exists. A renamed or deleted field leaving
    its excuse behind is how an allowlist stops describing the code."""
    declared = declared_fields(REPO / "src", REPO)
    gone = sorted(set(KNOWN_UNREAD) - set(declared))
    assert not gone, (
        f"KNOWN_UNREAD excuses field(s) that no longer exist: {gone}. "
        "Delete the entries."
    )


def test_every_excuse_is_an_actual_sentence():
    """The allowlist's value is the reasons. An empty one re-admits exactly
    the silence this audit exists to break."""
    thin = sorted(f for f, why in KNOWN_UNREAD.items() if len(why.split()) < 4)
    assert not thin, f"KNOWN_UNREAD entries need a real reason, not a shrug: {thin}"


def test_the_audit_finds_a_planted_defect(tmp_path):
    """The control. A test that only ever passes proves nothing about whether
    it could fail — ADR-059's lesson, applied to ADR-059's own successor."""
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "m.py").write_text(
        "from pydantic import BaseModel\n\n\n"
        "class R(BaseModel):\n"
        "    read_by_someone: str = ''\n"
        "    nobody_reads_this: str = ''\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "reader.py").write_text(
        "def f(r):\n    return r.read_by_someone\n", encoding="utf-8"
    )
    unread = unread_fields(tmp_path)
    assert "nobody_reads_this" in unread
    assert "read_by_someone" not in unread


def test_the_five_defects_adr_060_fixed_stay_fixed(unread):
    """Named individually, because each was a distinct reader that had to be
    built — not one change that happened to clear five rows."""
    for field in (
        "wireup_issues",      # built, tests green, nothing imports it
        "modified_existing",  # "visible, reviewed, never silent", and invisible
        "policy_path",        # which policy authorized a merge or a deploy
        "escalate_on",        # a cascade trigger tuple nothing consulted
        "build_floor",        # the bar the kill criterion judged against
        "probe_floor",
    ):
        assert field not in unread, (
            f"{field} lost its reader again — see ADR-060; the reader it had "
            "is the point of the field, not a nicety."
        )
