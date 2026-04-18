import logging
from typing import Dict, Optional

from analyzer.notifier.generate_report import generate_report
from analyzer.notifier.mail_notifier import send_root_cause_mail

logger = logging.getLogger(__name__)


class ExecuteNotifier:
    """Orchestrates HTML report generation and email notification."""

    @staticmethod
    def execute_notifier(
        failure_id: str,
        branch_name: str,
        job_name: str,
        build_number: int,
        mail_recipient: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Generate the HTML incident report and dispatch it by email.

        This is the single entry point called by the ``analyze_failure`` Celery
        task after RCA is complete.  Report generation failures are logged and
        re-raised; email failures are logged and swallowed so a missing SMTP
        configuration never prevents the pipeline from reaching RESOLVED.

        Args:
            failure_id:     UUID identifying the failure record and its artefacts.
            branch_name:    Git branch on which the failure occurred.
            job_name:       CI job or repository name for the report header.
            build_number:   Build / run number for the report header.
            mail_recipient: Optional role-to-address mapping.  When *None* the
                            notifier falls back to ``settings.DEFAULT_MAIL``.
        """
        generate_report(
            failure_id=failure_id,
            branch_name=branch_name,
            job_name=job_name,
            build_number=build_number,
        )
        if mail_recipient:
            send_root_cause_mail(
                failure_id=failure_id,
                mail_recipient=mail_recipient,
            )
        else:
            logger.warning(
                "failure_id=%s — no mail_recipient provided; skipping email.",
                failure_id,
            )


execute_notify = ExecuteNotifier()