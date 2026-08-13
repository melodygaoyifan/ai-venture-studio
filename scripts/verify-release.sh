#!/usr/bin/env bash
# Post-publish release verification: prove the version on PyPI actually
# installs and runs, then (optionally) upgrade the deployed binary.
#
# WHY THIS EXISTS. Three releases in a row — 0.82.0, 0.83.0, 0.84.0 — the
# first install "succeeded" and produced no `avs` binary, and each time it was
# written off as a PyPI propagation hiccup that `--force-reinstall` fixed.
# Neither half was true. The install had FAILED, and the command doing the
# verifying was `pip install --quiet ... 2>&1 | tail -3` — which renders a
# failed install byte-for-byte identical to a successful one:
#
#     (blank line)
#     [notice] A new release of pip is available: 24.0 -> 26.2.1
#     [notice] To update, run: python -m pip install --upgrade pip
#
# pip had printed the reason. `--quiet` dropped the success line, `tail -3`
# dropped the ERROR lines, and what was left was two notices pip emits either
# way. The retry a minute later is what actually fixed it; --force-reinstall
# got the credit. So the diagnosis was never even wrong — it was never made,
# because the evidence was discarded before anyone read it.
#
# That is the same shape as the probe bug fixed in v0.84.0: a check that
# reports on something it did not observe. Hence the two rules this script
# exists to enforce, and which tests/test_version_consistency.py asserts it
# still follows:
#
#   1. pip's output is NEVER silenced or truncated. On failure you see all
#      of it. Nothing here pipes pip through `tail`, `head`, or `-q`.
#   2. "Successfully installed" is not evidence. The console script must
#      exist and `avs --version` must print the version we asked for.
#
# Usage:
#   scripts/verify-release.sh                 # verify the version in pyproject
#   scripts/verify-release.sh 0.84.0          # verify a specific version
#   scripts/verify-release.sh --deploy        # ...and upgrade the deployed avs
#   scripts/verify-release.sh --quick         # skip init/replay (install only)
#
# Env:
#   PYTHON      interpreter used to build the throwaway venv (default python3)
#   WAIT_SECS   how long to wait for a just-published version to appear (600)
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=""
DEPLOY=false
QUICK=false
for arg in "$@"; do
  case "$arg" in
    --deploy) DEPLOY=true ;;
    --quick)  QUICK=true ;;
    -*)       echo "unknown flag: $arg" >&2; exit 2 ;;
    *)        VERSION="$arg" ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION=$(python3 -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
fi

PYTHON="${PYTHON:-python3}"
WAIT_SECS="${WAIT_SECS:-600}"
WORK=$(mktemp -d -t avs-verify-release)
trap 'rm -rf "$WORK"' EXIT

echo "verifying ai-venture-studio==$VERSION from PyPI"
echo "  venv:   $WORK/venv  (built with $PYTHON)"
echo

"$PYTHON" -m venv "$WORK/venv"
VPY="$WORK/venv/bin/python"

# --- 1. install, retrying ONLY while the index has not caught up ------------
#
# A freshly published version is genuinely not installable for a short while
# after the publish job goes green, which is the retryable case and the one
# that actually bit us. Everything else — a broken dependency pin, a bad
# wheel, no network — fails on the first attempt with the full log, because
# spinning for ten minutes on an error that will never clear is how a real
# defect gets mistaken for a propagation delay.
LOG="$WORK/pip.log"
deadline=$(( $(date +%s) + WAIT_SECS ))
attempt=0
while true; do
  attempt=$(( attempt + 1 ))
  if "$VPY" -m pip install --disable-pip-version-check "ai-venture-studio==$VERSION" >"$LOG" 2>&1; then
    echo "install ok (attempt $attempt)"
    break
  fi
  if ! grep -q "No matching distribution found\|Could not find a version that satisfies" "$LOG"; then
    echo "FAILED: pip could not install $VERSION, and not because the index is behind." >&2
    echo "--- full pip output ------------------------------------------------" >&2
    cat "$LOG" >&2
    exit 1
  fi
  if (( $(date +%s) >= deadline )); then
    echo "FAILED: $VERSION never appeared on the index within ${WAIT_SECS}s." >&2
    echo "--- full pip output from the last attempt --------------------------" >&2
    cat "$LOG" >&2
    exit 1
  fi
  echo "  attempt $attempt: index has not published $VERSION yet, retrying in 20s"
  sleep 20
done

# --- 2. the console script must exist --------------------------------------
#
# This is the check whose absence cost three releases. `pip` reporting
# success says a wheel was unpacked; it says nothing about whether the thing
# the founder types actually landed on disk.
for script in avs autoproduct; do
  if [[ ! -x "$WORK/venv/bin/$script" ]]; then
    echo "FAILED: pip reported success but $script was not installed." >&2
    ls -la "$WORK/venv/bin/" >&2
    exit 1
  fi
done
echo "console scripts present: avs, autoproduct"

# --- 3. it must run, and be the version we asked for ------------------------
got=$("$WORK/venv/bin/avs" --version 2>&1 | tr -d '[:space:]')
if [[ "$got" != "$VERSION" ]]; then
  echo "FAILED: installed avs reports '$got', expected '$VERSION'." >&2
  exit 1
fi
echo "avs --version: $got"

# --- 4. the paths a founder takes on day one --------------------------------
if [[ "$QUICK" == false ]]; then
  # Keys are unset deliberately: a wheel that only works because the verifying
  # shell happened to have credentials in it is not verified.
  echo
  echo "running init + replay with all provider keys unset"
  env -u ANTHROPIC_API_KEY -u ANTHROPIC_API_KEY_FILE -u OPENAI_API_KEY -u GOOGLE_API_KEY \
    "$WORK/venv/bin/avs" init "$WORK/ws" --profile web >"$WORK/init.log" 2>&1 || {
      echo "FAILED: avs init" >&2; cat "$WORK/init.log" >&2; exit 1; }
  echo "  avs init --profile web: ok"

  env -u ANTHROPIC_API_KEY -u ANTHROPIC_API_KEY_FILE -u OPENAI_API_KEY -u GOOGLE_API_KEY \
    "$WORK/venv/bin/avs" replay --demo >"$WORK/replay.log" 2>&1 || {
      echo "FAILED: avs replay --demo" >&2; cat "$WORK/replay.log" >&2; exit 1; }
  echo "  avs replay --demo: $(tail -1 "$WORK/replay.log")"
fi

echo
echo "PyPI verification passed for $VERSION"

# --- 5. published is not deployed -------------------------------------------
if [[ "$DEPLOY" == true ]]; then
  deployed=$(command -v avs || true)
  if [[ -z "$deployed" ]]; then
    echo "FAILED: --deploy given but no avs on PATH to upgrade." >&2
    exit 1
  fi
  # Upgrade whichever interpreter owns the deployed script, rather than a
  # hardcoded path: the machine that runs the LaunchAgents is not the only
  # machine this can run on.
  dpy=$(head -1 "$deployed" | sed 's/^#!//')
  echo
  echo "upgrading deployed avs: $deployed (via $dpy)"
  "$dpy" -m pip install --disable-pip-version-check --upgrade "ai-venture-studio==$VERSION"
  now=$("$deployed" --version 2>&1 | tr -d '[:space:]')
  if [[ "$now" != "$VERSION" ]]; then
    echo "FAILED: deployed avs still reports '$now' after upgrade." >&2
    exit 1
  fi
  echo "deployed avs is now $now"
fi
