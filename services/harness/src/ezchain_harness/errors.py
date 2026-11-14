"""The typed failures a provider client raises.

Every message here is built from the provider, the HTTP status and the provider's own error
text. None of them carries a request header, so an exception that reaches a log line or a
session detail cannot leak a credential.
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    """The provider could not produce a turn. The session must fail closed."""


class ProviderTimeout(ProviderError):
    """The provider did not answer inside the configured timeout."""


class ProviderTransportError(ProviderError):
    """The request never completed: DNS, connection reset, TLS, proxy."""


class ProviderHTTPError(ProviderError):
    """The provider answered, but not with a turn."""

    def __init__(self, provider: str, status: int, detail: str) -> None:
        super().__init__(f"{provider} returned HTTP {status}: {detail}")
        self.provider = provider
        self.status = status


class ProviderProtocolError(ProviderError):
    """The response body is not the shape this client can map to a turn."""


class CredentialError(ProviderError):
    """No usable provider credential is available."""


class MissingCredential(CredentialError):
    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not set in the environment")
        self.env = name


__all__ = [
    "CredentialError",
    "MissingCredential",
    "ProviderError",
    "ProviderHTTPError",
    "ProviderProtocolError",
    "ProviderTimeout",
    "ProviderTransportError",
]
