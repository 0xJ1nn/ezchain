# SECURITY — threat model and controls

Assets: private keys, exchange API keys, funds, strategy IP, user identity, order history.

Adversaries: internet attacker, malicious strategy plugin, compromised dependency,
insider/mistaken operator, stolen laptop, malicious RPC/venue, supply-chain attacker.

This document has two parts. Part I describes controls that run in the shipped build. Part II
records design targets; none of that path is part of the shipped API, and none of it protects
a deployment today. Where a passage is not under Part I, read it as a target and not as a
description of current behaviour.

## Part I — Current state

### 1. Deployment posture

A deployment is one paper workspace. The API and web build hold no private keys, expose no
signing route and have no path to a live venue, so no order in this build can move funds.
There is no login, no account system and no per-user identity: a mutation is attributed to
the credential that made it, not to a person. Operators share one deployment secret, and
anyone who holds it can mutate the workspace.

### 2. Credential model

Every mutation under `/api/` requires an `Authorization: Bearer <token>` header. Two kinds
of credential are accepted.

The deployment token (`EZCHAIN_API_TOKEN`) is a shared secret held only in the server
environment. It names the deployment, not an operator, and carries every scope. The API
compares it with a constant-time comparison. Production refuses to boot without a token of
at least 32 ASCII characters or with authentication disabled; development with no token
allows mutations after logging one warning, which must never be exposed.

Scoped keys are minted through `POST /api/v1/agent/keys`. Each token begins with `ezk_`, is
returned once at creation and stored only as a SHA-256 hash. An optional pepper
(`EZCHAIN_API_KEY_PEPPER`) makes a database-only leak insufficient, and production refuses
to mint a key without it. A row records the subject label, scopes, creation time, last use,
expiry (90 days by default) and revocation; revoking a key stops it immediately. The subject
is a label inside the one workspace, not an authenticated person.

There are three flat scopes and no hierarchy between them:

- `read` — read routes, including reads when authentication is required;
- `trade` — paper orders, strategies, runs and backtests;
- `admin` — key management, plugin reload and the operator kill switch. `admin` implies the
  other two scopes.

The middleware checks every request before its route runs. A route's least scope follows its
path and method: reads need `read`, mutations need `trade`, and key management, system
controls and plugin reload need `admin`. A request with no usable credential gets a `401`
with `WWW-Authenticate: Bearer`; a credential that lacks the scope gets a `403`. Reads are
open by default so a self-hosted workspace works without a token; setting
`EZCHAIN_REQUIRE_AUTH_FOR_READS=true` closes them behind the `read` scope. Two reads are
special: `/api/v1/agent/state` always needs a principal, and `/api/v1/agent/buckets` resolves
a supplied credential so a scoped key sees only its own row.

### 3. Boundary controls

- The browser never holds a credential. It calls the Next server at `/api/[...path]`; that
  proxy discards any inbound `Authorization` header, injects the deployment token from its
  server environment and forwards the call. The token is never in a browser bundle or a page
  source.
- The proxy denies a cross-origin request using `Sec-Fetch-Site` and, when that header is
  absent, the `Origin` header. The API allows only configured CORS origins and does not allow
  credentialed cross-origin requests.
- The interactive schema (`/docs`, `/redoc`, `/openapi.json`) answers only to a loopback
  client. `/healthz` and `/readyz` are public orchestration probes; `/health` and `/metrics`
  answer to loopback or to a request carrying the deployment token. A remote client cannot
  claim loopback with a forwarded header: `X-Forwarded-For` is read only from a loopback or
  configured proxy peer, and `EZCHAIN_TRUSTED_PROXY_CIDRS` is empty by default.
- An unauthenticated read rate limit applies by default and must be greater than zero in
  production. Access logs redact bearer credentials and known secrets before writing them.

### 4. What protects a paper-only deployment

- No key material, no signing route and no live venue access. This is the control that keeps
  funds still, whatever a caller can authenticate as.
