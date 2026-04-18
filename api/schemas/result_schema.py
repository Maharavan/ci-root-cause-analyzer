from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class AnalysisResult:
    summary: str
    root_cause: str
    recommendation: str
    confidence: float

class FailureResult(BaseModel):
    failure_id: str
    model_name: str
    analysis_result : AnalysisResult

class FailureResultRecord(FailureResult):
    status: str
    created_at: datetime