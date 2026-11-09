class GatewayError(Exception):
    """Base class for gateway errors."""


class NotConfigured(GatewayError):
    """Adapter or price is not configured / paid use not allowed."""


class DataClassViolation(GatewayError):
    """Customer data sent to an adapter that only accepts public data. Never retried."""


class BudgetExceeded(GatewayError):
    """A spend cap would be exceeded by the call. Raised before the call."""


class RecordedMiss(GatewayError):
    """No recorded fixture entry for the cache key."""


class TransientError(GatewayError):
    """Retryable failure (throttling, 5xx, timeout)."""


class Throttled(TransientError):
    pass


class ServerError(TransientError):
    pass


class CallTimeout(TransientError):
    pass


class RequestRejected(GatewayError):
    """Provider rejected the request (validation, access denied, unknown model). Never retried."""
