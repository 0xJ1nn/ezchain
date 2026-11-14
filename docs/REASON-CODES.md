# REASON CODES — the stable failure vocabulary

One code, one meaning, one home. Every failure that can block or move money is named
here, and the list is hand-maintained and test-checked against every emission site in a
single module:
[`packages/core-py/src/ezchain_core/reason_codes.py`](../packages/core-py/src/ezchain_core/reason_codes.py).
The API, the executor, the engine, the adapters and the web UI all read that module (or
the derived registries it exports) instead of re-typing a literal. A drift guard fails the
suite when a code is emitted anywhere without a row here, so this document cannot fall
behind the code.

**Scale:** 259 codes across 17 categories, 6 layers.

---

## 1. The stability promise

| Promise | Detail |
|---|---|
| A code string is permanent | `QUOTE_STALE` is `QUOTE_STALE` in every release. Never renamed, never re-used for a different failure. |
| A code's meaning is permanent | If the meaning changes, it is a new code. Two meanings never share one string. |
| A code is never removed | A retired failure keeps its row. Persisted `order.reason` values and log lines outlive the code that wrote them. |
| `message` may change | Display text is not the contract. Compare on `code`, never on `message`. |
| New codes are additive | Adding a code is a non-breaking change. A client that meets an unknown code renders it verbatim (see §6). |

The code is the contract. Everything else — the message, the severity, the HTTP status —
may be corrected within a release. Anything that consumes a code must branch on the string
and must tolerate a code it has never seen.

---

## 2. The mechanism

The taxonomy is a module, not a spreadsheet:

- `ReasonSpec` is one frozen record per code: `code`, `category`, `layer`, `source`,
  `summary`, `message`, `http_status`, `retryable`, `severity`.
- `REASON_CODES` is the registry: `dict[str, ReasonSpec]`, keyed by code.
- The older registries are **derived, not duplicated**: `RISK_REASON_MESSAGES`,
  `VAULT_REASONS` and `BINDING_REASONS` are built from `REASON_CODES`, so a message or a
  membership test has exactly one source.
- `reason(code)`, `message_for(code)` and `codes_in(category)` are the accessors. An
  unknown code raises in `reason()` and renders as itself in `message_for()` — an unknown
  code is never blank and never a zero.
- `EXECUTION_STAGES` is the stage vocabulary of the single execution path. A stage says
  how far the pipeline got; `ExecutionResult.reason` says why it stopped. They are
  different axes and are deliberately not merged (see §4).

The `source` column names the module that defines the behaviour, so an operator reading a
log knows where to look. The string never appears in two modules as an independent
literal.

---

## 3. The codes

Layer is where the code is emitted today: `core`, `sdk`, `adapters`, `api`, `engine`,
`executor`. HTTP is the status the code actually returns at the HTTP edge; `—` means the
code never crosses HTTP as an error (it is a persisted row reason, a WS-free body `reason`
or a process-local refusal).

### Authentication, keys and scopes (15)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `UNAUTHORIZED` | api | 401 | no | warning | The bearer token is missing or invalid. |
| `FORBIDDEN` | api | 403 | no | warning | The principal lacks the route's scope. |
| `API_KEY_NOT_FOUND` | api | 404 | no | warning | No agent key has that id. |
| `API_KEY_PEPPER_REQUIRED` | api | 409 | no | warning | Agent keys cannot be managed without a pepper. |
| `API_KEY_SCOPES_REQUIRED` | api | 422 | no | warning | A key was minted with no scopes. |
| `API_KEY_SCOPE_UNKNOWN` | api | 422 | no | warning | A key names a scope that does not exist. |
| `API_KEY_SUBJECT_INVALID` | api | 422 | no | warning | The key's subject is empty or too long. |
| `PROVIDER_KEY_ERROR` | api | 422 | no | warning | A provider credential operation failed. |
| `PROVIDER_KEY_INVALID` | api | 422 | no | warning | The supplied provider credential is invalid. |
| `PROVIDER_KEY_PROVIDER_UNKNOWN` | api | 422 | no | warning | The named provider is not in the catalogue. |
| `PROVIDER_KEY_NOT_FOUND` | api | 404 | no | warning | No stored credential exists for that provider. |
| `PROVIDER_KEY_ALREADY_EXISTS` | api | 409 | no | warning | A credential for that provider already exists. |
| `PROVIDER_KEY_ENCRYPTION_UNAVAILABLE` | api | 503 | no | warning | The credential cipher is not configured. |
| `PROVIDER_KEY_FORGET_FAILED` | api | 503 | no | warning | The stored credential could not be deleted. |
| `PROVIDER_KEY_PEPPER_REQUIRED` | api | 503 | no | warning | Provider keys cannot be stored without a pepper. |

### Request validation and not-found (9)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `STRATEGY_NOT_FOUND` | api | 404 | no | warning | No strategy has that id. |
| `PROFILE_NOT_FOUND` | api | 404 | no | warning | No protection profile has that name. |
| `ARM_NOT_FOUND` | api | 404 | no | warning | No protection arm has that id. |
| `ARM_NOT_EDITABLE` | api | 409 | no | warning | The arm is not armed, so it cannot be edited. |
| `ARM_NOT_CANCELLABLE` | api | 409 | no | warning | The arm is not armed, so it cannot be cancelled. |
| `CHAIN_NOT_FOUND` | api | 404 | no | warning | The chain is not in the registry. |
| `INVALID_CURSOR` | api | 400 | no | warning | The pagination cursor is not decodable. |
| `INVALID_FILTER` | api | 422 | no | warning | A query filter is not a valid value for its field. |
| `INVALID_PRECONDITION` | api | 400 | no | warning | The If-Match header is not an integer arm version. |

