import asyncio
from typing import Protocol

from opensearchpy import OpenSearch


class OpenSearchGateway(Protocol):
    async def search(self, index: str, body: dict) -> dict: ...


class AsyncOpenSearchGateway:
    def __init__(self, client: OpenSearch):
        self.client = client

    async def search(self, index: str, body: dict) -> dict:
        return await asyncio.to_thread(
            self.client.search,
            index=index,
            body=body,
        )
