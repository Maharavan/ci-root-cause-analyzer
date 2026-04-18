from __future__ import annotations

import logging
from openai import OpenAI
from typing import List
from analyzer.ownership.ownership_config import resolve_owner
from api.app.config import settings
from api.schemas.classified_schema import ClassifiedSignal, ClassificationResult
from api.schemas.failure_category_schema import FailureCategory
import litellm
import instructor

logger = logging.getLogger(__name__)


class LLMClassifier:
    """Fallback classifier that uses an LLM to resolve UNKNOWN-category signals."""

    def __init__(self) -> None:
        litellm.api_key = settings.LLM_API_KEY
        self.client = instructor.from_litellm(litellm.completion)

    def build_category_prompt(
        self,
        classified_signal: ClassifiedSignal,
    ) -> str:
        """
        Build a deterministic classification prompt for the LLM.

        The prompt encodes hard rules that mirror the regex/semantic logic to
        ensure the LLM produces consistent, rule-based overrides rather than
        guessing.

        Args:
            classified_signal: The signal whose category needs to be verified
                               or overridden.

        Returns:
            Fully formatted prompt string ready for the chat API.
        """

        signal = classified_signal.signal



        allowed_categories = "\n".join(
            [f"- {category.value}" for category in FailureCategory]
        )
        return f"""
            You are a deterministic CI/CD failure classification engine.

            Your job is to validate or correct the failure category STRICTLY based on rules.

            --------------------------------------------------
            HARD RULES (FOLLOW EXACTLY, NO EXCEPTIONS)

            Rule 1: CI/INFRA FAILURES (HIGHEST PRIORITY)
            If error contains ANY of:
            "401", "unauthorized", "authentication failed", "token expired",
            "connection refused", "timeout", "network", "dns",
            "artifactory", "registry", "docker", "kubernetes", "k8s",
            "pipeline", "jenkinsfile", "workflow", "runner", "agent",
            "secret", "env", "OOM", "out of memory", "resource", "disk full"
            → CI_INFRA_FAILURE

            Rule 2: DEV FAILURES
            If error contains ANY of:
            "compilation error", "build failed", "linker error", "syntax error",
            "import error", "module not found", "dependency", "unresolved",
            "coverage", "lint", "checkstyle", "sonar", "fossid", "quality gate"
            → DEV_FAILURE

            Rule 3: TEST FAILURES
            If error contains ANY of:
            "assertion", "assert", "expected", "actual", "test failed",
            "flaky", "fixture", "snapshot mismatch", "test timeout"
            → TEST_FAILURE

            Rule 4: UNKNOWN
            If none of the above rules match with clear evidence
            → UNKNOWN

            --------------------------------------------------
            IMPORTANT

            - Rule 1 overrides Rules 2, 3, 4
            - Do NOT guess
            - Do NOT use intuition
            - Only use explicit evidence from logs
            - Be deterministic
            - ONLY output a category from the ALLOWED CATEGORIES list below

            --------------------------------------------------
            FAILURE INPUT

            Stage:
            {signal.stage}

            Error Line:
            {signal.error_line}

            Pre Context:
            {signal.pre_content}

            Post Context:
            {signal.post_content}

            --------------------------------------------------
            CURRENT CLASSIFICATION

            Category:
            {classified_signal.best_category}

            Confidence:
            {classified_signal.classified_confidence}

            --------------------------------------------------
            ALLOWED CATEGORIES

            {allowed_categories}

            --------------------------------------------------
            TASK

            1. Identify which rule applies (1–5)
            2. Check if current category matches the rule
            3. If correct → keep it
            4. If incorrect → override it

            --------------------------------------------------
            OUTPUT FORMAT (STRICT JSON ONLY)

            Return EXACTLY this JSON:

            {{
            "best_category": "<one of allowed categories>",
            "classified_confidence": <float between 0.0 and 1.0>
            }}

            Do NOT include:
            - signal
            - owner_team
            - explanations
            - extra fields

            ONLY return JSON.
            """

    def classify(self, signal: ClassifiedSignal) -> ClassifiedSignal:
        """
        Classify an UNKNOWN signal using the LLM.

        Builds a deterministic prompt, calls the LLM via LiteLLM with an
        instructor-validated response model, then resolves owner team and
        returns a fully populated :class:`ClassifiedSignal`.

        Args:
            signal: The UNKNOWN or low-confidence signal to reclassify.

        Returns:
            :class:`ClassifiedSignal` with LLM-determined category,
            confidence, and resolved owner team.
        """
        generated_prompt = self.build_category_prompt(classified_signal=signal)
        logger.info("Running LLM classification for signal fingerprint=%s", signal.signal.fingerprint)
        response = self.client.chat.completions.create(
            model=settings.RCA_LLM_DEPLOYMENT,
            response_model=ClassificationResult,
            messages=[
                {"role": "user", "content": generated_prompt}
            ],
            temperature=settings.CLASSIFY_TEMPERATURE
        )

        resolved_owner = resolve_owner(response.best_category)
        structured_response = ClassifiedSignal(
            signal=signal.signal,
            owner_team=resolved_owner.team,
            classified_confidence=response.classified_confidence,
            best_category= response.best_category

        )
        return structured_response