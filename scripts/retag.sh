#!/usr/bin/env bash
# Move a release tag onto a corrected commit, without racing its own publish.
#
#   scripts/retag.sh v0.111.0 [commit]
#
# A tag push IS the publish here (Trusted Publishing OIDC), so force-moving a
# tag starts a second publish run while the first one is still uploading. Both
# are trying to write the same filenames to an index where a version can never
# be replaced, only yanked. The loser skips its upload and goes green, and PyPI
# keeps whichever build won — possibly not the one the tag points at.
#
# The order below is the whole point, and it is the order that is easy to get
# wrong from memory at the end of a release:
#
#   1. Refuse outright if PyPI already serves this version. Moving the tag then
#      cannot change what users install; it only makes the tag a lie. The fix
#      is a new version number, and it is better to hear that before the push.
#   2. Cancel any in-flight publish run for this tag and WAIT for it to stop.
#   3. Only then move the tag.
#
# Step 1 is not a race guard, it is the honest refusal — most of the time this
# script's job is to tell you not to do this. Steps 2 and 3 are for the window
# where the upload has not landed yet and the corrected build can still win.
set -euo pipefail

PKG="ai-venture-studio"
tag="${1:?usage: scripts/retag.sh <tag> [commit]}"
target="${2:-HEAD}"
version="${tag#v}"

command -v gh >/dev/null || { echo "gh is required (cancelling runs)" >&2; exit 2; }

echo "==> is $PKG $version already on PyPI?"
code=$(curl -s -o /dev/null -w '%{http_code}' \
  "https://pypi.org/pypi/${PKG}/${version}/json")
if [ "$code" = "200" ]; then
  cat >&2 <<EOF

REFUSING: PyPI already serves ${PKG} ${version}.

A published version is immutable. Moving ${tag} now would leave the tag
pointing at a commit that is NOT what anyone installing ${version} receives,
and no later run can repair it — publish would skip the upload and go green.

What to do instead: bump the version in pyproject.toml, land it, and tag the
new number. If ${version} is actively harmful, yank it on PyPI as well; yanking
hides it from resolvers without deleting it, which is the only lever there is.
EOF
  exit 1
fi
echo "    no (HTTP $code) — the version is still unclaimed"

echo "==> in-flight publish runs for $tag?"
# `--branch` matches the tag ref for tag-triggered runs.
runs=$(gh run list --workflow publish.yml --branch "$tag" \
  --json databaseId,status \
  --jq '.[] | select(.status != "completed") | .databaseId')
if [ -n "$runs" ]; then
  for id in $runs; do
    echo "    cancelling run $id"
    gh run cancel "$id" || true
  done
  # Waiting is the half that is actually load-bearing. `gh run cancel` returns
  # as soon as the request is accepted, and a job that is mid-upload keeps
  # uploading for a while after that — pushing the tag before it stops
  # recreates the exact race this script exists to prevent.
  for id in $runs; do
    echo "    waiting for $id to stop"
    for _ in $(seq 1 60); do
      state=$(gh run view "$id" --json status --jq .status)
      [ "$state" = "completed" ] && break
      sleep 5
    done
    echo "    run $id: $(gh run view "$id" --json status,conclusion \
      --jq '.status + "/" + (.conclusion // "-")')"
  done
else
  echo "    none"
fi

echo "==> moving $tag to $(git rev-parse --short "$target")"
git tag -f "$tag" "$target"
git push --force origin "$tag"

cat <<EOF

Pushed. The new publish run is the only one alive for $tag.

It ends with the "PyPI must serve what this run built" step, which compares the
sha256 of every uploaded artifact against the ones the run built. If that step
is red, PyPI is holding a different build under this version and the version
must be bumped — the release is NOT recoverable by re-running.

Then, as always: scripts/verify-release.sh $version --deploy, unpiped.
EOF
