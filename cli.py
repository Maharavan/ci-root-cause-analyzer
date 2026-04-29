#!/usr/bin/env python3
"""
CLI for CI Root Cause Analyzer.

Runs the full analysis pipeline (extract → deduplicate → classify → RCA → report)
synchronously, without Celery or Redis. Supports three modes:

  analyze jenkins  — fetch logs from Jenkins, run full pipeline
  analyze github   — fetch logs from GitHub Actions, run full pipeline
  analyze logs     — analyze local .log files directly (no CI connection needed)
"""

import uuid
import shutil
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="rca",
    help="Analyze CI/CD failures from Jenkins, GitHub Actions, or local log files.",
    no_args_is_help=True,
)
analyze_app = typer.Typer(help="Run root cause analysis.", no_args_is_help=True)
app.add_typer(analyze_app, name="analyze")

console = Console()


# ── Internal pipeline helpers ──────────────────────────────────────────────────

def _classify(failure_id: str, use_db: bool) -> Optional[bool]:
    """
    Extract signals, deduplicate, optionally check the pgvector knowledge store,
    and classify any unknowns.

    Returns:
        None  — no signals found in logs
        False — all signals resolved from the knowledge-store cache
                (root_cause.json already written, RCA not needed)
        True  — signals freshly classified and written to error.json/embeddings.json
                (RCA step should follow)
    """
    import numpy as np
    from analyzer.extractors.log_analyzer import log_analyzer_obj
    from analyzer.deduplicator.smart_deduplicator import dedup_obj
    from analyzer.classifiers.classification_orchestrator import ClassificationOrchestrator
    from storage.logs import log_obj

    with console.status("[bold cyan]Extracting log signals..."):
        signals = log_analyzer_obj.extract_signals(failure_id=failure_id)

    if not signals:
        console.print("[yellow]No signals found in logs.[/yellow]")
        return None

    console.print(f"[green]✓[/green] Extracted {len(signals)} signal(s)")

    with console.status("[bold cyan]Deduplicating signals (HDBSCAN)..."):
        deduped, embeddings = dedup_obj.deduplicate(signals=signals)

    console.print(f"[green]✓[/green] Deduplicated → {len(deduped)} unique signal(s)")

    rca_from_cache = []
    unclassified = []
    unclassified_emb = []

    if use_db:
        from storage.failure_knowledge_record import knowledge_store
        for idx, signal in enumerate(deduped):
            hit = knowledge_store.similar_search(embedding=embeddings[idx], threshold=0.92)
            if hit:
                rca_from_cache.append(hit)
            else:
                unclassified.append(signal)
                unclassified_emb.append(embeddings[idx])
        if rca_from_cache:
            console.print(f"[green]✓[/green] {len(rca_from_cache)} signal(s) resolved from knowledge store cache")
    else:
        unclassified = list(deduped)
        unclassified_emb = [embeddings[i] for i in range(len(deduped))]

    if not unclassified:
        # All signals resolved from cache — write cached RCA directly
        log_obj.write_root_cause_analysis(failure_id=failure_id, root_cause_signal=rca_from_cache)
        console.print("[green]All signals resolved from cache — skipping classification.[/green]")
        return False

    embeddings_dict = {
        signal.fingerprint: unclassified_emb[i].tolist()
        for i, signal in enumerate(unclassified)
    }

    with console.status("[bold cyan]Classifying signals (regex + semantic + LLM)..."):
        orchestrator = ClassificationOrchestrator.get_instance()
        classified = orchestrator.classify(
            signals=unclassified,
            embeddings=np.vstack(unclassified_emb),
        )

    console.print(f"[green]✓[/green] Classified {len(classified)} signal(s)")
    log_obj.write_classified_log(failure_id=failure_id, classified_signal=classified)
    log_obj.write_embeddings(failure_id=failure_id, embeddings_dict=embeddings_dict)

    _print_classified_table(classified)
    return True


