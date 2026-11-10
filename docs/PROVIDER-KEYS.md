# Provider API keys

A workspace can store its own provider credentials so a rate-limited provider is called
with a key the operator controls. ezCHAIN never returns that key through the API and never
writes it to a log line.

## Storage

Two stores are supported. There is no plaintext fallback.

| Store | `EZCHAIN_PROVIDER_KEY_STORE` | What is kept in the database |
|---|---|---|
| age | `age` | ciphertext encrypted to an operator-held age recipient |
| OS keyring | `keyring` | an opaque reference; the secret stays in the platform credential store |

`auto`, the default, uses age when `EZCHAIN_PROVIDER_KEY_AGE_RECIPIENT` is set and the OS
keyring otherwise.

For age, set `EZCHAIN_PROVIDER_KEY_AGE_RECIPIENT` to the public recipient and
`EZCHAIN_PROVIDER_KEY_AGE_IDENTITY` to the identity file used for reads. The `age` binary
must be on `PATH`; `EZCHAIN_PROVIDER_KEY_AGE_BINARY` overrides its name. Ciphertext is
armored and stored with the `age:v1:` prefix, so the database alone is not enough to
recover a key.

For the OS keyring, install the `keyring` package and a working platform backend. The
service stores each secret under the `ezchain-provider-keys` service name and keeps only
its reference.

When the selected store is unavailable, creating a key fails with HTTP 503 and
`PROVIDER_KEY_ENCRYPTION_UNAVAILABLE`. The process still boots and can list or revoke
existing metadata; it will not write a key it cannot protect.

Set `EZCHAIN_PROVIDER_KEY_FINGERPRINT_PEPPER` to keep fingerprints from being reproduced
by someone holding only the database. When it is unset, `EZCHAIN_API_KEY_PEPPER` is used.
Fingerprints are the first 32 hexadecimal characters of an HMAC-SHA-256 over the key and
are never sufficient to recover it.

## API

The management routes require the `admin` scope and operate only inside
`EZCHAIN_WORKSPACE_ID` (default `default`).

| Method | Path | Result |
|---|---|---|
| `POST` | `/api/v1/provider-keys` | stores a key and returns its fingerprint and metadata |
| `GET` | `/api/v1/provider-keys` | lists metadata for the workspace; fingerprints only |
| `DELETE` | `/api/v1/provider-keys/{id}` | revokes the key and clears its stored material |

A create request body is `{"provider": "jupiter", "label": "desk", "key": "<provider-key>"}`.
The response contains `id`, `provider`, `label`, `fingerprint`, the workspace and the audit
timestamps. It never contains the key or its ciphertext. Jupiter is the only provider that
accepts a stored key today.

A revoked key cannot be resolved for a provider call. The row keeps its fingerprint and
timestamps so the revocation is still visible in the list.

## Request budgets

Provider calls share `ProviderBudgetManager` from `ezchain_adapters`. The shipped Jupiter
client is constructed with this manager, so its requests are limited to one per second with
a burst of one. Callers wait for the next slot inside a bounded queue. When the queue is
full, or the next slot is beyond the wait window, the manager raises
`ProviderBudgetExhausted`; the API renders that exception as HTTP 429 with
`PROVIDER_BUDGET_EXHAUSTED` and `Retry-After: 1`. A caller is never left waiting
indefinitely for a provider budget.
