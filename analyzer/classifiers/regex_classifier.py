from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from api.schemas.classified_schema import ClassifiedSignal, ClassifiedScore
from api.schemas.failure_category_schema import FailureCategory
from api.schemas.log_signal_schema import LogSignal
from analyzer.classifiers.failure_patterns import (
    CATEGORY_PRIORITY,
    CATEGORY_THRESHOLDS,
    FAILURE_PATTERNS,
)


FIELD_WEIGHTS: Dict[str, int] = {
    "error_line":    ClassifiedScore.HIGH,    
    "post_content":  ClassifiedScore.MEDIUM,  
    "pre_content":   ClassifiedScore.LOW,     
    "stage":         ClassifiedScore.LOW,     
}

MAX_PER_PATTERN = sum(FIELD_WEIGHTS.values()) 

HIGH_SCORE_FLOOR = MAX_PER_PATTERN * 1    
MED_SCORE_FLOOR  = MAX_PER_PATTERN // 2


class RegexClassifier:
    

    def classify(self, signals: List[LogSignal]) -> List[ClassifiedSignal]:
        
        return [self._classify_one(signal) for signal in signals]


    def _classify_one(self, signal: LogSignal) -> ClassifiedSignal:
       

        scores, match_counts = self._calculate_scores(signal)

        if not scores:
            return ClassifiedSignal(
                signal=signal,
                best_category=FailureCategory.UNKNOWN,
                classified_confidence=0.0,
            )

        best_category = self._resolve_best(scores)
        confidence = self._calculate_confidence(
            scores[best_category],
            match_counts[best_category],
            best_category,
        )

        return ClassifiedSignal(
            signal=signal,
            best_category=best_category,
            classified_confidence=confidence,
        )

    def _calculate_scores(
        self, signal: LogSignal
    ) -> Tuple[Dict[FailureCategory, int], Dict[FailureCategory, int]]:
        
        scores: Dict[FailureCategory, int] = defaultdict(int)
        match_counts: Dict[FailureCategory, int] = defaultdict(int)

        raw_fields = {
            "error_line":   signal.error_line,
            "post_content": signal.post_content,
            "pre_content":  signal.pre_content,
            "stage":        signal.stage,
        }

        

        for category, patterns in FAILURE_PATTERNS.items():
            for pattern in patterns:
                pattern_matched = False
                for field_name, field_value in raw_fields.items():
                    if field_value and pattern.search(field_value):

                        scores[category] += FIELD_WEIGHTS[field_name]
                        pattern_matched = True

                if pattern_matched:
                    match_counts[category] += 1
        
        return dict(scores), dict(match_counts)


    def _resolve_best(self, scores: Dict[FailureCategory, int]) -> FailureCategory:
        
        max_score = max(scores.values())
        candidates = [cat for cat, s in scores.items() if s == max_score]

        if len(candidates) == 1:
            return candidates[0]

        candidates.sort(
            key=lambda c: (CATEGORY_PRIORITY.get(c, 999), c.value)
        )
        return candidates[0]

    def _calculate_confidence(
        self,
        score: int,
        match_count: int,
        category: FailureCategory,
    ) -> float:
        
        if match_count == 0:
            return 0.0

        if score >= HIGH_SCORE_FLOOR:
            boost_confidence = min(0.10, match_count * 0.02)
            return min(1.0, 0.85 + boost_confidence)

        if score >= MED_SCORE_FLOOR:
            boost_confidence = min(0.20, match_count * 0.05)
            return min(1.0, 0.65 + boost_confidence)

        theoretical_max = match_count * MAX_PER_PATTERN
        base = score / theoretical_max if theoretical_max > 0 else 0.0
        multi_pattern_bonus = max(0, match_count - 1) * 0.08
        return min(0.60, base + multi_pattern_bonus)


    def get_scores_for_signal(
        self, signal: LogSignal
    ) -> Dict[FailureCategory, dict]:
        scores, match_counts = self._calculate_scores(signal)
        return {
            cat: {
                "score": scores[cat],
                "match_count": match_counts[cat],
                "confidence": self._calculate_confidence(
                    scores[cat], match_counts[cat], cat
                ),
                "priority": CATEGORY_PRIORITY.get(cat, 999),
                "threshold": CATEGORY_THRESHOLDS.get(cat),
            }
            for cat in scores
        }