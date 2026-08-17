import json
import re
from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.security.redaction import sanitize_text

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
    ):
        self.client = client
        self.model = model
        self.native_structured_output = native_structured_output

    async def complete_text(self, system: str, user: str) -> str:
        return await self.complete_messages(
            system, [{"role": "user", "content": user}]
        )

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
        result = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": sanitize_text(system)},
                *safe_messages,
            ],
            temperature=0,
            **request_options,
        )
        return str(result.choices[0].message.content or "")

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
        return schema.model_validate_json(payload)