### Risk gate and order refusals (33)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `INVALID_SIDE` | core | — | no | warning | The order side is neither buy nor sell. |
| `INVALID_AMOUNT` | core | — | no | warning | The order amount is zero or negative. |
| `ORDER_LIMIT` | core | — | no | warning | The order exceeds the per-order cap. |
| `PROFILE_LOOSENS_STRATEGY` | api | 409 | no | warning | An order asked for a risk profile looser than the one its strategy is pinned to, so the strategy's bound would not apply. |
| `SIZE_BELOW_MIN` | core | — | no | warning | The order is under the profile's minimum ticket. |
| `SIZE_ABOVE_MAX` | core | — | no | warning | The order is over the profile's maximum ticket. |
| `EQUITY_UNKNOWN` | core | — | no | warning | Equity could not be read, so percentage caps cannot be sized. |
| `PAPER_MARK_LIMIT` | core | — | no | warning | The order exceeds what a paper mark may price. |
| `PRICE_IMPACT_TOO_HIGH` | core | — | no | warning | The quote's price impact exceeds the ceiling. |
| `QUOTE_STALE` | core | — | no | warning | The quote is older than its freshness budget. |
| `MARK_TIMESTAMP_FUTURE` | core | — | no | warning | A market observation is dated ahead of the local clock beyond the tolerated skew. |
| `PRICE_UNKNOWN` | core | — | no | warning | No usable price exists for the token. |
| `LIQUIDITY_UNKNOWN` | core | — | no | warning | Pool liquidity could not be read. |
| `LIQUIDITY_TOO_LOW` | core | — | no | warning | Pool liquidity is under the configured floor. |
| `NO_PAIR` | core | — | no | warning | No market pair exists for the token. |
| `SNAPSHOT_STALE` | core | — | no | warning | The market snapshot is older than its freshness budget. |
| `PAIR_AGE_UNKNOWN` | core | — | no | warning | The pair's creation time could not be read, so its age cannot be gated. |
| `PAIR_TOO_YOUNG` | core | — | no | warning | The pair is younger than the configured minimum age. |
| `EXIT_NO_ROUTE` | core | — | no | critical | An exit has no executable route, so the position cannot be reduced. |
| `NO_SELL_ROUTE` | core | — | no | warning | No sell route is executable for this token. |
| `NO_ROUTE` | core | — | no | warning | No executable route of any kind was found. |
| `POSITION_LIMIT` | core | — | no | warning | The order would breach the per-token position cap. |
| `MAX_OPEN_POSITIONS` | core | — | no | warning | The account already holds the maximum number of positions. |
| `DAILY_LOSS_LIMIT` | core | — | no | warning | Realized daily loss reached the configured limit. |
| `MISSING_RISK_INPUTS` | core | — | no | warning | A risk decision was requested with no market snapshot and no exit quote. |
| `RISK_DENIED` | executor | — | no | warning | A run was denied without a specific reason code. |
| `IDEMPOTENCY_CONFLICT` | api | 409 | no | warning | The idempotency key was already used for a different order. |
| `POSITION_CHANGED` | api | — | no | warning | The position moved between pricing and submission. |
| `INSUFFICIENT_POSITION` | api | — | no | warning | A sell asks for more than the position holds. |
| `INSUFFICIENT_CASH` | api | — | no | warning | The paper wallet cannot fund the buy. |
| `PROFILE_UNKNOWN` | api | — | no | warning | The requested risk profile is not allowed for this account. |
| `WALLET_UNKNOWN` | api | — | no | warning | No paper wallet settles this chain. |
| `SETTLEMENT_ASSET_UNKNOWN` | api | — | no | warning | The paper wallet does not settle in the ledger asset. |

### Market data, quotes and routes (6)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `MARKET_DATA_UNAVAILABLE` | engine | — | yes | warning | No fresh market data exists for the decision. |
| `QUOTE_TIMEOUT` | executor | — | yes | warning | The quote stage exceeded its deadline. |
| `NO_QUOTE` | executor | — | yes | warning | The venue returned no quote. |
| `QUOTE_SIZE_MISMATCH` | executor | — | no | warning | The quoted size is not the requested size within tolerance. |
| `QUOTE_NOTIONAL_TOO_LARGE` | api | 422 | no | warning | The requested quote notional exceeds the route's cap. |
| `QUOTE_SLIPPAGE_TOO_HIGH` | api | 422 | no | warning | The requested or realised slippage exceeds the cap. |

