import requests
import zipfile
import io
from collections import defaultdict
from typing import Dict, List, Optional
from api.schemas.status_schema import JobFailureStatus

class GitHubClient:
    """
    GitHub API client for fetching workflow run logs and actions.
    Mirrors JenkinsClient interface to provide stagewise logs.
    """

    def __init__(self, config):
        """
        Initialize GitHub client.
        
        Args:
            config: GitHubConfig object with base_url and token
        """
        self.base_url = str(config.base_url).rstrip("/")
        self.token = config.token
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }

    def get_workflow_runs(
        self,
        owner: str,
        repo: str,
        branch: Optional[str] = None,
        commit: Optional[str] = None,
        limit: int = 1
    ) -> List[Dict]:
        """
        Fetch workflow runs for a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            branch: Filter by branch name
            commit: Filter by commit SHA
            limit: Maximum number of runs to return
            
        Returns:
            List of workflow run objects
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs"
        params = {"per_page": limit}
        
        if branch:
            params["branch"] = branch
        
        resp = requests.get(url, headers=self.headers, params=params, timeout=10)
        resp.raise_for_status()
        
        runs = resp.json().get("workflow_runs", [])
        
        # Filter by commit if provided
        if commit:
            runs = [r for r in runs if r["head_commit"]["id"].startswith(commit)]
        
        return runs

    def get_nodes(
        self,
        owner: str,
        repo: str,
        run_id: int
    ) -> List[Dict]:
        """
        Get workflow jobs (nodes/stages) for a run.
        Mirrors JenkinsClient.get_nodes() structure.
        
        Args:
            owner: Repository owner
            repo: Repository name
            run_id: Workflow run ID
            
        Returns:
            List of job objects with id, displayName, result, etc.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        
        jobs = resp.json().get("jobs", [])
        
        # Transform GitHub jobs to match Jenkins stage structure
        transformed = []
        for job in jobs:
            transformed.append({
                "id": str(job["id"]),
                "displayName": job["name"],
                "result": self._normalize_github_status(job["conclusion"]),
                "status": job["status"]
            })
        
        return transformed

    def get_steps(
        self,
        owner: str,
        repo: str,
        run_id: int,
        job_id: str
    ) -> List[Dict]:
        """
        Get action steps within a job.
        Mirrors JenkinsClient.get_steps() structure.
        
        Args:
            owner: Repository owner
            repo: Repository name
            run_id: Workflow run ID
            job_id: Job ID
            
        Returns:
            List of step objects with id, name, conclusion, etc.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}"
        resp = requests.get(url, headers=self.headers, timeout=10)
        resp.raise_for_status()
        
        job_data = resp.json()
        steps = job_data.get("steps", [])
        
        # Transform GitHub steps to match Jenkins structure
        transformed = []
        for step in steps:
            transformed.append({
                "id": str(step["number"]),
                "name": step["name"],
                "conclusion": step["conclusion"],
                "status": step["status"]
            })
        
        return transformed

    def get_step_log(
        self,
        owner: str,
        repo: str,
        run_id: int,
        job_id: str,
        step_id: str
    ) -> str:
        """
        Get logs for a specific step.
        Mirrors JenkinsClient.get_step_log() interface.
        
        For GitHub, we need to fetch job logs and extract step output.
        
        Args:
            owner: Repository owner
            repo: Repository name
            run_id: Workflow run ID
            job_id: Job ID
            step_id: Step number
            
        Returns:
            Log content as string
        """
        # GitHub doesn't have step-level log endpoints
        # We fetch job logs (if available) or use step conclusion
        # This is a simplified implementation
        
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        try:
            resp = requests.get(url, headers=self.headers, timeout=10, stream=True)
            resp.raise_for_status()
            
            # Logs might be compressed
            content = resp.content
            try:
                # Try to decompress if gzipped
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    logs = []
                    for name in zf.namelist():
                        logs.append(zf.read(name).decode("utf-8", errors="ignore"))
                    return "\n".join(logs)
            except:
                # Return as-is if not compressed
                return content.decode("utf-8", errors="ignore")
        except requests.exceptions.HTTPError as e:
            # If logs endpoint fails, return placeholder
            return f"[Log unavailable for step {step_id}: {str(e)}]"

    def get_stagewise_logs(
        self,
        owner: str,
        repo: str,
        run_id: int
    ) -> Dict[str, str]:
        """
        Fetch all logs organized by job (stage).
        Mirrors normalize_failure() workflow from tasks.py.
        
        Args:
            owner: Repository owner
            repo: Repository name
            run_id: Workflow run ID
            
        Returns:
            Dictionary mapping stage names to concatenated logs
        """
        stagewise_log = defaultdict(list)
        
        try:
            nodes = self.get_nodes(owner, repo, run_id)
            
            for node in nodes:
                # Skip successful jobs
                if node["result"] in (JobFailureStatus.SUCCESS, JobFailureStatus.NOT_BUILT):
                    continue
                
                job_id = node["id"]
                stage_name = node["displayName"]
                
                try:
                    steps = self.get_steps(owner, repo, run_id, job_id)
                    
                    for step in steps:
                        step_id = step["id"]
                        try:
                            log = self.get_step_log(owner, repo, run_id, job_id, step_id)
                            stagewise_log[stage_name].append(log)
                        except Exception as e:
                            stagewise_log[stage_name].append(
                                f"[Failed to fetch step {step_id} log: {str(e)}]\n"
                            )
                except Exception as e:
                    stagewise_log[stage_name].append(
                        f"[Failed to fetch steps for job {job_id}: {str(e)}]\n"
                    )
        except Exception as e:
            return {"error": f"Failed to fetch workflow logs: {str(e)}"}
        
        # Join logs per stage
        return {
            stage: "".join(logs)
            for stage, logs in stagewise_log.items()
        }

    @staticmethod
    def _normalize_github_status(conclusion: Optional[str]) -> str:
        """
        Convert GitHub conclusion to Jenkins-like status.
        
        Args:
            conclusion: GitHub action conclusion (success, failure, skipped, etc.)
            
        Returns:
            Normalized status string
        """
        if not conclusion:
            return JobFailureStatus.NOT_BUILT
        
        status_map = {
            "success": JobFailureStatus.SUCCESS,
            "failure": JobFailureStatus.FAILURE,
            "skipped": JobFailureStatus.NOT_BUILT,
            "cancelled": JobFailureStatus.ABORTED,
            "timed_out": JobFailureStatus.FAILURE,
            "action_required": JobFailureStatus.UNSTABLE
        }
        
        return status_map.get(conclusion.lower(), JobFailureStatus.UNSTABLE)