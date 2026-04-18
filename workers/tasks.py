import logging
from collections import defaultdict

import numpy as np

from workers.celery_app import celery_app
from storage.pipeline_failure_record import pipeline_failure_retriever
from storage.logs import log_obj
from api.schemas.status_schema import StatusData

from analyzer.connectors.pipeline_factory import PipelineFactory
from analyzer.extractors.log_analyzer import log_analyzer_obj
from analyzer.embedding.embedding_service import embedding_obj
from analyzer.classifiers.classification_orchestrator import ClassificationOrchestrator
from analyzer.rca_engine.rca_engine import rca_obj
from analyzer.deduplicator.smart_deduplicator import dedup_obj
from storage.failure_knowledge_record import knowledge_store
from utils.execute_notifier import execute_notify

logger = logging.getLogger(__name__)
@celery_app.task(
    bind=True,
    name="normalize_failure",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 10},
    retry_backoff=True,
)
def normalize_failure(self, failure_id: str) -> None:
    """
    Celery task: fetch stage-wise CI logs and persist them to the filesystem.

    Reads the failure record from the database, determines the CI platform
    (Jenkins or GitHub), fetches logs via :class:`PipelineFactory`, writes
    each stage log to ``storage/logs/<failure_id>/<stage>.log`` and updates
    the failure status to ``LOGS_COLLECTED``.

    Args:
        failure_id: UUID that uniquely identifies the failure record.
    """
    try:
        record = pipeline_failure_retriever.get_data_by_failure_id(failure_id)
        if not record:
            return

        if record["status"] != StatusData.RECEIVED:
            return

        log_fetch_payload = {**record["payload_data"], "failure_id": failure_id}

        try:
            stagewise_logs = PipelineFactory.get_stagewise_logs(log_fetch_payload)
        except Exception as e:
            logger.error("Error fetching logs for failure_id=%s: %s", failure_id, e)
            raise

        for stage, console_log in stagewise_logs.items():
            log_obj.write_stage_log(failure_id, stage, console_log)

        pipeline_failure_retriever.update_failure_status(
            failure_id=failure_id, status=StatusData.LOGS_COLLECTED
        )

    except Exception as exc:
        pipeline_failure_retriever.update_failure_status(
            failure_id=failure_id, status=StatusData.FAILED
        )
        raise self.retry(exc=exc)

@celery_app.task(
    bind=True,
    name="classify_failure",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 10},
    retry_backoff=True,
)
def classify_failure(self, failure_id: str) -> None:
    """
    Celery task: extract signals from logs, deduplicate, and classify them.

    Pipeline:
    1. Extract :class:`LogSignal` objects from raw log files.
    2. Deduplicate using HDBSCAN semantic clustering.
    3. Look up each signal in the pgvector knowledge store.
    4. For signals not found in the cache, run the fused
       Regex + Semantic + LLM :class:`ClassificationOrchestrator`.
    5. Persist classified signals and embeddings to the filesystem.
    6. Update failure status to ``CLASSIFIED`` or ``RESOLVED``.

    Args:
        failure_id: UUID that uniquely identifies the failure record.
    """
    try:
        embeddings_dict = {}
        rca_classified = []
        unclassified = []
        unclassified_emb = []

        record = pipeline_failure_retriever.get_data_by_failure_id(failure_id)
        if not record:
            return
        if record["status"] != StatusData.LOGS_COLLECTED:
            return

        signals = log_analyzer_obj.extract_signals(failure_id=failure_id)
        deduplicated_signals, embeddings = dedup_obj.deduplicate(signals=signals)

        for idx, signal in enumerate(deduplicated_signals):
            result = knowledge_store.similar_search(
                embedding=embeddings[idx],
                threshold=0.92,
            )
            if result:
                rca_classified.append(result)
            else:
                unclassified.append(signal)
                unclassified_emb.append(embeddings[idx])

        logger.info(
            "failure_id=%s — dedup: %d, cache hits: %d, needs classification: %d.",
            failure_id,
            len(deduplicated_signals),
            len(rca_classified),
            len(unclassified),
        )

        if rca_classified:
            log_obj.write_root_cause_analysis(
                failure_id=failure_id,
                root_cause_signal=rca_classified,
            )

        if not unclassified and not rca_classified:
            logger.info("failure_id=%s — no signals found after deduplication.", failure_id)
            return

        if not unclassified and rca_classified:
            pipeline_failure_retriever.update_failure_status(
                failure_id=failure_id,
                status=StatusData.RESOLVED,
            )
            logger.info("failure_id=%s — all signals resolved from knowledge store cache.", failure_id)
            return

        for idx, signal in enumerate(unclassified):
            embeddings_dict[signal.fingerprint] = unclassified_emb[idx].tolist()

        orchestrator = ClassificationOrchestrator.get_instance()
        classified = orchestrator.classify(
            signals=unclassified,
            embeddings=np.vstack(unclassified_emb),
        )

        log_obj.write_classified_log(
            failure_id=failure_id,
            classified_signal=classified,
        )
        log_obj.write_embeddings(
            failure_id=failure_id,
            embeddings_dict=embeddings_dict,
        )

        pipeline_failure_retriever.update_failure_status(
            failure_id=failure_id,
            status=StatusData.CLASSIFIED,
        )
        logger.info("failure_id=%s — classification complete.", failure_id)

    except Exception as exc:
        import traceback
        traceback.print_exc()
        pipeline_failure_retriever.update_failure_status(
            failure_id=failure_id,
            status=StatusData.FAILED,
        )
        raise self.retry(exc=exc)

