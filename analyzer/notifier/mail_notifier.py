import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List
from api.app.config import settings
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_mail_recipients(
    failure_id: str,
    mail_recipient: Dict[str, str],
) -> List[str]:
    """
    Determine the email recipients for a failure incident report.

    Reads the ``root_cause.json`` produced by the RCA step, maps each owner
    team to the corresponding address supplied in *mail_recipient*, and returns
    the unique set of resolved addresses.  Falls back to
    ``settings.DEFAULT_MAIL`` when no owner can be resolved.

    Args:
        failure_id:     UUID of the failure whose RCA file should be read.
        mail_recipient: Mapping of role keys (``dev_email``, ``test_email``,
                        ``ci_email``) to actual email addresses.

    Returns:
        List of unique recipient email addresses.

    Raises:
        FileNotFoundError: If ``root_cause.json`` does not exist for the
                           given *failure_id*.
        json.JSONDecodeError: If the RCA file contains malformed JSON.
    """
    mail_recipient_path = Path(settings.LOG_PATH) / failure_id / 'root_cause.json'
    with open(mail_recipient_path, 'r', encoding='utf-8') as f:
        result = json.load(f)

    OWNER_EMAIL_MAP: Dict[str, str] = {
        "DEVOPS_ENGINEERS": mail_recipient.get("ci_email", ""),
        "DEVELOPERS":       mail_recipient.get("dev_email", ""),
        "TEST_ENGINEERS":   mail_recipient.get("test_email", ""),
    }
    owner_items = {item.get("owner") for item in result if item.get("owner")}
    recipients: set = set()
    for owner in owner_items:
        email = OWNER_EMAIL_MAP.get(owner, "")
        if email:
            recipients.add(email)
    if not recipients:
        return list(settings.DEFAULT_MAIL)
    return list(recipients)


def send_root_cause_mail(
    failure_id: str,
    mail_recipient: Dict[str, str],
) -> None:
    """
    Compose and send the HTML RCA incident report by email.

    Reads the rendered ``rca_report.html`` for *failure_id*, constructs a
    multipart MIME message and dispatches it via the configured SMTP server
    with STARTTLS.  Recipient addresses are resolved by
    :func:`resolve_mail_recipients`.

    Args:
        failure_id:     UUID of the failure whose report should be emailed.
        mail_recipient: Role-to-address mapping forwarded to
                        :func:`resolve_mail_recipients`.

    Raises:
        FileNotFoundError: If ``rca_report.html`` does not exist.
        smtplib.SMTPException: Logged at ERROR level; not re-raised so that a
                               notification failure never blocks the pipeline.
    """
    SMTP_SERVER = settings.SMTP_SERVER
    SMTP_PORT   = settings.SMTP_PORT
    FROM_EMAIL  = settings.SMTP_USER
    TO_EMAILS   = resolve_mail_recipients(
        failure_id=failure_id, mail_recipient=mail_recipient
    )

    msg = MIMEMultipart()
    msg['From']    = FROM_EMAIL
    msg['To']      = ", ".join(TO_EMAILS)
    msg['Subject'] = "Root cause analysis - Incident Report"

    report_path = Path(settings.LOG_PATH) / failure_id / 'rca_report.html'
    with open(report_path, 'r', encoding='utf-8') as f:
        html_body = f.read()
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())
        logger.info(
            "RCA report email sent for failure_id=%s to %s.",
            failure_id,
            TO_EMAILS,
        )
    except smtplib.SMTPException as exc:
        logger.error(
            "Failed to send RCA email for failure_id=%s: %s",
            failure_id,
            exc,
        )

