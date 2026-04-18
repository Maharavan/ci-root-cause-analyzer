import logging
import requests
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)

class JenkinsClient:
    """REST client for the Jenkins Blue Ocean API.

    Fetches pipeline stage information and step logs via the Blue Ocean
    ``/blue/rest/organizations/jenkins/`` endpoint.
    """

    def __init__(self, config):
        """Initialise the client with connection settings.

        Args:
            config: An object exposing ``base_url``, ``user``, and ``token``
                attributes (typically ``JENKINS_SERVER`` from config).
        """
        self.base_url = str(config.base_url).rstrip("/")
        self.auth = (config.user, config.token)

    def get_nodes(self, job_name: str, build_number: int) -> List[Dict]:
        """Fetch all pipeline stage nodes for a build.

        Args:
            job_name: Jenkins job name, with folder separators (``/``).
            build_number: Numeric build identifier.

        Returns:
            List of stage node objects as returned by the Blue Ocean API.

        Raises:
            requests.RequestException: If the HTTP request fails.
        """
        job_name = '/pipelines/'.join(job_name.split('/'))
        url = f"{self.base_url}/pipelines/{job_name}/runs/{build_number}/nodes"
        try:
            resp = requests.get(url, timeout=10, auth=self.auth, verify=False)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(
                "Failed to fetch nodes for job '%s' build %s: %s",
                job_name, build_number, e,
            )
            raise
        return resp.json()

    def get_steps(self, job_name: str, build_number: int, stage_id: str) -> List[Dict]:
        """Fetch all step objects for a given pipeline stage.

        Args:
            job_name: Jenkins job name.
            build_number: Numeric build identifier.
            stage_id: Blue Ocean node ID of the stage.

        Returns:
            List of step objects for the stage.

        Raises:
            requests.RequestException: If the HTTP request fails.
        """
        job_name = '/pipelines/'.join(job_name.split('/'))
        url = (
            f"{self.base_url}/pipelines/{job_name}"
            f"/runs/{build_number}/nodes/{stage_id}/steps"
        )
        try:
            resp = requests.get(url, timeout=10, auth=self.auth, verify=False)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(
                "Failed to fetch steps for job '%s' build %s stage %s: %s",
                job_name, build_number, stage_id, e,
            )
            raise
        return resp.json()

    def get_step_log(
        self,
        job_name: str,
        build_number: int,
        stage_id: str,
        step_id: str,
    ) -> str:
        """Fetch the console log for a single pipeline step.

        Args:
            job_name: Jenkins job name.
            build_number: Numeric build identifier.
            stage_id: Blue Ocean node ID of the stage.
            step_id: Blue Ocean step ID.

        Returns:
            Step log content decoded as UTF-8.

        Raises:
            requests.RequestException: If the HTTP request fails.
        """
        url = (
            f"{self.base_url}/pipelines/{job_name}"
            f"/runs/{build_number}/nodes/{stage_id}/steps/{step_id}/log/"
        )
        try:
            resp = requests.get(url, timeout=10, auth=self.auth, verify=False)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(
                "Failed to fetch log for job '%s' build %s stage %s step %s: %s",
                job_name, build_number, stage_id, step_id, e,
            )
            raise
        return resp.content.decode("utf-8", errors="ignore")

    def get_stagewise_logs(self, job_name: str, build_number: int) -> Dict[str, str]:
        """
        Fetch stagewise logs from Jenkins build.
        Aggregates logs from all stages and steps.
        
        Args:
            job_name: Jenkins job name
            build_number: Jenkins build number
            
        Returns:
            Dictionary mapping stage name to concatenated log content
        """
        from api.schemas.status_schema import JobFailureStatus
        
        stagewise_log = defaultdict(list)
        nodes = self.get_nodes(job_name, build_number)
        
        for stage in nodes:
            if stage["result"] not in (JobFailureStatus.SUCCESS, 
                                      JobFailureStatus.NOT_BUILT):
                steps = self.get_steps(job_name, build_number, stage["id"])
                
                for step in steps:
                    log = self.get_step_log(
                        job_name,
                        build_number,
                        stage["id"],
                        step["id"]
                    )
                    stagewise_log[stage["displayName"]].append(log)
        
        return {stage: ''.join(logs) for stage, logs in stagewise_log.items()}
