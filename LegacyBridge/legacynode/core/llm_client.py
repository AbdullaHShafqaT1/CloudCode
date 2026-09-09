"""
LegacyNode — LLM Client
OpenAI-compatible async client adapter for cloud-hosted Ollama/vLLM endpoints
exposed over Cloudflare tunnels. Handles health checks, retries, and streaming.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx
import structlog
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError

log = structlog.get_logger(__name__)


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str = "ollama"
    timeout_seconds: float = 180.0
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    temperature: float = 0.2
    max_tokens: int = 8192
    stream: bool = True


@dataclass
class LLMResponse:
    content: str
    model: str
    finish_reason: Optional[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class LLMClient:
    """
    Async wrapper around the OpenAI SDK, targeting Ollama/vLLM endpoints
    exposed through a Cloudflare/ngrok reverse tunnel.

    Key features:
    - Lazy initialization: client is created only after endpoint health is verified
    - Exponential backoff retries on transient errors
    - Streaming token delivery with an async generator interface
    - Tunnel health probe before first request and on reconnect
    """

    def __init__(self, config: LLMConfig):
        self._config = config
        self._client: Optional[AsyncOpenAI] = None
        self._healthy: bool = False
        self._last_probe: float = 0.0
        self._probe_interval: float = 30.0  # Re-probe if last check was >30s ago

    # ─── Lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Initialize the client and verify endpoint health."""
        self._client = AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            timeout=self._config.timeout_seconds,
            max_retries=0,  # We handle retries ourselves
        )
        await self.probe_health()

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
            self._healthy = False

    # ─── Health Probe ────────────────────────────────────────────────────────────

    async def probe_health(self) -> bool:
        """
        Check if the tunnel endpoint is reachable.
        Polls /api/tags (Ollama) or /v1/models (OpenAI-compat) with a short timeout.
        Returns True if healthy.
        """
        now = time.monotonic()
        if now - self._last_probe < self._probe_interval and self._healthy:
            return True

        probe_url = self._config.base_url.rstrip("/v1").rstrip("/") + "/api/tags"
        alt_url = self._config.base_url.rstrip("/") + "/models"

        async with httpx.AsyncClient(timeout=10.0) as http:
            for url in [probe_url, alt_url]:
                try:
                    resp = await http.get(url)
                    if resp.status_code < 500:
                        self._healthy = True
                        self._last_probe = time.monotonic()
                        log.info("LLM endpoint healthy", url=self._config.base_url)
                        return True
                except Exception:
                    continue

        self._healthy = False
        log.warning("LLM endpoint unreachable", url=self._config.base_url)
        return False

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    # ─── Chat Completion ─────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: Optional[bool] = None,
    ) -> LLMResponse:
        """
        Non-streaming chat completion with retry logic.
        Returns a single LLMResponse after the model finishes generating.
        """
        use_stream = stream if stream is not None else False
        return await self._chat_with_retry(messages, tools, use_stream)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion. Yields token chunks as they arrive.
        Caller is responsible for assembling the full response.
        """
        assert self._client, "Call connect() first"
        await self._ensure_healthy()

        collected_chunks: list[str] = []
        start = time.monotonic()

        stream = await self._client.chat.completions.create(
            model=self._config.model,
            messages=messages,
            tools=tools or [],
            stream=True,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                collected_chunks.append(delta.content)
                yield delta.content

        log.debug(
            "Streaming complete",
            tokens_approx=len("".join(collected_chunks).split()),
            latency_ms=round((time.monotonic() - start) * 1000, 1),
        )

    # ─── Internal ────────────────────────────────────────────────────────────────

    async def _ensure_healthy(self) -> None:
        if not self._healthy:
            healthy = await self.probe_health()
            if not healthy:
                raise ConnectionError(
                    f"LLM endpoint at {self._config.base_url} is unreachable. "
                    "Ensure your cloud runner notebook is active and the tunnel URL is correct."
                )

    async def _chat_with_retry(
        self,
        messages: list[dict],
        tools: Optional[list[dict]],
        stream: bool,
    ) -> LLMResponse:
        assert self._client, "Call connect() first"
        await self._ensure_healthy()

        retryable = (APIConnectionError, APITimeoutError, ConnectionError)
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                start = time.monotonic()
                kwargs = dict(
                    model=self._config.model,
                    messages=messages,
                    temperature=self._config.temperature,
                    max_tokens=self._config.max_tokens,
                    stream=False,
                )
                if tools:
                    kwargs["tools"] = tools

                response = await self._client.chat.completions.create(**kwargs)
                latency = (time.monotonic() - start) * 1000

                choice = response.choices[0]
                usage = response.usage

                return LLMResponse(
                    content=choice.message.content or "",
                    model=response.model,
                    finish_reason=choice.finish_reason,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                    total_tokens=usage.total_tokens if usage else 0,
                    latency_ms=round(latency, 1),
                )

            except RateLimitError as e:
                log.warning("Rate limit hit — backing off", attempt=attempt)
                await asyncio.sleep(self._config.retry_backoff_base ** attempt)
                last_exc = e

            except retryable as e:
                backoff = self._config.retry_backoff_base ** attempt
                log.warning(
                    "LLM request failed — retrying",
                    attempt=attempt,
                    backoff=backoff,
                    error=str(e),
                )
                self._healthy = False
                await asyncio.sleep(backoff)
                # Try to re-probe before next attempt
                await self.probe_health()
                last_exc = e

        raise RuntimeError(
            f"LLM request failed after {self._config.max_retries} retries: {last_exc}"
        )

    async def list_models(self) -> list[str]:
        """Return model names available on the endpoint."""
        assert self._client, "Call connect() first"
        try:
            models = await self._client.models.list()
            return [m.id for m in models.data]
        except Exception as e:
            log.warning("Could not list models", error=str(e))
            return []
