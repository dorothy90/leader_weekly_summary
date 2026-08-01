from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_USER_ID = "INVALID_USER_ID"
    UNAUTHORIZED_RESOURCE = "UNAUTHORIZED_RESOURCE"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
    RETRIEVAL_TIMEOUT = "RETRIEVAL_TIMEOUT"
    NO_EVIDENCE = "NO_EVIDENCE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    JOB_CANCELLED = "JOB_CANCELLED"


class AppError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
