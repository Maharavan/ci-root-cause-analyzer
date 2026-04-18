from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from api.schemas.log_signal_schema import LogSignal
from api.schemas.failure_category_schema import FailureCategory


class OwnerTeam(str, Enum):
    """
    Teams that can own a CI/CD failure.
    Extend as your org grows — the mapping lives in ownership_config.py.
    """
    DEVELOPERS          = "DEVELOPERS"           
    DEVOPS_ENGINEERS    = "DEVOPS_ENGINEERS"         
    QA_ENGINEERS        = "TEST_ENGINEERS"     
    UNOWNED             = "UNOWNED"


class ClassifiedSignal(BaseModel):
    signal:        LogSignal
    best_category: FailureCategory  = FailureCategory.UNKNOWN
    classified_confidence: float  = Field(ge=0.0, le=1.0)
    owner_team:    OwnerTeam        = OwnerTeam.UNOWNED

class ClassificationResult(BaseModel):
    best_category: FailureCategory
    classified_confidence: float   = Field(ge=0.0, le=1.0)


class ClassifiedScore(int, Enum):
    HIGH   = 5
    MEDIUM = 2
    LOW    = 1
    NONE   = 0

