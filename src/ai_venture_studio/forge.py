"""Forge dispatch — GitHub (`gh`) and GitLab (`glab`) behind one seam.

Enterprises overwhelmingly host code on GitLab (SaaS or self-managed) or
GitHub Enterprise, not github.com alone. Every side effect that used to
assume `gh` — comment posting, HITL issues, merge, head-branch lookup,
diff acquisition, fix-MR creation — routes through here, dispatched on
the shape of the target URL or the repo's origin remote.

Failure-tolerant like github.py: an unreachable forge degrades the run
to local artifacts; it never fails the review. Both CLIs are invoked as
list argv with explicit timeouts, and a missing CLI reports itself
visibly instead of pretending the forge does not exist.
"""

from __future__ import annotations

import json
import re
import subprocess

from ai_venture_studio.executables import resolve

# github.com and GitHub Enterprise Server share the /pull/<n> shape;
# GitLab (SaaS and self-managed) is unambiguous via /-/merge_requests/<n>.
GITHUB_PR_URL = re.compile(r"^https://[^/\s]+/[^/\s]+/[^/\s]+/pull/(\d+)$")
GITLAB_MR_URL = re.compile(r"^https://[^/\s]+/\S+/-/merge_requests/(\d+)$")

_MERGE_METHODS = ("squash", "merge", "rebase")


def detect(target: str) -> str | None:
    """'github' | 'gitlab' | None from the change-request URL shape."""
    if GITHUB_PR_URL.match(target):
        return "github"
    if GITLAB_MR_URL.match(target):
        return "gitlab"
    return None


# Forges we can NAME but not yet drive. Recognizing them exists so the
# failure is "Azure DevOps is not supported yet", never a `git diff` on a
# URL. Azure DevOps: dev.azure.com/{org}/{proj}/_git/{repo}/pullrequest/{n}
# (legacy {org}.visualstudio.com hosts too). Bitbucket (Cloud and Data
# Center) both use /pull-requests/{n}.
_AZURE_DEVOPS_PR = re.compile(
    r"^https://(dev\.azure\.com/|[^/\s]+\.visualstudio\.com/)\S*/pullrequest/\d+"
)
_BITBUCKET_PR = re.compile(r"^https://[^/\s]+/\S+/pull-requests/\d+")


def recognize_unsupported(target: str) -> str | None:
    """Name a forge we recognize but cannot drive, or None."""
    if detect(target) is not None:
        return None
    if _AZURE_DEVOPS_PR.match(target):
        return "Azure DevOps"
    if _BITBUCKET_PR.match(target):
        return "Bitbucket"
    return None


def is_change_request(target: str) -> bool:
    return detect(target) is not None


def _run(argv: list[str], cwd: str | None = None) -> tuple[bool, str]:
    # Resolved HERE rather than at the six call sites, so `argv[0]` stays the
    # bare name the message below prints — "`gh` is not installed" is what an
    # operator can act on, and "`/opt/homebrew/bin/gh` is not installed" is
    # nonsense. `ExecutableNotFound` is a `FileNotFoundError` precisely so
    # this handler, written years before it, keeps catching it (ADR-064).
    try:
        proc = subprocess.run(
            [resolve(argv[0]), *argv[1:]],
            capture_output=True, text=True, timeout=60, cwd=cwd,
        )
    except FileNotFoundError:
        return False, (
            f"`{argv[0]}` is not installed — install it and authenticate "
            "against your host (self-managed hosts included) to let avs "
            "talk to this forge"
        )
    except subprocess.TimeoutExpired as exc:
        return False, str(exc)
    output = (proc.stdout or proc.stderr).strip()
    return proc.returncode == 0, output


def _gitlab_parts(target: str) -> tuple[str, str]:
    """(repo URL, MR iid) from an MR URL — `glab -R` accepts the full
    repo URL, which keeps self-managed hosts working without GITLAB_HOST."""
    repo_url, _, iid = target.partition("/-/merge_requests/")
    return repo_url, iid.split("/")[0].split("?")[0]


def post_comment(target: str, body: str) -> str | None:
    """Comment on a PR/MR; returns an error note or None."""
    forge = detect(target)
    if forge == "github":
        ok, output = _run(["gh", "pr", "comment", target, "--body", body])
        return None if ok else f"gh pr comment failed: {output[:200]}"
    if forge == "gitlab":
        repo_url, iid = _gitlab_parts(target)
        ok, output = _run(
            ["glab", "mr", "note", iid, "--repo", repo_url, "--message", body]
        )
        return None if ok else f"glab mr note failed: {output[:200]}"
    return "target is not a PR/MR URL; comment not posted"


