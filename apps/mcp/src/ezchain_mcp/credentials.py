"""What the MCP server's own credential is allowed to do.

An MCP server holds one credential for its whole lifetime. The deployment token is an
admin principal: it can release the kill switch and reload plugins. An agent must never
operate through it, so the write surface opens only for a scoped, revocable key.

There is no scope-discovery route on the API. The cheapest honest probe is the admin-only
``GET /api/v1/agent/keys`` read: the deployment token and any ``admin`` key get 200, a
``read``/``trade`` key gets 403 ``FORBIDDEN``, and a revoked or invalid key gets 401. Only
that named denial opens writes: a bare 403 is any refusal a deployment might grow (the web
proxy's ``CROSS_ORIGIN_DENIED``, a WAF rule, a future guard), so the status alone is not
evidence of a scope. A missing credential, an unreachable API, another code or any other
status fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .client import ApiError, EzchainClient

ADMIN_PROBE_PATH = "/api/v1/agent/keys"

# The one error code that means "authenticated, but lacks the admin scope". Every other
# refusal is opaque: the status alone is not evidence that this is a scoped agent key.
SCOPE_DENIED_CODE = "FORBIDDEN"

# The one command an operator runs to replace the deployment token with a scoped key.
SCOPED_KEY_HELP = (
    "Mint a scoped key with the deployment token and start the MCP server with it: "
    'curl -sS -X POST "$EZCHAIN_API_URL/api/v1/agent/keys" '
    '-H "Authorization: Bearer $EZCHAIN_API_TOKEN" '
    '-H "content-type: application/json" '
    '-d \'{"subject":"<agent>","scopes":["read","trade"]}\''
)

CredentialStatus = Literal["scoped", "admin", "missing", "unverified"]


@dataclass(frozen=True)
class Credential:
    """A verified view of the credential the server runs with."""

    status: CredentialStatus
    detail: str = ""

    @property
    def may_write(self) -> bool:
        return self.status == "scoped"


def inspect_credential(client: EzchainClient, token: str | None) -> Credential:
    """Classify the server's credential without ever enabling an admin principal."""
    if token is None or not token.strip():
        return Credential("missing", "no credential was supplied")
    try:
        client.get(ADMIN_PROBE_PATH)
    except ApiError as exc:
        if exc.status == 403 and exc.code == SCOPE_DENIED_CODE:
            return Credential("scoped")
        return Credential("unverified", f"{exc.status} {exc.code}: {exc.message}")
    return Credential("admin", "the credential carries the admin scope")


def refusal_message(credential: Credential) -> str:
    """Why writes stay closed, in one line an operator can act on."""
    if credential.status == "admin":
        return (
            "writes refused: this credential carries the admin scope, which can release "
            "the kill switch and reload plugins. An agent gets a scoped key or read-only, "
            "never the deployment token. " + SCOPED_KEY_HELP
        )
    if credential.status == "missing":
        return "writes refused: EZCHAIN_API_TOKEN is unset. " + SCOPED_KEY_HELP
    return (
        f"writes refused: could not verify the credential ({credential.detail}); "
        "writes fail closed. " + SCOPED_KEY_HELP
    )


__all__ = [
    "ADMIN_PROBE_PATH",
    "SCOPED_KEY_HELP",
    "SCOPE_DENIED_CODE",
    "Credential",
    "CredentialStatus",
    "inspect_credential",
    "refusal_message",
]
