from typing import Optional

import json
import logging
import numpy as np

logger = logging.getLogger(__name__)
import psycopg2
from psycopg2 import sql
from pgvector.psycopg2 import register_vector

from api.app.config import settings
from api.schemas.classified_schema import OwnerTeam
from api.schemas.failure_category_schema import FailureCategory
from api.schemas.rca_schema import (
    DevRemediation,
    TestRemediation,
    InfraRemediation,
    ManualRemediation,
    RemediationAction,
    SignalRCA,
)
from storage.database import database_obj



_REMEDIATION_MAP: dict[str, type] = {
    "FIX_DEV":              DevRemediation,
    "FIX_TEST":             TestRemediation,
    "FIX_CI_INFRA":         InfraRemediation,
    "MANUAL_INVESTIGATION": ManualRemediation,
}


def _parse_remediation(raw) -> RemediationAction:
    if isinstance(raw, str):
        raw = json.loads(raw)

    action = raw.get("action", "MANUAL_INVESTIGATION")
    model_cls = _REMEDIATION_MAP.get(action, ManualRemediation)
    return model_cls.model_validate(raw)


def _parse_remediation_list(raw) -> list[RemediationAction]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [_parse_remediation(item) for item in raw]


class FailureKnowledgeDB:
    """Data-access layer for the failure pattern knowledge store.

    Provides static methods to persist RCA results as pgvector embeddings and
    to retrieve cached results via cosine similarity search.
    """

    @staticmethod
    def insert_pattern(signal: SignalRCA, embedding: np.ndarray) -> None:
        """Persist an RCA result and its embedding in the knowledge store.

        Upserts on ``fingerprint``: if the pattern already exists the
        ``recurrence_count``, ``confidence``, and remediation fields are
        updated; all other fields are left unchanged.

        Args:
            signal: Structured RCA result to store.
            embedding: 1-D float32 NumPy array representing the signal text.

        Raises:
            psycopg2.DatabaseError: Rolled back automatically on DB error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)
        register_vector(conn)
        try:
            with conn.cursor() as cursor:
                query = sql.SQL("""
                    INSERT INTO {} (
                        validated_category,
                        root_cause,
                        error_line,
                        owner_team,
                        remediation,
                        secondary_remediations,
                        confidence,
                        severity,
                        recurrence_count,
                        analyzed_at,
                        evidence_url,
                        embedding,
                        fingerprint
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fingerprint) DO UPDATE SET
                        recurrence_count = {}.recurrence_count + 1,
                        confidence       = EXCLUDED.confidence,
                        remediation      = EXCLUDED.remediation,
                        secondary_remediations = EXCLUDED.secondary_remediations
                """).format(
                    sql.Identifier(settings.FAILURE_PATTERN_TABLE),
                    sql.Identifier(settings.FAILURE_PATTERN_TABLE),
                )

                secondary = (
                    json.dumps([r.model_dump(mode="json") for r in signal.secondary_remediations])
                    if signal.secondary_remediations
                    else None
                )

                cursor.execute(
                    query,
                    (
                        signal.validated_category,
                        signal.root_cause,
                        signal.error_line,
                        signal.owner,
                        json.dumps(signal.remediation.model_dump(mode="json")),
                        secondary,
                        signal.rca_confidence,
                        signal.severity,
                        signal.recurrence_count,
                        signal.analyzed_at,
                        signal.evidence_url,
                        embedding.tolist(),
                        signal.fingerprint,
                    ),
                )
                conn.commit()

        except (psycopg2.DatabaseError, Exception) as e:
            conn.rollback()
            logger.error("PostgreSQL insert failed: %s", e)

        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def similar_search(
        embedding: np.ndarray,
        threshold: float = 0.92,
    ) -> Optional[SignalRCA]:
        """Search for a previously seen RCA result using cosine similarity.

        Uses pgvector's ``<=>`` operator (cosine distance) to find the closest
        stored embedding.  Returns the cached
        :class:`~api.schemas.rca_schema.SignalRCA` only when the similarity
        score meets *threshold*, allowing instant recall without a new LLM call.

        Args:
            embedding: Query embedding as a 1-D float32 NumPy array.
            threshold: Minimum cosine similarity (0–1) required for a cache hit.
                Defaults to ``0.92``.

        Returns:
            The best-matching :class:`~api.schemas.rca_schema.SignalRCA` with a
            ``similarity_score`` field populated, or ``None`` if no match meets
            the threshold.

        Raises:
            psycopg2.DatabaseError: Logged and suppressed; returns ``None`` on
                error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)
        register_vector(conn)

        try:
            with conn.cursor() as cursor:
                query = sql.SQL("""
                    SELECT
                        validated_category,
                        root_cause,
                        error_line,
                        owner_team,
                        remediation,
                        secondary_remediations,
                        confidence,
                        severity,
                        recurrence_count,
                        analyzed_at,
                        evidence_url,
                        fingerprint,
                        1 - (embedding <=> %s::vector) AS similarity_score
                    FROM {}
                    WHERE 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT 1
                """).format(sql.Identifier(settings.FAILURE_PATTERN_TABLE))

                vec = embedding.tolist()
                cursor.execute(query, (vec, vec, threshold, vec))
                row = cursor.fetchone()

                if not row:
                    return None

                (
                    validated_category,
                    root_cause,
                    error_line,
                    owner_team,
                    remediation_raw,
                    secondary_raw,
                    confidence,
                    severity,
                    recurrence_count,
                    analyzed_at,
                    evidence_url,
                    fingerprint,
                    similarity_score,
                ) = row

                remediation = _parse_remediation(remediation_raw)

                secondary_remediations = (
                    _parse_remediation_list(secondary_raw)
                    if secondary_raw
                    else None
                )

                return SignalRCA(
                    validated_category=FailureCategory(validated_category),
                    root_cause=root_cause,
                    error_line=error_line,
                    owner=OwnerTeam(owner_team),
                    remediation=remediation,
                    secondary_remediations=secondary_remediations,
                    rca_confidence=float(confidence),
                    severity=severity,
                    recurrence_count=recurrence_count,
                    analyzed_at=str(analyzed_at),
                    evidence_url=evidence_url,
                    fingerprint=fingerprint,
                    similarity_score=float(similarity_score),
                )

        except Exception as e:
            logger.error("PostgreSQL similarity search failed: %s", e)
            return None

        finally:
            if conn:
                conn.close()


knowledge_store = FailureKnowledgeDB()