"""Custom domain exceptions for clean and secure error reporting."""


class ProspectDiscoveryException(Exception):
    """Base exception for all domain errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class QueryValidationError(ProspectDiscoveryException):
    """Raised when an incoming user query fails validation or contains malicious input."""

    def __init__(self, message: str = "Invalid prospect query provided") -> None:
        super().__init__(message, status_code=422)


class LLMProcessingError(ProspectDiscoveryException):
    """Raised when query intent parsing fails across all available models."""

    def __init__(self, message: str = "Unable to process query intent") -> None:
        super().__init__(message, status_code=502)


class PlatformDiscoveryError(ProspectDiscoveryException):
    """Raised when an individual platform fails during isolated discovery."""

    def __init__(self, platform: str, reason: str) -> None:
        super().__init__(f"Platform discovery failed for {platform}: {reason}", status_code=502)
        self.platform = platform
        self.reason = reason


class SSRFSecurityViolation(ProspectDiscoveryException):
    """Raised when a candidate URL targets a forbidden private or loopback IP range."""

    def __init__(self, url: str) -> None:
        super().__init__(f"Security error: Access to URL is forbidden: {url}", status_code=403)
        self.url = url
