"""No new CLI command may ship without a test that at least types its name.

ADR-054 lost eleven bench runs to ten orphaned lines inside a command no test
ever invoked, and closed with a claim it had no way to check: *"`evaluate()`
has coverage; the CLI path around it had none."* ADR-068 measured that claim
across all 78 commands. Forty-two have never been entered by any test, and
thirty are not so much as named in one.

This is the part that keeps the number from growing. `command_never_run`
does the audit; `KNOWN_UNTYPED` below is the ledger.

**`KNOWN_UNTYPED` is debt, not justification.** The allowlist in
`test_write_without_reader.py` records fields that have a *reason* to have no
reader. This one records the opposite: thirty commands that ought to have a
test and do not, frozen at the 2026-08-22 measurement so the set can only
shrink. Each line says what the command is, so a reader can tell which of
these are merely unmeasured and which are load-bearing — `brief-approve`,
`plan-approve`, `spec-approve` and `scr-approve` are the human judgment
gates, and `automerge` and `deploy-execute` are the two commands that
actually merge and actually deploy.

That reads worse than it is, and the ADR is careful about it: the *logic*
under those commands is tested — `src/ai_venture_studio/policy.py` runs at
94% — and every one of these thirty was probed in an empty directory with
the credentials stripped, where all of them either refused cleanly or ran as
a server. None is broken today. What is missing is the CLI path around them,
which is exactly where ADR-054's defect lived.

Deliberately a subset check, not equality. A command that GAINS a test drops
out of the audit and leaves a harmless stale entry;
`test_the_ledger_has_not_rotted` catches entries for commands that no longer
exist. What must fail is the direction that adds an unmeasured command.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from command_never_run import (
    commands_named_in_tests,
    declared_commands,
    unnamed_commands,
)

REPO = Path(__file__).resolve().parent.parent

#: This file. It contains thirty command names as dict keys, and the audit
#: counts a name in any string literal as "typed by a test". Without this
#: exclusion the ledger below is read as its own evidence and the audit
#: returns nothing, forever. `test_the_ledger_is_not_its_own_evidence` is
#: what proves the exclusion is doing work.
SELF = {Path(__file__)}

#: command -> what it is. Measured 2026-08-22 at 0.111.0; see ADR-068.
KNOWN_UNTYPED: dict[str, str] = {
    # --- the human judgment gates -----------------------------------------
    # The decisions the system is forbidden to make for itself. Their CLI
    # path is the one a human actually touches, and it is unmeasured.
    "brief-approve": "Gate U1 — the human problem-selection decision",
    "plan-approve": "Gate U2 — locks scope; later changes need an SCR",
    "spec-approve": "Gate U3 — the approval that makes a spec buildable",
    "scr-approve": "grants exactly one regeneration of the named spec",
    # --- the two commands that act on the world ---------------------------
    # Both refuse without an armed, human-authored, expiring policy file
    # (ADR-031), and policy.py is 94% covered. The wrapper is not.
    "automerge": "merges a reviewed PR under .mas/automerge-policy.yaml",
    "deploy-execute": "runs the deploy a human wrote in .mas/deploy-exec-policy.yaml",
    "deploy-outcome": "records the human verdict on a past deploy recommendation",
    # --- gate and ledger machinery ----------------------------------------
    "attest": "attestation ledger — chains a review's gate/verdict records",
    "claim-lint": "deterministic claim-ledger lint, the outer loop's ears_lint",
    "eval-gate": "eval-set regression gate — score deltas vs the pinned set",
    "idempotency": "backfill check — the fixture-slice re-run must not drift",
    "review-gate": "fixture-registration gate for the REVIEW voters (probes as a server)",
    "voter-gate": "the voter fixture gate — 8 fixtures, >=87.5% to register",
    "mvp": "checks whether a first slice is minimum AND viable",
    "dwell": "approval-dwell-time report — how long humans sit on a gate",
    # --- export ------------------------------------------------------------
    "cab-package": "assembles a CAB change package from a finished review",
    "evidence-bundle": "exports the Gate-R evidence bundle (unsigned v0)",
    # --- long-running or interactive ---------------------------------------
    # These do not return, which is why no test types them. That explains
    # the absence; it does not make the CLI path measured.
    "serve": "webhook mode — forge events into reviews and incidents",
    "preview": "runs the built product locally for the founder (web profile)",
    "resume": "continues a review paused at Gate 3",
    "recover": "continues reviews and incidents that crashed mid-run",
    # --- founder-facing milestones ------------------------------------------
    "ship": "generates deployment artifacts plus a plain-language DEPLOY.md",
    "undo": "M7 — returns to the previous version behind a rescue branch",
    "walkthrough": "M4 — regenerates product/ACCEPTANCE.md",
    # --- needs a toolchain the hermetic suite does not have -----------------
    # Real reasons for the absence, and still not reasons the wrapper works.
    "calibrate": "calibrates a lane's manifest patterns against a real workspace",
    "data-checks": "runs the workspace's external data checks (dbt auto-detected)",
    "services-cloud": "attempts cloud provisioning, gated on a Supabase login",
    "setup-tests": "小程序 — installs jest + miniprogram-simulate (probes as a server)",
    "toolchain": "runs a language's det_tools slots (ADR-U16)",
    "tenant": "multi-tenant server registry (ADR-030)",
}


@pytest.fixture(scope="module")
def unnamed() -> dict[str, int]:
    return unnamed_commands(REPO, exclude=SELF)


def test_no_new_command_ships_without_a_test_that_names_it(unnamed):
    """The ratchet. The measured set may shrink; it may not grow."""
    new = {n: line for n, line in unnamed.items() if n not in KNOWN_UNTYPED}
    assert not new, (
        "these CLI commands are declared and no test file types their name:\n"
        + "\n".join(f"  avs {n}   (cli.py:{line})" for n, line in new.items())
        + "\n\nADR-054 cost eleven bench runs to exactly this: a command "
        "nobody ran, holding a defect that only appears on the healthy "
        "branch. Add a test that invokes it — or, if it genuinely cannot be "
        "invoked in a hermetic suite, add it to KNOWN_UNTYPED with what it "
        "is, which makes it a recorded debt instead of an accident."
    )


def test_the_ledger_is_not_its_own_evidence():
    """Proof that excluding this file is load-bearing, not decoration.

    ADR-060 was bitten from the other side: an allowlist of field names, in
    a file the audit scanned, supplied every reader it then asserted
    existed, and deleting the real readers left the test green. The same
    trap is sitting right here — thirty command names as dict keys, in a
    file under `tests/`, read by a scan that counts any string literal.

    So this asserts the trap. Scanned WITHOUT the exclusion, every single
    entry in the ledger disappears from the audit's output, because this
    file is the thing naming them. If that ever stops being true the
    exclusion has silently stopped working, and the ratchet above is
    measuring nothing.
    """
    without_exclusion = unnamed_commands(REPO)
    supplied = set(KNOWN_UNTYPED) & set(without_exclusion)
    assert not supplied, (
        "the self-exclusion is not working: these commands are still "
        f"reported as unnamed even while this file names them — {supplied}"
    )
    assert not without_exclusion, (
        "unscoped, the audit should report zero commands; anything left is "
        "a command not even this ledger mentions: "
        f"{sorted(without_exclusion)}"
    )


def test_the_ledger_has_not_rotted():
    """Every entry must still be a command. A renamed or deleted command
    leaves a key that silently excuses nothing, and the next command to take
    that name inherits the excuse."""
    declared = declared_commands(REPO)
    stale = sorted(set(KNOWN_UNTYPED) - set(declared))
    assert not stale, (
        f"KNOWN_UNTYPED names commands cli.py no longer declares: {stale}"
    )


def test_the_audit_detects_an_unnamed_command(tmp_path):
    """The instrument, on a tree where the answer is known.

    Without this, every assertion above passes just as well when the scan is
    broken — which is ADR-067's whole finding, that an empty measurement and
    a passing one are the same output.
    """
    cli = tmp_path / "src" / "pkg"
    cli.mkdir(parents=True)
    (cli / "cli.py").write_text(
        "import typer\n"
        "app = typer.Typer()\n"
        "@app.command()\n"
        "def typed_by_a_test():\n"
        "    pass\n"
        '@app.command("renamed-command")\n'
        "def some_function():\n"
        "    pass\n"
        "@app.command()\n"
        "def nobody_types_this():\n"
        "    pass\n"
        "@app.callback()\n"
        "def root():\n"
        "    pass\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        'def test_a():\n    run(["typed-by-a-test"])\n'
        '    run(["renamed-command"])\n',
        encoding="utf-8",
    )

    unnamed = unnamed_commands(tmp_path, cli_rel="src/pkg/cli.py")
    assert set(unnamed) == {"nobody-types-this"}, (
        "the underscore->dash default, the explicit @app.command(name) "
        "override, and the exclusion of @app.callback must all hold, or the "
        "real audit is keyed on names nobody types"
    )


def test_the_audit_refuses_to_report_from_no_measurement(tmp_path):
    """Three ways this could return a clean answer from nothing, all of them
    load-bearing: a moved cli.py, a decorator this scan stops recognising,
    and an exclusion that swallows the whole suite."""
    (tmp_path / "tests").mkdir()
    with pytest.raises(FileNotFoundError):
        declared_commands(tmp_path)

    cli = tmp_path / "src" / "pkg"
    cli.mkdir(parents=True)
    (cli / "cli.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="zero commands"):
        declared_commands(tmp_path, cli_rel="src/pkg/cli.py")

    with pytest.raises(RuntimeError, match="no test files"):
        commands_named_in_tests(tmp_path, {"a"}, exclude=set())
