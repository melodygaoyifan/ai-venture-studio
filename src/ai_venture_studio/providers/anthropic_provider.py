from __future__ import annotations

import os

from ai_venture_studio.providers.base import (
    Provider,
    ProviderError,
    record_stop_reason,
    register,
)


def _make_client():
    """Direct API by default; AVS_ANTHROPIC_MODE=bedrock|vertex|foundry
    routes the same Messages API through AWS Bedrock, GCP Vertex, or
    Microsoft Foundry — the doors enterprises actually have. Model IDs are
    platform-native and passed verbatim (Bedrock inference profiles, Vertex
    @-versioned IDs, Foundry deployment names) — auto-translation cannot
    express ARNs or custom deployment names, so none is attempted. Every
    mode errors loudly on missing credentials rather than running
    half-armed; nothing here is a silent fallback to a different provider."""
    import anthropic

    from ai_venture_studio.secrets import env_or_file

    mode = os.environ.get("AVS_ANTHROPIC_MODE", "direct").strip().lower() or "direct"
    if mode == "bedrock":
        try:
            return anthropic.AnthropicBedrock()
        except Exception as exc:
            raise ProviderError(
                "AVS_ANTHROPIC_MODE=bedrock but the Bedrock client could not "
                f"start ({exc}). Install `anthropic[bedrock]` and provide AWS "
                "credentials (env/instance profile) with bedrock:InvokeModel; "
                "profiles must name Bedrock model IDs (anthropic.claude-* / "
                "region-prefixed variants)."
            ) from exc
    if mode == "vertex":
        if not (
            os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
            and os.environ.get("CLOUD_ML_REGION")
        ):
            raise ProviderError(
                "AVS_ANTHROPIC_MODE=vertex requires ANTHROPIC_VERTEX_PROJECT_ID "
                "and CLOUD_ML_REGION"
            )
        try:
            return anthropic.AnthropicVertex()
        except Exception as exc:
            raise ProviderError(
                "AVS_ANTHROPIC_MODE=vertex but the Vertex client could not "
                f"start ({exc}). Install `anthropic[vertex]` and authenticate "
                "with Application Default Credentials."
            ) from exc
    if mode == "foundry":
        if not hasattr(anthropic, "AnthropicFoundry"):
            raise ProviderError(
                "AVS_ANTHROPIC_MODE=foundry needs an anthropic SDK with "
                "AnthropicFoundry — upgrade the `anthropic` package"
            )
        if not (
            env_or_file("ANTHROPIC_FOUNDRY_API_KEY")
            and (
                os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE")
                or os.environ.get("ANTHROPIC_FOUNDRY_BASE_URL")
            )
        ):
            raise ProviderError(
                "AVS_ANTHROPIC_MODE=foundry requires ANTHROPIC_FOUNDRY_API_KEY "
                "and ANTHROPIC_FOUNDRY_RESOURCE (or ANTHROPIC_FOUNDRY_BASE_URL); "
                "the model field is your Foundry deployment name"
            )
        try:
            return anthropic.AnthropicFoundry(
                api_key=env_or_file("ANTHROPIC_FOUNDRY_API_KEY")
            )
        except Exception as exc:
            raise ProviderError(
                f"AVS_ANTHROPIC_MODE=foundry but the Foundry client could "
                f"not start ({exc})"
            ) from exc
    if mode != "direct":
        raise ProviderError(
            f"unknown AVS_ANTHROPIC_MODE {mode!r}; "
            "expected direct|bedrock|vertex|foundry"
        )
    # ANTHROPIC_AUTH_TOKEN covers enterprise LLM gateways (bearer auth), the
    # SDK honors ANTHROPIC_BASE_URL natively so a proxy needs no code, and
    # *_FILE variants cover K8s/Docker secret mounts.
    api_key = env_or_file("ANTHROPIC_API_KEY")
    auth_token = env_or_file("ANTHROPIC_AUTH_TOKEN")
    if not (api_key or auth_token):
        raise ProviderError(
            "ANTHROPIC_API_KEY is not set (a gateway bearer token via "
            "ANTHROPIC_AUTH_TOKEN also works, and either accepts a mounted "
            "secret via ANTHROPIC_API_KEY_FILE / ANTHROPIC_AUTH_TOKEN_FILE; "
            "set AVS_ANTHROPIC_MODE=bedrock|vertex|foundry to route through "
            "AWS, GCP, or Azure instead)"
        )
    return anthropic.Anthropic(api_key=api_key, auth_token=auth_token)


