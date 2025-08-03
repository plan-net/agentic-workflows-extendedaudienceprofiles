"""
Custom exception classes for Extended Audience Profiles
"""


class ExtendedAudienceProfilesError(Exception):
    """Base exception for all Extended Audience Profiles errors"""
    pass


class BudgetExceededError(ExtendedAudienceProfilesError):
    """Raised when budget limits are exceeded"""
    def __init__(self, agent_name: str, requested: float, available: float):
        self.agent_name = agent_name
        self.requested = requested
        self.available = available
        super().__init__(
            f"Budget exceeded for {agent_name}: requested ${requested:.2f}, "
            f"but only ${available:.2f} available"
        )


class AgentNotFoundError(ExtendedAudienceProfilesError):
    """Raised when a requested agent is not found"""
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        super().__init__(f"Agent '{agent_name}' not found in configuration")


class JobNotFoundError(ExtendedAudienceProfilesError):
    """Raised when a job ID is not found"""
    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' not found")


class StorageError(ExtendedAudienceProfilesError):
    """Raised when storage operations fail"""
    def __init__(self, operation: str, path: str, cause: Exception = None):
        self.operation = operation
        self.path = path
        self.cause = cause
        message = f"Storage {operation} failed for {path}"
        if cause:
            message += f": {str(cause)}"
        super().__init__(message)


class MasumiNetworkError(ExtendedAudienceProfilesError):
    """Raised when Masumi Network operations fail"""
    def __init__(self, operation: str, agent_name: str = None, cause: Exception = None):
        self.operation = operation
        self.agent_name = agent_name
        self.cause = cause
        message = f"Masumi Network {operation} failed"
        if agent_name:
            message += f" for agent '{agent_name}'"
        if cause:
            message += f": {str(cause)}"
        super().__init__(message)


class ValidationError(ExtendedAudienceProfilesError):
    """Raised when input validation fails"""
    def __init__(self, field: str, value: any, reason: str):
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Validation failed for {field}: {reason}")


class TokenLimitExceededError(ExtendedAudienceProfilesError):
    """Raised when content exceeds model token limits"""
    def __init__(self, model: str, tokens: int, limit: int):
        self.model = model
        self.tokens = tokens
        self.limit = limit
        super().__init__(
            f"Token limit exceeded for {model}: {tokens:,} tokens "
            f"exceeds limit of {limit:,}"
        )