def merge(target: str, *, method: str = "squash") -> tuple[bool, str]:
    """Merge a PR/MR. Reachable ONLY through `automation.evaluate_merge`
    returning allowed=True (ADR-031) — never called from a review path.

    Deliberately no `gh --admin` and no GitLab equivalent: if branch
    protection blocks the merge, that is a human's configured intent and
    this must fail, not override it.
    """
    if method not in _MERGE_METHODS:
        return False, f"unknown merge method {method!r}"
    forge = detect(target)
    if forge == "github":
        return _run(["gh", "pr", "merge", target, f"--{method}"])
    if forge == "gitlab":
        repo_url, iid = _gitlab_parts(target)
        argv = ["glab", "mr", "merge", iid, "--repo", repo_url]
        if method == "squash":
            argv.append("--squash")
        elif method == "rebase":
            argv.append("--rebase")
        return _run(argv)
    return False, "target is not a PR/MR URL; refusing to merge"


def head_branch(target: str) -> str | None:
    """The source branch of a PR/MR, or None when it cannot be determined —
    callers must refuse rather than assume (ADR-031 §mechanism)."""
    forge = detect(target)
    if forge == "github":
        ok, output = _run(
            ["gh", "pr", "view", target, "--json", "headRefName",
             "-q", ".headRefName"]
        )
        return output.strip() if ok and output.strip() else None
    if forge == "gitlab":
        repo_url, iid = _gitlab_parts(target)
        ok, output = _run(
            ["glab", "mr", "view", iid, "--repo", repo_url, "--output", "json"]
        )
        if not ok:
            return None
        try:
            branch = json.loads(output).get("source_branch", "")
        except (json.JSONDecodeError, AttributeError):
            return None
        return branch or None
    return None


def _remote_forge(repo_dir: str) -> str | None:
    ok, remote = _run(
        ["git", "remote", "get-url", "origin"], cwd=repo_dir
    )
    if not ok:
        return None
    if "github" in remote:
        return "github"
    if "gitlab" in remote:
        return "gitlab"
    return None


def create_issue(repo_dir: str, title: str, body: str) -> tuple[str | None, str | None]:
    """Open a HITL issue on the reviewed repo's origin. Returns
    (issue_url, error_note). Forge chosen from the origin remote; an
    unrecognized remote is reported, never guessed."""
    forge = _remote_forge(repo_dir)
    if forge == "github":
        ok, output = _run(
            ["gh", "issue", "create", "--title", title, "--body", body],
            cwd=repo_dir,
        )
        if not ok:
            return None, f"gh issue create failed: {output[:200]}"
        return output.splitlines()[-1].strip(), None
    if forge == "gitlab":
        ok, output = _run(
            ["glab", "issue", "create", "--title", title,
             "--description", body, "--yes"],
            cwd=repo_dir,
        )
        if not ok:
            return None, f"glab issue create failed: {output[:200]}"
        return output.splitlines()[-1].strip(), None
    return None, "no GitHub/GitLab origin remote detected; issue not created"


def auth_status(forge: str) -> tuple[str, str]:
    """('ready'|'unauthenticated'|'missing', note) for a forge's CLI —
    the preflight question 'can this workspace actually talk to its
    forge?', answered without touching the network beyond the CLI's own
    auth check."""
    cli = {"github": "gh", "gitlab": "glab"}.get(forge)
    if cli is None:
        return "missing", f"unknown forge {forge!r}"
    ok, output = _run([cli, "auth", "status"])
    if ok:
        return "ready", f"{cli} authenticated"
    if "not installed" in output:
        return "missing", f"{cli} is not installed"
    return "unauthenticated", f"{cli} auth status failed: {output[:120]}"


def create_change_request(
    repo_dir: str, branch: str, title: str, body: str
) -> tuple[bool, str]:
    """Open a PR/MR from an already-pushed branch against the default
    branch. Returns (ok, output); the last output line is the URL on
    success."""
    forge = _remote_forge(repo_dir)
    if forge == "github":
        return _run(
            ["gh", "pr", "create", "--head", branch,
             "--title", title, "--body", body],
            cwd=repo_dir,
        )
    if forge == "gitlab":
        return _run(
            ["glab", "mr", "create", "--source-branch", branch,
             "--title", title, "--description", body, "--yes"],
            cwd=repo_dir,
        )
    return False, "no GitHub/GitLab origin remote detected"


def fetch_change_diff(target: str) -> str:
    """The unified diff of a PR/MR. Raises CalledProcessError like a
    failed `git diff` would — diff acquisition failing must stop the
    review, not degrade it."""
    forge = detect(target)
    if forge == "gitlab":
        repo_url, iid = _gitlab_parts(target)
        return subprocess.run(
            [resolve("glab"), "mr", "diff", iid, "--repo", repo_url,
             "--color", "never"],
            capture_output=True, text=True, check=True, timeout=120,
        ).stdout
    return subprocess.run(
        [resolve("gh"), "pr", "diff", target],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout
