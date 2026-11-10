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

For the OS keyring, install the API's optional extra (`pip install "ezchain-api[keyring]"`) and a
working platform backend. The
service stores each secret under the `ezchain-provider-keys` service name and keeps only
its reference.

When the selected store is unavailable, creating a key fails with HTTP 503 and
`PROVIDER_KEY_ENCRYPTION_UNAVAILABLE`. The process still boots and can list or revoke
existing metadata; it will not write a key it cannot protect.

Set `EZCHAIN_PROVIDER_KEY_FINGERPRINT_PEPPER` to keep fingerprints from being reproduced
by someone holding only the database. When it is unset, `EZCHAIN_API_KEY_PEPPER` is used.
In production one of the two must be set; creation returns HTTP 503
`PROVIDER_KEY_PEPPER_REQUIRED` otherwise. Fingerprints are the first 32 hexadecimal
characters of an HMAC-SHA-256 over the key and are never sufficient to recover it.

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

A workspace holds exactly one active key per provider, and the database enforces it with a
partial unique index, so two concurrent creates cannot both succeed. A create that would
add a second active key is refused with HTTP 409 `PROVIDER_KEY_ALREADY_EXISTS`; nothing is
stored and the existing key keeps working. Replacing it is an explicit request: send
`"replace": true` in the body. That retires the active key through the revoke path — the
row is marked revoked and its stored secret deleted — before the new key is saved. A
revoke of the new key therefore cannot make the old one resolvable again. The retired row
stays in the list with its fingerprint and timestamps.

A revoked key cannot be resolved for a provider call. The row keeps its fingerprint and
timestamps so the revocation is still visible in the list. Revocation deletes the stored
secret through the backend named by its own prefix, even when the current configuration
selects a different one.

Revocation happens in two steps so a failed delete cannot strand a secret. The row is
marked revoked and committed first, which retires the key immediately; the stored
reference is cleared only after the external store confirms the deletion. If that
deletion fails the request returns HTTP 503 `PROVIDER_KEY_FORGET_FAILED` with
`"retryable": true`, and the row keeps the reference, so repeating the same `DELETE`
finishes the purge instead of returning 404. A second `DELETE` after a completed
revocation returns 404, because there is nothing left to delete and nothing active to
report.

## Use

The active Jupiter key is resolved for each provider request and sent as the `x-api-key`
header to `https://api.jup.ag`. Without a key, the client uses the keyless
`https://lite-api.jup.ag` endpoint and sends no key header. Create and revoke take effect on
the next provider request: no new call starts with a revoked key. A call that already
resolved the key can still be in flight when the revoke commits, and it can send that key
once — revocation cannot recall a request that has left the process.

If a stored key exists but cannot be decrypted, the provider call fails with HTTP 503
`PROVIDER_KEY_UNAVAILABLE`. It does not silently downgrade to the keyless endpoint, because
that would hide a broken credential store and change the provider's rate limit. That
failure is marked `"retryable": false`: a rotated age identity or a deleted keyring entry
needs an operator, and a retry would fail the same way. A store that is briefly unreachable
returns the same code with `"retryable": true`.

## Request budgets

Provider calls share `ProviderBudgetManager` from `ezchain_adapters`. The shipped Jupiter
client is constructed with this manager, so its requests are limited to one per second with
a burst of one. Callers wait for the next slot inside a bounded queue. The wait window is
the total a caller may spend queued, measured from the moment it joins, so a caller that
cannot be served inside it is refused even when every individual sleep was short. That
refusal, and a full queue, raise `ProviderBudgetExhausted`; the API renders it as HTTP 429
with `PROVIDER_BUDGET_EXHAUSTED` and `Retry-After: 1`. The window stays below the quote
router's per-attempt timeout, and a wait is additionally capped by the attempt's remaining
time, so a slow queue answers with that typed 429 instead of timing out and surfacing as a
route failure. A caller is never left waiting indefinitely for a provider budget.

The budget is per process, not shared between processes. Two API workers behind one
provider key each get their own one-request-per-second allowance, so a deployment that
runs more than one worker must either accept that multiplication or put a shared limiter
in front of the provider. Run one API worker unless a shared limiter exists.

## Known limits

If a create's database commit fails and the cleanup deletion of the new secret also fails,
that secret stays in the store with no row pointing at it and no retry path.
