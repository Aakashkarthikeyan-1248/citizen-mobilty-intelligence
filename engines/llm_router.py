"""
============================================================
CMI — Multi-Provider LLM Router

Sequential-fallback engine that guarantees the pipeline always
gets an LLM response by cascading through multiple providers:

    Priority 1 → NVIDIA NIM  (via OpenAI-compatible endpoint)
    Priority 2 → Groq Cloud  (via OpenAI-compatible endpoint)

If a provider hits a timeout, rate-limit (429), server error
(5xx), or connection failure, the router catches the exception
and immediately tries the next provider in the chain.

All providers speak the OpenAI chat-completions wire format,
so the router uses a single `_call_openai_compat` method and
only varies the base URL / API key / model name per provider.
============================================================
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = logging.getLogger("cmi.engines.llm_router")


# ── Data contracts ─────────────────────────────────────────────

@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider endpoint."""
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 30.0
    max_retries: int = 1

    @property
    def is_configured(self) -> bool:
        """Returns True only if an API key is actually set."""
        return bool(self.api_key and self.api_key.strip())


@dataclass
class LLMResponse:
    """
    Standardized response wrapper returned by the router.

    Attributes:
        text:          The generated text content.
        provider_used: Which provider actually served the response.
        model_used:    The specific model identifier used.
        latency_ms:    Round-trip time in milliseconds.
        usage:         Token usage dict if the provider returns it.
        fallbacks:     List of providers that were tried and failed.
    """
    text: str
    provider_used: str
    model_used: str
    latency_ms: float = 0.0
    usage: dict = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)


# ── Exceptions ─────────────────────────────────────────────────

class LLMProviderError(Exception):
    """Raised when a single provider call fails."""
    def __init__(self, provider: str, reason: str):
        self.provider = provider
        self.reason = reason
        super().__init__(f"[{provider}] {reason}")


class LLMAllProvidersExhausted(Exception):
    """Raised when every provider in the chain has failed."""
    def __init__(self, attempted: list[str]):
        self.attempted = attempted
        super().__init__(
            f"All LLM providers exhausted. Tried: {', '.join(attempted)}"
        )


# ── Router Engine ──────────────────────────────────────────────

class LLMRouter:
    """
    Multi-provider LLM gateway with sequential fallback.

    Usage:
        router = LLMRouter.from_settings(settings)
        response = await router.generate(
            prompt="Summarize the following traffic report...",
            system="You are a traffic analysis assistant.",
        )
        print(response.text, response.provider_used)
    """

    def __init__(self, providers: list[LLMProviderConfig]):
        """
        Initialize with an ordered list of provider configs.
        Providers are tried in list order; only configured
        providers (those with API keys) are kept.
        """
        self._providers = [p for p in providers if p.is_configured]
        if not self._providers:
            logger.warning(
                "LLMRouter initialized with ZERO configured providers. "
                "All generate() calls will fail."
            )
        else:
            names = [p.name for p in self._providers]
            logger.info("LLMRouter initialized with providers: %s", names)

    @classmethod
    def from_settings(cls, settings) -> "LLMRouter":
        """
        Factory that builds the provider chain from a Settings object.
        Provider priority order:
            1. Model A → OpenAI (gpt-4o-mini / gpt-4o)
            2. Model B → NVIDIA NIM (meta/llama-3.1-70b-instruct)
            3. Model C → Groq Cloud (llama-3.1-70b-versatile)
        """
        providers = [
            # ── Priority 1: Model A (OpenAI) ───────────────────
            LLMProviderConfig(
                name="openai",
                base_url="https://api.openai.com/v1",
                api_key=getattr(settings, "openai_api_key", ""),
                model="gpt-4o-mini",
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            ),
            # ── Priority 2: Model B (NVIDIA NIM) ───────────────
            LLMProviderConfig(
                name="nvidia",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=settings.nvidia_api_key,
                model="meta/llama-3.1-70b-instruct",
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            ),
            # ── Priority 3: Model C (Groq Cloud) ───────────────
            LLMProviderConfig(
                name="groq",
                base_url="https://api.groq.com/openai/v1",
                api_key=settings.groq_api_key,
                model="llama-3.1-70b-versatile",
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
            ),
        ]
        return cls(providers)

    async def generate(
        self,
        prompt: str,
        *,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Send a prompt through the provider chain until one succeeds.

        Args:
            prompt:      The user-facing query / context string.
            system:      System-level instruction for the LLM.
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens:  Maximum tokens in the generated response.

        Returns:
            LLMResponse with the generated text and metadata.

        Raises:
            LLMAllProvidersExhausted: If every provider fails.
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must not be empty.")

        failed_providers: list[str] = []

        for provider in self._providers:
            try:
                logger.info(
                    "Attempting LLM call → %s (%s)",
                    provider.name,
                    provider.model,
                )
                response = await self._call_openai_compat(
                    provider=provider,
                    system=system,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                response.fallbacks = failed_providers
                logger.info(
                    "LLM response received from %s in %.0f ms",
                    provider.name,
                    response.latency_ms,
                )
                return response

            except LLMProviderError as exc:
                logger.warning(
                    "Provider %s failed: %s — falling back.",
                    provider.name,
                    exc.reason,
                )
                failed_providers.append(provider.name)
                continue

        # All providers exhausted
        raise LLMAllProvidersExhausted(attempted=failed_providers)

    async def _call_openai_compat(
        self,
        provider: LLMProviderConfig,
        system: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Makes a single chat-completion call to an OpenAI-compatible
        endpoint. Handles timeouts, HTTP errors, and malformed
        responses uniformly.
        """
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(provider.timeout)
            ) as client:
                resp = await client.post(url, json=payload, headers=headers)

        except httpx.TimeoutException:
            raise LLMProviderError(
                provider.name,
                f"Timeout after {provider.timeout}s",
            )
        except httpx.ConnectError as exc:
            raise LLMProviderError(
                provider.name,
                f"Connection failed: {exc}",
            )

        latency_ms = (time.monotonic() - t0) * 1000

        # ── HTTP-level error handling ──────────────────────────
        if resp.status_code == 429:
            raise LLMProviderError(provider.name, "Rate limited (429)")
        if resp.status_code >= 500:
            raise LLMProviderError(
                provider.name,
                f"Server error ({resp.status_code})",
            )
        if resp.status_code != 200:
            raise LLMProviderError(
                provider.name,
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        # ── Parse response body ────────────────────────────────
        try:
            body = resp.json()
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMProviderError(
                provider.name,
                f"Malformed response body: {exc}",
            )

        return LLMResponse(
            text=text.strip(),
            provider_used=provider.name,
            model_used=provider.model,
            latency_ms=latency_ms,
            usage=usage,
        )

    def list_providers(self) -> list[dict]:
        """Returns a summary of configured providers for diagnostics."""
        return [
            {
                "name": p.name,
                "model": p.model,
                "base_url": p.base_url,
                "configured": p.is_configured,
            }
            for p in self._providers
        ]
