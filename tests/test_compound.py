import datetime

import yaml

from ai_venture_studio.compound import (
    SECTION_HEADER,
    Proposal,
    apply_to_claude_md,
    collect_signals,
    propose,
    render_proposal,
)


def _write_final(tmp_path, review_id: str, titles: list[str], verdict="REQUEST_CHANGES"):
    review_dir = tmp_path / ".mas" / "reviews" / review_id
    review_dir.mkdir(parents=True)
    (review_dir / "08-final.yaml").write_text(
        yaml.safe_dump(
            {
                "node": "final",
                "written_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "verdict": verdict,
                "findings": [
                    {"title": t, "taxonomy_hint": "P9", "severity": "high"} for t in titles
                ],
            }
        )
    )


def test_collect_signals_finds_recurrence(tmp_path):
    _write_final(tmp_path, "r1", ["Swallowed exception hides failures"])
    _write_final(tmp_path, "r2", ["Swallowed exception hides failures!"])
    _write_final(tmp_path, "r3", ["One-off finding"])
    signals = collect_signals(tmp_path, days=7)
    assert signals.review_count == 3
    assert signals.taxonomy_counts["P9"] == 3
    assert signals.recurring_titles[0][1] == 2  # normalized dedupe across runs


def test_old_reviews_fall_outside_window(tmp_path):
    review_dir = tmp_path / ".mas" / "reviews" / "old"
    review_dir.mkdir(parents=True)
    stale = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
    (review_dir / "08-final.yaml").write_text(
        yaml.safe_dump(
            {"written_at": stale.isoformat(), "verdict": "APPROVE", "findings": []}
        )
    )
    assert collect_signals(tmp_path, days=7).review_count == 0


def test_propose_via_mock_meets_evidence_bar(tmp_path):
    _write_final(tmp_path, "r1", ["Swallowed exception"])
    _write_final(tmp_path, "r2", ["Swallowed exception"])
    signals = collect_signals(tmp_path, days=7)
    proposals = propose(signals, provider="mock", model="m")
    assert proposals
    assert "Swallowed exception" in proposals[0].constraint


def test_no_signals_no_proposals(tmp_path):
    signals = collect_signals(tmp_path, days=7)
    assert propose(signals, provider="mock", model="m") == []


def test_apply_to_claude_md_is_idempotent_and_preserves_content(tmp_path):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text("# Project rules\n\nAlways use uv.\n")
    p1 = [Proposal(constraint="Never swallow exceptions", rationale="seen 2x")]
    apply_to_claude_md(tmp_path, p1, date="2026-07-22")
    p2 = [Proposal(constraint="Parameterize all SQL", rationale="seen 3x")]
    apply_to_claude_md(tmp_path, p2, date="2026-07-29")
    text = claude.read_text()
    assert "Always use uv." in text                    # user content preserved
    assert text.count(SECTION_HEADER) == 1             # section replaced, not stacked
    assert "Parameterize all SQL" in text
    assert "Never swallow exceptions" not in text      # superseded window


def test_render_proposal_mentions_human_gate(tmp_path):
    _write_final(tmp_path, "r1", ["X"])
    signals = collect_signals(tmp_path, days=7)
    report = render_proposal(signals, [], date="2026-07-22")
    assert "Human-gated" in report


# ── an empty window records WHY it was empty ─────────────────────────────


def _write_stale_final(tmp_path, review_id: str, *, days_ago: int):
    review_dir = tmp_path / ".mas" / "reviews" / review_id
    review_dir.mkdir(parents=True)
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    (review_dir / "08-final.yaml").write_text(
        yaml.safe_dump({"written_at": when.isoformat(), "verdict": "APPROVE",
                        "findings": []})
    )
    return when


def test_signals_count_the_reviews_the_window_left_behind(tmp_path):
    """A window of zero is ambiguous by itself: a loop pointed at a workspace
    nobody ever built and a loop over a workspace where the work stopped both
    read "0 reviews", and only one of those is a misconfiguration."""
    _write_stale_final(tmp_path, "old-1", days_ago=30)
    newest = _write_stale_final(tmp_path, "old-2", days_ago=11)

    signals = collect_signals(tmp_path, days=7)

    assert signals.review_count == 0
    assert signals.reviews_on_disk == 2
    assert signals.newest_written_at.startswith(newest.date().isoformat())


def test_a_workspace_with_no_reviews_at_all_says_nothing_it_cannot_know(tmp_path):
    signals = collect_signals(tmp_path, days=7)

    assert (signals.reviews_on_disk, signals.newest_written_at) == (0, "")


def test_the_proposal_records_why_its_window_was_empty(tmp_path):
    """`avs cadence` reads this file back. Without the sentence it can only
    report the symptom — "nothing to compound" — which names no cause and
    supports no decision."""
    _write_stale_final(tmp_path, "old", days_ago=11)
    signals = collect_signals(tmp_path, days=7)

    report = render_proposal(signals, [], date="2026-08-12")

    assert "Nothing reached this window: 1 review(s) exist, newest " in report
    assert signals.newest_written_at[:10] in report


def test_a_never_built_workspace_is_reported_as_such(tmp_path):
    """The other cause, and the one that means the loop is watching the
    wrong directory."""
    report = render_proposal(collect_signals(tmp_path, days=7), [],
                             date="2026-08-12")

    assert "no review has ever been written here" in report


def test_a_window_with_reviews_claims_no_emptiness(tmp_path):
    """The sentence appears only when it is true — a run that read reviews
    and found nothing worth proposing did real work."""
    _write_final(tmp_path, "r1", ["X"])

    report = render_proposal(collect_signals(tmp_path, days=7), [],
                             date="2026-08-12")

    assert "Nothing reached this window" not in report
