import logging

from celery import Celery
from celery.signals import task_failure
from api.app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "agentic_ai_worker",
    broker=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0",
    backend=f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/1",
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@task_failure.connect
def on_task_failure(
    sender=None,
    task_id: str = None,
    exception: Exception = None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **kw,
) -> None:
    """
    Celery signal handler invoked whenever a task raises an unhandled exception.

    Logs the task name, ID, exception and full traceback at ERROR level so the
    failure is captured by any configured log handler without relying on print.

    Args:
        sender:    The task class that failed.
        task_id:   Celery task UUID string.
        exception: The exception instance that caused the failure.
        args:      Positional arguments the task was called with.
        kwargs:    Keyword arguments the task was called with.
        traceback: Python traceback object.
        einfo:     Celery ``ExceptionInfo`` wrapper containing the formatted
                   traceback string.
    """
    task_name = sender.name if sender else "unknown"
    logger.error(
        "Task failed | name=%s | task_id=%s | exception=%r | args=%s | kwargs=%s",
        task_name,
        task_id,
        exception,
        args,
        kwargs,
    )
    if einfo:
        logger.error("Full traceback for task %s:\n%s", task_name, einfo)


import workers.tasks  # noqa: E402 — registers tasks with the app