def _rca(failure_id: str, use_db: bool) -> None:
    """Run LLM root cause analysis on classified signals, optionally store patterns."""
    from analyzer.rca_engine.rca_engine import rca_obj
    from storage.logs import log_obj

    with console.status("[bold cyan]Running LLM root cause analysis..."):
        rca_signals = rca_obj.run_rca_for_signals(failure_id=failure_id)

    log_obj.write_root_cause_analysis(failure_id=failure_id, root_cause_signal=rca_signals)
    console.print(f"[green]✓[/green] RCA complete — {len(rca_signals)} result(s)")

    if use_db and rca_signals:
        from storage.failure_knowledge_record import knowledge_store
        for rca in rca_signals:
            embedding = log_obj.get_embedding_for_signal(
                failure_id=failure_id, fingerprint=rca.fingerprint
            )
            knowledge_store.insert_pattern(rca, embedding=embedding)
        console.print("[dim]Patterns stored in knowledge base[/dim]")

    _print_rca_table(rca_signals)


def _notify(
    failure_id: str,
    branch: str,
    job_name: str,
    build_number: int,
    mail_recipient: Optional[dict],
) -> None:
    from utils.execute_notifier import execute_notify
    with console.status("[bold cyan]Generating HTML report..."):
        execute_notify.execute_notifier(
            failure_id=failure_id,
            branch_name=branch,
            job_name=job_name,
            build_number=build_number,
            mail_recipient=mail_recipient,
        )
    suffix = " and email sent" if mail_recipient else " (no email — pass --dev-email/--test-email/--ci-email to notify)"
    console.print(f"[green]✓[/green] Report generated{suffix}")


def _build_mail_recipient(
    dev_email: Optional[str],
    test_email: Optional[str],
    ci_email: Optional[str],
) -> Optional[dict]:
    recipient = {}
    if dev_email:
        recipient["dev_email"] = dev_email
    if test_email:
        recipient["test_email"] = test_email
    if ci_email:
        recipient["ci_email"] = ci_email
    return recipient or None


def _print_output_paths(failure_id: str) -> None:
    base = Path("storage") / "logs" / failure_id
    artifacts = [f for f in ("error.json", "root_cause.json", "rca_report.html") if (base / f).exists()]
    if artifacts:
        console.print(f"\n[bold]Artifacts:[/bold] {base}")
        for name in artifacts:
            console.print(f"  [dim]•[/dim] {name}")


def _print_classified_table(classified) -> None:
    table = Table(title="Classified Signals", show_lines=True, expand=False)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("Conf", justify="right")
    table.add_column("Owner", style="yellow")
    table.add_column("Error Line")
    for c in classified:
        conf = c.classified_confidence
        color = "green" if conf >= 0.7 else ("yellow" if conf >= 0.4 else "red")
        table.add_row(
            c.signal.signal_type.value,
            c.best_category.value,
            f"[{color}]{conf:.2f}[/{color}]",
            c.owner_team.value,
            (c.signal.error_line or "")[:80],
        )
    console.print(table)


def _print_rca_table(rca_results) -> None:
    sev_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "orange3", "CRITICAL": "red"}
    table = Table(title="Root Cause Analysis", show_lines=True, expand=False)
    table.add_column("Category", style="magenta")
    table.add_column("Severity", justify="center")
    table.add_column("Conf", justify="right")
    table.add_column("Owner", style="yellow")
    table.add_column("Root Cause")
    for rca in rca_results:
        color = sev_color.get(rca.severity, "white")
        table.add_row(
            rca.validated_category.value,
            f"[{color}]{rca.severity}[/{color}]",
            f"{rca.rca_confidence:.2f}",
            rca.owner.value,
            rca.root_cause[:120],
        )
    console.print(table)


# ── Commands ───────────────────────────────────────────────────────────────────

