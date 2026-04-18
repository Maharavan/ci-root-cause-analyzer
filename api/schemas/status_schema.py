from enum import Enum

class StatusData(str, Enum):
    RECEIVED = "RECEIVED"
    LOGS_COLLECTED = "LOGS_COLLECTED"
    CLASSIFIED = "CLASSIFIED"
    ANALYZING = "ANALYZING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"


class JobFailureStatus(str,Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    NOT_BUILT = "NOT_BUILT"
    ABORTED = "ABORTED"
    UNSTABLE = "UNSTABLE"