- A bearer credential on every mutation, a production boot that refuses an incomplete token
  configuration, and a token that stays in the server environment.
- Three flat scopes that let an operator give an agent a `read` or `trade` key without giving
  it key management or the kill switch; keys are hashed, expiring and revocable.
- A web proxy that keeps the token out of the browser and rejects cross-origin calls.
- A persisted kill switch with global and strategy levels, changed only by an authenticated
  mutation that records a reason, plus paper limits on order size, position size and daily
  loss.
- The plugin containment in section 5, which bounds a strategy process without isolating it.

These controls give no per-user attribution, second factor, session, role model or key
custody. Scoped keys divide capability, not identity.

### 5. Strategy plugin containment (shipped, bounded)

The v1 worker is containment, not a sandbox. It is documented that way so nobody mistakes a
timeout for an isolation boundary.

- Each plugin strategy runs in a separate interpreter (`services/engine/plugin_worker`),
  started with a scrubbed environment: no API token, signing secret, vault path or `HOME`.
  Only the SDK and the plugin's own dependencies are importable.
- `RLIMIT_AS` (default 512 MiB) and `RLIMIT_CPU` bound memory and CPU; a per-callback wall
  timeout (default 5 s) kills the process group.
- Plugin stdout is redirected to stderr; the JSON-lines protocol is the only thing read
  from stdout.
- Three failures in ten minutes quarantine the strategy. `POST /api/v1/plugins/reload`
  retires workers after an operator fixes the plugin.
- Manifest permissions are explicit: `read_market`, `read_account`, `submit_intent`.
  Unknown permissions fail discovery. Chains are declared and enforced before a tick.
- The SDK exposes no wallet, key, signing, HTTP, filesystem or subprocess API. This is a
  contract, not an OS boundary: a determined plugin can still open sockets and read files.
  Installs are trusted operator actions (`pip install` or a directory under
  `EZCHAIN_PLUGIN_PATHS`).
- A plugin's intent is clamped by the existing paper risk policy; a plugin cannot submit
  an order, choose a mode or reach the executor.

Not yet implemented: read-only FS, network namespace, seccomp/bwrap, signed packages and a
registry. Until they exist, do not describe the worker as a security sandbox.

### 6. Supply chain

- Lockfiles are committed for both ecosystems; the dependency-pins step of the gate
  (`tools/ci.sh` step 5) refuses a lock that is not exact, unhashed, or no longer satisfies
  the ranges it was compiled from. [DEPENDENCIES.md](DEPENDENCIES.md) is the procedure.
- Python: `pip-compile --generate-hashes` into `requirements*.lock`; both the dev setup and
  the API image install with `--require-hashes`. No `pip install <range>` path remains.
- Node: `pnpm-lock.yaml` with an `integrity` hash per resolution, installed
  `--frozen-lockfile`.
- `tools/audit_pins.py` fails the build on an advisory that is not allowlisted with a
  written reason; it exits 2, never 0, when the audit could not run.
- Dependabot/Renovate is not configured; a pin moves by the review ritual in
  [DEPENDENCIES §3](DEPENDENCIES.md).
- A secret scan runs in the gate (`tools/check-secrets.mjs`, step 4), and the pre-push hook
  runs the gate for main. No GitHub Actions workflows are configured, so there are no
  third-party actions to pin.
- Container hardening is a target, not a shipped control: distroless/non-root images,
  read-only root FS, no shell in production, pinned digests, an SBOM and a Trivy gate with no
  HIGH/CRITICAL finding without an accepted exception. The SBOM and Trivy gate are not built.

### 7. Data protection and privacy

- Self-hosted: data stays on the operator's machine. No telemetry by default; opt-in
  crash reports are explicit and documented.
- Logs redact keys, cookies, authorization headers, API keys and signed transactions.
- PII: email only, hashed for lookups where possible; deletion is a documented admin
  action, except ledger/audit records required for financial integrity (retained with
  pseudonymous ids).
