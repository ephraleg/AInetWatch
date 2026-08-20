"""Provider-neutral AI interface. Anthropic is the initial implementation."""
from __future__ import annotations

import json
import os
import urllib.request


class AIProviderError(RuntimeError):
    pass


class AIProvider:
    name = "unknown"
    def generate_json(self, operation: str, prompt: str, schema_hint: str) -> tuple[dict, dict]:
        raise NotImplementedError


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("AINWA_ANTHROPIC_MODEL", "claude-sonnet-4-6")

    def generate_json(self, operation, prompt, schema_hint):
        if not self.api_key:
            raise AIProviderError("ANTHROPIC_API_KEY is not configured")
        request_body = {
            "model": self.model,
            "max_tokens": 1800,
            "temperature": 0,
            "messages": [{"role": "user", "content": (
                "Return only valid JSON. Use only supplied evidence; never invent inaccessible details.\n"
                f"Required shape: {schema_hint}\n\n{prompt}"
            )}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(request_body).encode(), method="POST",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                raw = json.load(response)
        except Exception as exc:
            raise AIProviderError(f"Anthropic request failed: {exc}") from exc
        text = "".join(block.get("text", "") for block in raw.get("content", []) if block.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIProviderError("provider returned invalid JSON") from exc
        usage = raw.get("usage") or {}
        return result, {"provider": self.name, "model": self.model, "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}


def configured_provider() -> AIProvider:
    provider = os.environ.get("AINWA_AI_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return AnthropicProvider()
    raise AIProviderError(f"AI provider {provider!r} is not installed; configure an adapter implementing AIProvider")
