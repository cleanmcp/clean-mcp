"""Local embedder using sentence-transformers (all-MiniLM-L6-v2)."""

from __future__ import annotations

import threading
from typing import Sequence

from sentence_transformers import SentenceTransformer

from ..core.config import EmbedderConfig
from ..util.logging import get_logger

logger = get_logger(__name__)

MAX_ENCODE_BATCH_SIZE = 100


class SentenceTransformerEmbedder:
    """Embedder using a local sentence-transformers model."""

    def __init__(self, config: EmbedderConfig | None = None) -> None:
        self._config = config or EmbedderConfig()
        self._model: SentenceTransformer | None = None
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._config.embedding_dimensions

    def _get_model(self) -> SentenceTransformer:
        if self._model is not None:
            return self._model
        with self._lock:
            # Double-check after acquiring lock
            if self._model is None:
                logger.info("Loading embedding model '%s'...", self._config.model_name)
                self._model = SentenceTransformer(self._config.model_name)
                logger.info("Embedding model ready")
        return self._model

    def warmup(self) -> None:
        """Pre-load the model. Call at startup to avoid cold start delay."""
        self._get_model()

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed multiple texts efficiently.

        Large batches are split into chunks of MAX_ENCODE_BATCH_SIZE to
        bound memory usage and prevent very long encode calls.
        """
        if not texts:
            return []
        model = self._get_model()
        text_list = list(texts)

        if len(text_list) > MAX_ENCODE_BATCH_SIZE:
            logger.warning(
                "Large embedding batch (%d texts), splitting into chunks of %d",
                len(text_list),
                MAX_ENCODE_BATCH_SIZE,
            )

        all_embeddings: list[list[float]] = []
        for i in range(0, len(text_list), MAX_ENCODE_BATCH_SIZE):
            chunk = text_list[i : i + MAX_ENCODE_BATCH_SIZE]
            embeddings = model.encode(
                chunk,
                convert_to_numpy=True,
                show_progress_bar=self._config.show_progress_bar,
            )
            all_embeddings.extend(emb.tolist() for emb in embeddings)
        return all_embeddings

    def embed_query(self, query: str) -> list[float]:
        """Embed a single search query."""
        model = self._get_model()
        embedding = model.encode(query, convert_to_numpy=True)
        return embedding.tolist()
