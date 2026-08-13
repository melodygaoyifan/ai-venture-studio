"""`__version__` drifted from pyproject for two releases (v0.71.0 and
v0.71.1 both shipped declaring 0.70.1) because nothing compared them. The
release checklist said to bump both; a checklist is a habit, and this repo's
own thesis is that habits lapse. This is the check that does not."""

import pathlib
import re
import tomllib

import ai_venture_studio

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_dunder_version_matches_pyproject():
    assert ai_venture_studio.__version__ == _pyproject_version(), (
        "src/ai_venture_studio/__init__.py __version__ and pyproject.toml "
        "version disagree — bump both."
    )


def test_changelog_leads_with_the_version_being_shipped():
    """The top entry must be the version in pyproject, so a release cannot go
    out describing itself as the previous one."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## v(\d+\.\d+\.\d+)", text, flags=re.MULTILINE)
    assert headings, "CHANGELOG.md has no '## vX.Y.Z' entries"
    assert headings[0] == _pyproject_version(), (
        f"CHANGELOG's newest entry is v{headings[0]} but pyproject says "
        f"{_pyproject_version()}"
    )


# --- the release-verification script's own invariants ----------------------
#
# 0.82.0, 0.83.0 and 0.84.0 were each "verified" from PyPI by a command of the
# shape `pip install --quiet ... 2>&1 | tail -3`. That renders a FAILED
# install indistinguishable from a successful one — pip's ERROR lines scroll
# past the tail and the two upgrade notices it prints either way are all that
# survives. Three times the failure was read as a propagation hiccup that
# `--force-reinstall` fixed; it was neither. scripts/verify-release.sh exists
# so the verification is a script instead of a typed-from-memory pipeline, and
# these tests exist so the script cannot quietly grow the habit back.

VERIFY_RELEASE = ROOT / "scripts" / "verify-release.sh"


def _verify_release_commands() -> list[str]:
    """The script's lines with comments and blanks removed — the part that runs."""
    return [
        line
        for raw in VERIFY_RELEASE.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


def test_release_verification_never_silences_pip():
    """Every pip invocation must leave its output readable."""
    for line in _verify_release_commands():
        if "pip install" not in line:
            continue
        assert " -q" not in line and "--quiet" not in line, (
            f"verify-release.sh silences pip: {line!r}. A quiet failure looks "
            "exactly like a success; that is the bug this script is for."
        )
        assert "| tail" not in line and "| head" not in line, (
            f"verify-release.sh truncates pip's output: {line!r}. The reason "
            "an install failed is in the lines a tail throws away."
        )


def test_release_verification_checks_the_console_script_not_just_the_install():
    """`Successfully installed` is not evidence that `avs` landed on disk."""
    body = VERIFY_RELEASE.read_text(encoding="utf-8")
    assert '-x "$WORK/venv/bin/$script"' in body, (
        "verify-release.sh must assert the console script exists — pip "
        "reporting success is what fooled us three releases running."
    )
    assert '"$WORK/venv/bin/avs" --version' in body, (
        "verify-release.sh must run the installed avs and compare its "
        "reported version against the one being verified."
    )


def test_release_verification_is_executable():
    import os

    assert os.access(VERIFY_RELEASE, os.X_OK), (
        "scripts/verify-release.sh is not executable; chmod +x it."
    )
