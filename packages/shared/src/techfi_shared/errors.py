class ApplicationError(Exception):
    """Messages may contain sensitive details; expose only stable codes."""

    code = "internal_error"
    status_code = 500


class ConfigurationError(ApplicationError):
    code = "invalid_configuration"


class ProviderError(ApplicationError):
    code = "provider_unavailable"
    status_code = 503
