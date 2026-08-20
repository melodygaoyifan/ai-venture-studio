"""Provider adapters — Layer 1 of the stack (§08.3.1).

Voters name a model family in their spec frontmatter; the registry maps the
family to an adapter. Heterogeneity is the default posture (Principle 4);
running everything on one family is allowed only during bootstrap and is
surfaced in the YAML mirror so the substitution is visible, not silent.
"""

from __future__ import annotations

import abc
import threading


class ProviderError(RuntimeError):
    pass


# Why a thread-local instead of a return value: the `chat()` contract returns
# `str`, and widening it to carry metadata would touch every writer, critic,
# implementer and verifier call site. The reason a caller needs it is narrow
# and after-the-fact ("was what I just got the WHOLE answer?"), so the adapter
# records it and whoever cares asks. Thread-local because voters and parallel
# lane builds run concurrently — a module global would report one thread's
# truncation to another.
_local = threading.local()

# A response that hit the output cap is a PARTIAL answer wearing the shape of a
# complete one. That is the dangerous case: truncated YAML usually still parses
# (a block scalar simply ends), so a half-written source file reaches disk and
# the failure surfaces three iterations later as an unrelated test error.
TRUNCATION_REASONS = frozenset({"max_tokens", "length", "MAX_TOKENS"})


def record_stop_reason(stop_reason: str | None) -> None:
    """Called by every adapter on every successful response. Recording
    unconditionally is the point: the old code kept `stop_reason` only when the
    text came back empty, which is the one case where nothing is at stake."""
    _local.stop_reason = stop_reason


def last_stop_reason() -> str | None:
    return getattr(_local, "stop_reason", None)


def last_response_truncated() -> bool:
    """True when the most recent response on THIS thread stopped because it ran
    out of output budget rather than because the model was finished."""
    return last_stop_reason() in TRUNCATION_REASONS


class Provider(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        """Multi-turn completion. messages are [{'role': 'user'|'assistant',
        'content': str}, ...]; returns the raw text response."""

    def complete(self, *, model: str, system: str, user: str, max_tokens: int = 4096) -> str:
        return self.chat(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
        )


_REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    _REGISTRY[cls.name] = cls
    return cls


# Which members of the registry are not measuring instruments. `mock` answers
# from a regex table: run the product bench against it and every rate in the
# result file describes the fixture set, not the system. The registry is the
# only place that knows this, so it is the only place that says it — a second
# copy of the list is a second definition of "real run", and the thing the two
# would drift about is which files the capability kill criterion counts
# (ADR-038, ADR-051).
SIMULATED_PROVIDERS = frozenset({"mock"})


def is_simulated(name: str | None) -> bool:
    """True when `name` names a provider that fabricates its answers.

    `None` is NOT simulated: it means "the default", which is anthropic. That
    asymmetry is deliberate — a reader that cannot tell what produced a number
    must assume it was real, because the alternative is quietly dropping
    genuine capability readings out of the series.
    """
    return name in SIMULATED_PROVIDERS


def get_provider(name: str) -> Provider:
    # Imports deferred so an SDK missing for an unused provider never breaks startup.
    from ai_venture_studio.providers import (  # noqa: F401
        anthropic_provider,
        google_provider,
        mock,
        openai_compat,
    )

    if name not in _REGISTRY:
        raise ProviderError(
            f"unknown provider {name!r}; available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]()
