"""The release path's two silent outcomes, made loud (ADR-065).

Both are about the same gap: `publish` going green is evidence that the
workflow finished, and it was being read as evidence that PyPI holds this
tag's build. Those come apart exactly when a tag is force-moved, which is
exactly when someone is already having a bad afternoon.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "publish.yml"


@pytest.fixture(scope="module")
def verify_published():
    """`scripts/verify-published.py`, imported by path.

    It lives in `scripts/` rather than in the package because nothing at
    runtime needs it — but the rules inside it are worth a test, which is why
    the comparison is a pure function taking two dicts instead of something
    that can only be exercised by uploading to PyPI.
    """
    path = REPO / "scripts" / "verify-published.py"
    spec = importlib.util.spec_from_file_location("verify_published", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_identical_artifacts_are_no_finding(verify_published):
    local = {"pkg-1.0-py3-none-any.whl": "a" * 64, "pkg-1.0.tar.gz": "b" * 64}
    assert verify_published.mismatches(local, dict(local)) == []


def test_a_skipped_upload_is_a_finding(verify_published):
    """The cancelled-race case: the run built it and PyPI has never seen it."""
    problems = verify_published.mismatches({"pkg-1.0.tar.gz": "a" * 64}, {})
    assert len(problems) == 1
    assert "NOT served by PyPI" in problems[0]


def test_a_different_build_under_the_same_version_is_a_finding(verify_published):
    """THE case this file exists for.

    Same filename, different bytes: PyPI is serving the pre-fix build and the
    tag points at the corrected commit. `uv publish --check-url` skips this
    silently, because it decides "already uploaded" by filename.
    """
    problems = verify_published.mismatches(
        {"pkg-1.0-py3-none-any.whl": "a" * 64},
        {"pkg-1.0-py3-none-any.whl": "b" * 64},
    )
    assert len(problems) == 1
    assert "DIFFERENT build" in problems[0]
    # And it must say the only thing that actually resolves it. A version on
    # PyPI cannot be replaced, so "re-run the release" is wrong advice, and
    # wrong advice at the top of a red release is worse than none.
    assert "bump the version" in problems[0]


def test_a_partial_upload_names_only_the_file_that_disagrees(verify_published):
    """A cancelled run can land the sdist and not the wheel. The report has to
    be per-file, or the operator cannot tell a skipped upload from a mismatched
    one — and those have different remedies."""
    problems = verify_published.mismatches(
        {"pkg-1.0.tar.gz": "a" * 64, "pkg-1.0-py3-none-any.whl": "c" * 64},
        {"pkg-1.0.tar.gz": "a" * 64},
    )
    assert len(problems) == 1
    assert problems[0].startswith("pkg-1.0-py3-none-any.whl")


def test_publish_verifies_what_it_uploaded():
    """Prose in a workflow is not a mechanism (ADR-051). The step has to be
    there, and it has to run after the upload rather than beside it."""
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/verify-published.py" in body, (
        "publish.yml no longer checks what PyPI ended up serving — a skipped "
        "upload is green again"
    )
    assert body.index("uv publish") < body.index("scripts/verify-published.py"), (
        "the verification runs before the upload it is meant to verify"
    )


def test_publish_cancels_only_a_rerun_of_the_same_tag():
    """The concurrency group is keyed on the ref, and the key is the whole
    correctness argument.

    Cancelling on a re-pushed tag is the remedy. Cancelling across tags would
    be a new defect with a worse blast radius: two releases pushed a minute
    apart are both legitimate, and the older one would be killed mid-upload,
    leaving a half-published version that can never be completed.
    """
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    concurrency = spec.get("concurrency")
    assert concurrency, "publish.yml has no concurrency group — a re-pushed tag races itself"
    assert concurrency.get("cancel-in-progress") is True
    assert "github.ref" in concurrency["group"], (
        f"the group is {concurrency['group']!r}, which is not per-tag — this "
        f"would cancel an unrelated release that is still uploading"
    )


def test_retag_refuses_before_it_cancels():
    """Order, asserted on the source, the same way `test_built_flag_durable`
    asserts the bookkeeping order.

    Most of the time this script's job is to refuse: once PyPI serves the
    version, moving the tag cannot change what anyone installs and only makes
    the tag a lie. Cancelling a run first and refusing afterwards would leave
    a legitimate release killed for a move that was never going to be allowed.
    """
    src = (REPO / "scripts" / "retag.sh").read_text(encoding="utf-8")
    refuse = src.index("REFUSING: PyPI already serves")
    cancel = src.index("gh run cancel")
    push = src.index("git push --force origin")
    assert refuse < cancel < push
    # And the wait, without which the cancel is decorative: `gh run cancel`
    # returns when the request is accepted, not when the upload has stopped.
    assert "waiting for $id to stop" in src