@analyze_app.command("jenkins")
def analyze_jenkins(
    job_name: str = typer.Option(..., prompt=True, help="Jenkins job name"),
    build_number: int = typer.Option(..., prompt=True, help="Jenkins build number"),
    commit: str = typer.Option(..., prompt=True, help="Commit SHA"),
    branch: str = typer.Option(..., prompt=True, help="Branch name"),
    dev_email: Optional[str] = typer.Option(None, help="Developer team recipient email"),
    test_email: Optional[str] = typer.Option(None, help="Test engineer recipient email"),
    ci_email: Optional[str] = typer.Option(None, help="DevOps/CI team recipient email"),
    use_db: bool = typer.Option(
        False,
        "--use-db",
        help="Persist records and use pgvector knowledge store (requires PostgreSQL).",
    ),
) -> None:
    """Fetch logs from Jenkins and run the full analysis pipeline."""
    from dotenv import load_dotenv
    load_dotenv()

    from api.schemas.ingest_schema import JenkinsFailureIngestRequest, MailRecipient
    from analyzer.connectors.pipeline_factory import PipelineFactory
    from storage.logs import log_obj

    failure_id = str(uuid.uuid4())
    console.print(f"\n[bold]Failure ID:[/bold] {failure_id}")

    payload = JenkinsFailureIngestRequest(
        job_name=job_name,
        build_number=build_number,
        commit=commit,
        branch=branch,
        mailRecipient=MailRecipient(dev_email=dev_email, test_email=test_email, ci_email=ci_email),
    )

    if use_db:
        from storage.pipeline_failure_record import pipeline_failure_retriever
        from api.schemas.status_schema import StatusData
        with console.status("[bold cyan]Inserting failure record..."):
            pipeline_failure_retriever.insert_failure_values(failure_id, payload)

    with console.status(f"[bold cyan]Fetching logs from Jenkins ({job_name} #{build_number})..."):
        stage_logs = PipelineFactory.get_stagewise_logs({**payload.model_dump(), "failure_id": failure_id})

    for stage, content in stage_logs.items():
        log_obj.write_stage_log(failure_id, stage, content)
    if use_db:
        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.LOGS_COLLECTED)
    console.print(f"[green]✓[/green] Fetched {len(stage_logs)} stage(s): {', '.join(stage_logs)}")

    result = _classify(failure_id, use_db=use_db)

    if result is None:
        if use_db:
            pipeline_failure_retriever.update_failure_status(failure_id, StatusData.FAILED)
        return

    if result is True:
        if use_db:
            pipeline_failure_retriever.update_failure_status(failure_id, StatusData.CLASSIFIED)
        _rca(failure_id, use_db=use_db)

    mail_recipient = _build_mail_recipient(dev_email, test_email, ci_email)
    _notify(failure_id, branch, job_name, build_number, mail_recipient)
    if use_db:
        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.RESOLVED)
    _print_output_paths(failure_id)


