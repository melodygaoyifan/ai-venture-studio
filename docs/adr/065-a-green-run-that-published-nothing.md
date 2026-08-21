# ADR-065 — A green run that published nothing

**Status**: accepted · **Date**: 2026-08-21 · **Release**: v0.111.0

## Context

Publishing here is Trusted Publishing over OIDC, which means **the tag push is
the publish**. That is the property that makes releases cheap, and it is also
what makes a moved tag dangerous: force-moving `v0.110.0` onto a corrected
commit starts a second `publish` run while the first one is still uploading.

The hazard was already known — it is written down in the operator's notes from
2026-08-20, when a pre-fix publish run had to be cancelled by hand so a
corrected commit's run could win. What was written down was advice: *cancel the
in-flight run for the old ref before moving a tag*. Advice is not a mechanism,
and this one has to be remembered at the end of a release, by someone who has
just found a defect in the thing they were about to ship.

Worse, the repository had quietly removed the symptom that would have made a
lost race visible. The publish step reads:

```yaml
# --check-url makes this idempotent: files already on PyPI are skipped
# rather than failing the run, so re-tagging or a manual publish
# followed by a tag push does not produce a red release.
run: uv publish --check-url https://pypi.org/simple/
```

The flag was added so re-tagging would not go red. **Re-tagging is exactly the
case where going red was correct.** "Already on PyPI" is decided by *filename*,
and a re-tagged commit builds a different wheel under the same filename. So:

1. `v0.110.0` is pushed; its run begins uploading.
2. A defect is found; the tag is force-moved to the fix.
3. The corrected run builds a different wheel, sees the filename already on
   PyPI, **skips its own upload, and reports success.**

PyPI now serves the pre-fix build, the tag points at the corrected commit, and
the release is green. A PyPI version can never be replaced, only yanked, so no
later run can repair it. The only exit is a new version number — and nothing in
the system was in a position to say so.

## Decision

Three changes, in decreasing order of how much they matter.

**1. The publish job verifies what PyPI ended up serving.** After `uv publish`,
`scripts/verify-published.py` fetches the version-scoped JSON view and compares
the sha256 of every file PyPI serves against the files this run built. Three
previously identical outcomes become distinguishable:

- every digest matches → the release is what the tag says it is;
- a file this run built is **absent** from PyPI → the upload never happened (a
  cancelled run, a skipped duplicate);
- a file is present with **different bytes** → PyPI holds another build under
  this version, and the message says the only thing that resolves it: *bump the
  version*.

This deliberately does not depend on how `uv publish` behaves when hashes
differ. The guarantee wanted is a property of **the index**, not of the
uploader, and a check that assumes the uploader is careful stops working when
the uploader changes.

**2. A `concurrency` group keyed on the tag.** `group: publish-${{ github.ref }}`
with `cancel-in-progress: true`, so a re-pushed tag cancels its own predecessor
instead of racing it.

The key is the entire correctness argument. A constant group would be a new and
worse defect: `v0.110.0` and `v0.111.0` pushed a minute apart are two
legitimate releases, and the second would kill the first mid-upload, leaving a
half-published version that can never be completed. Cancel across re-pushes of
one tag; never across tags.

**3. `scripts/retag.sh`, which mostly refuses.** The operator path, in the order
that is easy to get wrong from memory:

1. **Refuse outright if PyPI already serves this version.** Moving the tag then
   cannot change what anyone installs; it only makes the tag a lie. This is not
   a race guard — it is the honest answer most of the time, and it is much
   better heard before the push than after.
2. Cancel any in-flight publish run for the tag **and wait for it to stop**.
   `gh run cancel` returns when the request is accepted, not when the upload
   has stopped; pushing before then recreates the race.
3. Only then move the tag.

The refusal comes first on purpose. Cancelling a run and *then* discovering the
move was never allowed would have killed a legitimate release for nothing.

## Consequences

- A skipped upload, a partially-uploaded cancelled race, and a correct release
  are three distinct outcomes instead of one green check.
- The failure message names the remedy that exists rather than the one someone
  would reach for: re-running the release cannot fix a mismatched version.
- `--check-url` stays. Its idempotence is genuinely wanted for the harmless
  case (a manual publish followed by a tag push); what it must not do is decide
  the dangerous case silently, and now it does not get to.
- The digest comparison is a pure function over two dicts, so the rules are
  testable without uploading anything to PyPI. A check whose logic can only be
  exercised by publishing is a check nobody tests.

## Mechanism

`tests/test_a_green_run_that_published_nothing.py`, 7 tests. The ones that
carry weight are the two about *shape* rather than about the comparison:

- the concurrency group must contain `github.ref` — a test that fails if the
  group is ever made constant, which is the plausible future edit and the one
  that turns this fix into a bigger bug;
- `retag.sh` must refuse before it cancels and cancel before it pushes,
  asserted on the source in the same style as
  `test_built_flag_durable.py`'s ordering check, plus the wait without which
  the cancel is decorative.

## References

- ADR-051 — one control implemented on one of two call paths; the reason
  `publish.yml` and `ci.yml` are pinned to the same lint gate by a test
- ADR-056 — a reading that cannot name its instrument; same shape, aimed at
  the bench ledger instead of the release path
- `scripts/verify-release.sh` — published is not deployed, the step after this
  one
