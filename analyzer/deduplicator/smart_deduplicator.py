import logging
from collections import defaultdict
from typing import List, Tuple
import numpy as np
import hdbscan
from sklearn.metrics.pairwise import cosine_similarity

from api.schemas.log_signal_schema import LogSignal
from utils.text_normalizer import text_norm_obj
from analyzer.embedding.embedding_service import embedding_obj

logger = logging.getLogger(__name__)


class SmartDeDuplicator:
    """
    Semantic deduplicator for CI/CD log signals.

    Uses HDBSCAN density-based clustering on normalised text embeddings to
    identify and remove redundant signals, retaining one representative per
    cluster.  Noise points (signals that HDBSCAN assigns label ``-1``) are
    further deduplicated by cosine-similarity threshold.

    An in-memory embedding cache keyed on signal fingerprints avoids
    redundant API calls across repeated invocations.
    """

    def __init__(self) -> None:
        self.min_cluster: int = 2
        self.noise_threshold: float = 0.85
        self._embedding_cache: dict = {}

    def deduplicate(
        self,
        signals: List[LogSignal],
    ) -> Tuple[List[LogSignal], np.ndarray]:
        """
        Deduplicate a list of log signals using HDBSCAN semantic clustering.

        Each cluster is reduced to the single signal whose embedding is
        closest to the cluster centroid.  Noise signals are deduped separately
        via cosine similarity.

        Args:
            signals: Raw list of extracted log signals.

        Returns:
            Tuple of (representative_signals, corresponding_embeddings).
        """
        if not signals:
            return [], np.array([])

        texts = [self._extract_text(s) for s in signals]
        embeddings = self._get_embeddings_with_cache(signals, texts)

        if embeddings.size == 0:
            return signals, embeddings

        if len(signals) < self.min_cluster:
            return signals, embeddings

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings_normalized = embeddings / (norms + 1e-10)

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster,
            min_samples=1,
            cluster_selection_epsilon=0.1,
            metric='euclidean',
        )
        labels = clusterer.fit_predict(embeddings_normalized)

        clusters: defaultdict = defaultdict(list)
        noise_indices: List[int] = []

        for idx, label in enumerate(labels):
            if label == -1:
                noise_indices.append(idx)
            else:
                clusters[label].append(idx)

        representative_indices: List[int] = []

        for cluster_indices in clusters.values():
            cluster_embeddings = embeddings_normalized[cluster_indices]
            centroid = np.mean(cluster_embeddings, axis=0)
            distances = np.linalg.norm(cluster_embeddings - centroid, axis=1)
            closest_idx = cluster_indices[np.argmin(distances)]
            representative_indices.append(closest_idx)

        noise_reps: List[int] = []
        if noise_indices:
            noise_reps = self._dedupe_noise(
                noise_indices,
                embeddings_normalized[noise_indices],
            )
            representative_indices.extend(noise_reps)

        representative_indices.sort()

        representative_signals = [signals[i] for i in representative_indices]
        representative_embeddings = embeddings[representative_indices]

        logger.info(
            "Deduplication: %d \u2192 %d signals (%d clusters, %d unique noise).",
            len(signals),
            len(representative_signals),
            len(clusters),
            len(noise_reps),
        )

        return representative_signals, representative_embeddings

    def _get_embeddings_with_cache(
        self,
        signals: List[LogSignal],
        texts: List[str],
    ) -> np.ndarray:
        """
        Return embeddings for all signals, fetching uncached ones from the API.

        Args:
            signals: Signal list parallel to *texts*.
            texts:   Pre-extracted text representations.

        Returns:
            Float32 numpy array of shape (len(signals), embedding_dim).
        """
        embeddings: list = []
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        for i, signal in enumerate(signals):
            cache_key = signal.fingerprint
            if cache_key in self._embedding_cache:
                embeddings.append(self._embedding_cache[cache_key])
            else:
                embeddings.append(None)
                uncached_indices.append(i)
                uncached_texts.append(texts[i])

        if uncached_texts:
            logger.debug(
                "Embedding cache: %d/%d hits.",
                len(signals) - len(uncached_texts),
                len(signals),
            )
            new_embeddings = embedding_obj.embed_batch(text=uncached_texts)
            for idx, embedding in zip(uncached_indices, new_embeddings):
                cache_key = signals[idx].fingerprint
                self._embedding_cache[cache_key] = embedding
                embeddings[idx] = embedding

        return np.array(embeddings)

    def _dedupe_noise(
        self,
        noise_indices: List[int],
        noise_embeddings: np.ndarray,
    ) -> List[int]:
        """
        Remove near-duplicate noise signals by cosine similarity.

        Args:
            noise_indices:    Original indices of noise-labelled signals.
            noise_embeddings: Normalised embedding matrix for those signals.

        Returns:
            Subset of *noise_indices* after deduplication.
        """
        if len(noise_indices) <= 1:
            return noise_indices

        similarities = cosine_similarity(noise_embeddings)
        kept_indices: List[int] = []
        seen: set = set()

        for i in range(len(noise_indices)):
            if i in seen:
                continue
            similar = np.where(similarities[i] > self.noise_threshold)[0]
            seen.update(similar)
            kept_indices.append(noise_indices[i])

        return kept_indices

    def _extract_text(self, signal: LogSignal) -> str:
        """
        Build a normalised text representation of a log signal for embedding.

        Args:
            signal: Log signal to convert.

        Returns:
            Space-joined normalised string.
        """
        parts = []
        if signal.error_line:
            parts.append(text_norm_obj.normalize_for_embedding(text=signal.error_line))
        if signal.post_content:
            parts.append(text_norm_obj.normalize_for_embedding(text=signal.post_content[:200]))
        if signal.pre_content:
            parts.append(text_norm_obj.normalize_for_embedding(text=signal.pre_content[-100:]))
        return " ".join(parts)

    def clear_cache(self) -> None:
        """Evict all cached embeddings."""
        self._embedding_cache.clear()

    def get_cache_stats(self) -> dict:
        """Return a snapshot of the current embedding cache statistics."""
        return {
            "cache_size": len(self._embedding_cache),
            "memory_mb": len(self._embedding_cache) * 512 * 4 / 1024 / 1024,
        }


dedup_obj = SmartDeDuplicator()
