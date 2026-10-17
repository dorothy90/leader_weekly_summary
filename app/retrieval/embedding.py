import asyncio
import hashlib
import json
import math
import time
import logging
from collections import OrderedDict
from typing import Protocol

from openai import AsyncOpenAI, APIConnectionError, APIStatusError


class EmbeddingGateway(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class OpenAIEmbeddingGateway:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        providers: list[str] | None = None,
        timeout_seconds: float = 7.0,
        cache_ttl_seconds: float = 600,
        cache_size: int = 128,
        hedge_delay_seconds: float = 1.5,
    ):
        self.client = client
        self.model = model
        self.providers = list(providers or [])
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.cache_size = cache_size
        self.hedge_delay_seconds = hedge_delay_seconds
        self._cache = OrderedDict()
        self._inflight = {}

    async def embed(self, text: str) -> list[float]:
        content = text[:8000]
        key = hashlib.sha256(
            json.dumps(
                [self.model, self.providers, content], ensure_ascii=False
            ).encode()
        ).hexdigest()
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._cache.move_to_end(key)
            return list(cached[1])
        self._cache.pop(key, None)
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= 32:
                raise RuntimeError("embedding concurrency limit reached")
            task = asyncio.create_task(self._fetch_and_cache(key, content))
            self._inflight[key] = task
            # A cancelled caller must not cancel work shared with other requests.
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
        return list(await asyncio.shield(task))

    @staticmethod
    def _retryable(exc):
        return isinstance(exc, APIConnectionError) or (
            isinstance(exc, APIStatusError)
            and exc.status_code in (408, 429, 500, 502, 503, 504)
        )

    async def _request_vector(self, content, provider=None):
        options = {}
        if provider:
            options["extra_body"] = {
                "provider": {"only": [provider], "allow_fallbacks": False}
            }
        started = time.monotonic()
        status = "ok"
        try:
            response = await self.client.embeddings.create(
                model=self.model, input=content, **options
            )
            if not response.data or not getattr(response.data[0], "embedding", None):
                raise ValueError("empty embedding response")
            vector = tuple(float(value) for value in response.data[0].embedding)
            if not vector or not all(math.isfinite(value) for value in vector):
                raise ValueError("invalid embedding vector")
            return vector
        except BaseException as exc:
            status = type(exc).__name__
            raise
        finally:
            # Record timing, never input text, vectors, credentials or request bodies.
            logging.getLogger(__name__).info(
                "embedding_attempt provider=%s seconds=%.3f status=%s",
                provider or "auto",
                time.monotonic() - started,
                status,
            )

    async def _hedged_vector(self, content):
        primary = asyncio.create_task(self._request_vector(content, self.providers[0]))
        tasks = {primary}
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=self.hedge_delay_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                try:
                    return primary.result()
                except Exception as exc:
                    if not (self._retryable(exc) or isinstance(exc, ValueError)):
                        raise
            backup = asyncio.create_task(
                self._request_vector(content, self.providers[1])
            )
            tasks.add(backup)
            pending = set(pending) | {backup}
            last_error = None
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                errors = []
                for task in done:
                    try:
                        return task.result()
                    except Exception as exc:
                        errors.append(exc)
                for exc in errors:
                    last_error = exc
                    # Once both are running, a provider failure must not
                    # discard the other provider's potentially valid result.
            raise last_error or RuntimeError("embedding providers unavailable")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_and_cache(self, key, content):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                if len(self.providers) > 1:
                    # At most two attempts, on distinct explicitly allowed providers.
                    vector = await self._hedged_vector(content)
                else:
                    for attempt in range(2):
                        try:
                            vector = await self._request_vector(
                                content, self.providers[0] if self.providers else None
                            )
                            break
                        except (APIConnectionError, APIStatusError) as exc:
                            if attempt or not self._retryable(exc):
                                raise
                            await asyncio.sleep(0.15)
                self._cache[key] = (time.monotonic() + self.cache_ttl_seconds, vector)
                self._cache.move_to_end(key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
                return vector
        finally:
            self._inflight.pop(key, None)
