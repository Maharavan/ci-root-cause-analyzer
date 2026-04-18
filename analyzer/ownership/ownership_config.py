from dataclasses import dataclass
from typing import Dict
from api.schemas.classified_schema import OwnerTeam
from api.schemas.failure_category_schema import FailureCategory


@dataclass(frozen=True)
class OwnershipRule:
    """Defines the ownership rule for a failure category."""
    team:    OwnerTeam

OWNERSHIP_MAP: Dict[FailureCategory, OwnershipRule] = {
    FailureCategory.DEV_FAILURE: OwnershipRule(
        team = OwnerTeam.DEVELOPERS,
    ),
    FailureCategory.TEST_FAILURE: OwnershipRule(
        team = OwnerTeam.QA_ENGINEERS,
    ),
    FailureCategory.CI_INFRA_FAILURE: OwnershipRule(
        team = OwnerTeam.DEVOPS_ENGINEERS,
    ),
}


def resolve_owner(category: FailureCategory) -> OwnershipRule:
    """
    Return the OwnershipRule for a category.
    Falls back to UNOWNED with a generic context for UNKNOWN or unmapped categories.
    """
    return OWNERSHIP_MAP.get(
        category,
        OwnershipRule(
            team    = OwnerTeam.UNOWNED,
        ),
    )