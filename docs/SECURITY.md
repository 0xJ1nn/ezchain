# SECURITY — threat model and controls

Assets: private keys, exchange API keys, funds, strategy IP, user identity, order history.

Adversaries: internet attacker, malicious strategy plugin, compromised dependency,
insider/mistaken operator, stolen laptop, malicious RPC/venue, supply-chain attacker.

## 1. Key custody tiers

Custody is the user's (ADR-0040). A key is added at runtime by the person who owns it,
encrypted at rest under that person's credential, and used only for that person's orders.
The platform protects the key from **outsiders** — encryption, sandboxing, no key leaves
the vault boundary, no third-party access — and does not gate the owner, who decides
whether an order is approved per order or automated. Every key is addressed by a
`KeyHandle`: an opaque id, its owner, its custody tier, the chain and public address, and a
SHA-256 fingerprint of the public key. There is no field in which a seed, a private key or
a passphrase could be stored, and the ledger records the fingerprint, never a key.

| Tier | Where the key lives | Signing | Use |
|---|---|---|---|
| `local_vault` | encrypted file; the cipher and KDF are **not yet decided** — XChaCha20-Poly1305 with an Argon2id-derived KEK and a per-key DEK is the planned target (ADR-0039 leaves both open) | executor process, in memory per request | default hot wallet |
| `hardware` | Ledger/Trezor or another device | device; executor builds unsigned tx, the owning user approves | one option, not the only one |
| `watch` | none | — | portfolio, signals; never signs |
| `cex_key` | vault, encrypted | exchange API | read-only by default; trade scope opt-in |

A device approval is one custody choice among these, never a requirement on every order:
automation is a per-user setting (`manual` = the approval queue; `full_auto` = an explicit
opt-in), and the product tells the user what each choice means at key-add and at the
automation opt-in, then does what they asked.

- The signer sits behind a vendor-agnostic `SignerTransport`, so a USB device, a local
  signer daemon on a unix socket and a remote signer are interchangeable and no vendor SDK
  is a dependency.
- Every non-answer is one stable code (`VAULT_UNREACHABLE`, `VAULT_APPROVAL_DENIED`,
  `VAULT_UNKNOWN_HANDLE`, `VAULT_LOCKED`, `VAULT_WATCH_ONLY`, `VAULT_SIGN_FAILED`) and no
  signature. Another user's handle refuses exactly like an unknown one.
- Master key: `EZCHAIN_VAULT_KEY` (env), a sealed file, or KMS. Never in the repo,
  never in the web bundle, never logged.
- Unlock is explicit at boot; auto-lock after idle; every unlock is audited.
- No key export via API. Backup is a user-initiated, passphrase-protected export file;
  it is never stored server-side.
- Executor runs with the minimum filesystem and network privileges; signing and
  broadcasting are the only outbound calls it makes (plus RPC).
- Key material never enters a strategy process. The SDK has no signing API by design.
- A non-paper pipeline requires a boundary whose signer carries a signable per-user
  handle; a bare in-memory signer refuses with `VAULT_BOUNDARY_REQUIRED` before the venue.
  A signer whose `handle` exists but is not a `KeyHandle` is refused the same way rather
  than dereferenced.
- The order's principal and the handle's owner are compared in the pipeline before any
  signer runs, and again in the signer before the vault is called. The principal comes
  from the order's attribution (the authenticated caller, the agent key or the strategy),
  never from a value the executor asserts about itself, so one executor instance cannot
  sign user B's order with user A's key (`CUSTODY_OWNER_MISMATCH`).
- An unattended (automation) intent needs the opt-in consent record for that same user and
  rule. With no matching `AutomationSetting` the run refuses with
  `AUTOMATION_CONSENT_REQUIRED` before an order exists, so an automation path cannot skip
  the opt-in by leaving the setting unconfigured.

## 2. Authentication and authorization

- Passkey (WebAuthn) preferred, TOTP fallback, password + Argon2id if self-hosted simply.
- Sessions: `__Host-` cookie, `HttpOnly`, `Secure`, `SameSite=Lax`, 30 min idle / 12 h
  absolute; server-side revocation list.
- 2FA required for: live mode, key operations, withdrawals, member changes, API key
  creation.
- RBAC: owner / trader / viewer, enforced server-side per route and per entity. The UI
  hides, the API denies.
- API keys: scoped (`read`, `trade`, `admin`), optional IP allowlist, expiry, last-used,
  revocable; hashed at rest (SHA-256 + pepper), shown once.
- Rate limits per IP, per session, per API key; login lockout with exponential backoff.
- CSRF: same-site cookies + origin check + double-submit token for state-changing form
  posts.

## 3. The executor boundary

- Only the executor can decrypt and sign. API/engine request `execute(intent_id)`.
- Requests are authenticated with mTLS or a short-lived HMAC token bound to intent id.
- The executor validates the intent against the risk decision it receives (defence in
  depth): amount, venue, wallet, mode, expiry.