@celery_app.task(
    bind=True,
    name="analyze_failure",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 10},
    retry_backoff=True,
)
def analyze_failure(self, failure_id: str, payload: dict) -> None:
    """
    Celery task: run LLM-based root-cause analysis and send the incident report.

    If the failure was already fully resolved via the knowledge-store cache
    (status ``RESOLVED``) then only the notification step is executed.  For
    ``CLASSIFIED`` failures the task runs structured RCA via
    :class:`RCAEngine`, stores each pattern in the pgvector knowledge base,
    writes ``root_cause.json``, generates the HTML report and sends it by
    email.

    Args:
        failure_id: UUID that uniquely identifies the failure record.
        payload:    Original ingest request body as a plain dict, used to
                    extract branch, job name, build number and mail recipients.
    """
    try:
        record = pipeline_failure_retriever.get_data_by_failure_id(failure_id)
        if not record:
            return

        if record["status"] == StatusData.RESOLVED:
            logger.info("failure_id=%s — already resolved, dispatching notification only.", failure_id)
            execute_notify.execute_notifier(
                failure_id=failure_id,
                branch_name=payload.get("branch"),
                job_name=payload.get("job_name") or payload.get("repo"),
                build_number=payload.get("build_number") or payload.get("run_id"),
            )
            return

        if record["status"] not in (StatusData.CLASSIFIED,):
            logger.warning(
                "failure_id=%s — unexpected status '%s', skipping analysis.",
                failure_id,
                record["status"],
            )
            return

        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.ANALYZING)

        rca_signals = rca_obj.run_rca_for_signals(failure_id=failure_id)
        for rca in rca_signals:
            embedding = log_obj.get_embedding_for_signal(
                failure_id=failure_id, fingerprint=rca.fingerprint
            )
            knowledge_store.insert_pattern(rca, embedding=embedding)

        log_obj.write_root_cause_analysis(
            failure_id=failure_id, root_cause_signal=rca_signals
        )
        execute_notify.execute_notifier(
            failure_id=failure_id,
            branch_name=payload.get("branch"),
            job_name=payload.get("job_name") or payload.get("repo"),
            build_number=payload.get("build_number") or payload.get("run_id"),
            mail_recipient=payload.get("mailRecipient"),
        )

        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.RESOLVED)
        logger.info("failure_id=%s — RCA complete.", failure_id)

    except Exception as exc:
        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.FAILED)
        raise self.retry(exc=exc)
