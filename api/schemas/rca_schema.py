from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.schemas.failure_category_schema import FailureCategory
from api.schemas.classified_schema import OwnerTeam




class DevRemediation(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    action: Literal["FIX_DEV"] = "FIX_DEV"
    strategy: Literal[
        "CLEAN_REBUILD",
        "RESOLVE_DEPENDENCY",
        "APPLY_LINT_FIX",
        "GENERATE_CODE_PATCH",
        "UPDATE_ENV_MOCK",
        "REVERT_COMMIT",
        "UPGRADE_DEPENDENCY",
    ]
    target: str

    fix_commands: list[str] = Field(default_factory=list)

    suggested_patch: Optional[str] = None
    patch_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    related_files: list[str] = Field(default_factory=list)

    notes: Optional[str] = None

    @model_validator(mode="after")
    def enforce_strategy_fields(self) -> "DevRemediation":
        if self.strategy != "GENERATE_CODE_PATCH":
            self.suggested_patch = None
            self.patch_confidence = None
        return self


class TestRemediation(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    action: Literal["FIX_TEST"] = "FIX_TEST"
    strategy: Literal[
        "MARK_FLAKY_RETRY",
        "RESET_TEST_DATA",
        "UPDATE_TEST_SNAPSHOT",
        "INCREASE_TEST_TIMEOUT",
        "SKIP_TEST_TEMPORARILY",
        "FIX_ASSERTION",
        "MOCK_EXTERNAL_SERVICE",
    ]
    target: str

    fix_commands: list[str] = Field(default_factory=list)

    retry_count: Optional[int] = Field(None, ge=1, le=5)

    skip_reason: Optional[str] = None

    related_files: list[str] = Field(default_factory=list)

    notes: Optional[str] = None

    @model_validator(mode="after")
    def enforce_strategy_fields(self) -> "TestRemediation":
        if self.strategy == "SKIP_TEST_TEMPORARILY" and not self.skip_reason:
            self.skip_reason = "Pending investigation — add ticket reference before merging."
        if self.strategy != "MARK_FLAKY_RETRY":
            self.retry_count = None
        return self


class InfraRemediation(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    action: Literal["FIX_CI_INFRA"] = "FIX_CI_INFRA"
    strategy: Literal[
        "RESTART_RESOURCE",
        "RETRY_WITH_CLEAN_CACHE",
        "SCALE_RESOURCES",
        "UPDATE_SECRET",
        "REPROVISION_RUNNER",
        "FIX_PATH_OR_PATTERN",
        "ENSURE_ARTIFACT_GENERATION",
        "UPDATE_PLUGIN_VERSION",
        "FIX_PLUGIN_CONFIGURATION",
        "VALIDATE_FILE_EXISTENCE",
        "ROTATE_CREDENTIALS",
        "FIX_NETWORK_POLICY",
    ]
    target: str

    fix_commands: list[str] = Field(default_factory=list)

    related_files: list[str] = Field(default_factory=list)

    estimated_recovery_time_seconds: Optional[int] = Field(None, ge=0)

    requires_human_approval: bool = False

    notes: Optional[str] = None


class ManualRemediation(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    action: Literal["MANUAL_INVESTIGATION"] = "MANUAL_INVESTIGATION"

    reason: str

    suggested_next_step: Optional[str] = None

    escalation_team: Optional[str] = None

    priority: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"

    investigation_links: list[str] = Field(default_factory=list)

    related_files: list[str] = Field(default_factory=list)

    notes: Optional[str] = None


RemediationAction = Union[DevRemediation, TestRemediation, InfraRemediation, ManualRemediation]


class SignalRCA(BaseModel):
    model_config = ConfigDict(extra="ignore", use_enum_values=True)

    validated_category: FailureCategory
    root_cause: str
    error_line: str
    owner: OwnerTeam

    fingerprint: str = ""

    remediation: RemediationAction = Field(..., discriminator="action")
    secondary_remediations: Optional[list[RemediationAction]] = None

    rca_confidence: float = Field(..., ge=0.0, le=1.0)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    recurrence_count: int = Field(default=0, ge=0)
    analyzed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    evidence_url: Optional[str] = None
    similarity_score: Optional[float] = Field(None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def low_confidence_suggests_manual(self) -> "SignalRCA":
        if self.rca_confidence < 0.3 and not isinstance(self.remediation, ManualRemediation):
            raise ValueError(
                "rca_confidence < 0.3 — remediation should be MANUAL_INVESTIGATION "
                "when evidence is this ambiguous."
            )
        return self