#!/usr/bin/env python3
"""The reconciliation gate's JUDGMENT, measured on its own (ADR-046/049).

WHY THIS EXISTS. `gate_rate` has never been recorded. The only thing that
would record it is bench case 05, and case 05 is an end-to-end run: it
builds the base product, then applies three follow-up FDRs. That is hours
of billing, and it couples two independent questions —

  1. can the system BUILD the 报修 API?          (build/probe/clean rates)
  2. does the gate READ a follow-up correctly?  (gate rate)

— such that a failure at (1) leaves (2) unmeasured. Run 16 is the proof:
the gate shipped inert in Chinese for two releases (ADR-048) and no rate
noticed, because no rate looked.

This script asks only (2). Reading `reconcile.py`, the gate is two stages
and only one of them costs anything:

  - `requirements.relevant()` is DETERMINISTIC — content-word overlap, no
    model, no embeddings. Free. The ADR-048 inert-gate defect lived
    entirely here (`tokens()` returned an empty set, so `reconcile` bailed
    with checked=False without ever calling the model), and it is already
    covered by hermetic tests.
  - `reconcile.reconcile()` is EXACTLY ONE call, max_tokens=2048.

So the gate's whole model-dependent surface is three small calls, not
hours. This runs those three.

WHAT THIS IS NOT. It is not run 17 and not a capability reading of the
series. Two reasons, both load-bearing:

  1. The ledger here is SEEDED, not built. A real run earns its
     requirements from specs the system wrote; this hands them over. A
     seeded ledger is a cleaner input than a real build produces, so a
     pass here does NOT imply a pass end-to-end.
  2. It skips retrieval-over-a-real-corpus. `relevant()` caps at 12 of
     however many requirements a real build produced; here it sees six.

The seed is printed on every run so a reader can audit whether it was
tuned toward the answer. It is a verbatim clause-by-clause decomposition
of case 05's own base FDR — no paraphrase, no English.

Per ADR-056 the result names the instrument that produced it and is
written to `.mas/gate-probe/`, NEVER `benchmarks/results/`.

Usage:
  scripts/gate-probe.py                     # live, ~3 calls, cents
  scripts/gate-probe.py --provider mock     # free; exercises this script only
  scripts/gate-probe.py --model claude-opus-4-8
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import yaml  # noqa: E402

from ai_venture_studio import __version__  # noqa: E402
from ai_venture_studio.product_bench import load_cases  # noqa: E402
from ai_venture_studio.upstream import reconcile as _rec  # noqa: E402
from ai_venture_studio.upstream.requirements import (  # noqa: E402
    load_ledger,
    relevant,
    sync_ledger,
)

CASE = REPO / "benchmarks" / "products-real" / "05-increment-repairs.yaml"

# A verbatim clause-by-clause decomposition of the base FDR in CASE. Each
# entry is a promise the FDR states in its own words; nothing is added,
# reworded, or translated. Criterion 5 is the one the second follow-up is
# expected to collide with, and it is quoted with the reason the FDR gives
# for it ("这条是为了留痕") because that reason is what makes the clash a
# contradiction rather than a preference.
SEED_CRITERIA = [
    "住户能提交一条报修（房号、故障描述）",
    "住户能查看自己提交过的报修",
    "物业能看到全部报修",
    "物业能把某一条报修标记为已完成",
    "报修一旦提交就不能删除，只能由物业标记完成——这条是为了留痕",
    "数据要存在数据库里，重启不丢",
]


def seed_workspace(root: Path) -> None:
    """Write the specs a completed base build would have left behind."""
    spec_dir = root / "specs" / "01-baoxiu-api"
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.yaml").write_text(
        yaml.safe_dump(
            {
                "slug": "01-baoxiu-api",
                "status": "approved",
                # `built: true` matters: `_derived_status` maps it to
                # "built", and only LIVE statuses are retrievable. A
                # seed left at "proposed" would still be live, but it
                # would misdescribe a product that is supposed to exist.
                "built": True,
                "criteria": SEED_CRITERIA,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def outcome_of(rec: _rec.Reconciliation) -> tuple[str, str]:
    """Map a reconciliation onto the EXPECTATIONS vocabulary.

    Mirrors `autopilot._run_feature`'s branch order exactly — duplicates
    win over conflicts there, so they win here. `--yes` is assumed, which
    is what the bench harness passes: under it a conflict does not halt,
    it proceeds and records an UNAPPROVED SCR, which is what the bench
    reads back as `raises_scr`.
    """
    if not rec.checked:
        # Never a clean verdict. "Nobody looked" must not present as
        # "nothing conflicts" — that conflation is the ADR-041 defect and
        # it is exactly how the inert gate scored as a working one.
        return "not_checked", rec.note
    if rec.duplicates:
        ids = ", ".join(d.requirement_id for d in rec.duplicates)
        return "already_satisfied", f"duplicate of {ids}"
    if rec.conflicts:
        ids = ", ".join(c.requirement_id for c in rec.conflicts)
        return "raises_scr", f"contradicts {ids}"
    return "completed", "no duplicate, no contradiction"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--repo-dir", default=str(REPO))
    args = ap.parse_args()

    case = next(c for c in load_cases(CASE.parent) if c.name == CASE.stem)
    if len(case.feature_fdrs) != len(case.feature_expectations):
        print("case is malformed: fdrs and expectations differ in length")
        return 2

    print(f"gate probe — {case.name}")
    print(f"  instrument: provider={args.provider} model={args.model} "
          f"avs={__version__}")
    print(f"  ledger:     SEEDED, {len(SEED_CRITERIA)} criteria from the "
          f"base FDR (printed below for audit)")
    for i, text in enumerate(SEED_CRITERIA, 1):
        print(f"    {i}. {text}")
    print()

    rows = []
    with tempfile.TemporaryDirectory(prefix="avs-gate-probe-") as tmp:
        root = Path(tmp)
        seed_workspace(root)
        sync_ledger(root)
        ledger = load_ledger(root)
        print(f"  ledger synced: {len(ledger)} live requirements "
              f"({', '.join(r.id for r in ledger)})")
        print()

        for fdr, expected in zip(case.feature_fdrs, case.feature_expectations):
            slice_ = relevant(root, fdr)
            rec = _rec.reconcile(
                fdr, slice_, provider=args.provider, model=args.model
            )
            actual, why = outcome_of(rec)
            rows.append(
                {
                    "request": fdr.strip(),
                    "expected": expected,
                    "actual": actual,
                    "why": why,
                    "retrieved": [r.id for r in slice_.shown],
                    "matched": slice_.matched,
                    "pass": actual == expected,
                }
            )

    width = max(len(r["expected"]) for r in rows)
    for row in rows:
        mark = "PASS" if row["pass"] else "FAIL"
        head = row["request"].splitlines()[0]
        print(f"  [{mark}] expected {row['expected']:<{width}}  "
              f"got {row['actual']}")
        print(f"         request:   {head}")
        print(f"         retrieved: {row['retrieved'] or 'NOTHING'} "
              f"(of {row['matched']} scored)")
        print(f"         because:   {row['why']}")
        print()

    passed = sum(1 for r in rows if r["pass"])
    rate = passed / len(rows)
    print(f"  gate judgment: {passed}/{len(rows)} = {rate:.0%}")
    print("  NOT a capability reading of the bench series — seeded ledger, "
          "no build (ADR-049/056).")

    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    out_dir = Path(args.repo_dir) / ".mas" / "gate-probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"gate-probe-{stamp}.yaml"
    out.write_text(
        yaml.safe_dump(
            {
                "kind": "gate-probe",
                "not_a_bench_run": (
                    "seeded ledger, no build; measures reconcile() judgment "
                    "only and never enters benchmarks/results/"
                ),
                "case": case.name,
                "provider": args.provider,
                "model": args.model,
                "avs_version": __version__,
                "seed_criteria": SEED_CRITERIA,
                "gate_judgment_rate": rate,
                "results": rows,
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    print(f"  recorded: {out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    if os.environ.get("ANTHROPIC_API_KEY_FILE") and os.environ.get(
        "ANTHROPIC_API_KEY"
    ):
        # Both set is not an error, but which one wins is worth saying out
        # loud when the point of the run is to know what took the reading.
        print("note: ANTHROPIC_API_KEY is set and takes precedence over "
              "ANTHROPIC_API_KEY_FILE\n")
    raise SystemExit(main())
