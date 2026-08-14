"""OpenAI-compatible chat-completions adapters (OpenAI, xAI).

Thin httpx clients rather than full SDKs — the voter only needs
single-turn text completion, and one adapter shape covers both providers.
"""

from __future__ import annotations

import os

import httpx

from ai_venture_studio.providers.base import (
    Provider,
    ProviderError,
    record_stop_reason,
    register,
)


class _ChatCompletionsProvider(Provider):
    base_url: str
    base_url_env: str
    api_key_env: str

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        from ai_venture_studio.secrets import env_or_file

        # base_url_env lets an enterprise LLM gateway or an on-prem
        # OpenAI-compatible server (vLLM, NIM) front this seat; the key may
        # arrive as a mounted-file secret (NAME_FILE).
        base_url = (os.environ.get(self.base_url_env) or self.base_url).rstrip("/")
        api_key = env_or_file(self.api_key_env)
        if not api_key:
            raise ProviderError(f"{self.api_key_env} is not set")
        # gpt-5 / o-series reject the legacy max_tokens param; older models
        # and most compatible endpoints still require it. Send the modern
        # name for models that demand it, retry once on the 400 that says
        # the other name is unsupported.
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        modern = model.startswith(("gpt-5", "o1", "o3", "o4"))
        params = (
            ("max_completion_tokens", "max_tokens") if modern
            else ("max_tokens", "max_completion_tokens")
        )
        response = None
        for attempt in params:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={**payload, attempt: max_tokens},
                timeout=120,
            )
            if response.status_code == 400 and "max_tokens" in response.text:
                continue
            response.raise_for_status()
            body = response.json()
            # Metered before `choices` is indexed: a malformed body still
            # cost tokens, and the ledger must not lose them to a KeyError.
            _record_usage(
                self.name, model, body.get("usage") or {},
                stop_reason=(body.get("choices") or [{}])[0].get("finish_reason"),
            )
            choice = body["choices"][0]
            # OpenAI-compatible bodies say finish_reason: "length" for the
            # ran-out-of-budget case.
            record_stop_reason(choice.get("finish_reason"))
            # `content` is null, not "", when a reasoning model spends its
            # whole budget thinking — observed on gpt-5 at a small cap, where
            # the answer came back empty with finish_reason=length. Every
            # caller does `raw.strip()`, so a None here raised AttributeError
            # inside the voter's generic retry and surfaced as
            # BLOCKED_TOOL_FAILURE with a message about attributes rather
            # than about budget. The empty string is the honest answer: the
            # model said nothing, and the recorded stop_reason says why.
            return choice["message"].get("content") or ""
        response.raise_for_status()
        raise ProviderError(f"{self.name}: both token params rejected for {model}")


def _record_usage(
    provider: str, model: str, usage: dict, *, stop_reason: str | None = None,
) -> None:
    """Meter at the adapter, same as the anthropic path. OpenAI-compatible
    bodies report prompt_tokens/completion_tokens."""
    if not usage:
        return
    from ai_venture_studio import spend

    spend.record(
        model,
        usage.get("prompt_tokens") or usage.get("input_tokens"),
        usage.get("completion_tokens") or usage.get("output_tokens"),
        stop_reason=stop_reason,
    )


@register
class OpenAIProvider(_ChatCompletionsProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    base_url_env = "OPENAI_BASE_URL"  # the OpenAI SDK's own convention
    api_key_env = "OPENAI_API_KEY"


@register
class XAIProvider(_ChatCompletionsProvider):
    name = "xai"
    base_url = "https://api.x.ai/v1"
    base_url_env = "XAI_BASE_URL"
    api_key_env = "XAI_API_KEY"