### Custody, vault and consent (9)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `VAULT_UNREACHABLE` | core | — | no | critical | The signer or device could not be contacted. |
| `VAULT_APPROVAL_DENIED` | core | — | no | warning | The user or device refused the signature. |
| `VAULT_UNKNOWN_HANDLE` | core | — | no | warning | The key handle does not exist, or belongs to another principal (deliberately alike). |
| `VAULT_LOCKED` | core | — | no | warning | The vault is locked; the key is unavailable. |
| `VAULT_WATCH_ONLY` | core | — | no | critical | A watch-only custody tier can never sign. |
| `VAULT_SIGN_FAILED` | core | — | no | critical | The signer failed to produce a signature. |
| `VAULT_BOUNDARY_REQUIRED` | core | — | no | critical | A non-paper pipeline was started without a signing boundary. |
| `CUSTODY_OWNER_MISMATCH` | core | — | no | critical | The order's principal is not the key handle's owner. |
| `AUTOMATION_CONSENT_REQUIRED` | core | — | no | critical | An unattended run has no consent record for that user and automation. |

### Approvals and automation (12)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `AWAITING_APPROVAL` | api | — | no | info | An agent order under the manual automation tier is held for a person's decision. |
| `APPROVAL_NOT_FOUND` | api | 404 | no | warning | No approval has that id. |
| `APPROVAL_EXPIRED` | api | 409 | no | warning | The approval window closed before a decision; no order was placed. |
| `APPROVAL_NOT_PENDING` | api | 409 | no | warning | The approval was already decided or expired. |
| `APPROVAL_REQUIRES_PERSON` | api | 403 | no | warning | An agent principal tried to decide an approval or change the automation tier. |
| `APPROVAL_INTENT_INVALID` | api | 409 | no | warning | The stored intent behind an approval cannot be read. |
| `APPROVAL_STATE_UNREADABLE` | api | 409 | no | critical | The approval queue could not be read; the order refused closed. |
| `APPROVAL_SUPERSEDED` | api | — | no | info | A newer proposal for the same order replaced this approval. |
| `APPROVAL_REJECTED` | api | — | no | info | A person rejected the proposed order. |
| `APPROVAL_DECISION_NOTE_INVALID` | api | 409 | no | warning | A rejection without a person's written reason was refused. |
| `AUTOMATION_TIER_UNKNOWN` | api | 409 | no | warning | The requested automation tier is not manual or full_auto. |
| `EXECUTION_REFUSED` | api | — | no | warning | An approved execution ended without a more specific reason code. |

### Transaction binding and signing (16)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `SIGNING_BOUNDARY_REQUIRED` | core | — | no | critical | A submit was attempted with no signing boundary in a real-money mode. |
| `TRANSACTION_NOT_APPROVABLE` | core | — | no | critical | The order carries no approved intent to bind against. |
| `TRANSACTION_UNDECODABLE` | core | — | no | critical | The bytes are not a transaction this boundary can read. |
| `TRANSACTION_PROGRAM_MISMATCH` | core | — | no | critical | The program id changed after approval. |
| `TRANSACTION_CLUSTER_MISMATCH` | core | — | no | critical | The cluster changed after approval. |
| `TRANSACTION_MINT_MISMATCH` | core | — | no | critical | The mint changed after approval. |
| `TRANSACTION_SIDE_MISMATCH` | core | — | no | critical | The side changed after approval. |
| `TRANSACTION_AMOUNT_MISMATCH` | core | — | no | critical | The amount changed after approval. |
| `TRANSACTION_MIN_OUT_MISMATCH` | core | — | no | critical | The minimum-output floor changed after approval. |
| `TRANSACTION_RECIPIENT_MISMATCH` | core | — | no | critical | The recipient changed after approval. |
| `TRANSACTION_IDEMPOTENCY_MISMATCH` | core | — | no | critical | The idempotency key changed after approval. |
| `TRANSACTION_DIGEST_MISMATCH` | core | — | no | critical | The signed bytes do not match the approved digest. |
| `TRANSACTION_APPROVAL_MISMATCH` | core | — | no | critical | The signature belongs to another approval's digest. |
| `TRANSACTION_UNSIGNED` | core | — | no | critical | No signature is present on a payload that must be signed. |
| `TRANSACTION_BUILD_FAILED` | core | — | no | critical | The adapter failed to build the unsigned transaction. |
| `TRANSACTION_SIGN_FAILED` | core | — | no | critical | The signer failed. |

### Kill switch and operational governance (3)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `KILL_SWITCH_ACTIVE` | executor | — | no | critical | The operator switch is on; no new risk may be taken. |
| `KILL_SWITCH_ARMED` | api | — | no | warning | The kill switch is armed; new risk-increasing orders are refused, exits settle. |
| `KILL_SWITCH_UNAVAILABLE` | executor | — | yes | critical | The switch state could not be read, so execution fails closed. |