- Backups are encrypted at rest; restore requires the vault key for key material.

## Part II — Target state (not implemented)

Nothing in this part is part of the shipped API or web build. It is recorded so the direction
is visible and so a plan is never read as a control that is running.

### 8. Authentication and identity

- Passkey (WebAuthn) sign-in would be preferred, with TOTP fallback and password plus
  Argon2id for simple self-hosted installs. This would replace the shared bearer token with
  a person's identity.
- Sessions would use `__Host-` cookies with `HttpOnly`, `Secure` and `SameSite=Lax`, a
  30-minute idle and 12-hour absolute lifetime, and a server-side revocation list.
- A second factor would be required for live mode, key operations, withdrawals, member
  changes and API key creation.
- Roles (`owner`, `trader`, `viewer`) would be enforced per route and per entity on the
  server; the interface would hide what the API denies.
- API keys would gain an optional IP allowlist. Rate limits would be tracked per IP, per
  session and per key, with login lockout and exponential backoff.
- State-changing form posts would use same-site cookies, an origin check and a double-submit
  token.

None of these protects the current build. There is no passkey enrollment, TOTP, cookie
session, second factor, role hierarchy, IP allowlist, login lockout or CSRF token.

### 9. Key custody (design)

The intended custody model puts the key with its owner (ADR-0040). A key would be added at
runtime by the person who owns it, encrypted at rest under that person's credential and used
only for that person's orders. The platform would protect the key from outsiders and would
not gate the owner, who decides whether an order is approved per order or automated. The
shipped build has none of this: there is no vault, no key import and no signing path exposed
to the API or the web app. Every key would be addressed by a `KeyHandle`: an opaque id, its
owner, its custody tier, the chain and public address, and a SHA-256 fingerprint of the
public key. There would be no field in which a seed, a private key or a passphrase could be
stored, and the ledger would record the fingerprint, never a key.

| Tier | Where the key would live | Signing | Use |
|---|---|---|---|
| `local_vault` | encrypted file; the cipher and KDF are **not yet decided** — XChaCha20-Poly1305 with an Argon2id-derived KEK and a per-key DEK is the planned target (ADR-0039 leaves both open) | executor process, in memory per request | default hot wallet |
| `hardware` | Ledger/Trezor or another device | device; executor builds unsigned tx, the owning user approves | one option, not the only one |
| `watch` | none | — | portfolio, signals; never signs |
| `cex_key` | vault, encrypted | exchange API | read-only by default; trade scope opt-in |

A device approval would be one custody choice among these, never a requirement on every
order: automation would be a per-user setting (`manual` = the approval queue; `full_auto` =
an explicit opt-in), with the choice explained at key-add and at the automation opt-in.

- The signer would sit behind a vendor-agnostic `SignerTransport`, so a USB device, a local
  signer daemon on a unix socket and a remote signer are interchangeable and no vendor SDK
  is a dependency.
- Every non-answer would be one stable code (`VAULT_UNREACHABLE`, `VAULT_APPROVAL_DENIED`,
  `VAULT_UNKNOWN_HANDLE`, `VAULT_LOCKED`, `VAULT_WATCH_ONLY`, `VAULT_SIGN_FAILED`) and no
  signature. Another user's handle would refuse exactly like an unknown one.
- The master key would be `EZCHAIN_VAULT_KEY` (env), a sealed file or KMS: never in the repo,
  the web bundle or a log. Unlock would be explicit at boot, with auto-lock after idle and
  an audit record for every unlock. Backup would be a user-initiated, passphrase-protected
  export file, never stored server-side.
- The executor would run with the minimum filesystem and network privileges; signing and
  broadcasting would be its only outbound calls apart from RPC. Key material would never
  enter a strategy process, and the SDK would have no signing API.
- A non-paper pipeline would need a boundary whose signer carries a signable per-user
  handle; a bare in-memory signer would refuse with `VAULT_BOUNDARY_REQUIRED` before the
  venue. A signer whose `handle` exists but is not a `KeyHandle` would be refused the same
  way rather than dereferenced.
