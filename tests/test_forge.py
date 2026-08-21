"""Forge dispatch (forge.py): GitHub and GitLab behind one seam.

Hermetic: every `gh`/`glab`/`git` invocation is intercepted; nothing here
touches a network or requires either CLI installed.

That last clause got harder in ADR-064. `forge._run` now resolves `argv[0]`
through `PATH` BEFORE `subprocess.run` is reached, so intercepting
`subprocess.run` no longer intercepts everything — on a machine without `glab`,
these tests would take the "not installed" branch and never exercise dispatch
at all. So `PATH` is faked too, and the assertions compare the tool's NAME
rather than the absolute path a given machine happens to produce.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from ai_venture_studio import forge

_FAKE_BIN = "/fake/bin"


@pytest.fixture(autouse=True)
def _every_cli_is_installed(monkeypatch):
    """Hermeticity, restored: every CLI resolves, none of them exists."""
    monkeypatch.setattr(
        "ai_venture_studio.executables.shutil.which",
        lambda name: f"{_FAKE_BIN}/{name}",
    )


def _cmd(argv: list[str]) -> list[str]:
    """`argv` with the resolved absolute path reduced back to the tool name."""
    return [argv[0].rsplit("/", 1)[-1], *argv[1:]]

GH_PR = "https://github.com/acme/widgets/pull/42"
GHE_PR = "https://github.acme-internal.com/platform/widgets/pull/7"
GL_MR = "https://gitlab.com/acme/widgets/-/merge_requests/26"
GL_SELF_MR = "https://gitlab.acme-internal.com/data/subgroup/mapop/-/merge_requests/26"


class _Recorder:
    """Stands in for subprocess.run; records argv and plays back a result."""

    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.results:
            returncode, stdout = self.results.pop(0)
        else:
            returncode, stdout = 0, "ok"
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


# --- URL detection ------------------------------------------------------------


def test_detect_github_gitlab_and_selfmanaged_hosts():
    assert forge.detect(GH_PR) == "github"
    assert forge.detect(GHE_PR) == "github"  # GitHub Enterprise Server
    assert forge.detect(GL_MR) == "gitlab"
    assert forge.detect(GL_SELF_MR) == "gitlab"  # subgroups + own host
    assert forge.detect("main...HEAD") is None
    assert forge.detect("https://example.com/some/page") is None


def test_gitlab_parts_keeps_host_and_subgroups():
    repo_url, iid = forge._gitlab_parts(GL_SELF_MR)
    assert repo_url == "https://gitlab.acme-internal.com/data/subgroup/mapop"
    assert iid == "26"


# --- comments -----------------------------------------------------------------


def test_post_comment_dispatches_to_glab_for_mr_urls(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(forge.subprocess, "run", rec)
    note = forge.post_comment(GL_SELF_MR, "review body")
    assert note is None
    assert _cmd(rec.calls[0])[:4] == ["glab", "mr", "note", "26"]
    assert "https://gitlab.acme-internal.com/data/subgroup/mapop" in rec.calls[0]


def test_post_comment_refuses_non_change_request_targets():
    assert forge.post_comment("main...HEAD", "x") is not None


# --- merge (ADR-031 posture preserved) ----------------------------------------


def test_merge_refuses_non_cr_targets_and_unknown_methods():
    ok, note = forge.merge("not-a-url")
    assert ok is False and "refusing to merge" in note
    ok, note = forge.merge(GH_PR, method="force-push-somehow")
    assert ok is False and "unknown merge method" in note


def test_merge_never_overrides_branch_protection():
    """No `gh --admin`, no GitLab force flag: a blocked merge is a human's
    configured intent (mirrors test_automation's github.py guarantee)."""
    source = inspect.getsource(forge.merge)
    body = source[source.index('"""', source.index('"""') + 3):]
    assert "--admin" not in body


def test_merge_maps_methods_to_glab_flags(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(forge.subprocess, "run", rec)
    forge.merge(GL_MR, method="squash")
    assert _cmd(rec.calls[-1])[:3] == ["glab", "mr", "merge"]
    assert "--squash" in rec.calls[-1]
    forge.merge(GL_MR, method="merge")
    assert "--squash" not in rec.calls[-1] and "--rebase" not in rec.calls[-1]


# --- head branch --------------------------------------------------------------


def test_head_branch_parses_glab_json(monkeypatch):
    payload = json.dumps({"iid": 26, "source_branch": "fix/repo-bug-sweep"})
    rec = _Recorder(results=[(0, payload)])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    assert forge.head_branch(GL_MR) == "fix/repo-bug-sweep"
    assert "--output" in rec.calls[0] and "json" in rec.calls[0]


def test_head_branch_returns_none_on_garbage_never_guesses(monkeypatch):
    rec = _Recorder(results=[(0, "not json at all")])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    assert forge.head_branch(GL_MR) is None
    assert forge.head_branch("main...HEAD") is None


# --- issues + change requests via the origin remote ---------------------------


def test_create_issue_routes_by_origin_remote(monkeypatch, tmp_path):
    issue_url = "https://gitlab.com/acme/widgets/-/issues/9"
    rec = _Recorder(results=[
        (0, "git@gitlab.com:acme/widgets.git"),  # git remote get-url
        (0, issue_url),                           # glab issue create
    ])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    url, note = forge.create_issue(str(tmp_path), "title", "body")
    assert note is None and url == issue_url
    assert _cmd(rec.calls[1])[:3] == ["glab", "issue", "create"]


def test_create_issue_reports_unrecognized_remote_instead_of_guessing(monkeypatch, tmp_path):
    rec = _Recorder(results=[(0, "https://bitbucket.org/acme/widgets.git")])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    url, note = forge.create_issue(str(tmp_path), "t", "b")
    assert url is None and "not created" in note


def test_create_change_request_uses_glab_mr_create(monkeypatch, tmp_path):
    rec = _Recorder(results=[
        (0, "https://gitlab.acme-internal.com/data/mapop.git"),
        (0, "https://gitlab.acme-internal.com/data/mapop/-/merge_requests/27"),
    ])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    ok, output = forge.create_change_request(str(tmp_path), "fix/x", "t", "b")
    assert ok and output.endswith("/merge_requests/27")
    assert _cmd(rec.calls[1])[:3] == ["glab", "mr", "create"]
    assert "--source-branch" in rec.calls[1] and "fix/x" in rec.calls[1]


# --- diff acquisition ---------------------------------------------------------


def test_fetch_change_diff_dispatches_gh_vs_glab(monkeypatch):
    rec = _Recorder(results=[(0, "diff --git a b"), (0, "diff --git c d")])
    monkeypatch.setattr(forge.subprocess, "run", rec)
    forge.fetch_change_diff(GH_PR)
    assert _cmd(rec.calls[0])[:3] == ["gh", "pr", "diff"]
    forge.fetch_change_diff(GL_SELF_MR)
    assert _cmd(rec.calls[1])[:3] == ["glab", "mr", "diff"]


# --- availability gating ------------------------------------------------------


def test_missing_cli_reports_itself_visibly(monkeypatch):
    def _raise(argv, **kwargs):
        raise FileNotFoundError("glab")

    monkeypatch.setattr(forge.subprocess, "run", _raise)
    note = forge.post_comment(GL_MR, "body")
    assert note is not None and "not installed" in note


def test_a_cli_that_is_not_on_path_degrades_the_same_way(monkeypatch):
    """ADR-064's compatibility hinge, at the call site that depends on it.

    Absence used to surface as `FileNotFoundError` from `subprocess`; it now
    surfaces as `ExecutableNotFound` from the resolver, one frame earlier.
    `_run` catches the same exception either way, and the note it writes still
    names the bare `glab` — not a path, which no operator could act on.
    """
    monkeypatch.setattr(
        "ai_venture_studio.executables.shutil.which", lambda _: None
    )
    note = forge.post_comment(GL_MR, "body")
    assert note is not None
    assert "`glab` is not installed" in note
