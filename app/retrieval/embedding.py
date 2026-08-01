from typing import Protocol

from openai import AsyncOpenAI


class EmbeddingGateway(Protocol):
    async def embed(self, text: str) -> list[float]: ...


class OpenAIEmbeddingGateway:
    def __init__(self, client: AsyncOpenAI, model: str):
        self.client = client
        self.model = model

    async def embed(self, text: str) -> list[float]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=text[:8000],
        )
        return list(response.data[0].embedding)