- No generic "sign arbitrary transaction" endpoint. The adapter builds; the executor signs
  only envelopes matching the intent.
- Replay protection: unique intent id, nonce tracking per chain/account, expiry.
- Panic: executor refuses new intents when the kill switch is on, even if the API is
  compromised; kill switch state is read from the DB and cached fail-closed.

## 4. Strategy plugin containment (v1)

The v1 worker is **containment, not a sandbox**. It is documented that way so nobody
mistakes a timeout for an isolation boundary.

- Each plugin strategy runs in a separate interpreter (`services/engine/plugin_worker`),
  started with a scrubbed environment: no API token, signing secret, vault path or
  `HOME`. Only the SDK and the plugin's own dependencies are importable.
- `RLIMIT_AS` (default 512 MiB) and `RLIMIT_CPU` bound memory and CPU; a per-callback
  wall timeout (default 5 s) kills the process group.
- Plugin stdout is redirected to stderr; the JSON-lines protocol is the only thing read
  from stdout.
- 3 failures in 10 minutes quarantine the strategy. `POST /api/v1/plugins/reload`
  retires workers after an operator fixes the plugin.
- Manifest permissions are explicit: `read_market`, `read_account`, `submit_intent`.
  Unknown permissions fail discovery. Chains are declared and enforced before a tick.
- The SDK exposes no wallet, key, signing, HTTP, filesystem or subprocess API. This is a
  contract, not an OS boundary: a determined plugin can still open sockets and read
  files. Installs are trusted operator actions (`pip install` or a directory under
  `EZCHAIN_PLUGIN_PATHS`).
- A plugin's intent is clamped by the existing paper risk policy; a plugin cannot submit
  an order, choose a mode or reach the executor.

Not yet implemented: read-only FS, network namespace, seccomp/bwrap, signed packages and
a registry. Those are tracked as follow-ups; until they exist, do not describe the worker
as a security sandbox. The executor remains the only key holder (ADR-0026).

## 5. API and web

- Input validation at the edge (Pydantic), output schemas (OpenAPI), no raw SQL in routers.
- CSP: default-src 'self'; no inline scripts; connect-src limited to the API origin;
  frame-ancestors 'none'. HSTS in public mode.
- No secrets in the bundle; no source maps in prod; dependency lockfiles committed.
- WebSocket: origin check, session auth, per-user channel, heartbeat, message size cap.
- Export endpoints stream and are audited; no unauthenticated static export paths.
- Error responses never echo stack traces, keys or SQL.

## 6. Supply chain

- Lockfiles for npm/uv; Dependabot/Renovate with a review gate.
- Container images: distroless/non-root, read-only root FS, no shell in prod, pinned
  digests, SBOM, Trivy gate (no HIGH/CRITICAL without an accepted exception).
- Python: `uv lock` + hash verification; no `pip install` at runtime.
- Third-party actions pinned by SHA.
- Secrets scanning (gitleaks) in pre-commit and CI; history scan before first public push.

## 7. Data protection and privacy

- Self-hosted: data stays on the operator's machine. No telemetry by default; opt-in
  crash reports are explicit and documented.
- Logs redact keys, cookies, authorization headers, API keys and signed transactions.
- PII: email only, hashed for lookups where possible; deletion is a documented admin
  action, except ledger/audit records required for financial integrity (retained with
  pseudonymous ids).
- Backups are encrypted at rest; restore requires the vault key for key material.

## 8. Threat table

| Threat | Control | Residual |
|---|---|---|
| Stolen laptop | vault passphrase + OS keychain + auto-lock; watch-only survives | unlocked session can trade until lock |
| Malicious plugin | sandbox + no keys + intent caps | a plugin can still lose its allocation via allowed intents |
| Compromised RPC | multi-provider, response sanity checks, quote divergence, simulation | a coordinated lie can still misprice; caps limit loss |
| Malicious venue/aggregator | sell-quote requirement, route identity, min-out, simulation | exotic tokens may be unsellable — shown honestly |
| API key leak | scopes, IP allowlist, hashing, revocation, audit | trade scope can still act until revoked |
| Supply chain | pinned digests, SBOM, scan, review | zero-day in a dependency |
| Insider | RBAC, audit log, approval threshold, no key export | owner is trusted by definition |
| Replay/double-spend | intent idempotency, nonce tracking, receipt checks | chain reorg can reorder; adapter handles confirmation depth |
| DoS | rate limits, bounded queues, timeouts, backpressure | public instance needs a reverse-proxy/WAF |
| Data loss | verified backups, restore drill, forward-only migrations | vault key loss is unrecoverable by design |

## 9. Reporting

`SECURITY.md` defines the disclosure process (private advisory, 90-day window). Never
open a public issue with a working exploit or key material.
