#!/usr/bin/env python3
"""Did PyPI end up holding the artifacts THIS run built?

`uv publish --check-url` skips files already present on the index. That flag
was added so that re-tagging would not produce a red release — and re-tagging
is the exact case where skipping is the wrong answer. The sequence it makes
silent:

    1. `v0.110.0` is pushed. Its publish run starts uploading.
    2. A defect is found. The tag is force-moved to the corrected commit.
    3. The corrected run builds a different wheel with the same filename,
       sees that filename already on PyPI, skips the upload, and goes GREEN.

PyPI now serves the pre-fix build, the tag points at the corrected commit, and
the release is green. Nothing in the run says so. A version cannot be replaced
on PyPI, only yanked, so this is not a state a later run can repair — the only
way out is a new version number, and the only way to know you need one is to
be told here.

So this asks PyPI what it is serving and compares it, byte for byte, against
what this run built. It deliberately does NOT depend on how `uv publish`
behaves when the hashes differ: the guarantee wanted is a property of the
index, not of the uploader, and a check that assumes the uploader is careful
is a check that stops working when the uploader changes.

Usage:  verify-published.py <name> <version> [dist-dir]
Exit 0 when every locally built file is served by PyPI with an identical
sha256; exit 1, loudly and by filename, otherwise.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

#: PyPI's JSON view of a freshly uploaded version can lag the upload by a few
#: seconds. Retried rather than slept-once, so the common case costs nothing
#: and the slow case still answers.
_ATTEMPTS = 6
_BACKOFF_S = 5


def digests(paths: list[pathlib.Path]) -> dict[str, str]:
    """sha256 by filename, for the artifacts on this runner."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def mismatches(local: dict[str, str], remote: dict[str, str]) -> list[str]:
    """Every way PyPI can disagree with this run, as sentences.

    Pure, and separated from the fetch on purpose: this is the part with the
    rules in it, and a check whose logic can only be exercised by uploading
    something to PyPI is a check nobody tests.
    """
    problems = []
    for name, want in sorted(local.items()):
        got = remote.get(name)
        if got is None:
            problems.append(
                f"{name}: built by this run and NOT served by PyPI — the "
                f"upload did not happen (a cancelled or skipped publish)"
            )
        elif got != want:
            problems.append(
                f"{name}: PyPI serves sha256 {got[:12]}…, this run built "
                f"{want[:12]}… — the index holds a DIFFERENT build under this "
                f"version. It cannot be replaced, only yanked: bump the "
                f"version and release again"
            )
    return problems


def _fetch(name: str, version: str) -> dict[str, str]:
    """sha256 by filename, as PyPI reports them for this exact version.

    The version-scoped URL, never `/pypi/<name>/json` — that one caches its
    `info.version` and has answered with a stale release often enough in this
    project to be worth naming here.
    """
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    last = ""
    for attempt in range(_ATTEMPTS):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
                data = json.load(response)
            return {f["filename"]: f["digests"]["sha256"] for f in data["urls"]}
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < _ATTEMPTS - 1:
                time.sleep(_BACKOFF_S)
    raise SystemExit(
        f"could not read {url} after {_ATTEMPTS} attempts ({last}). This check "
        f"failing is not the same as the release being fine — treat it as "
        f"unverified and look at PyPI by hand."
    )


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    name, version = argv[1], argv[2]
    dist = pathlib.Path(argv[3] if len(argv) > 3 else "dist")
    built = sorted(p for p in dist.iterdir() if p.suffix in {".whl", ".gz"})
    if not built:
        print(f"::error::no artifacts in {dist}/ to verify")
        return 1
    problems = mismatches(digests(built), _fetch(name, version))
    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1
    print(
        f"PyPI serves exactly the {len(built)} artifact(s) this run built for "
        f"{name} {version}: " + ", ".join(p.name for p in built)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
