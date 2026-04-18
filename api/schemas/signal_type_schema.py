from enum import Enum

class SignalType(str, Enum):
    ERROR = "ERROR"
    EXIT_CODE = "EXIT_CODE"
    TEST_FAILURE = "TEST_FAILURE"
    SECURITY = "SECURITY"
    RESOURCE = "RESOURCE"
    BUILD_FAILURE = "BUILD_FAILURE"

