"""AIService — multi-provider abstraction.

Ported from Vision's ai_manager/services.py but with Anthropic Claude as the
default backend. Provider selection: Constance `ACTIVE_AI_PROVIDER` (a name
from the AIProvider model) if set, otherwise falls back to an Anthropic
provider that uses the ANTHROPIC_API_KEY / ANTHROPIC_DEFAULT_MODEL env vars.

Phase 1 implements text-only completion (used by health check + email-drafting
stub). Tool-use support for Lini lands in a later phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from constance import config
from django.conf import settings

from .models import AIProvider, AIUsageLog


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class AIService:
    @staticmethod
    def _resolve_provider() -> tuple[AIProvider | None, str, str]:
        """Returns (provider_row_or_None, provider_type, api_key, model_name)."""
        name = getattr(config, 'ACTIVE_AI_PROVIDER', None) or ''
        if name:
            try:
                provider = AIProvider.objects.get(name=name, is_active=True)
                api_key = provider.api_key or _env_key_for(provider.provider_type)
                return provider, provider.provider_type, api_key, provider.model_name
            except AIProvider.DoesNotExist:
                pass
        # Default: Anthropic via env var
        return None, AIProvider.PROVIDER_ANTHROPIC, settings.ANTHROPIC_API_KEY, settings.ANTHROPIC_DEFAULT_MODEL

    @classmethod
    def complete(
        cls,
        *,
        prompt: str,
        system_prompt: str | None = None,
        function_name: str = 'generic',
        user=None,
        max_tokens: int = 1024,
    ) -> str:
        """Text-only completion. Returns the assistant's text response."""
        provider_row, provider_type, api_key, model_name = cls._resolve_provider()
        if not api_key:
            raise RuntimeError(f'No API key configured for provider {provider_type}')

        if provider_type == AIProvider.PROVIDER_ANTHROPIC:
            result = cls._call_anthropic(api_key, model_name, prompt, system_prompt, max_tokens)
        else:
            raise NotImplementedError(f'Provider {provider_type} is not implemented yet')

        AIUsageLog.objects.create(
            user=user,
            provider=provider_row,
            function_name=function_name,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost=_estimate_cost(provider_type, result.input_tokens, result.output_tokens),
            prompt=prompt[:2000],
            response=result.text[:4000],
        )
        return result.text

    @staticmethod
    def _call_anthropic(api_key: str, model_name: str, prompt: str, system_prompt: str | None, max_tokens: int) -> Completion:
        from anthropic import Anthropic  # local import so health check still works without the SDK

        client = Anthropic(api_key=api_key)
        kwargs = {
            'model': model_name,
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if system_prompt:
            kwargs['system'] = system_prompt
        resp = client.messages.create(**kwargs)

        text_blocks = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
        return Completion(
            text=''.join(text_blocks).strip(),
            input_tokens=getattr(resp.usage, 'input_tokens', 0) or 0,
            output_tokens=getattr(resp.usage, 'output_tokens', 0) or 0,
        )


def _env_key_for(provider_type: str) -> str:
    if provider_type == AIProvider.PROVIDER_ANTHROPIC:
        return settings.ANTHROPIC_API_KEY
    return ''


def _estimate_cost(provider_type: str, input_tokens: int, output_tokens: int) -> Decimal:
    # Rough Sonnet-class pricing; tighten later.
    if provider_type == AIProvider.PROVIDER_ANTHROPIC:
        return Decimal(input_tokens) * Decimal('0.000003') + Decimal(output_tokens) * Decimal('0.000015')
    return Decimal('0')
