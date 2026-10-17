import json
import logging
import re
from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.security.redaction import sanitize_text

logger = logging.getLogger(__name__)


class LLMResponseError(RuntimeError):
    """Safe provider output classification; never retain raw model output."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


T = TypeVar("T", bound=BaseModel)


def _require_all_json_schema_properties(schema: dict) -> dict:
    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {key: visit(item) for key, item in value.items()}
        properties = normalized.get("properties")
        if normalized.get("type") == "object" and isinstance(properties, dict):
            normalized["required"] = list(properties)
        return normalized

    return visit(schema)


class LLMGateway(Protocol):
    async def complete_text(self, system: str, user: str) -> str: ...

    async def complete_messages(
        self, system: str, messages: list[dict[str, str]]
    ) -> str: ...

    async def complete_model(
        self,
        system: str,
        user: str,
        schema: type[T],
    ) -> T: ...

    async def complete_messages_model(
        self,
        system: str,
        messages: list[dict[str, str]],
        schema: type[T],
    ) -> T: ...


class OpenAILLMGateway:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        *,
        native_structured_output: bool = False,
        output_token_limit: int | None = None,
        reasoning_enabled: bool | None = None,
    ):
        self.client = client
        self.model = model
        self.native_structured_output = native_structured_output
        self.output_token_limit = output_token_limit
        self.reasoning_enabled = reasoning_enabled

    async def complete_text(self, system: str, user: str) -> str:
        return await self.complete_messages(system, [{"role": "user", "content": user}])

    async def complete_messages(
        self, system: str, messages: list[dict[str, str]]
    ) -> str:
        return await self._complete_messages(system, messages)

    async def _complete_messages(
        self,
        system: str,
        messages: list[dict[str, str]],
        **request_options: object,
    ) -> str:
        safe_messages = []
        for message in messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            safe_messages.append(
                {
                    "role": role,
                    "content": sanitize_text(str(message.get("content", "")))
                    or "[REDACTED]",
                }
            )
        if self.output_token_limit is not None:
            request_options["max_tokens"] = self.output_token_limit
        if self.reasoning_enabled is not None:
            extra_body = dict(request_options.get("extra_body") or {})
            extra_body["reasoning"] = {"enabled": self.reasoning_enabled}
            request_options["extra_body"] = extra_body
        result = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": sanitize_text(system)},
                *safe_messages,
            ],
            temperature=0,
            **request_options,
        )
        if not result.choices:
            raise LLMResponseError("LLM_EMPTY_RESPONSE")
        choice = result.choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        content = str(choice.message.content or "")
        logger.info(
            "llm_completion model=%s finish=%s content_chars=%d",
            getattr(result, "model", self.model),
            finish_reason,
            len(content),
        )
        if finish_reason == "length":
            raise LLMResponseError("LLM_OUTPUT_TRUNCATED")
        if finish_reason == "content_filter" or getattr(
            choice.message, "refusal", None
        ):
            raise LLMResponseError("LLM_REFUSED")
        if not content.strip():
            raise LLMResponseError("LLM_EMPTY_RESPONSE")
        return content

    async def complete_model(
        self,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        return await self.complete_messages_model(
            system, [{"role": "user", "content": user}], schema
        )

    async def complete_messages_model(
        self,
        system: str,
        messages: list[dict[str, str]],
        schema: type[T],
    ) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        structured_system = (
            f"{system}\nReturn exactly one JSON object matching this schema:\n"
            f"{schema_json}"
        )
        if self.native_structured_output:
            response = await self._complete_messages(
                structured_system,
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": _require_all_json_schema_properties(
                            schema.model_json_schema()
                        ),
                    },
                },
                max_tokens=4096,
                extra_body={"provider": {"require_parameters": True}},
            )
        else:
            response = await self.complete_messages(structured_system, messages)
        fenced = re.fullmatch(
            r"\s*```(?:json)?\s*(.*?)\s*```\s*",
            response,
            flags=re.DOTALL | re.IGNORECASE,
        )
        payload = fenced.group(1) if fenced else response
        try:
            return schema.model_validate_json(payload)
        except ValidationError as error:
            logger.warning(
                "llm_invalid_format model=%s error_types=%s",
                self.model,
                [item["type"] for item in error.errors(include_input=False)][:5],
            )
            raise LLMResponseError("LLM_INVALID_FORMAT") from None
