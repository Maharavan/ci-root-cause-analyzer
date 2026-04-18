import json
import logging
from pathlib import Path
from typing import List
import instructor
import litellm
from api.app.config import settings
from api.schemas.classified_schema import ClassifiedSignal
from api.schemas.rca_schema import SignalRCA
from analyzer.rca_engine.prompt import build_rca_prompt

logger = logging.getLogger(__name__)


class RCAEngine:
    """Orchestrates LLM-based Root Cause Analysis for classified pipeline signals.

    Loads classified signals from disk, builds category-aware prompts, and calls
    the configured LLM via *instructor* to produce structured
    :class:`~api.schemas.rca_schema.SignalRCA` results.
    """
    def __init__(self):
        litellm.api_key = settings.LLM_API_KEY
        self.client = instructor.from_litellm(litellm.completion)

    def _load_classified_signals(self, failure_id: str) -> List[ClassifiedSignal]:
        """Load classified signals from the ``error.json`` artefact on disk.

        Args:
            failure_id: Unique identifier of the pipeline failure.

        Returns:
            List of :class:`~api.schemas.classified_schema.ClassifiedSignal` objects.

        Raises:
            FileNotFoundError: If ``error.json`` does not exist for *failure_id*.
        """
        path = Path(settings.LOG_PATH) / failure_id / "error.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Classified signals not found: {path}. "
                "Run classify_failure task first."
            )
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [ClassifiedSignal.model_validate(sig) for sig in raw]

    def _run_rca(self, signal: ClassifiedSignal) -> SignalRCA:
        """Run a single RCA call for one classified signal.

        Builds a category-aware prompt, calls the LLM, and enriches the
        response with the validated category, error line, owner, and fingerprint
        from the classifier result.

        Args:
            signal: A classified signal ready for root-cause analysis.

        Returns:
            :class:`~api.schemas.rca_schema.SignalRCA` with all fields populated.

        Raises:
            Exception: If the LLM API call fails.
        """
        prompt = build_rca_prompt(classified_signal=signal)
        try:
            response: SignalRCA = self.client.chat.completions.create(
                model=settings.RCA_LLM_DEPLOYMENT,
                response_model=SignalRCA,
                messages=[{"role": "user", "content": prompt}],
                temperature=settings.RCA_TEMPERATURE,
            )
        except Exception as e:
            logger.error(
                "LLM RCA call failed for signal '%s': %s",
                signal.signal.error_line,
                e,
            )
            raise
        return response.model_copy(
            update={
                "validated_category": signal.best_category,
                "error_line":         signal.signal.error_line,
                "owner":              signal.owner_team,
                "fingerprint":        signal.signal.fingerprint,
            }
        )

    def run_rca_for_signals(self, failure_id: str) -> List[SignalRCA]:
        """Run RCA for every classified signal belonging to a failure.

        Args:
            failure_id: Unique identifier of the pipeline failure.

        Returns:
            List of :class:`~api.schemas.rca_schema.SignalRCA` results, one per
            classified signal.

        Raises:
            FileNotFoundError: If the ``error.json`` artefact is missing.
            Exception: If any individual LLM call fails.
        """
        classified_signals = self._load_classified_signals(failure_id)
        results = []
        for signal in classified_signals:
            rca = self._run_rca(signal)
            results.append(rca)
        return results


rca_obj = RCAEngine()