# SECURITY — threat model and controls

Assets: private keys, exchange API keys, funds, strategy IP, user identity, order history.

Adversaries: internet attacker, malicious strategy plugin, compromised dependency,
insider/mistaken operator, stolen laptop, malicious RPC/venue, supply-chain attacker.

## 1. Key custody tiers

| Tier | Where the key lives | Signing | Use |
|---|---|---|---|
| `local_vault` | encrypted file (XChaCha20-Poly1305, Argon2id-derived KEK, per-key DEK) | executor process, in memory per request | default hot wallet |
| `hardware` | Ledger/Trezor | device; executor builds unsigned tx, user approves | large balances |
| `watch` | none | — | portfolio, signals |
| `cex_key` | vault, encrypted | exchange API | read-only by default; trade scope opt-in |

- Master key: `EZCHAIN_VAULT_KEY` (env), a sealed file, or KMS. Never in the repo,
  never in the web bundle, never logged.
- Unlock is explicit at boot; auto-lock after idle; every unlock is audited.
- No key export via API. Backup is a user-initiated, passphrase-protected export file;
  it is never stored server-side.
- Executor runs with the minimum filesystem and network privileges; signing and
  broadcasting are the only outbound calls it makes (plus RPC).
- Key material never enters a strategy process. The SDK has no signing API by design.

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

## 4. Strategy plugin sandbox

- Separate process, isolated env, read-only FS, no network namespace by default.
- Only the SDK API; no imports of platform modules; no `os.environ`, no `subprocess`.
- CPU/wall/memory budgets; overrun ⇒ quarantine.
- Manifest permissions are explicit and shown at install: chains, venues, data scopes.
- Install requires a confirmation showing the diff of requested permissions.
- Signed packages (sigstore) optional; unsigned local installs are allowed but labelled
  `UNVERIFIED` in the UI.

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
