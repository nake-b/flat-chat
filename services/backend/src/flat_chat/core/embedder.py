from collections.abc import Sequence

from fastapi import Request
from pydantic_ai import Embedder
from pydantic_ai.embeddings import (
    EmbeddingResult,
    EmbeddingSettings,
    WrapperEmbeddingModel,
)
from pydantic_ai.embeddings.base import EmbedInputType
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.providers.openai import OpenAIProvider

from flat_chat.core.config import settings


class JinaTaskEmbedder(WrapperEmbeddingModel):
    """Auto-inject Jina v3's task LoRA based on input_type.

    v3's asymmetric retrieval needs `task: retrieval.query` for queries and
    `task: retrieval.passage` for documents. Setting it here keeps callers
    from having to know about the Jina-specific field.
    """

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        task = "retrieval.query" if input_type == "query" else "retrieval.passage"
        merged: EmbeddingSettings = {**(settings or {}), "extra_body": {"task": task}}
        return await self.wrapped.embed(inputs, input_type=input_type, settings=merged)


def build_jina_embedder() -> Embedder | None:
    if not settings.jina_api_key:
        return None
    base = OpenAIEmbeddingModel(
        "jina-embeddings-v3",
        provider=OpenAIProvider(
            base_url=settings.jina_base_url, api_key=settings.jina_api_key
        ),
    )
    return Embedder(JinaTaskEmbedder(base), settings={"dimensions": 1024})


def get_embedder(request: Request) -> Embedder | None:
    return request.app.state.embedder