### Strategy parameters (11)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `STRATEGY_PARAM_MISSING` | core | 422 | no | warning | A required strategy parameter is absent. |
| `STRATEGY_PARAM_TYPE` | core | 422 | no | warning | A strategy parameter has the wrong type or is not finite. |
| `STRATEGY_PARAM_RANGE` | core | 422 | no | warning | A strategy parameter is outside its range. |
| `STRATEGY_PARAM_UNKNOWN` | core | 422 | no | warning | The parameters carry a key the kind does not declare. |
| `STRATEGY_LOOKBACK_UNSUPPORTED` | core | 422 | no | warning | The lookback is not in the supported set. |
| `STRATEGY_GRAPH_INVALID` | core | 422 | no | warning | A graph strategy's document is missing or invalid. |
| `STRATEGY_KIND_UNSUPPORTED` | core | 422 | no | warning | The strategy kind is not one this build supports. |
| `POLICY_FIELD_UNKNOWN` | core | 422 | no | warning | A caller asked for one field of a trade profile by a name the registry does not declare. |
| `POLICY_INVALID` | api | 422 | no | warning | A stored or requested policy contradicts itself: a bound sits below the size it is supposed to allow. |
| `RISK_ACK_REQUIRED` | api | 409 | no | warning | Changing a strategy to a profile that loosens a bound governing the owner's own funds needs an explicit acknowledgement. |
| `TRADE_POLICY_UPDATED` | api | — | no | info | A strategy's risk profile changed; the run log records the previous and the new value. |

### Strategy graph (21)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `GRAPH_SCHEMA` | core | 422 | no | warning | The document is not a valid graph document. |
| `NODE_TYPE_UNKNOWN` | core | 422 | no | warning | A node names a type the catalogue does not know. |
| `PARAM_INVALID` | core | 422 | no | warning | A node parameter is out of range or the wrong type. |
| `PORT_UNKNOWN` | core | 422 | no | warning | An edge references a port that does not exist. |
| `PIN_TYPE_MISMATCH` | core | 422 | no | warning | An edge connects incompatible pin types. |
| `INPUT_MULTIPLE` | core | 422 | no | warning | A data input is wired more than once. |
| `EXEC_FANOUT` | core | 422 | no | warning | An exec output feeds more than one target. |
| `INPUT_REQUIRED` | core | 422 | no | warning | A required input port is unconnected. |
| `CYCLE` | core | 422 | no | warning | The graph contains a cycle. |
| `NO_EVENT` | core | 422 | no | warning | The graph has no event tick node. |
| `NO_ACTION` | core | 422 | no | warning | The graph has no reachable order action. |
| `ACTION_UNREACHABLE` | core | 422 | no | warning | An action node is unreachable from any event. |
| `NODE_ORPHAN` | core | — | no | info | A node's output feeds nothing. |
| `WARMUP_LONG` | core | — | no | info | Warmup bars exceed the supported maximum. |
| `NO_EXIT` | core | — | no | info | Buys are reachable but no sell is. |
| `DUPLICATE_ORDER_PATH` | core | — | no | info | Two order nodes sit on one exec path. |
| `INVALID_GRAPH` | core | — | no | warning | The document could not be parsed or failed validation at run time. |
| `UNKNOWN_CONDITION` | core | — | no | warning | A branch condition was undecidable, so neither branch ran. |
| `SUPPRESSED_ONE_ORDER_PER_TICK` | core | — | no | warning | A second order on the same tick was dropped. |
| `GRAPH_RUNTIME_UNAVAILABLE` | engine | — | no | warning | No graph runtime is configured for this process. |
| `GRAPH_CONTEXT_UNAVAILABLE` | engine | — | no | warning | The graph context could not be built. |

### Strategy plugins (34)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `PLUGIN_MANIFEST_INVALID` | sdk | 422 | no | warning | The manifest is not a valid declaration. |
| `PLUGIN_MANIFEST_UNKNOWN_KEY` | sdk | 422 | no | warning | The manifest carries a key the schema does not declare. |
| `PLUGIN_MANIFEST_UNREADABLE` | sdk | 422 | no | warning | The manifest file could not be read. |
| `PLUGIN_MANIFEST_TOO_LARGE` | sdk | 422 | no | warning | The manifest exceeds the size cap. |
| `PLUGIN_MANIFEST_MISSING` | sdk | 422 | no | warning | No manifest exists where one is required. |
| `PLUGIN_SDK_INVALID` | sdk | 422 | no | warning | The declared SDK version is not parseable. |
| `PLUGIN_SDK_INCOMPATIBLE` | sdk | 422 | no | warning | The declared SDK version is outside the host's range. |
| `PLUGIN_PARAMS_INVALID` | sdk | 422 | no | warning | Plugin configuration does not satisfy its schema. |
| `PLUGIN_SCHEMA_UNSUPPORTED` | sdk | 422 | no | warning | The declared parameter schema uses an unsupported construct. |
| `PLUGIN_ERROR` | sdk | — | no | warning | A plugin failed without a more specific code. |
| `PLUGIN_LOAD_FAILED` | sdk | — | no | warning | The manifest is valid but the entrypoint could not be imported. |
| `PLUGIN_CONTRACT_VIOLATION` | sdk | — | no | warning | The plugin returned something the host refuses. |
| `PLUGIN_NOT_CONFIGURED` | api | 422 | no | warning | The strategy has no plugin id to run. |
| `PLUGIN_NOT_FOUND` | engine | 404 | no | warning | No available plugin has that id. |
| `PLUGIN_CHAIN_UNSUPPORTED` | api | 422 | no | warning | The plugin does not declare this chain. |
| `PLUGIN_VERSION_MISMATCH` | api | 422 | no | warning | The requested plugin version is not the installed one. |
| `PLUGIN_UNAVAILABLE` | engine | — | no | warning | The plugin exists in the catalogue but cannot run. |
| `PLUGIN_DUPLICATE_ID` | engine | — | no | info | Two discovery sources provide the same plugin id. |
| `PLUGIN_PATH_MISSING` | engine | — | no | warning | A configured plugin path does not exist. |
| `PLUGIN_PATH_UNREADABLE` | engine | — | no | warning | A configured plugin path could not be listed. |
| `PLUGIN_INTENT_NOT_PERMITTED` | engine | — | no | warning | The plugin submitted an intent without the permission. |
| `PLUGIN_MULTIPLE_INTENTS` | engine | — | no | warning | The plugin returned more than one intent for a tick. |
| `PLUGIN_INTENT_INVALID` | engine | — | no | warning | The intent payload could not be read or has a non-positive notional. |
| `PLUGIN_UNSUPPORTED_INTENT` | engine | — | no | warning | The intent uses a field the host does not support. |
| `PLUGIN_POSITION_UNKNOWN` | engine | — | no | warning | A buy needs the position's value and no price exists. |
| `PLUGIN_CONTEXT_UNAVAILABLE` | engine | — | no | warning | The context builder failed. |
| `PLUGIN_RUNTIME_UNAVAILABLE` | engine | — | no | warning | No plugin runtime is configured for this process. |
| `PLUGIN_QUARANTINED` | engine | — | no | warning | The host quarantined the plugin after a failure. |
| `PLUGIN_SPAWN_FAILED` | engine | — | no | warning | The worker process could not be started. |
| `PLUGIN_CRASHED` | engine | — | no | warning | The worker process died. |
| `PLUGIN_CONTEXT_INVALID` | engine | — | no | warning | The context payload sent to the worker is invalid. |
| `PLUGIN_TIMEOUT` | engine | — | yes | warning | The worker exceeded its deadline. |
| `PLUGIN_PROTOCOL_ERROR` | engine | — | no | warning | The worker sent a message the host cannot read. |
| `PLUGIN_START_FAILED` | engine | — | no | warning | The worker started but failed its handshake. |

