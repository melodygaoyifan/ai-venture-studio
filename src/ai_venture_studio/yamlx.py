"""Tolerant YAML extraction from LLM responses.

Models narrate despite instructions — especially on large diffs. The
envelope contract is enforced on the *extracted* mapping, not on the raw
response: we try, in order, any fenced block, the raw text, and the text
from the first expected top-level key onward.
"""

from __future__ import annotations

import re

import yaml

_FENCE = re.compile(r"```(?:yaml|yml)?\s*\n(.*?)```", re.DOTALL)


def extract_mapping(raw: str, expected_keys: tuple[str, ...]) -> dict:
    text = raw.strip()
    candidates: list[str] = []
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1))
    candidates.append(text.strip("`"))
    key_match = re.search(
        rf"^({'|'.join(map(re.escape, expected_keys))}):", text, re.MULTILINE
    )
    if key_match:
        candidates.append(text[key_match.start() :])

    # What the parser objected to, per candidate. The last candidate is the
    # key-anchored one when it exists — the model's actual envelope — so
    # `problems[-1]` is the most on-topic diagnosis. Without this, the
    # ValueError names no problem at all, and a revision prompt built from
    # it asks the model to "fix that exact problem" while showing none
    # (ADR-041's shape; run 19b case 04 spent all three planner attempts
    # exactly there).
    problems: list[str] = []
    for candidate in candidates:
        try:
            data = yaml.safe_load(candidate)
        except yaml.YAMLError as exc:
            problems.append(" ".join(str(exc).split()))
            continue
        if isinstance(data, dict) and any(k in data for k in expected_keys):
            return data
        if isinstance(data, dict):
            problems.append(
                f"a mapping parsed but its keys are {sorted(data)[:8]}"
            )
        else:
            problems.append(f"parsed to {type(data).__name__}, not a mapping")
    error = ValueError(
        f"no YAML mapping with any of {expected_keys} found in response "
        f"({len(raw)} chars)"
        + (f" — closest attempt: {problems[-1]}" if problems else "")
    )
    error.raw_snippet = raw  # surfaced in failure notes for debugging
    raise error
