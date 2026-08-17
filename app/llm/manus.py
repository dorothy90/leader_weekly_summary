import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.security.redaction import sanitize_text


T = TypeVar("T", bound=BaseModel)


class ManusLLMError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ManusTaskDiagnostics:
    requested_profile: str
    actual_profile: str | None
    status: str
    credit_usage: int | None
    duration_ms: int


@dataclass(frozen=True)
class ManusCompletion(Generic[T]):
    value: T
    diagnostics: ManusTaskDiagnostics


def _structured_envelope(schema: type[BaseModel]) -> dict:
    compact_schema = json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "object",
        "properties": {
            "payload_json": {
                "type": "string",
                "description": (
                    "A JSON object that validates against this application "
                    f"schema: {compact_schema}"
                ),
            }
        },
        "required": ["payload_json"],
        "additionalProperties": False,
    }


class ManusLLMGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        profile: str,
        poll_interval_seconds: float,
        task_timeout_seconds: float,
    ):
        self.client = client
        self.profile = profile
        self.poll_interval_seconds = max(0.0, float(poll_interval_seconds))
        self.task_timeout_seconds = max(0.001, float(task_timeout_seconds))

    async def complete_text(self, system: str, user: str) -> str:
        return await self.complete_messages(
            system,
            [{"role": "user", "content": user}],
        )

    async def complete_messages(
        self,
        system: str,
        messages: list[dict[str, str]],
    ) -> str:
        prompt = self._safe_prompt(system, messages)
        started = monotonic()
        deadline = started + self.task_timeout_seconds
        task_id = await self._create_task(prompt)
        await self._wait_for_stopped(task_id, deadline)

        while monotonic() < deadline:
            status_code, payload = await self._request_allowing_status(
                "GET",
                "/v2/task.listMessages",
                params={"task_id": task_id},
            )
            if status_code == 404:
                await self._pause()
                continue
            for message in reversed(self._messages(payload)):
                content = self._assistant_content(message)
                if content is not None:
                    return content
            await self._pause()
        raise ManusLLMError("task_timeout")

    async def complete_model(
        self,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        completion = await self.complete_model_with_diagnostics(
            system,
            user,
            schema,
        )
        return completion.value

    async def complete_messages_model(
        self,
        system: str,
        messages: list[dict[str, str]],
        schema: type[T],
    ) -> T:
        completion = await self._complete_messages_model_with_diagnostics(
            system,
            messages,
            schema,
        )
        return completion.value

    async def complete_model_with_diagnostics(
        self,
        system: str,
        user: str,
        schema: type[T],
    ) -> ManusCompletion[T]:
        return await self._complete_messages_model_with_diagnostics(
            system,
            [{"role": "user", "content": user}],
            schema,
        )

    async def _complete_messages_model_with_diagnostics(
        self,
        system: str,
        messages: list[dict[str, str]],
        schema: type[T],
    ) -> ManusCompletion[T]:
        started = monotonic()
        deadline = started + self.task_timeout_seconds
        prompt = self._safe_prompt(system, messages)
        task_id = await self._create_task(
            prompt,
            structured_output_schema=_structured_envelope(schema),
        )
        task = await self._wait_for_stopped(task_id, deadline)

        while monotonic() < deadline:
            status_code, payload = await self._request_allowing_status(
                "GET",
                "/v2/task.listMessages",
                params={"task_id": task_id},
            )
            if status_code == 404:
                await self._pause()
                continue
            result = self._latest_structured_result(payload)
            if result is None:
                await self._pause()
                continue
            if result.get("success") is not True:
                raise ManusLLMError("structured_output_failed")
            value = result.get("value")
            payload_json = (
                value.get("payload_json") if isinstance(value, dict) else None
            )
            if not isinstance(payload_json, str):
                raise ManusLLMError("structured_output_invalid")
            try:
                validated = schema.model_validate_json(payload_json)
            except (ValidationError, ValueError, TypeError):
                raise ManusLLMError("structured_output_invalid") from None
            return ManusCompletion(
                value=validated,
                diagnostics=ManusTaskDiagnostics(
                    requested_profile=self.profile,
                    actual_profile=self._optional_string(
                        task.get("agent_profile")
                    ),
                    status="stopped",
                    credit_usage=self._optional_int(task.get("credit_usage")),
                    duration_ms=int((monotonic() - started) * 1000),
                ),
            )
        raise ManusLLMError("task_timeout")

    async def _create_task(
        self,
        prompt: str,
        *,
        structured_output_schema: dict | None = None,
    ) -> str:
        payload = {
            "message": {"content": prompt},
            "agent_profile": self.profile,
            "interactive_mode": False,
            "hide_in_task_list": True,
            "share_visibility": "private",
        }
        if structured_output_schema is not None:
            payload["structured_output_schema"] = structured_output_schema
        created = await self._request(
            "POST",
            "/v2/task.create",
            json=payload,
        )
        task_id = created.get("task_id")
        if created.get("ok") is False or not isinstance(task_id, str):
            raise ManusLLMError("task_create_failed")
        return task_id

    async def _wait_for_stopped(
        self,
        task_id: str,
        deadline: float,
    ) -> dict:
        while monotonic() < deadline:
            status_code, detail = await self._request_allowing_status(
                "GET",
                "/v2/task.detail",
                params={"task_id": task_id},
            )
            if status_code == 404:
                await self._pause()
                continue
            task = self._task(detail)
            status = task.get("status")
            if status == "running":
                await self._pause()
                continue
            if status == "waiting":
                raise ManusLLMError("task_waiting")
            if status == "error":
                raise ManusLLMError("task_error")
            if status == "stopped":
                return task
            raise ManusLLMError("task_status_invalid")
        raise ManusLLMError("task_timeout")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        status_code, payload = await self._request_allowing_status(
            method,
            path,
            **kwargs,
        )
        if status_code == 404:
            raise ManusLLMError("provider_request_failed")
        return payload

    async def _request_allowing_status(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> tuple[int, dict]:
        try:
            response = await self.client.request(method, path, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            raise ManusLLMError("provider_unavailable") from None
        except httpx.HTTPError:
            raise ManusLLMError("provider_unavailable") from None

        if response.status_code in {401, 403}:
            raise ManusLLMError("authentication_failed")
        if response.status_code == 429:
            raise ManusLLMError("rate_limited")
        if response.status_code >= 500:
            raise ManusLLMError("provider_unavailable")
        if response.status_code >= 400 and response.status_code != 404:
            raise ManusLLMError("provider_request_failed")
        try:
            payload = response.json()
        except ValueError:
            raise ManusLLMError("response_invalid") from None
        if not isinstance(payload, dict):
            raise ManusLLMError("response_invalid")
        return response.status_code, payload

    async def _pause(self) -> None:
        await asyncio.sleep(self.poll_interval_seconds)

    @staticmethod
    def _safe_prompt(system: str, messages: list[dict[str, str]]) -> str:
        sections = [
            "System instructions:",
            sanitize_text(system) or "[REDACTED]",
            "Conversation:",
        ]
        for message in messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = sanitize_text(str(message.get("content", "")))
            sections.append(f"{role.upper()}: {content or '[REDACTED]'}")
        return "\n".join(sections)

    @staticmethod
    def _task(payload: dict) -> dict:
        task = payload.get("task")
        if isinstance(task, dict):
            return task
        data = payload.get("data")
        if isinstance(data, dict):
            nested = data.get("task")
            return nested if isinstance(nested, dict) else data
        return payload

    @staticmethod
    def _messages(payload: dict) -> list[dict]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            data = payload.get("data")
            messages = data.get("messages") if isinstance(data, dict) else []
        return [message for message in messages if isinstance(message, dict)]

    @classmethod
    def _latest_structured_result(cls, payload: dict) -> dict | None:
        for message in reversed(cls._messages(payload)):
            if message.get("type") != "structured_output_result":
                continue
            result = message.get("structured_output_result")
            if isinstance(result, dict):
                return result
        return None

    @staticmethod
    def _assistant_content(message: dict) -> str | None:
        if message.get("type") != "assistant_message":
            return None
        for candidate in (
            message.get("content"),
            message.get("assistant_message"),
            (message.get("message") or {}).get("content")
            if isinstance(message.get("message"), dict)
            else None,
        ):
            if isinstance(candidate, str):
                return candidate
            if isinstance(candidate, dict) and isinstance(
                candidate.get("content"), str
            ):
                return candidate["content"]
        return None

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None