### Backtests (7)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `BACKTEST_STRATEGY_NOT_GRAPH` | api | 422 | no | warning | Only graph strategies can be backtested. |
| `BACKTEST_STRATEGY_MISMATCH` | api | 422 | no | warning | The requested strategy and graph version disagree. |
| `BACKTEST_TARGET_REQUIRED` | api | 422 | no | warning | A backtest needs a target token. |
| `BACKTEST_NO_CANDLES` | api | 422 | no | warning | No candle data covers the requested window. |
| `BACKTEST_BARS_LIMIT` | api | 422 | no | warning | The requested window exceeds the bar limit. |
| `BACKTEST_INVALID` | api | 422 | no | warning | The backtest could not be built from its inputs. |
| `BACKTEST_NOT_FOUND` | api | 404 | no | warning | No backtest has that id. |

### Protection arms and profiles (15)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `PROTECTION_PARAM_TYPE` | core | 422 | no | warning | An arm parameter has the wrong type. |
| `PROTECTION_PARAM_MISSING` | core | 422 | no | warning | A required arm parameter is absent. |
| `PROTECTION_PARAM_RANGE` | core | 422 | no | warning | An arm parameter is out of range or the ladder is inconsistent. |
| `PROTECTION_PARAM_UNKNOWN` | core | 422 | no | warning | The arm carries a key the kind does not declare. |
| `PROTECTION_KIND_UNSUPPORTED` | core | 422 | no | warning | The arm kind is not stop-loss, trailing-stop or take-profit. |
| `PROTECTION_CONFLICT` | api | 409 | yes | warning | The arm version changed under the caller (compare-and-swap lost). |
| `PROFILE_PARAM_TYPE` | core | 422 | no | warning | A profile parameter has the wrong type. |
| `PROFILE_PARAM_MISSING` | core | 422 | no | warning | A profile has no defaults. |
| `PROFILE_PARAM_RANGE` | core | 422 | no | warning | A profile name, chain or value is out of range. |
| `PROFILE_KIND_UNSUPPORTED` | core | 422 | no | warning | The profile kind is neither manual nor smart. |
| `PROFILE_RULES_REQUIRED` | core | 422 | no | warning | A smart profile was saved with no rules. |
| `PROFILE_RULES_NOT_ALLOWED` | core | 422 | no | warning | A manual profile was saved with rules. |
| `NOT_ARMED` | core | — | no | info | The arm's status is not armed. |
| `EXPIRED` | core | — | no | info | The arm's expiry has passed. |
| `INVALID_ARM` | core | — | no | warning | The arm's parameters cannot compute a threshold. |

