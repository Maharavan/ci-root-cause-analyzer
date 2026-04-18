from fastapi import APIRouter, HTTPException
import uuid
from storage.logs import log_obj
from storage.pipeline_failure_record import pipeline_failure_retriever
from workers.tasks import normalize_failure, classify_failure, analyze_failure
from api.app.config import settings
from api.schemas.status_schema import StatusData
from api.schemas.ingest_schema import (
    JenkinsFailureIngestRequest,
    GithubFailureIngestRequest,
    JenkinsFailureIngestResponse,
    GithubFailureIngestResponse
)

router = APIRouter()

@router.post('/failures/jenkins', response_model=JenkinsFailureIngestResponse)
async def jenkins_data_ingestion(payload: JenkinsFailureIngestRequest):
    """Ingest Jenkins failure data"""
    
    # Check if failure already exists
    

    failure_id = str(uuid.uuid4())
    
    # Store failure record
    pipeline_failure_retriever.insert_failure_values(failure_id=failure_id, payload=payload)
    
    # Queue tasks
    normalize_failure.delay(failure_id)
    classify_failure.delay(failure_id)
    analyze_failure.delay(failure_id, payload.model_dump())
    
    return JenkinsFailureIngestResponse(
        failure_id=failure_id,
        data=payload,
        status="Received successfully"
    )


@router.post('/failures/github', response_model=GithubFailureIngestResponse)
async def github_data_ingestion(payload: GithubFailureIngestRequest):
    """Ingest GitHub workflow failure data"""
    
    # Check if failure already exists
    # existing = pipeline_failure_retriever.check_if_failure_data_exist(payload)
    # if existing:
    #     return GithubFailureIngestResponse(
    #         failure_id=existing["failure_id"],
    #         data=payload,
    #         status="Already Received"
    #     )

    failure_id = str(uuid.uuid4())
    
    # Store failure record
    pipeline_failure_retriever.insert_failure_values(failure_id=failure_id, payload=payload)
    
    # Queue tasks
    normalize_failure.delay(failure_id)
    classify_failure.delay(failure_id)
    analyze_failure.delay(failure_id, payload.model_dump())
    
    return GithubFailureIngestResponse(
        failure_id=failure_id,
        data=payload,
        status="Received successfully"
    )