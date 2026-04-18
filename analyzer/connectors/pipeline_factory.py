"""
Pipeline Factory - Routes to appropriate CI/CD client based on payload.
Provides a unified interface for accessing logs from both Jenkins and GitHub.
"""
import logging
from typing import Union, Dict, Any

logger = logging.getLogger(__name__)
from api.schemas.ingest_schema import (
    JenkinsFailureIngestRequest,
    GithubFailureIngestRequest,
)
from analyzer.connectors.jenkins_client import JenkinsClient
from analyzer.connectors.github_client import GitHubClient
from api.app.config import JENKINS_SERVER, GITHUB_SERVER


class PipelineFactory:
    """
    Factory for fetching stagewise logs from any CI/CD platform.
    Provides a unified interface for both Jenkins and GitHub.
    """

    @staticmethod
    def get_stagewise_logs(
        record_or_payload: Union[Dict[str, Any], object]
    ) -> Dict[str, str]:
        """Fetch stagewise logs from the correct CI/CD platform.

        Platform detection is performed by inspecting the payload fields:

        * If **``owner``** and **``repo``** are present → GitHub Actions
          (delegates to :class:`~analyzer.connectors.github_client.GitHubClient`).
        * If **``job_name``** is present → Jenkins
          (delegates to :class:`~analyzer.connectors.jenkins_client.JenkinsClient`).

        Args:
            record_or_payload: A failure record object or plain dict containing
                platform-specific fields.  GitHub requires ``owner``, ``repo``,
                and ``run_id`` (or ``build_number`` as alias).  Jenkins requires
                ``job_name`` and ``build_number``.

        Returns:
            Dictionary mapping stage or job names to their concatenated log
            content.

        Raises:
            ValueError: If the platform cannot be determined or required fields
                are absent.
        """
        # Normalize to dict-like access
        if isinstance(record_or_payload, dict):
            data = record_or_payload
        else:
            data = {
                k: getattr(record_or_payload, k, None)
                for k in [
                    "job_name", "build_number", "repo", "owner", "run_id",
                    "commit", "branch"
                ]
            }
        if data.get("owner") and data.get("repo"):
            # GitHub: requires owner, repo, run_id (or build_number as alias)
            run_id = data.get("run_id") or data.get("build_number")
            if not run_id:
                raise ValueError("GitHub requires 'owner', 'repo', and 'run_id'")
            
            github_client = GitHubClient(GITHUB_SERVER)
            # pylint: disable=unexpected-keyword-arg
            return github_client.get_stagewise_logs(
                owner=data["owner"],
                repo=data["repo"],
                run_id=run_id
            )
    
        elif data.get("job_name"):
            # Jenkins: requires job_name, build_number, 
            if not data.get("build_number"):
                raise ValueError("Jenkins requires 'job_name', and 'build_number'")
        
            jenkins_client = JenkinsClient(JENKINS_SERVER)
            return jenkins_client.get_stagewise_logs(
                job_name=data["job_name"],
                build_number=data["build_number"]
            )
        else:
            raise ValueError(
                "Cannot determine platform. Provide either "
                "(job_name) for Jenkins or "
                "(owner + repo) for GitHub"
            )