### Engine run outcomes (14)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `INVALID_PARAMS` | engine | — | no | warning | The strategy's parameters failed validation at run time. |
| `ORDER_EXECUTOR_UNAVAILABLE` | engine | 503 | yes | warning | The order executor is not wired into this process. |
| `EXECUTION_FAILED` | engine | — | no | warning | The executor raised while running the order. |
| `ORDER_REJECTED` | engine | — | no | warning | The executor refused the order; the order row carries the specific code. |
| `ORDER_FAILED` | engine | — | no | warning | An arm's exit order failed. |
| `ARM_CHANGED` | engine | — | yes | warning | The arm row changed between read and write. |
| `EVALUATION_FAILED` | engine | — | no | warning | The arm evaluator raised. |
| `STRATEGY_RUN_NOT_LOGGED` | api | 500 | yes | critical | A run completed but its log row could not be written. |
| `TIMEOUT` | executor | — | yes | warning | Order confirmation exceeded its deadline. |
| `VENUE_ERROR` | executor | — | yes | warning | The venue raised while submitting. |
| `VENUE_REJECTED` | executor | — | no | warning | The venue refused the order. |
| `UNBOOKABLE_RECEIPT` | executor | — | no | critical | The receipt cannot be booked to the ledger. |
| `RECEIPT_EXCEEDS_ORDER` | executor | — | no | critical | The receipt fills more than the order asked for. |
| `JOURNAL_FAILED` | executor | — | yes | critical | The ledger journal could not be written for a confirmed fill. |

### Upstream providers and adapters (12)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `ADAPTER_ERROR` | adapters | 502 | yes | warning | An adapter failed without a more specific code. |
| `NOT_FOUND` | adapters | 404 | no | warning | The adapter has no data for that entity. |
| `UPSTREAM_ERROR` | adapters | 502 | yes | warning | An upstream provider returned an error. |
| `UPSTREAM_UNREACHABLE` | adapters | 502 | yes | warning | The bars could not be obtained: transport failure or an exhausted rate limit. |
| `RATE_LIMITED` | adapters | 429 | yes | warning | The provider or the local limiter refused the request rate. |
| `PROVIDER_BUDGET_EXHAUSTED` | adapters | 429 | yes | warning | The provider's queue or wait budget is exhausted. |
| `PROVIDER_KEY_UNAVAILABLE` | adapters | 503 | no | warning | A stored provider credential could not be loaded. |
| `CANDLES_UNSUPPORTED_CHAIN` | adapters | 404 | no | warning | The chain has no candle provider; history is a capability, not a default. |
| `CANDLES_NOT_FOUND` | adapters | 404 | no | warning | The provider has no series for this token and timeframe. |
| `CANDLES_EMPTY` | adapters | — | no | info | The provider returned no closed bars. |
| `NO_POOL` | adapters | — | no | info | No dex pool exists for this token, so there is no price history. |
| `SAFETY_NOT_IMPLEMENTED` | adapters | — | no | warning | Token safety screening is not implemented, so safety is unknown. |

### Read model, ledger and alerts (13)

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `PROVIDER_STALE` | api | — | no | warning | A price source stopped producing fresh observations. |
| `NO_MARKET_ROWS` | api | — | no | info | The screener returned no market rows. |
| `NO_QUALIFIED_MOVERS` | api | — | no | info | Rows existed but none passed the mover filters. |
| `NO_SERIES_DATA` | api | — | no | info | A performance series has no equity and no realised points. |
| `MARKS_UNKNOWN` | api | — | no | info | The series exists but its marks are unknown. |
| `POSITION_MARK_FUTURE` | api | — | no | info | A position mark is dated ahead of the clock. |
| `POSITION_MARK_STALE` | api | — | no | warning | A position mark is older than its budget. |
| `POSITION_MARK_UNKNOWN` | api | — | no | warning | A position has no usable mark. |
| `ORDERS_REJECTED` | api | — | no | info | Rejected orders accumulated in the alert window. |
| `RECONCILIATION_FAILED` | api | — | no | critical | At least one ledger reconciliation check failed. |
| `NEGATIVE_CASH` | api | — | no | critical | A cash account sums below zero. |
| `NEGATIVE_POSITION` | api | — | no | critical | A position account holds a negative quantity. |
| `UNKNOWN` | api | — | no | warning | A rejected order carries no reason code. |

### Decision and informational outcomes (29)

These are not refusals. They record what a strategy or arm decided, so a run log explains
itself. `ReasonSpec.is_denial` is `False` for this category.

| Code | Layer | HTTP | Retry | Severity | Meaning |
|---|---|---|---|---|---|
| `GRAPH_BUY` | core | — | no | info | The graph produced a buy intent. |
| `GRAPH_SELL` | core | — | no | info | The graph produced a sell intent. |
| `GRAPH_NO_ACTION` | core | — | no | info | The graph produced no order this tick. |
| `PAUSED` | core | — | no | info | The strategy is not active, so nothing ran. |
| `GRAPH_NEEDS_CONTEXT` | core | — | no | info | A graph strategy was evaluated through an entry point that lacks candles or position state. |
| `PLUGIN_NEEDS_CONTEXT` | core | — | no | info | A plugin strategy was evaluated through the snapshot-only entry point. |
| `MARK_STALE` | core | — | no | info | The mark is stale, so no decision is trusted. |
| `CHANGE_UNKNOWN` | core | — | no | info | The lookback price change is unavailable. |
| `MAX_POSITION` | core | — | no | info | The position cap leaves no headroom to buy. |
| `NO_POSITION` | core | — | no | info | A sell was evaluated with no position held. |
| `UNKNOWN_KIND` | core | — | no | info | The strategy kind has no evaluator. |
| `DCA_BUY` | core | — | no | info | The DCA interval elapsed and a buy fired. |
| `DCA_INTERVAL` | core | — | no | info | The DCA interval has not elapsed. |
| `THRESHOLD_BUY` | core | — | no | info | Price is below the buy threshold. |
| `THRESHOLD_SELL` | core | — | no | info | Price is above the sell threshold. |
| `THRESHOLD_WAIT` | core | — | no | info | Price is inside the threshold band. |
| `MOMENTUM_ENTRY` | core | — | no | info | Lookback change crossed the momentum entry band. |
| `MOMENTUM_EXIT` | core | — | no | info | Lookback change crossed the momentum exit band. |
| `MOMENTUM_WAIT` | core | — | no | info | Lookback change is between the momentum bands. |
| `DIP_ENTRY` | core | — | no | info | Lookback change crossed the dip entry band. |
| `DIP_WAIT` | core | — | no | info | Lookback change has not reached the dip band. |
| `ARMED` | core | — | no | info | The arm is live and holding; no threshold was crossed. |
| `TAKE_PROFIT` | core | — | no | info | Unrealized gain crossed the take-profit band. |
| `TAKE_PROFIT_LEVEL` | core | — | no | info | A take-profit ladder level was reached. |
| `STOP_LOSS` | core | — | no | info | Unrealized loss crossed the stop band. |
| `TRAILING_STOP` | core | — | no | info | Price fell from the peak by the trailing amount. |
| `PLUGIN_NO_ACTION` | engine | — | no | info | The plugin returned no intents. |
| `INTERVAL_NOT_ELAPSED` | engine | — | no | info | The scheduled interval has not elapsed. |
| `SHORTFALL_FUNDED` | api | — | no | info | A cash shortfall was funded rather than refused. |

