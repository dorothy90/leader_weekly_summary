import json
import re
from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.security.redaction import sanitize_text

T = TypeVar("T", bound=BaseModel)


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
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def complete_text(self, system: str, user: str) -> str:
        return await self.complete_messages(
            system, [{"role": "user", "content": user}]
        )

    async def complete_messages(
        self, system: str, messages: list[dict[str, str]]
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
        response = await self.complete_messages(
            f"{system}\nReturn exactly one JSON object matching this schema:\n{schema_json}",
            messages,
        )
        fenced = re.fullmatch(
            r"\s*```(?:json)?\s*(.*?)\s*```\s*",
            response,
            flags=re.DOTALL | re.IGNORECASE,
        )
        payload = fenced.group(1) if fenced else response
        return schema.model_validate_json(payload)
