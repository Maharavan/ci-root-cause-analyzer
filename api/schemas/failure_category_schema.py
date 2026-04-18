from enum import Enum

class FailureCategory(str, Enum):
    # Developer-owned failures
    DEV_FAILURE      = "DEV_FAILURE"       # Compilation, linking, dependencies, code quality

    # Test failures
    TEST_FAILURE     = "TEST_FAILURE"      # Test framework, assertion errors, flaky tests

    # CI/CD and infrastructure failures
    CI_INFRA_FAILURE = "CI_INFRA_FAILURE"  # Pipeline, env/config, artifacts, Docker,
                                           # Kubernetes, cloud, network, resources, agents

    UNKNOWN          = "UNKNOWN"