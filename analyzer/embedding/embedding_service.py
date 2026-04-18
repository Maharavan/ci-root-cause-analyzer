import logging
from typing import List
import numpy as np
from litellm import embedding
from api.app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Singleton wrapper around the LiteLLM embedding API.

    Ensures a single shared instance is used across the application to avoid
    redundant initialisation and API client overhead.
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            # No client needed for litellm
            self.model = settings.EMBEDDING_MODEL
            self.api_key = settings.LLM_API_KEY
            self._initialized = True

    def embed_batch(self, text: List[str]) -> np.ndarray:
        """Generate embeddings for a batch of text strings.

        Args:
            text: List of strings to embed.

        Returns:
            A 2-D float32 array of shape ``(len(text), embedding_dim)``.

        Raises:
            Exception: If the embedding API call fails.
        """
        try:
            response = embedding(
                model=self.model,
                input=text,
                api_key=self.api_key,
                encoding_format="float",
                input_type="passage"
            )
        except Exception as e:
            logger.error("Embedding API call failed: %s", e)
            raise

        embeddings = np.vstack(
            [item["embedding"] for item in response["data"]],
            dtype=np.float32,
        )

        return embeddings


embedding_obj = EmbeddingService()