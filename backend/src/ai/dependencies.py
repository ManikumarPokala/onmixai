"""AI FastAPI dependencies — provide the Embedder behind its Protocol."""

from functools import lru_cache

from src.ai.embedding import Embedder
from src.shared.config import get_settings


@lru_cache
def get_embedder() -> Embedder:
    """Process-wide embedder, built from settings on first use. The provider SDK is
    imported lazily (only in the adapter), so non-embedding code never loads it."""
    from src.ai.adapters.openai_embedder import OpenAIEmbedder

    return OpenAIEmbedder(get_settings())