@analyze_app.command("github")
def analyze_github(
    owner: str = typer.Option(..., prompt=True, help="Repository owner or org"),
    repo: str = typer.Option(..., prompt=True, help="Repository name"),
    run_id: int = typer.Option(..., prompt=True, help="GitHub Actions workflow run ID"),
    commit: str = typer.Option(..., prompt=True, help="Commit SHA"),
    branch: str = typer.Option(..., prompt=True, help="Branch name"),
    dev_email: Optional[str] = typer.Option(None, help="Developer team recipient email"),
    test_email: Optional[str] = typer.Option(None, help="Test engineer recipient email"),
    ci_email: Optional[str] = typer.Option(None, help="DevOps/CI team recipient email"),
    use_db: bool = typer.Option(
        False,
        "--use-db",
        help="Persist records and use pgvector knowledge store (requires PostgreSQL).",
    ),
) -> None:
    """Fetch logs from GitHub Actions and run the full analysis pipeline."""
    from dotenv import load_dotenv
    load_dotenv()

    from api.schemas.ingest_schema import GithubFailureIngestRequest, MailRecipient
    from analyzer.connectors.pipeline_factory import PipelineFactory
    from storage.logs import log_obj

    failure_id = str(uuid.uuid4())
    console.print(f"\n[bold]Failure ID:[/bold] {failure_id}")

    payload = GithubFailureIngestRequest(
        owner=owner,
        repo=repo,
        run_id=run_id,
        commit=commit,
        branch=branch,
        mailRecipient=MailRecipient(dev_email=dev_email, test_email=test_email, ci_email=ci_email),
    )

    if use_db:
        from storage.pipeline_failure_record import pipeline_failure_retriever
        from api.schemas.status_schema import StatusData
        with console.status("[bold cyan]Inserting failure record..."):
            pipeline_failure_retriever.insert_failure_values(failure_id, payload)

    with console.status(f"[bold cyan]Fetching logs from GitHub ({owner}/{repo} run #{run_id})..."):
        stage_logs = PipelineFactory.get_stagewise_logs({**payload.model_dump(), "failure_id": failure_id})

    for stage, content in stage_logs.items():
        log_obj.write_stage_log(failure_id, stage, content)
    if use_db:
        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.LOGS_COLLECTED)
    console.print(f"[green]✓[/green] Fetched {len(stage_logs)} stage(s): {', '.join(stage_logs)}")

    result = _classify(failure_id, use_db=use_db)

    if result is None:
        if use_db:
            pipeline_failure_retriever.update_failure_status(failure_id, StatusData.FAILED)
        return

    if result is True:
        if use_db:
            pipeline_failure_retriever.update_failure_status(failure_id, StatusData.CLASSIFIED)
        _rca(failure_id, use_db=use_db)

    mail_recipient = _build_mail_recipient(dev_email, test_email, ci_email)
    _notify(failure_id, branch, repo, run_id, mail_recipient)
    if use_db:
        pipeline_failure_retriever.update_failure_status(failure_id, StatusData.RESOLVED)
    _print_output_paths(failure_id)


@analyze_app.command("logs")
def analyze_logs(
    logs_dir: Path = typer.Argument(..., help="Directory containing .log files to analyze"),
    branch: str = typer.Option("local", show_default=True, help="Branch name (metadata only)"),
    job_name: str = typer.Option("local-analysis", show_default=True, help="Job name (metadata only)"),
    dev_email: Optional[str] = typer.Option(None, help="Developer team recipient email"),
    test_email: Optional[str] = typer.Option(None, help="Test engineer recipient email"),
    ci_email: Optional[str] = typer.Option(None, help="DevOps/CI team recipient email"),
    use_db: bool = typer.Option(
        False,
        "--use-db",
        help="Enable pgvector knowledge-store lookup and pattern storage (requires PostgreSQL).",
    ),
) -> None:
    """
    Analyze local .log files directly — no Jenkins or GitHub connection required.

    Copies .log files from LOGS_DIR into the standard storage layout, then runs
    signal extraction → deduplication → classification → RCA.
    """
    from dotenv import load_dotenv
    load_dotenv()

    logs_dir = logs_dir.resolve()
    if not logs_dir.is_dir():
        console.print(f"[red]Error:[/red] '{logs_dir}' is not a valid directory.")
        raise typer.Exit(1)

    log_files = list(logs_dir.glob("*.log"))
    if not log_files:
        console.print(f"[red]Error:[/red] No .log files found in '{logs_dir}'.")
        raise typer.Exit(1)

    failure_id = str(uuid.uuid4())
    console.print(f"\n[bold]Failure ID:[/bold] {failure_id}")
    console.print(f"[bold]Source:[/bold]     {logs_dir} ({len(log_files)} file(s))")

    dest = Path("storage") / "logs" / failure_id
    dest.mkdir(parents=True, exist_ok=True)
    for f in log_files:
        shutil.copy2(f, dest / f.name)
    console.print(f"[green]✓[/green] Staged logs → {dest}")

    result = _classify(failure_id, use_db=use_db)

    if result is None:
        return

    if result is True:
        _rca(failure_id, use_db=use_db)

    mail_recipient = _build_mail_recipient(dev_email, test_email, ci_email)
    _notify(failure_id, branch, job_name, 0, mail_recipient)
    _print_output_paths(failure_id)


if __name__ == "__main__":
    app()
