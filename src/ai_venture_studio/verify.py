"""Fresh-agent verification pass (§09.4.6) — the /ultrareview pattern.

Every candidate finding is handed to a fresh agent that sees ONLY the
finding and the diff — no voter reasoning, no other findings — and is
prompted to refute it. One hop, one direction, never relayed back to the
originating voter (§08.1.5 channel table).
"""

from __future__ import annotations

import logging
from ai_venture_studio.harness.spec_validator import LoadedSkill, VoterSpec
from ai_venture_studio.providers import ProviderError, get_provider
from ai_venture_studio.state import VoterFinding
from ai_venture_studio.yamlx import extract_mapping

#: Where a deliberate degradation says what it degraded. Every handler
#: below that skips a row, a page, or a piece of bookkeeping logs here
#: first: CLAUDE.md forbids swallowing an exception silently, and until
#: ADR-062 nothing enforced it (`S110`/`S112` found 15). DEBUG, so it is
#: silent unless asked for — `AVS_DEBUG=1` is the ask.
_log = logging.getLogger(__name__)

VERIFIER_MARKER = "independent verification agent"

_SYSTEM = f"""You are an {VERIFIER_MARKER} in a code review system. A reviewer
you cannot talk to claims the finding below. Your job is to try to REFUTE it
against the diff — the finding is guilty until proven reproducible.

Verdicts:
- VERIFIED: the quoted evidence appears in the diff and the claimed defect
  follows from it.
- NOT_REPRODUCIBLE: the evidence is not in the diff, is misquoted, or the
  claimed defect does not follow. When uncertain, choose this.
- NEEDS_RUNTIME: the evidence is present and plausible, but confirming the
  defect requires executing code or seeing files outside the diff.

Respond with ONLY a YAML document:
verdict: VERIFIED | NOT_REPRODUCIBLE | NEEDS_RUNTIME
reason: one sentence
"""

_USER = """<finding>
title: {title}
file_path: {file_path}
lines: {line_start}-{line_end}
evidence: {evidence}
claim: {explanation}
</finding>

<untrusted_diff>
{diff}
</untrusted_diff>
"""

_VALID = {"VERIFIED", "NOT_REPRODUCIBLE", "NEEDS_RUNTIME"}


def verify_finding(
    finding: VoterFinding,
    diff_text: str,
    *,
    provider: str,
    model: str,
    fallback: tuple[str, str] | None = None,
    max_retries: int = 2,
) -> str:
    user = _USER.format(
        title=finding.title,
        file_path=finding.file_path,
        line_start=finding.line_start,
        line_end=finding.line_end,
        evidence=finding.evidence,
        explanation=finding.explanation,
        diff=diff_text,
    )
    provider_name, model_name = provider, model
    for _ in range(max_retries + 1):
        try:
            raw = get_provider(provider_name).complete(
                model=model_name, system=_SYSTEM, user=user, max_tokens=512
            )
            data = extract_mapping(raw, ("verdict",))
            verdict = str(data.get("verdict", "")).strip()
            if verdict in _VALID:
                return verdict
        except ProviderError:
            if fallback and (provider_name, model_name) != fallback:
                provider_name, model_name = fallback
                continue
            break
        except Exception as exc:  # noqa: BLE001 — transient; retry
            _log.debug("verifier attempt failed, retrying: %s", exc)
            continue
    # A verifier that cannot run must not silently bless the finding.
    return "NEEDS_RUNTIME"


def verifier_config_for(skill: LoadedSkill) -> tuple[str, str, tuple[str, str] | None]:
    """Verification runs on the same provider stack as the voter whose
    finding it checks (a different invocation — charter rule 5), inheriting
    the skill's fallback."""
    spec: VoterSpec = skill.spec
    fallback = (spec.fallback.provider, spec.fallback.model) if spec.fallback else None
    return spec.provider, spec.model, fallback