- The order's principal and the handle's owner would be compared before any signer runs and
  again before the vault is called. The principal would come from the order's attribution
  (the authenticated caller, the agent key or the strategy), never from a value the executor
  asserts about itself, so one executor instance could not sign user B's order with user A's
  key (`CUSTODY_OWNER_MISMATCH`).
- An unattended intent would need the opt-in consent record for that same user and rule.
  With no matching `AutomationSetting` the run would refuse with
  `AUTOMATION_CONSENT_REQUIRED` before an order exists.
- An agent's money would be bounded separately from its approval: the agent could deploy only
  the net cash its owner capped for it (`agent_buckets.cap_usd`), replayed from the ledger's
  attributed cash legs. An order past the cap would refuse with `AGENT_BUCKET_EXCEEDED`
  before a quote. Only the agent's owner, and only a person, could move that cap; an agent
  key would get `AGENT_BUCKET_REQUIRES_PERSON`, and the cap would be per key.

### 10. Executor signing boundary (design)

- Only the executor would decrypt and sign; the API and engine would request
  `execute(intent_id)`.
- Those requests would be authenticated with mTLS or a short-lived HMAC token bound to the
  intent id.
- The executor would validate the intent against the risk decision it receives: amount,
  venue, wallet, mode and expiry.
- There would be no generic "sign arbitrary transaction" endpoint. The adapter would build
  and the executor would sign only envelopes matching the intent.
- Replay protection would use a unique intent id, nonce tracking per chain and account, and
  expiry.
- The executor would refuse new intents when the kill switch is on, even if the API were
  compromised; the kill switch state would be read from the database and cached fail-closed.

### 11. API and web hardening (design)

- Content security policy would set `default-src 'self'`, no inline scripts, `connect-src`
  limited to the API origin and `frame-ancestors 'none'`; HSTS would be sent in public mode.
- WebSockets would check origin and session, scope each channel to a user, heartbeat and cap
  message size.

Neither is in the shipped build: no such response headers are configured and no WebSocket
route is registered.

### 12. Threat table

The table records the intended control for each threat. Where the control is not in Part I,
it is a target and is not protecting a deployment today.

| Threat | Control | Residual |
|---|---|---|
| Stolen laptop | vault passphrase + OS keychain + auto-lock; watch-only survives | unlocked session can trade until lock |
| Malicious plugin | containment (section 5), no keys, intent caps | a plugin can still lose its allocation via allowed intents; OS isolation is a target |
| Compromised RPC | multi-provider, response sanity checks, quote divergence, simulation | a coordinated lie can still misprice; caps limit loss |
| Malicious venue/aggregator | sell-quote requirement, route identity, min-out, simulation | exotic tokens may be unsellable — shown honestly |
| Credential leak | three flat scopes, hashing, revocation, audit | `trade` or `admin` can still act until revoked; no per-user identity |
| Supply chain | pinned digests, SBOM, scan, review | zero-day in a dependency |
| Insider | audit log, approval threshold, no key export | the deployment token is shared; per-user roles are a target |
| Replay/double-spend | intent idempotency, nonce tracking, receipt checks | chain reorg can reorder; adapter handles confirmation depth |
| DoS | read rate limit, bounded queues, timeouts, backpressure | a public instance needs a reverse proxy or WAF |
| Data loss | verified backups, restore drill, forward-only migrations | vault key loss is unrecoverable by design |
| Prompt injection into an agent | scoped key, no approve/arm/mint tool, automation gate queues every agent order, hard budgets, no key in the loop, bucket reads scoped to the calling key | a compromised model that only lies in its prose: no structural control catches prose, so read the platform record — see [PROMPT-INJECTION.md](PROMPT-INJECTION.md) |

## Reporting

The root `SECURITY.md` defines the disclosure process (private advisory, 90-day window).
Never open a public issue with a working exploit or key material.
