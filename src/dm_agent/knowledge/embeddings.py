"""Embeddings behind an interface.

Anthropic has no embeddings API, so we run a small sentence-transformers model
in-process (see the plan). The interface keeps the rest of the knowledge layer
free of any torch import — code loads and unit tests run with a fake provider;
torch is only imported the first time a real embedding is requested.

Vectors are L2-normalized, so cosine distance and inner product agree and the
pgvector `<=>` (cosine) queries in `store.py` behave consistently.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from dm_agent.config import get_settings
from dm_agent.db import EMBED_DIM


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts into unit-normalized vectors."""
        ...

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text into a unit-normalized vector."""
        ...


class SentenceTransformerEmbedder:
    """Lazy wrapper around a sentence-transformers model.

    The model (and torch) load on first use, not at import — so importing this
    module is cheap and side-effect free.
    """

    def __init__(self, model_name: str | None = None, dimension: int = EMBED_DIM) -> None:
        self.model_name = model_name or get_settings().embedding_model
        self.dimension = dimension
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # heavy: torch

            self._model = SentenceTransformer(self.model_name)
            # method was renamed get_sentence_embedding_dimension -> get_embedding_dimension
            get_dim = getattr(
                self._model, "get_embedding_dimension", None
            ) or self._model.get_sentence_embedding_dimension
            actual = get_dim()
            if actual != self.dimension:
                raise ValueError(
                    f"Model {self.model_name} emits dim {actual}, but the schema expects "
                    f"{self.dimension} (EMBED_DIM). Update EMBED_DIM + migrate, or pick a "
                    f"model matching the schema."
                )
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [v.tolist() for v in vecs]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


@lru_cache
def get_embedder() -> EmbeddingProvider:
    """Process-wide singleton embedder (the model is expensive to load)."""
    return SentenceTransformerEmbedder()
