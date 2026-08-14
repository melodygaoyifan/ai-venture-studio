from __future__ import annotations

import os

import httpx

from ai_venture_studio.providers.base import (
    Provider,
    ProviderError,
    record_stop_reason,
    register,
)


@register
class GoogleProvider(Provider):
    name = "google"

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        from ai_venture_studio.secrets import env_or_file

        api_key = env_or_file("GEMINI_API_KEY") or env_or_file("GOOGLE_API_KEY")
        if not api_key:
            raise ProviderError("GEMINI_API_KEY / GOOGLE_API_KEY is not set")
        base_url = (
            os.environ.get("GEMINI_BASE_URL")
            or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
        ]
        response = httpx.post(
            f"{base_url}/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
            timeout=120,
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usageMetadata") or {}
        if usage:
            from ai_venture_studio import spend

            spend.record(
                model,
                usage.get("promptTokenCount"),
                usage.get("candidatesTokenCount"),
                stop_reason=(body.get("candidates") or [{}])[0].get("finishReason"),
            )
        candidate = body["candidates"][0]
        # Gemini spells it finishReason / "MAX_TOKENS"; the shared reason set in
        # providers/base.py carries both spellings.
        record_stop_reason(candidate.get("finishReason"))
        parts = candidate["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)
