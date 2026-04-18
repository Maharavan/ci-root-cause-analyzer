from typing import Union, Optional
from pydantic import BaseModel, Field

class MailRecipient(BaseModel):
    dev_email: Optional[str] = Field(None, description="Developer's email address")
    test_email: Optional[str] = Field(None, description="Test email address for notifications")
    ci_email: Optional[str] = Field(None, description="CI email address for notifications")

class FailureBase(BaseModel):
    commit: str = Field(..., description="Commit hash/ID")
    branch: str = Field(..., description="Branch name")
    mailRecipient: MailRecipient = Field(None, description="Email recipients for notifications")

class JenkinsFailureIngestRequest(FailureBase):
    job_name: str = Field(..., description="Jenkins job name")
    build_number: int = Field(..., description="Jenkins build number")

class GithubFailureIngestRequest(FailureBase):
    repo: str = Field(..., description="GitHub repository name")
    owner: str = Field(..., description="GitHub repository owner/org")
    run_id: Optional[int] = Field(None, description="GitHub workflow run ID")
    job_name: Optional[str] = Field(None, description="For compatibility with notification, alias for repo")
    build_number: Optional[int] = Field(None, description="For compatibility with notification, alias for run_id")

class JenkinsFailureIngestResponse(BaseModel):
    failure_id: str = Field(..., description="Unique failure identifier")
    data: JenkinsFailureIngestRequest
    status: str = Field(..., description="Ingestion status")

class GithubFailureIngestResponse(BaseModel):
    failure_id: str = Field(..., description="Unique failure identifier")
    data: GithubFailureIngestRequest
    status: str = Field(..., description="Ingestion status")

class FailureIngestResponse(BaseModel):
    failure_id: str = Field(..., description="Unique failure identifier")
    data: Union[JenkinsFailureIngestResponse, GithubFailureIngestResponse]
    status: str = Field(..., description="Ingestion status")