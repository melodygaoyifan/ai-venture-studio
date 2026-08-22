"""Root conftest — session-scoped hooks only.

Session hooks live here and not in `tests/conftest.py`. The original reason
given was that pytest invokes `pytest_sessionfinish` "only from the rootdir
conftest, never from a subdirectory one", and **that is not true** — measured
directly on pytest 9.1.1 while adding the ADR-071 hook, a subdirectory
conftest's `pytest_sessionfinish` fires. Whatever silently swallowed the
original PC-1 check (the suite passed 1667 against a claim of 1655 and said
nothing), it was not this.

The rule survives its explanation, with the real reason: a subdirectory
conftest is loaded only if collection reaches that directory, so a session
hook living there is conditional on the run's arguments. A check that measures
the whole session must not be one the invocation can switch off.

Correcting it in place rather than leaving it standing — a false reason
attached to a correct rule is what ADR-069's `tests/` scope note turned out to
be, and ADR-070 had to go and count the population it had been declined on.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests"))

import exec_audit  # noqa: E402 - needs the sys.path line above

# Installed at rootdir-conftest import, which is the earliest hook pytest has,
# so the audit covers collection as well as the tests themselves (ADR-071).
exec_audit.install()


def pytest_sessionfinish(session, exitstatus):
    """Fail the run on a bare executable name, then measure PC-1.

    The exec audit is checked first and unconditionally: unlike PC-1 it is not
    a whole-suite-only question, and a subset that spawns an offender should
    fail on the subset.

    Reported here rather than raised from the audit hook itself. An exception
    thrown inside `subprocess.Popen` surfaces at the call site, and this
    codebase has try/except around plenty of external tools — the accusation
    could be swallowed by the very code it is accusing.
    """
    if exec_audit.VIOLATIONS:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line("")
            reporter.write_line(exec_audit.report(), red=True)
        else:
            print("\n" + exec_audit.report())
        session.exitstatus = 1

    # `or session.exitstatus` so a bare-name failure suppresses the PC-1 line
    # for the same reason any other failure does: a failing suite has a louder
    # problem than a stale claim.
    _check_pc1(session, exitstatus or session.exitstatus)


def _check_pc1(session, exitstatus):
    """Measure PC-1 against the run that just happened (see tests/claim_count.py).

    Only on a whole-suite, unfiltered run — a subset would compare its own
    handful against the whole suite's claim, and subsets are what everyone runs
    all day. A mismatch fails the RELEASE run (`AVS_RELEASE_CHECK`, set by
    publish.yml at the tag, which is what PC-1's "at the current tag" means)
    and is reported loudly otherwise, because main drifting between releases is
    normal while a released number being fiction is not.
    """
    from claim_count import (
        RELEASE_ENV,
        claimed_test_count,
        is_whole_suite_run,
        verdict,
    )

    config = session.config
    if not is_whole_suite_run(
        config.args, config.option.keyword, config.option.markexpr, config.rootdir
    ):
        return
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    # A failing suite has a louder problem than a stale claim; don't bury it.
    if exitstatus != 0:
        return
    problem = verdict(len(reporter.stats.get("passed", [])), claimed_test_count())
    if problem is None:
        return
    reporter.write_line("")
    if os.environ.get(RELEASE_ENV):
        reporter.write_line("PC-1 SELF-CHECK FAILED: " + problem, red=True)
        session.exitstatus = 1
    else:
        reporter.write_line(
            "PC-1 drift (not fatal outside a release): " + problem, yellow=True
        )