#: Above this many output tokens the request is streamed. The SDK computes
#: an expected duration from max_tokens and refuses a non-streaming call that
#: could exceed 10 minutes, so the ceiling is a property of the SDK, not of
#: our patience. Kept comfortably under the smallest per-model non-streaming
#: limit rather than tuned to one model's number, because that number is not
#: ours to depend on.
_STREAM_ABOVE = 8192

#: Transient-failure budget. Four attempts with 2/4/8s backoff spent the
#: whole allowance in fourteen seconds, which is nothing next to a real
#: overload event — a 529 on one voter killed a build that had already run
#: for eleven minutes. Six attempts capped at 60s is worst-case just over two
#: minutes of waiting, which is obviously the right trade against losing an
#: hour-long run.
_TRANSIENT_ATTEMPTS = 6
_BACKOFF_CAP_S = 60.0


def _backoff_seconds(attempt: int, exc: Exception | None = None) -> float:
    """How long to wait before retry `attempt` (0-based).

    Honours a `retry-after` header when the server sends one — it knows more
    than our exponent does. Jitter matters because the review voters run in a
    thread pool: without it, six voters that all got 529 retry in lockstep
    and hammer the same instant.
    """
    import random

    response = getattr(exc, "response", None)
    header = getattr(response, "headers", None)
    if header is not None:
        try:
            after = float(header.get("retry-after") or 0)
            if after > 0:
                return min(after, _BACKOFF_CAP_S)
        except (TypeError, ValueError):
            pass
    return min(2.0 ** (attempt + 1), _BACKOFF_CAP_S) + random.random()


@register
class AnthropicProvider(Provider):
    name = "anthropic"

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        import time

        import anthropic

        client = _make_client()
        # Transient-error resilience at the ADAPTER layer: overload/rate
        # limits retry with backoff here, so every direct .complete() call
        # site (writers, critics, implementer) inherits it — a 529 killed
        # an entire 2-hour bench run before this existed.
        response = None
        for attempt in range(_TRANSIENT_ATTEMPTS):
            try:
                if max_tokens > _STREAM_ABOVE:
                    # The SDK REFUSES a non-streaming request whose max_tokens
                    # implies it could run past 10 minutes — it raises before
                    # sending anything. The implementer asks for 32000 (it
                    # writes whole files, and 16384 truncated real builds), so
                    # every single build call died on
                    #   ValueError: Streaming is required for operations that
                    #   may take longer than 10 minutes
                    # and no task could be built at all. Streaming the big
                    # calls is the fix the SDK is asking for; the final
                    # message has the same shape, so everything below is
                    # unchanged.
                    with client.messages.stream(
                        model=model,
                        max_tokens=max_tokens,
                        system=system,
                        messages=messages,
                    ) as stream:
                        response = stream.get_final_message()
                else:
                    response = client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=system,
                        messages=messages,
                    )
                break
            except (
                anthropic.APIStatusError,
                anthropic.APIConnectionError,
            ) as exc:
                status = getattr(exc, "status_code", None)
                transient = status in (429, 500, 502, 503, 529) or isinstance(
                    exc, anthropic.APIConnectionError
                )
                if not transient or attempt == _TRANSIENT_ATTEMPTS - 1:
                    raise
                time.sleep(_backoff_seconds(attempt, exc))
        # Record why the model stopped on EVERY response, not only the empty
        # ones. `stop_reason == "max_tokens"` means the text below is a partial
        # answer, and a partial answer that parses is worse than one that
        # doesn't — see providers/base.py.
        record_stop_reason(getattr(response, "stop_reason", None))

        # Meter here, where usage exists. The chat() contract still returns
        # str — threading a usage object through the writers, critics,
        # implementer and verifier would touch every call site for no gain,
        # and this adapter already owns retries and empty-response
        # diagnostics. Recording never raises; the ledger is written later by
        # whoever knows the workspace (spend.flush).
        usage = getattr(response, "usage", None)
        if usage is not None:
            from ai_venture_studio import spend

            spend.record(
                model,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                stop_reason=getattr(response, "stop_reason", None),
            )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        if not text.strip():
            # Diagnostics for the empty-response mystery (context voter,
            # PR #9): keep the API's own explanation for the failure note.
            global LAST_EMPTY_META
            LAST_EMPTY_META = {
                "model": model,
                "stop_reason": getattr(response, "stop_reason", None),
                "output_tokens": getattr(
                    getattr(response, "usage", None), "output_tokens", None
                ),
                "content_blocks": [getattr(b, "type", "?") for b in response.content],
            }
        return text


LAST_EMPTY_META: dict | None = None
