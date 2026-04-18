from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

from api.schemas.classified_schema import ClassifiedSignal
from api.schemas.failure_category_schema import FailureCategory
from api.schemas.log_signal_schema import LogSignal
from analyzer.classifiers.failure_patterns import (
    CATEGORY_PRIORITY,
    CATEGORY_THRESHOLDS,
)
from analyzer.classifiers.regex_classifier import RegexClassifier
from analyzer.classifiers.semantic_classifier import SemanticClassifier
from analyzer.classifiers.llm_classifier import LLMClassifier
from analyzer.ownership.ownership_config import resolve_owner

logger = logging.getLogger(__name__)

# Fusion weights — must sum to 1.0
REGEX_WEIGHT: float = 0.65
SEMANTIC_WEIGHT: float = 0.35

# Absolute floor below which we always return UNKNOWN regardless of category threshold
ABSOLUTE_MIN_CONFIDENCE: float = 0.20


class ClassificationOrchestrator:
    """
    Thread-safe orchestrator.  Both classifiers are initialised once.
    SemanticClassifier loads its model from disk in its own __init__ (lazy).
    """

    _instance: "ClassificationOrchestrator | None" = None
    _init_lock = threading.Lock()

    def __init__(self, semantic_model_path: str = "models/semantic.pkl"):
        self.regex = RegexClassifier()
        self.semantic = SemanticClassifier(model_path=semantic_model_path)
        self.llm_classifer = LLMClassifier()
        self._learned_fingerprints = set()

    def classify(
        self,
        signals: List[LogSignal],
        embeddings: Optional[np.ndarray] = None,
    ) -> List[ClassifiedSignal]:
        """
        Classify a list of signals using fused regex + semantic scoring.

        Signals that remain UNKNOWN after fusion are routed to the LLM
        classifier as a final fallback.  High-confidence results are fed
        back into the FAISS index for incremental learning.

        Args:
            signals:    Raw log signals to classify.
            embeddings: Pre-computed embeddings aligned with *signals*.
                        When *None* the semantic classifier generates them
                        internally.

        Returns:
            List of :class:`ClassifiedSignal` objects in the same order as
            *signals*, each with a resolved category, confidence and owner.
        """
        if not signals:
            logger.debug("classify() called with empty signal list — returning early.")
            return []

        regex_results = self.regex.classify(signals)
        semantic_results = self.semantic.classify(signals, embeddings)
        sem_by_id = {id(r.signal): r for r in semantic_results}

        final: List[ClassifiedSignal] = []
        for regex_result in regex_results:
            sem_result = sem_by_id.get(id(regex_result.signal))
            fused = self._fuse(regex_result, sem_result)
            logger.debug("Fused result: %s", fused)
            fused = self._attach_ownership(fused)
            final.append(fused)

        resolved = self._resolve_unknowns_with_llm(final)
        self._auto_learn(resolved)
        return resolved
    
    def _auto_learn(self, results: List[ClassifiedSignal]) -> None:
        """
        Feed high-confidence classifications back into the semantic classifier.

        Signals whose fingerprint has already been submitted are skipped to
        avoid redundant index updates.  The fingerprint cache is cleared when
        it exceeds 100 000 entries to prevent unbounded memory growth.

        Args:
            results: Classified signals to evaluate for feedback.
        """
        if not results:
            return
        if len(self._learned_fingerprints) > 100_000:
            logger.debug("Fingerprint cache limit reached — clearing cache.")
            self._learned_fingerprints.clear()
        for r in results:
            fingerprint = r.signal.fingerprint
            if fingerprint in self._learned_fingerprints:
                continue
            if (
                r.best_category != FailureCategory.UNKNOWN
                and r.classified_confidence > 0.8
            ):
                self.semantic.add_feedback(r.signal, r.best_category)
                self._learned_fingerprints.add(r.signal.fingerprint)
        
    def _resolve_unknowns_with_llm(
        self, signals: List[ClassifiedSignal]
    ) -> List[ClassifiedSignal]:
        """
        Route UNKNOWN-category signals through the LLM classifier.

        Signals that already carry a determined category pass through
        unchanged.  None entries are silently dropped.

        Args:
            signals: Mixed list of classified and UNKNOWN signals.

        Returns:
            List with all UNKNOWN signals replaced by LLM-classified results.
        """
        if not signals:
            return []
        result: List[ClassifiedSignal] = []
        for signal in signals:
            if signal is None:
                continue
            if signal.best_category == FailureCategory.UNKNOWN:
                result.append(self.llm_classifer.classify(signal))
            else:
                result.append(signal)
        return result

    @staticmethod
    def _attach_ownership(result: ClassifiedSignal) -> ClassifiedSignal:
        """
        Resolve the failure category to an owner team and attach it to the signal.

        Args:
            result: Classified signal whose category has already been determined.

        Returns:
            A new :class:`ClassifiedSignal` with *owner_team* populated.
        """
        if result is None:
            return result
        rule = resolve_owner(result.best_category)
        return result.model_copy(update={"owner_team": rule.team})


    def _fuse(
        self,
        regex_result: ClassifiedSignal,
        sem_result: ClassifiedSignal | None,
    ) -> ClassifiedSignal:
        """
        Combine regex and semantic results into a single ClassifiedSignal.

        Threshold note
        --------------
        The fused score is structurally lower than the raw regex score because
        it is weighted: fused = (0.65 * regex) + (0.35 * semantic).  When the
        semantic classifier is untrained or returns UNKNOWN the maximum possible
        fused score is 0.65 — so we compare against a *scaled* threshold:
            effective_threshold = category_threshold * REGEX_WEIGHT
        This preserves the intent of per-category floors without penalising the
        regex classifier for semantic being absent.
        """
        signal = regex_result.signal

        fused_scores: dict[FailureCategory, float] = {}
        semantic_contributed = False

        if regex_result.best_category != FailureCategory.UNKNOWN:
            cat = regex_result.best_category
            fused_scores[cat] = fused_scores.get(cat, 0.0) + (
                REGEX_WEIGHT * regex_result.classified_confidence
            )

        if (
            sem_result is not None
            and sem_result.best_category != FailureCategory.UNKNOWN
            and sem_result.classified_confidence > 0.0
        ):
            cat = sem_result.best_category
            fused_scores[cat] = fused_scores.get(cat, 0.0) + (
                SEMANTIC_WEIGHT * sem_result.classified_confidence
            )
            semantic_contributed = True

        if not fused_scores:
            return ClassifiedSignal(
                signal=signal,
                best_category=FailureCategory.UNKNOWN,
                confidence=0.0,
            )

        best_category = self._pick_best(fused_scores)
        best_score = fused_scores[best_category]
        if best_score < ABSOLUTE_MIN_CONFIDENCE:
            return ClassifiedSignal(
                signal=signal,
                best_category=FailureCategory.UNKNOWN,
                classified_confidence=0.0,
            )

        threshold = CATEGORY_THRESHOLDS.get(best_category)
        raw_min = threshold.regex_min if threshold else 0.50
        effective_weight = REGEX_WEIGHT + (SEMANTIC_WEIGHT if semantic_contributed else 0.0)
        effective_min = raw_min * effective_weight

        if best_score < effective_min:
            return ClassifiedSignal(
            signal=signal,
            best_category=FailureCategory.UNKNOWN,
            classified_confidence=0.0,
        )

        return ClassifiedSignal(
            signal=signal,
            best_category=best_category,
            classified_confidence=min(1.0, best_score),
        )

    @staticmethod
    def _pick_best(fused_scores: dict[FailureCategory, float]) -> FailureCategory:
        """
        Return the category with the highest fused score.
        Ties broken by CATEGORY_PRIORITY (lower = more specific = wins).
        """
        max_score = max(fused_scores.values())
        candidates = [c for c, s in fused_scores.items() if s == max_score]
        if len(candidates) == 1:
            return candidates[0]
        candidates.sort(key=lambda c: (CATEGORY_PRIORITY.get(c, 999), c.value))
        return candidates[0]

    @classmethod
    def get_instance(
        cls, semantic_model_path: str = "models/semantic.pkl"
    ) -> "ClassificationOrchestrator":
        """
        Return the process-wide singleton.
        Initialised lazily and only once, even under concurrent Celery workers.
        Use this instead of a bare module-level instantiation.
        """
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls(
                        semantic_model_path=semantic_model_path
                    )
        return cls._instance