---

## 4. Code → `Intent` / `Order` state

The state machines are normative in [DOMAIN-MODEL.md](DOMAIN-MODEL.md#4-state-machines).
A code never invents a state; it explains the transition that occurred.

| Where the code lands | Codes | Effect on the record |
|---|---|---|
| `Intent` → `denied` (risk) | every `risk` category code, `MISSING_RISK_INPUTS`, `RISK_DENIED` | The order row is still persisted with `status: "rejected"`; the attempt is replayable. |
| `Intent` → `expired` (quote) | `QUOTE_STALE`, `NO_QUOTE`, `QUOTE_TIMEOUT`, `SNAPSHOT_STALE`, `MARKET_DATA_UNAVAILABLE` | No order was submitted; nothing is booked. |
| `Intent` → `rejected` (identity/divergence) | `SIGNING_BOUNDARY_REQUIRED`, every `TRANSACTION_*`, `CUSTODY_OWNER_MISMATCH`, `VAULT_*`, `AUTOMATION_CONSENT_REQUIRED` | No signature, no submission. Real money fails closed. |
| `quoting` → `expired` (no route) | `NO_ROUTE`, `NO_SELL_ROUTE`, `EXIT_NO_ROUTE` | A `Position` with a held token and no route is `unsellable`, never `closed`. |
| `submitting` → `failed` (broadcast) | `VENUE_ERROR`, `VENUE_REJECTED`, `TIMEOUT`, `QUOTE_SIZE_MISMATCH` | No fill. The journal writes nothing. |
| `submitted` → `filled` / `partially_filled` | `UNBOOKABLE_RECEIPT`, `RECEIPT_EXCEEDS_ORDER` on failure; no code on success | A receipt that cannot be booked is a failure, never a zero fill. |
| `journal_error` | `JOURNAL_FAILED` | The fill happened; the journal did not. This is a critical operator page, not a refusal. |
| Any refusal, before submission | `KILL_SWITCH_*`, `IDEMPOTENCY_CONFLICT`, `POSITION_CHANGED`, `INSUFFICIENT_POSITION`, `INSUFFICIENT_CASH` | Refused before the venue; the row records why. |

`ExecutionStatus` (`filled`, `partially_filled`, `rejected`, `timeout`, `denied`,
`journal_error`) is the **stage** the pipeline reached. `ExecutionResult.reason` is the
**code** that explains it. They are separate axes: `denied` almost always carries a
`risk`-category reason, while `rejected` carries a signing or venue reason. Do not infer
one from the other.

---

## 5. Dual surface: what the UI shows

The web app renders the API code and the API message unchanged — that is a deliberate
product rule (`apps/web/components/ops.tsx`). The taxonomy is what makes that rule safe:
the message the UI prints is the registry's `message`, not a locally invented string.

| Surface | What it renders | Where |
|---|---|---|
| Generic error block | `code` badge + `message` verbatim | `apps/web/components/ops.tsx` `Unavailable` |
| Markets / launches read failure | `code` badge, retry hint for `RATE_LIMITED` | `apps/web/app/markets/page.tsx`, `launches/page.tsx` |
| Candle chart failure | friendly title/body keyed by code | `apps/web/lib/candle-view.ts` `candleErrorCopy` |
| Candle empty reason | friendly title/body keyed by `NO_POOL` / `CANDLES_EMPTY` | `apps/web/lib/candle-view.ts` `candleReasonCopy` |
| Token quote failure | friendly copy for `NO_SELL_ROUTE`, `QUOTE_STALE`, `RATE_LIMITED`; otherwise `message` | `apps/web/app/tokens/[chain]/[id]/page.tsx` `reasonMessage` |
| Risk rejection summary | raw `order.reason` counts, `UNKNOWN_REASON` when absent | `apps/web/app/risk/page.tsx` |
| Order row | raw `order.reason` | `apps/web/app/ledger/page.tsx` |
| Route-level error | `RENDER_FAILED` + `error.digest` | `apps/web/app/error.tsx` and the per-segment clones |
| Route not found | no code; a "Not found" page | `apps/web/app/not-found.tsx` |

**The UI mapping rule:** a code with a friendly title/body in `candle-view.ts` renders that
copy *and* keeps the code visible; every other code renders the API `message` untouched.
An unknown code renders itself, never a blank and never a fabricated message. The web does
not keep its own code list: it keys off the code string and falls through to the API text.

Three UI-only labels are **not** API codes and are deliberately absent from the registry:
`RENDER_FAILED` (a React render crashed), `UNKNOWN_REASON` (a null `order.reason`), and
`WEB_MODE_INVALID` (the web build's own mode guard). They are display states of the web
process, not refusals the platform emits.

---

## 6. Audit: what this pass found

The audit that produced this taxonomy compared every emitted code against the modules that
define them. It found the following, all resolved in the same change.

| Finding | Detail | Resolution |
|---|---|---|
| A code with no message | `MISSING_RISK_INPUTS` was emitted by `execution.py` but absent from `RISK_REASON_MESSAGES`, so `RiskReason.message` fell back to the code string. | Added to the taxonomy with a real message. |
| Two registries of the same risk codes | `RISK_REASON_MESSAGES` keys were the only list; the API re-typed ~20 of them as literals. | One registry; the messages dict is derived from it. |
| Three homes for one spelling | `SNAPSHOT_STALE` in `freshness.py` (private constant), `risk.py` (literal) and `types.py` (dict key). | All three now resolve to one spec. |
| A code in two meanings | `KILL_SWITCH_ACTIVE` (engine hold + account alert) and `KILL_SWITCH_ARMED` (order refusal) looked like duplicates. | Kept distinct and documented: the held code is what run logs and stress readers key on; collapsing them would break those readers (ADR-0034). |
| Near-synonyms, kept on purpose | `NO_ROUTE` / `NO_SELL_ROUTE` / `EXIT_NO_ROUTE`; `UPSTREAM_ERROR` / `UPSTREAM_UNREACHABLE`; `PROVIDER_KEY_UNAVAILABLE` / `PROVIDER_KEY_ERROR`. | Each names a different surface and a different operator action; all four rows state the difference. |
| `KILL_SWITCH_UNAVAILABLE` with no home | Defined twice (executor class attr, pipeline literal) and never in `RISK_REASON_MESSAGES`. | One registry row, `governance` category. |
| An unmatchable code | `JOURNAL_FAILED: {exc}` interpolated a free-form suffix, so no reader could match the string exactly. | The taxonomy records `JOURNAL_FAILED` as the stable prefix; the suffix stays operator detail. |
| One code, two contracts | `PROFILE_UNKNOWN` is both a `ValueError` argument and a persisted order reason. | Documented as one code with one meaning; the raise site is unchanged (no behaviour change). |
| Codes that never cross HTTP | 157 of 254 never appear as an HTTP error (executor custody codes, engine holds, read-model reasons). | The registry's `http_status` is `None` for each, so nobody invents a status for them. |
| An undocumented UI expectation | `apps/web/e2e/kill-switch-topbar.spec.ts` asserts `KILL_SWITCH_REASON_REQUIRED`, which no Python code emits. | Recorded as an open follow-up (§8); the taxonomy does not invent the code. |
| A message with no emitter | `APPROVAL_REQUIRED` was a key in `RISK_REASON_MESSAGES` that no code emits; the queue emits `AWAITING_APPROVAL`. | The derived dict keeps only codes the code base emits, so the phantom key is dropped rather than given a row. |

**What was deliberately not unified:** `RiskReason` (a `str` subclass returned from a
decision) and `ExecutionResult.reason` (a free-form field) still carry plain strings. They
now carry *taxonomy* strings, but changing their types would change the refusal contract,
which is out of scope for this item.

---

## 7. Adding a code

1. Add one `_S(...)` row to `_SPECS` in
   [`reason_codes.py`](../packages/core-py/src/ezchain_core/reason_codes.py).
2. Emit it from the module named in `source`. Do not add a second literal.
3. Add the row to the matching table in §3 of this document.
4. Run the suite. The drift guard fails if the code is emitted but not registered, or
   registered but not documented.

Never renumber, never rename, never reuse. If a failure disappeared, keep the row and note
it; a persisted row may still carry the code.

---

## 8. Open, tracked elsewhere

- `KILL_SWITCH_REASON_REQUIRED` is expected by the web kill-switch e2e test but is not
  emitted by the API (the release-reason guard raises a bare `ValueError`). Either the
  route gains the code or the test is corrected; the taxonomy will not list a code that
  nothing emits, because the drift guard checks both directions.
- `SIGNING_BOUNDARY_REQUIRED` and `VAULT_BOUNDARY_REQUIRED` remain distinct: the first
  refuses a pipeline with no boundary at all, the second refuses a boundary that cannot
  sign the caller's handle. Merging them would lose which condition an operator must fix.
- A future shared TypeScript package could import the generated table instead of the web
  keying off strings; today the web deliberately renders the API text unchanged.
