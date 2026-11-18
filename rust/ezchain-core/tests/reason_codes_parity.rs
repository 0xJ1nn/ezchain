//! Recorded cases from the Python oracle `ezchain_core.reason_codes`
//! (`packages/core-py/src/ezchain_core/reason_codes.py`), enumerated with the venv oracle
//! and recorded before the port. Every code, its nine fields, every category's ordered
//! membership and the lookup edge cases are mirrored here. ADR-0049/0050 parity gate.

use std::str::FromStr;

use ezchain_core::reason_codes::{
    codes_in, message_for, reason, risk_reason_message, CATEGORIES, REASON_SPECS,
    RISK_REASON_CODES, RISK_REASON_MESSAGES,
};
use ezchain_core::reason_codes::{Category, Layer, Severity};

/// One recorded `ReasonSpec`: code, category, layer, source, summary, message, http_status,
/// retryable, severity. Order is the Python declaration order.
type RecordedSpec = (
    &'static str,
    Category,
    Layer,
    &'static str,
    &'static str,
    &'static str,
    Option<u16>,
    bool,
    Severity,
);

const EXPECTED: &[RecordedSpec] = &[

    ("INVALID_SIDE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order side is neither buy nor sell.", "Order side must be buy or sell.", None, false, Severity::Warning),
    ("INVALID_AMOUNT", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order amount is zero or negative.", "Order amount must be greater than zero.", None, false, Severity::Warning),
    ("ORDER_LIMIT", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order exceeds the per-order cap.", "Order size exceeds the configured limit.", None, false, Severity::Warning),
    ("SIZE_BELOW_MIN", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order is under the profile's minimum ticket.", "Order size is below the configured minimum.", None, false, Severity::Warning),
    ("SIZE_ABOVE_MAX", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order is over the profile's maximum ticket.", "Order size is above the configured maximum.", None, false, Severity::Warning),
    ("EQUITY_UNKNOWN", Category::Risk, Layer::Core, "ezchain_core/risk.py", "Equity could not be read, so percentage caps cannot be sized.", "Account equity is unknown; cannot size against it.", None, false, Severity::Warning),
    ("PAPER_MARK_LIMIT", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order exceeds what a paper mark may price.", "Order size exceeds the paper-mark limit.", None, false, Severity::Warning),
    ("PRICE_IMPACT_TOO_HIGH", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The quote's price impact exceeds the ceiling.", "Price impact exceeds the configured ceiling.", None, false, Severity::Warning),
    ("QUOTE_STALE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The quote is older than its freshness budget.", "Quote is too old to trust.", None, false, Severity::Warning),
    ("MARK_TIMESTAMP_FUTURE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "A market observation is dated ahead of the local clock beyond the tolerated skew.", "Market observation is dated ahead of the clock.", None, false, Severity::Warning),
    ("PRICE_UNKNOWN", Category::Risk, Layer::Core, "ezchain_core/risk.py", "No usable price exists for the token.", "Token price is unknown.", None, false, Severity::Warning),
    ("LIQUIDITY_UNKNOWN", Category::Risk, Layer::Core, "ezchain_core/risk.py", "Pool liquidity could not be read.", "Token liquidity is unknown.", None, false, Severity::Warning),
    ("LIQUIDITY_TOO_LOW", Category::Risk, Layer::Core, "ezchain_core/risk.py", "Pool liquidity is under the configured floor.", "Token liquidity is below the minimum.", None, false, Severity::Warning),
    ("NO_PAIR", Category::Risk, Layer::Core, "ezchain_core/risk.py", "No market pair exists for the token.", "No market pair is available for the token.", None, false, Severity::Warning),
    ("SNAPSHOT_STALE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The market snapshot is older than its freshness budget.", "Market snapshot is stale.", None, false, Severity::Warning),
    ("PAIR_AGE_UNKNOWN", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The pair's creation time could not be read, so its age cannot be gated.", "Pair creation time is unknown.", None, false, Severity::Warning),
    ("PAIR_TOO_YOUNG", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The pair is younger than the configured minimum age.", "Pair is younger than the minimum age.", None, false, Severity::Warning),
    ("EXIT_NO_ROUTE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "An exit has no executable route, so the position cannot be reduced.", "No executable exit route is available.", None, false, Severity::Critical),
    ("NO_SELL_ROUTE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "No sell route is executable for this token.", "No sell route is available.", None, false, Severity::Warning),
    ("NO_ROUTE", Category::Risk, Layer::Core, "ezchain_core/risk.py", "No executable route of any kind was found.", "No route is available.", None, false, Severity::Warning),
    ("POSITION_LIMIT", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The order would breach the per-token position cap.", "Position size would exceed the configured limit.", None, false, Severity::Warning),
    ("MAX_OPEN_POSITIONS", Category::Risk, Layer::Core, "ezchain_core/risk.py", "The account already holds the maximum number of positions.", "Maximum number of open positions reached.", None, false, Severity::Warning),
    ("DAILY_LOSS_LIMIT", Category::Risk, Layer::Core, "ezchain_core/risk.py", "Realized daily loss reached the configured limit.", "Daily loss limit reached.", None, false, Severity::Warning),
    ("MISSING_RISK_INPUTS", Category::Risk, Layer::Core, "ezchain_core/execution.py", "A risk decision was requested with no market snapshot and no exit quote.", "Risk inputs are missing; unknown is not safe.", None, false, Severity::Warning),
    ("RISK_DENIED", Category::Risk, Layer::Executor, "ezchain_executor/pipeline.py", "A run was denied without a specific reason code.", "Risk denied the order.", None, false, Severity::Warning),
    ("IDEMPOTENCY_CONFLICT", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The idempotency key was already used for a different order.", "Idempotency key already used with different parameters.", Some(409), false, Severity::Warning),
    ("POSITION_CHANGED", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The position moved between pricing and submission.", "Position changed while the order was being priced.", None, false, Severity::Warning),
    ("INSUFFICIENT_POSITION", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "A sell asks for more than the position holds.", "Insufficient position to sell.", None, false, Severity::Warning),
    ("INSUFFICIENT_CASH", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The paper wallet cannot fund the buy.", "Insufficient cash to cover the order.", None, false, Severity::Warning),
    ("PROVIDER_STALE", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A price source stopped producing fresh observations.", "A price source went stale.", None, false, Severity::Warning),
    ("VAULT_UNREACHABLE", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The signer or device could not be contacted.", "The signing vault is unreachable.", None, false, Severity::Critical),
    ("VAULT_APPROVAL_DENIED", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The user or device refused the signature.", "Approval was denied on the signing device.", None, false, Severity::Warning),
    ("VAULT_UNKNOWN_HANDLE", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The key handle does not exist, or belongs to another principal (deliberately alike).", "The key handle is unknown.", None, false, Severity::Warning),
    ("VAULT_LOCKED", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The vault is locked; the key is unavailable.", "The signing vault is locked.", None, false, Severity::Warning),
    ("VAULT_WATCH_ONLY", Category::Custody, Layer::Core, "ezchain_core/vault.py", "A watch-only custody tier can never sign.", "A watch-only key cannot sign.", None, false, Severity::Critical),
    ("VAULT_SIGN_FAILED", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The signer failed to produce a signature.", "Signing failed.", None, false, Severity::Critical),
    ("VAULT_BOUNDARY_REQUIRED", Category::Custody, Layer::Core, "ezchain_core/vault.py", "A non-paper pipeline was started without a signing boundary.", "A signing boundary is required for real money.", None, false, Severity::Critical),
    ("CUSTODY_OWNER_MISMATCH", Category::Custody, Layer::Core, "ezchain_core/vault.py", "The order's principal is not the key handle's owner.", "The order's principal does not own that key.", None, false, Severity::Critical),
    ("AUTOMATION_CONSENT_REQUIRED", Category::Custody, Layer::Core, "ezchain_core/vault.py", "An unattended run has no consent record for that user and automation.", "Full auto needs an acknowledged risk disclosure.", None, false, Severity::Critical),
    ("SIGNING_BOUNDARY_REQUIRED", Category::Signing, Layer::Core, "ezchain_core/signing.py", "A submit was attempted with no signing boundary in a real-money mode.", "A signing boundary is required.", None, false, Severity::Critical),
    ("TRANSACTION_NOT_APPROVABLE", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The order carries no approved intent to bind against.", "The order has nothing to approve.", None, false, Severity::Critical),
    ("TRANSACTION_UNDECODABLE", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The bytes are not a transaction this boundary can read.", "The transaction could not be decoded.", None, false, Severity::Critical),
    ("TRANSACTION_PROGRAM_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The program id changed after approval.", "The transaction program changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_CLUSTER_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The cluster changed after approval.", "The transaction cluster changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_MINT_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The mint changed after approval.", "The transaction mint changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_SIDE_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The side changed after approval.", "The transaction side changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_AMOUNT_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The amount changed after approval.", "The transaction amount changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_MIN_OUT_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The minimum-output floor changed after approval.", "The transaction minimum output changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_RECIPIENT_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The recipient changed after approval.", "The transaction recipient changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_IDEMPOTENCY_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The idempotency key changed after approval.", "The transaction idempotency key changed after approval.", None, false, Severity::Critical),
    ("TRANSACTION_DIGEST_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The signed bytes do not match the approved digest.", "The signed transaction does not match the approval.", None, false, Severity::Critical),
    ("TRANSACTION_APPROVAL_MISMATCH", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The signature belongs to another approval's digest.", "The signature covers a different approval.", None, false, Severity::Critical),
    ("TRANSACTION_UNSIGNED", Category::Signing, Layer::Core, "ezchain_core/signing.py", "No signature is present on a payload that must be signed.", "The transaction is unsigned.", None, false, Severity::Critical),
    ("TRANSACTION_BUILD_FAILED", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The adapter failed to build the unsigned transaction.", "The transaction could not be built.", None, false, Severity::Critical),
    ("TRANSACTION_SIGN_FAILED", Category::Signing, Layer::Core, "ezchain_core/signing.py", "The signer failed.", "The transaction could not be signed.", None, false, Severity::Critical),
    ("KILL_SWITCH_ACTIVE", Category::Governance, Layer::Executor, "ezchain_executor/kill_switch.py", "The operator switch is on; no new risk may be taken.", "Kill switch is active.", None, false, Severity::Critical),
    ("KILL_SWITCH_ARMED", Category::Governance, Layer::Api, "ezchain_api/services/paper.py", "The kill switch is armed; new risk-increasing orders are refused, exits settle.", "Kill switch is armed; new risk-increasing orders are refused.", None, false, Severity::Warning),
    ("KILL_SWITCH_UNAVAILABLE", Category::Governance, Layer::Executor, "ezchain_executor/kill_switch.py", "The switch state could not be read, so execution fails closed.", "The kill switch state is unreadable.", None, true, Severity::Critical),
    ("GATE_NOT_MET", Category::Governance, Layer::Api, "ezchain_api/mode.py", "A live start has no verified gate record for this build, so execution is refused.", "The live gate is not met; execution stays in paper.", None, false, Severity::Critical),
    ("GRAPH_SCHEMA", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "The document is not a valid graph document.", "The graph document is invalid.", Some(422), false, Severity::Warning),
    ("NODE_TYPE_UNKNOWN", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "A node names a type the catalogue does not know.", "A node type is unknown.", Some(422), false, Severity::Warning),
    ("PARAM_INVALID", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "A node parameter is out of range or the wrong type.", "A node parameter is invalid.", Some(422), false, Severity::Warning),
    ("PORT_UNKNOWN", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "An edge references a port that does not exist.", "An edge references an unknown port.", Some(422), false, Severity::Warning),
    ("PIN_TYPE_MISMATCH", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "An edge connects incompatible pin types.", "Two connected pins have incompatible types.", Some(422), false, Severity::Warning),
    ("INPUT_MULTIPLE", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "A data input is wired more than once.", "A data input has more than one source.", Some(422), false, Severity::Warning),
    ("EXEC_FANOUT", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "An exec output feeds more than one target.", "An exec output fans out.", Some(422), false, Severity::Warning),
    ("INPUT_REQUIRED", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "A required input port is unconnected.", "A required input is not connected.", Some(422), false, Severity::Warning),
    ("CYCLE", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "The graph contains a cycle.", "The graph contains a cycle.", Some(422), false, Severity::Warning),
    ("NO_EVENT", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "The graph has no event tick node.", "The graph has no event node.", Some(422), false, Severity::Warning),
    ("NO_ACTION", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "The graph has no reachable order action.", "The graph has no action node.", Some(422), false, Severity::Warning),
    ("ACTION_UNREACHABLE", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "An action node is unreachable from any event.", "An action cannot be reached from an event.", Some(422), false, Severity::Warning),
    ("NODE_ORPHAN", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "A node's output feeds nothing.", "A node output is unused.", None, false, Severity::Info),
    ("WARMUP_LONG", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "Warmup bars exceed the supported maximum.", "Warmup exceeds the supported bar count.", None, false, Severity::Info),
    ("NO_EXIT", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "Buys are reachable but no sell is.", "The graph can buy but never sell.", None, false, Severity::Info),
    ("DUPLICATE_ORDER_PATH", Category::Graph, Layer::Core, "ezchain_core/graph/validate.py", "Two order nodes sit on one exec path.", "Two order nodes share an exec path.", None, false, Severity::Info),
    ("INVALID_GRAPH", Category::Graph, Layer::Core, "ezchain_core/graph/evaluate.py", "The document could not be parsed or failed validation at run time.", "The graph could not be evaluated.", None, false, Severity::Warning),
    ("UNKNOWN_CONDITION", Category::Graph, Layer::Core, "ezchain_core/graph/evaluate.py", "A branch condition was undecidable, so neither branch ran.", "A branch condition is unknown.", None, false, Severity::Warning),
    ("SUPPRESSED_ONE_ORDER_PER_TICK", Category::Graph, Layer::Core, "ezchain_core/graph/evaluate.py", "A second order on the same tick was dropped.", "Only one order per tick is allowed.", None, false, Severity::Warning),
    ("GRAPH_BUY", Category::Outcome, Layer::Core, "ezchain_core/graph/evaluate.py", "The graph produced a buy intent.", "The graph signalled a buy.", None, false, Severity::Info),
    ("GRAPH_SELL", Category::Outcome, Layer::Core, "ezchain_core/graph/evaluate.py", "The graph produced a sell intent.", "The graph signalled a sell.", None, false, Severity::Info),
    ("GRAPH_NO_ACTION", Category::Outcome, Layer::Core, "ezchain_core/graph/evaluate.py", "The graph produced no order this tick.", "The graph produced no order.", None, false, Severity::Info),
    ("PAUSED", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The strategy is not active, so nothing ran.", "The strategy is paused.", None, false, Severity::Info),
    ("GRAPH_NEEDS_CONTEXT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "A graph strategy was evaluated through an entry point that lacks candles or position state.", "A graph strategy needs the engine's context.", None, false, Severity::Info),
    ("PLUGIN_NEEDS_CONTEXT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "A plugin strategy was evaluated through the snapshot-only entry point.", "A plugin strategy needs the engine's context.", None, false, Severity::Info),
    ("MARK_STALE", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The mark is stale, so no decision is trusted.", "The mark is stale.", None, false, Severity::Info),
    ("CHANGE_UNKNOWN", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The lookback price change is unavailable.", "The lookback change is unknown.", None, false, Severity::Info),
    ("MAX_POSITION", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The position cap leaves no headroom to buy.", "The position cap is reached.", None, false, Severity::Info),
    ("NO_POSITION", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "A sell was evaluated with no position held.", "There is no position to sell.", None, false, Severity::Info),
    ("UNKNOWN_KIND", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The strategy kind has no evaluator.", "The strategy kind is unknown.", None, false, Severity::Info),
    ("DCA_BUY", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The DCA interval elapsed and a buy fired.", "DCA bought on schedule.", None, false, Severity::Info),
    ("DCA_INTERVAL", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "The DCA interval has not elapsed.", "DCA interval has not elapsed.", None, false, Severity::Info),
    ("THRESHOLD_BUY", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Price is below the buy threshold.", "Threshold buy triggered.", None, false, Severity::Info),
    ("THRESHOLD_SELL", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Price is above the sell threshold.", "Threshold sell triggered.", None, false, Severity::Info),
    ("THRESHOLD_WAIT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Price is inside the threshold band.", "Threshold is waiting.", None, false, Severity::Info),
    ("MOMENTUM_ENTRY", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Lookback change crossed the momentum entry band.", "Momentum entry triggered.", None, false, Severity::Info),
    ("MOMENTUM_EXIT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Lookback change crossed the momentum exit band.", "Momentum exit triggered.", None, false, Severity::Info),
    ("MOMENTUM_WAIT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Lookback change is between the momentum bands.", "Momentum is waiting.", None, false, Severity::Info),
    ("DIP_ENTRY", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Lookback change crossed the dip entry band.", "Dip entry triggered.", None, false, Severity::Info),
    ("DIP_WAIT", Category::Outcome, Layer::Core, "ezchain_core/strategy.py", "Lookback change has not reached the dip band.", "Dip is waiting.", None, false, Severity::Info),
    ("STRATEGY_PARAM_MISSING", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "A required strategy parameter is absent.", "A required strategy parameter is missing.", Some(422), false, Severity::Warning),
    ("STRATEGY_PARAM_TYPE", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "A strategy parameter has the wrong type or is not finite.", "A strategy parameter has the wrong type.", Some(422), false, Severity::Warning),
    ("STRATEGY_PARAM_RANGE", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "A strategy parameter is outside its range.", "A strategy parameter is out of range.", Some(422), false, Severity::Warning),
    ("STRATEGY_PARAM_UNKNOWN", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "The parameters carry a key the kind does not declare.", "A strategy parameter is unknown.", Some(422), false, Severity::Warning),
    ("STRATEGY_LOOKBACK_UNSUPPORTED", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "The lookback is not in the supported set.", "The lookback is not supported.", Some(422), false, Severity::Warning),
    ("STRATEGY_GRAPH_INVALID", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "A graph strategy's document is missing or invalid.", "The strategy graph is invalid.", Some(422), false, Severity::Warning),
    ("STRATEGY_KIND_UNSUPPORTED", Category::Strategy, Layer::Core, "ezchain_core/strategy.py", "The strategy kind is not one this build supports.", "The strategy kind is unsupported.", Some(422), false, Severity::Warning),
    ("STRATEGY_NOT_FOUND", Category::Validation, Layer::Api, "ezchain_api/routers/strategies.py", "No strategy has that id.", "Strategy not found.", Some(404), false, Severity::Warning),
    ("STRATEGY_RUN_NOT_LOGGED", Category::Engine, Layer::Api, "ezchain_api/routers/strategies.py", "A run completed but its log row could not be written.", "The strategy run could not be logged.", Some(500), true, Severity::Critical),
    ("PROTECTION_PARAM_TYPE", Category::Protection, Layer::Core, "ezchain_core/protection.py", "An arm parameter has the wrong type.", "An arm parameter has the wrong type.", Some(422), false, Severity::Warning),
    ("PROTECTION_PARAM_MISSING", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A required arm parameter is absent.", "A required arm parameter is missing.", Some(422), false, Severity::Warning),
    ("PROTECTION_PARAM_RANGE", Category::Protection, Layer::Core, "ezchain_core/protection.py", "An arm parameter is out of range or the ladder is inconsistent.", "An arm parameter is out of range.", Some(422), false, Severity::Warning),
    ("PROTECTION_PARAM_UNKNOWN", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The arm carries a key the kind does not declare.", "An arm parameter is unknown.", Some(422), false, Severity::Warning),
    ("PROTECTION_KIND_UNSUPPORTED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The arm kind is not stop-loss, trailing-stop or take-profit.", "The arm kind is unsupported.", Some(422), false, Severity::Warning),
    ("PROTECTION_CONFLICT", Category::Protection, Layer::Api, "ezchain_api/services/protection.py", "The arm version changed under the caller (compare-and-swap lost).", "The arm changed since it was read.", Some(409), true, Severity::Warning),
    ("PROFILE_PARAM_TYPE", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A profile parameter has the wrong type.", "A profile parameter has the wrong type.", Some(422), false, Severity::Warning),
    ("PROFILE_PARAM_MISSING", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A profile has no defaults.", "A profile parameter is missing.", Some(422), false, Severity::Warning),
    ("PROFILE_PARAM_RANGE", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A profile name, chain or value is out of range.", "A profile value is out of range.", Some(422), false, Severity::Warning),
    ("PROFILE_KIND_UNSUPPORTED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The profile kind is neither manual nor smart.", "The profile kind is unsupported.", Some(422), false, Severity::Warning),
    ("PROFILE_RULES_REQUIRED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A smart profile was saved with no rules.", "A smart profile needs rules.", Some(422), false, Severity::Warning),
    ("PROFILE_RULES_NOT_ALLOWED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "A manual profile was saved with rules.", "A manual profile cannot carry rules.", Some(422), false, Severity::Warning),
    ("PROFILE_NOT_FOUND", Category::Validation, Layer::Api, "ezchain_api/services/protection.py", "No protection profile has that name.", "Protection profile not found.", Some(404), false, Severity::Warning),
    ("PROFILE_UNKNOWN", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The requested risk profile is not allowed for this account.", "The requested risk profile is unknown; use low, mid or high.", None, false, Severity::Warning),
    ("ARM_NOT_FOUND", Category::Validation, Layer::Api, "ezchain_api/services/protection.py", "No protection arm has that id.", "Protection arm not found.", Some(404), false, Severity::Warning),
    ("ARM_NOT_EDITABLE", Category::Validation, Layer::Api, "ezchain_api/services/protection.py", "The arm is not armed, so it cannot be edited.", "Only an armed arm can be edited.", Some(409), false, Severity::Warning),
    ("ARM_NOT_CANCELLABLE", Category::Validation, Layer::Api, "ezchain_api/services/protection.py", "The arm is not armed, so it cannot be cancelled.", "Only an armed arm can be cancelled.", Some(409), false, Severity::Warning),
    ("NOT_ARMED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The arm's status is not armed.", "The arm is not armed.", None, false, Severity::Info),
    ("EXPIRED", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The arm's expiry has passed.", "The arm expired.", None, false, Severity::Info),
    ("INVALID_ARM", Category::Protection, Layer::Core, "ezchain_core/protection.py", "The arm's parameters cannot compute a threshold.", "The arm parameters are insufficient.", None, false, Severity::Warning),
    ("ARMED", Category::Outcome, Layer::Core, "ezchain_core/protection.py", "The arm is live and holding; no threshold was crossed.", "The arm is armed.", None, false, Severity::Info),
    ("TAKE_PROFIT", Category::Outcome, Layer::Core, "ezchain_core/protection.py", "Unrealized gain crossed the take-profit band.", "Take profit triggered.", None, false, Severity::Info),
    ("TAKE_PROFIT_LEVEL", Category::Outcome, Layer::Core, "ezchain_core/protection.py", "A take-profit ladder level was reached.", "A take-profit ladder level triggered.", None, false, Severity::Info),
    ("STOP_LOSS", Category::Outcome, Layer::Core, "ezchain_core/protection.py", "Unrealized loss crossed the stop band.", "Stop loss triggered.", None, false, Severity::Info),
    ("TRAILING_STOP", Category::Outcome, Layer::Core, "ezchain_core/protection.py", "Price fell from the peak by the trailing amount.", "Trailing stop triggered.", None, false, Severity::Info),
    ("PLUGIN_MANIFEST_INVALID", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The manifest is not a valid declaration.", "The plugin manifest is invalid.", Some(422), false, Severity::Warning),
    ("PLUGIN_MANIFEST_UNKNOWN_KEY", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The manifest carries a key the schema does not declare.", "The plugin manifest has an unknown key.", Some(422), false, Severity::Warning),
    ("PLUGIN_MANIFEST_UNREADABLE", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The manifest file could not be read.", "The plugin manifest is unreadable.", Some(422), false, Severity::Warning),
    ("PLUGIN_MANIFEST_TOO_LARGE", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The manifest exceeds the size cap.", "The plugin manifest is too large.", Some(422), false, Severity::Warning),
    ("PLUGIN_MANIFEST_MISSING", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "No manifest exists where one is required.", "The plugin manifest is missing.", Some(422), false, Severity::Warning),
    ("PLUGIN_SDK_INVALID", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The declared SDK version is not parseable.", "The plugin SDK version is invalid.", Some(422), false, Severity::Warning),
    ("PLUGIN_SDK_INCOMPATIBLE", Category::Plugin, Layer::Sdk, "ezchain_sdk/manifest.py", "The declared SDK version is outside the host's range.", "The plugin SDK version is incompatible.", Some(422), false, Severity::Warning),
    ("PLUGIN_PARAMS_INVALID", Category::Plugin, Layer::Sdk, "ezchain_sdk/params.py", "Plugin configuration does not satisfy its schema.", "The plugin parameters are invalid.", Some(422), false, Severity::Warning),
    ("PLUGIN_SCHEMA_UNSUPPORTED", Category::Plugin, Layer::Sdk, "ezchain_sdk/params.py", "The declared parameter schema uses an unsupported construct.", "The plugin schema is unsupported.", Some(422), false, Severity::Warning),
    ("PLUGIN_ERROR", Category::Plugin, Layer::Sdk, "ezchain_sdk/errors.py", "A plugin failed without a more specific code.", "The plugin failed.", None, false, Severity::Warning),
    ("PLUGIN_LOAD_FAILED", Category::Plugin, Layer::Sdk, "ezchain_sdk/errors.py", "The manifest is valid but the entrypoint could not be imported.", "The plugin failed to load.", None, false, Severity::Warning),
    ("PLUGIN_CONTRACT_VIOLATION", Category::Plugin, Layer::Sdk, "ezchain_sdk/errors.py", "The plugin returned something the host refuses.", "The plugin violated its contract.", None, false, Severity::Warning),
    ("PLUGIN_NOT_CONFIGURED", Category::Plugin, Layer::Api, "ezchain_api/routers/strategies.py", "The strategy has no plugin id to run.", "The strategy has no plugin configured.", Some(422), false, Severity::Warning),
    ("PLUGIN_NOT_FOUND", Category::Plugin, Layer::Engine, "ezchain_engine/plugins.py", "No available plugin has that id.", "Plugin not found.", Some(404), false, Severity::Warning),
    ("PLUGIN_CHAIN_UNSUPPORTED", Category::Plugin, Layer::Api, "ezchain_api/routers/strategies.py", "The plugin does not declare this chain.", "The plugin does not support this chain.", Some(422), false, Severity::Warning),
    ("PLUGIN_VERSION_MISMATCH", Category::Plugin, Layer::Api, "ezchain_api/routers/strategies.py", "The requested plugin version is not the installed one.", "The plugin version does not match.", Some(422), false, Severity::Warning),
    ("PLUGIN_UNAVAILABLE", Category::Plugin, Layer::Engine, "ezchain_engine/plugins.py", "The plugin exists in the catalogue but cannot run.", "The plugin is unavailable.", None, false, Severity::Warning),
    ("PLUGIN_DUPLICATE_ID", Category::Plugin, Layer::Engine, "ezchain_engine/plugins.py", "Two discovery sources provide the same plugin id.", "A duplicate plugin id was found.", None, false, Severity::Info),
    ("PLUGIN_PATH_MISSING", Category::Plugin, Layer::Engine, "ezchain_engine/plugins.py", "A configured plugin path does not exist.", "The plugin path does not exist.", None, false, Severity::Warning),
    ("PLUGIN_PATH_UNREADABLE", Category::Plugin, Layer::Engine, "ezchain_engine/plugins.py", "A configured plugin path could not be listed.", "The plugin path is unreadable.", None, false, Severity::Warning),
    ("PLUGIN_NO_ACTION", Category::Outcome, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The plugin returned no intents.", "The plugin produced no order.", None, false, Severity::Info),
    ("PLUGIN_INTENT_NOT_PERMITTED", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The plugin submitted an intent without the permission.", "The plugin may not submit intents.", None, false, Severity::Warning),
    ("PLUGIN_MULTIPLE_INTENTS", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The plugin returned more than one intent for a tick.", "A plugin may return one intent per tick.", None, false, Severity::Warning),
    ("PLUGIN_INTENT_INVALID", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The intent payload could not be read or has a non-positive notional.", "The plugin intent is invalid.", None, false, Severity::Warning),
    ("PLUGIN_UNSUPPORTED_INTENT", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The intent uses a field the host does not support.", "The plugin intent uses an unsupported field.", None, false, Severity::Warning),
    ("PLUGIN_POSITION_UNKNOWN", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "A buy needs the position's value and no price exists.", "The plugin position value is unknown.", None, false, Severity::Warning),
    ("PLUGIN_CONTEXT_UNAVAILABLE", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_runtime.py", "The context builder failed.", "The plugin context is unavailable.", None, false, Severity::Warning),
    ("PLUGIN_RUNTIME_UNAVAILABLE", Category::Plugin, Layer::Engine, "ezchain_engine/scheduler.py", "No plugin runtime is configured for this process.", "The plugin runtime is unavailable.", None, false, Severity::Warning),
    ("PLUGIN_QUARANTINED", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The host quarantined the plugin after a failure.", "The plugin is quarantined.", None, false, Severity::Warning),
    ("PLUGIN_SPAWN_FAILED", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The worker process could not be started.", "The plugin worker failed to start.", None, false, Severity::Warning),
    ("PLUGIN_CRASHED", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The worker process died.", "The plugin worker crashed.", None, false, Severity::Warning),
    ("PLUGIN_CONTEXT_INVALID", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The context payload sent to the worker is invalid.", "The plugin context is invalid.", None, false, Severity::Warning),
    ("PLUGIN_TIMEOUT", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The worker exceeded its deadline.", "The plugin timed out.", None, true, Severity::Warning),
    ("PLUGIN_PROTOCOL_ERROR", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The worker sent a message the host cannot read.", "The plugin protocol is broken.", None, false, Severity::Warning),
    ("PLUGIN_START_FAILED", Category::Plugin, Layer::Engine, "ezchain_engine/plugin_host.py", "The worker started but failed its handshake.", "The plugin failed to start.", None, false, Severity::Warning),
    ("INVALID_PARAMS", Category::Engine, Layer::Engine, "ezchain_engine/scheduler.py", "The strategy's parameters failed validation at run time.", "The strategy parameters are invalid.", None, false, Severity::Warning),
    ("INTERVAL_NOT_ELAPSED", Category::Outcome, Layer::Engine, "ezchain_engine/scheduler.py", "The scheduled interval has not elapsed.", "The interval has not elapsed.", None, false, Severity::Info),
    ("GRAPH_RUNTIME_UNAVAILABLE", Category::Graph, Layer::Engine, "ezchain_engine/scheduler.py", "No graph runtime is configured for this process.", "The graph runtime is unavailable.", None, false, Severity::Warning),
    ("GRAPH_CONTEXT_UNAVAILABLE", Category::Graph, Layer::Engine, "ezchain_engine/scheduler.py", "The graph context could not be built.", "The graph context is unavailable.", None, false, Severity::Warning),
    ("MARKET_DATA_UNAVAILABLE", Category::Market, Layer::Engine, "ezchain_engine/scheduler.py", "No fresh market data exists for the decision.", "Market data is unavailable.", None, true, Severity::Warning),
    ("ORDER_EXECUTOR_UNAVAILABLE", Category::Engine, Layer::Engine, "ezchain_engine/scheduler.py", "The order executor is not wired into this process.", "The order executor is unavailable.", Some(503), true, Severity::Warning),
    ("EXECUTION_FAILED", Category::Engine, Layer::Engine, "ezchain_engine/scheduler.py", "The executor raised while running the order.", "Execution failed.", None, false, Severity::Warning),
    ("ORDER_REJECTED", Category::Engine, Layer::Engine, "ezchain_engine/scheduler.py", "The executor refused the order; the order row carries the specific code.", "The order was rejected.", None, false, Severity::Warning),
    ("ORDER_FAILED", Category::Engine, Layer::Engine, "ezchain_engine/protection.py", "An arm's exit order failed.", "The protection order failed.", None, false, Severity::Warning),
    ("ARM_CHANGED", Category::Engine, Layer::Engine, "ezchain_engine/protection.py", "The arm row changed between read and write.", "The arm changed while evaluating.", None, true, Severity::Warning),
    ("EVALUATION_FAILED", Category::Engine, Layer::Engine, "ezchain_engine/protection.py", "The arm evaluator raised.", "The arm evaluation failed.", None, false, Severity::Warning),
    ("ADAPTER_ERROR", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "An adapter failed without a more specific code.", "An upstream adapter failed.", Some(502), true, Severity::Warning),
    ("NOT_FOUND", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The adapter has no data for that entity.", "Not found.", Some(404), false, Severity::Warning),
    ("UPSTREAM_ERROR", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "An upstream provider returned an error.", "The upstream provider failed.", Some(502), true, Severity::Warning),
    ("UPSTREAM_UNREACHABLE", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The bars could not be obtained: transport failure or an exhausted rate limit.", "The upstream provider is unreachable.", Some(502), true, Severity::Warning),
    ("RATE_LIMITED", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The provider or the local limiter refused the request rate.", "Rate limited.", Some(429), true, Severity::Warning),
    ("PROVIDER_BUDGET_EXHAUSTED", Category::Adapter, Layer::Adapters, "ezchain_adapters/provider_budget.py", "The provider's queue or wait budget is exhausted.", "The provider request budget is exhausted.", Some(429), true, Severity::Warning),
    ("PROVIDER_KEY_UNAVAILABLE", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "A stored provider credential could not be loaded.", "The provider credential is unavailable.", Some(503), false, Severity::Warning),
    ("CANDLES_UNSUPPORTED_CHAIN", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The chain has no candle provider; history is a capability, not a default.", "This chain has no candle provider.", Some(404), false, Severity::Warning),
    ("CANDLES_NOT_FOUND", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The provider has no series for this token and timeframe.", "No candle history for this token.", Some(404), false, Severity::Warning),
    ("CANDLES_EMPTY", Category::Adapter, Layer::Adapters, "ezchain_adapters/errors.py", "The provider returned no closed bars.", "The candle series is empty.", None, false, Severity::Info),
    ("NO_POOL", Category::Adapter, Layer::Adapters, "ezchain_adapters/quotes.py", "No dex pool exists for this token, so there is no price history.", "No pool was found.", None, false, Severity::Info),
    ("UNAUTHORIZED", Category::Auth, Layer::Api, "ezchain_api/auth.py", "The bearer token is missing or invalid.", "Authentication is required.", Some(401), false, Severity::Warning),
    ("FORBIDDEN", Category::Auth, Layer::Api, "ezchain_api/auth.py", "The principal lacks the route's scope.", "This key lacks the required scope.", Some(403), false, Severity::Warning),
    ("API_KEY_NOT_FOUND", Category::Auth, Layer::Api, "ezchain_api/routers/agent.py", "No agent key has that id.", "Agent key not found.", Some(404), false, Severity::Warning),
    ("API_KEY_PEPPER_REQUIRED", Category::Auth, Layer::Api, "ezchain_api/routers/agent.py", "Agent keys cannot be managed without a pepper.", "An API key pepper is required.", Some(409), false, Severity::Warning),
    ("API_KEY_SCOPES_REQUIRED", Category::Auth, Layer::Api, "ezchain_api/services/api_keys.py", "A key was minted with no scopes.", "At least one scope is required.", Some(422), false, Severity::Warning),
    ("API_KEY_SCOPE_UNKNOWN", Category::Auth, Layer::Api, "ezchain_api/services/api_keys.py", "A key names a scope that does not exist.", "An unknown scope was requested.", Some(422), false, Severity::Warning),
    ("API_KEY_SUBJECT_INVALID", Category::Auth, Layer::Api, "ezchain_api/services/api_keys.py", "The key's subject is empty or too long.", "The key subject is invalid.", Some(422), false, Severity::Warning),
    ("PROVIDER_KEY_ERROR", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "A provider credential operation failed.", "The provider key operation failed.", Some(422), false, Severity::Warning),
    ("PROVIDER_KEY_INVALID", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "The supplied provider credential is invalid.", "The provider key is invalid.", Some(422), false, Severity::Warning),
    ("PROVIDER_KEY_PROVIDER_UNKNOWN", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "The named provider is not in the catalogue.", "The provider is unknown.", Some(422), false, Severity::Warning),
    ("PROVIDER_KEY_NOT_FOUND", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "No stored credential exists for that provider.", "No provider key is stored.", Some(404), false, Severity::Warning),
    ("PROVIDER_KEY_ALREADY_EXISTS", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "A credential for that provider already exists.", "A provider key already exists.", Some(409), false, Severity::Warning),
    ("PROVIDER_KEY_ENCRYPTION_UNAVAILABLE", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "The credential cipher is not configured.", "Provider key encryption is unavailable.", Some(503), false, Severity::Warning),
    ("PROVIDER_KEY_FORGET_FAILED", Category::Auth, Layer::Api, "ezchain_api/services/provider_keys.py", "The stored credential could not be deleted.", "The provider key could not be forgotten.", Some(503), false, Severity::Warning),
    ("PROVIDER_KEY_PEPPER_REQUIRED", Category::Auth, Layer::Api, "ezchain_api/routers/provider_keys.py", "Provider keys cannot be stored without a pepper.", "A provider key pepper is required.", Some(503), false, Severity::Warning),
    ("CHAIN_NOT_FOUND", Category::Validation, Layer::Api, "ezchain_api/routers/markets.py", "The chain is not in the registry.", "Unknown chain.", Some(404), false, Severity::Warning),
    ("INVALID_CURSOR", Category::Validation, Layer::Api, "ezchain_api/routers/markets.py", "The pagination cursor is not decodable.", "The cursor is invalid.", Some(400), false, Severity::Warning),
    ("INVALID_FILTER", Category::Validation, Layer::Api, "ezchain_api/routers/markets.py", "A query filter is not a valid value for its field.", "A filter value is invalid.", Some(422), false, Severity::Warning),
    ("INVALID_PRECONDITION", Category::Validation, Layer::Api, "ezchain_api/routers/protection.py", "The If-Match header is not an integer arm version.", "The If-Match precondition is invalid.", Some(400), false, Severity::Warning),
    ("QUOTE_NOTIONAL_TOO_LARGE", Category::Market, Layer::Api, "ezchain_api/routers/markets.py", "The requested quote notional exceeds the route's cap.", "The quote notional is too large.", Some(422), false, Severity::Warning),
    ("QUOTE_SLIPPAGE_TOO_HIGH", Category::Market, Layer::Api, "ezchain_api/routers/markets.py", "The requested or realised slippage exceeds the cap.", "The slippage is too high.", Some(422), false, Severity::Warning),
    ("QUOTE_TIMEOUT", Category::Market, Layer::Executor, "ezchain_executor/pipeline.py", "The quote stage exceeded its deadline.", "The quote timed out.", None, true, Severity::Warning),
    ("NO_QUOTE", Category::Market, Layer::Executor, "ezchain_executor/pipeline.py", "The venue returned no quote.", "No quote is available.", None, true, Severity::Warning),
    ("QUOTE_SIZE_MISMATCH", Category::Market, Layer::Executor, "ezchain_executor/pipeline.py", "The quoted size is not the requested size within tolerance.", "The quote size does not match the order.", None, false, Severity::Warning),
    ("WALLET_UNKNOWN", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "No paper wallet settles this chain.", "No paper wallet settles this chain.", None, false, Severity::Warning),
    ("SETTLEMENT_ASSET_UNKNOWN", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The paper wallet does not settle in the ledger asset.", "The paper wallet does not settle in the ledger asset.", None, false, Severity::Warning),
    ("SHORTFALL_FUNDED", Category::Outcome, Layer::Api, "ezchain_api/services/paper.py", "A cash shortfall was funded rather than refused.", "Shortfall was funded.", None, false, Severity::Info),
    ("TOPUP_CAP_REACHED", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The no-risk shortfall is larger than the configured top-up cap.", "Paper funding cap reached; the order was not funded.", None, false, Severity::Warning),
    ("AWAITING_APPROVAL", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "An agent order under the manual automation tier is held for a person's decision.", "Waiting for a person to approve this order.", None, false, Severity::Info),
    ("APPROVAL_NOT_FOUND", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "No approval has that id.", "Approval not found.", Some(404), false, Severity::Warning),
    ("APPROVAL_EXPIRED", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "The approval window closed before a decision; no order was placed.", "The approval window closed before a person decided.", Some(409), false, Severity::Warning),
    ("APPROVAL_NOT_PENDING", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "The approval was already decided or expired.", "This approval was already decided.", Some(409), false, Severity::Warning),
    ("APPROVAL_REQUIRES_PERSON", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "An agent principal tried to decide an approval or change the automation tier.", "Only a person may approve or reject an order.", Some(403), false, Severity::Warning),
    ("APPROVAL_INTENT_INVALID", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "The stored intent behind an approval cannot be read.", "The stored order behind this approval cannot be read.", Some(409), false, Severity::Warning),
    ("APPROVAL_STATE_UNREADABLE", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "The approval queue could not be read; the order refused closed.", "The approval queue could not be read; no order was placed.", Some(409), false, Severity::Critical),
    ("APPROVAL_SUPERSEDED", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "A newer proposal for the same order replaced this approval.", "A newer proposal replaced this one.", None, false, Severity::Info),
    ("APPROVAL_REJECTED", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "A person rejected the proposed order.", "A person rejected this order.", None, false, Severity::Info),
    ("APPROVAL_DECISION_NOTE_INVALID", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "A rejection without a person's written reason was refused.", "A rejection needs a reason a person wrote.", Some(409), false, Severity::Warning),
    ("AUTOMATION_TIER_UNKNOWN", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "The requested automation tier is not manual or full_auto.", "The requested automation tier is not one of manual, full_auto.", Some(409), false, Severity::Warning),
    ("EXECUTION_REFUSED", Category::Approval, Layer::Api, "ezchain_api/services/approvals.py", "An approved execution ended without a more specific reason code.", "The execution was refused.", None, false, Severity::Warning),
    ("AGENT_BUCKET_EXCEEDED", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "The entry would take the agent's net cash deployment past its cap.", "This order would exceed the agent's bucket.", Some(409), false, Severity::Warning),
    ("AGENT_BUCKET_UNREADABLE", Category::Risk, Layer::Api, "ezchain_api/services/buckets.py", "The cap or the journal could not be read, so the cap is unknown.", "The agent's bucket could not be read; no order was placed.", Some(409), false, Severity::Critical),
    ("AGENT_BUCKET_UNKNOWN", Category::Risk, Layer::Api, "ezchain_api/routers/buckets.py", "The agent has no bucket cap row; no cap was ever set for it.", "This agent has no bucket cap on record.", Some(404), false, Severity::Info),
    ("AGENT_BUCKET_REQUIRES_PERSON", Category::Risk, Layer::Api, "ezchain_api/services/buckets.py", "An agent principal tried to set a bucket cap.", "Only a person may set an agent's bucket cap.", Some(403), false, Severity::Warning),
    ("AGENT_BUCKET_CAP_INVALID", Category::Risk, Layer::Api, "ezchain_api/services/buckets.py", "The cap is negative or larger than the absolute maximum.", "An agent's bucket cap must be zero or greater.", Some(422), false, Severity::Warning),
    ("NO_MARKET_ROWS", Category::Observability, Layer::Api, "ezchain_api/routers/signals.py", "The screener returned no market rows.", "No market rows were found.", None, false, Severity::Info),
    ("NO_QUALIFIED_MOVERS", Category::Observability, Layer::Api, "ezchain_api/routers/signals.py", "Rows existed but none passed the mover filters.", "No qualified movers.", None, false, Severity::Info),
    ("NO_SERIES_DATA", Category::Observability, Layer::Api, "ezchain_api/services/pnl.py", "A performance series has no equity and no realised points.", "No series data is available.", None, false, Severity::Info),
    ("MARKS_UNKNOWN", Category::Observability, Layer::Api, "ezchain_api/services/pnl.py", "The series exists but its marks are unknown.", "Marks are unknown.", None, false, Severity::Info),
    ("POSITION_MARK_FUTURE", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A position mark is dated ahead of the clock.", "A position mark is dated in the future.", None, false, Severity::Info),
    ("POSITION_MARK_STALE", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A position mark is older than its budget.", "A position mark is stale.", None, false, Severity::Warning),
    ("POSITION_MARK_UNKNOWN", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A position has no usable mark.", "A position mark is unknown.", None, false, Severity::Warning),
    ("ORDERS_REJECTED", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "Rejected orders accumulated in the alert window.", "Orders were rejected.", None, false, Severity::Info),
    ("RECONCILIATION_FAILED", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "At least one ledger reconciliation check failed.", "Ledger reconciliation failed.", None, false, Severity::Critical),
    ("NEGATIVE_CASH", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A cash account sums below zero.", "A cash account is negative.", None, false, Severity::Critical),
    ("PAPER_TOPUP_CAP_REACHED", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A no-risk demand exceeded the manual top-up cap, so the person is told.", "The paper funding cap was reached.", None, false, Severity::Warning),
    ("WATCHLIST_CHAIN_UNKNOWN", Category::Validation, Layer::Api, "ezchain_api/services/watchlist.py", "The chain named in a watchlist write is not in the registry.", "That chain is not in the registry.", Some(422), false, Severity::Warning),
    ("WATCHLIST_TOKEN_UNKNOWN", Category::Validation, Layer::Api, "ezchain_api/services/watchlist.py", "The token named in a watchlist write is not a valid address for its chain.", "That is not a valid token address on that chain.", Some(422), false, Severity::Warning),
    ("NEGATIVE_POSITION", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A position account holds a negative quantity.", "A position quantity is negative.", None, false, Severity::Critical),
    ("UNKNOWN", Category::Observability, Layer::Api, "ezchain_api/services/account_read.py", "A rejected order carries no reason code.", "The rejection has no reason code.", None, false, Severity::Warning),
    ("SAFETY_NOT_IMPLEMENTED", Category::Adapter, Layer::Adapters, "ezchain_adapters/dexscreener.py", "Token safety screening is not implemented, so safety is unknown.", "Token safety is not implemented.", None, false, Severity::Warning),
    ("TIMEOUT", Category::Engine, Layer::Executor, "ezchain_executor/pipeline.py", "Order confirmation exceeded its deadline.", "The order timed out.", None, true, Severity::Warning),
    ("VENUE_ERROR", Category::Engine, Layer::Executor, "ezchain_executor/pipeline.py", "The venue raised while submitting.", "The venue failed the order.", None, true, Severity::Warning),
    ("VENUE_REJECTED", Category::Engine, Layer::Executor, "ezchain_executor/venue.py", "The venue refused the order.", "The venue rejected the order.", None, false, Severity::Warning),
    ("UNBOOKABLE_RECEIPT", Category::Engine, Layer::Executor, "ezchain_executor/pipeline.py", "The receipt cannot be booked to the ledger.", "The fill receipt cannot be booked.", None, false, Severity::Critical),
    ("RECEIPT_EXCEEDS_ORDER", Category::Engine, Layer::Executor, "ezchain_executor/pipeline.py", "The receipt fills more than the order asked for.", "The fill exceeds the order.", None, false, Severity::Critical),
    ("JOURNAL_FAILED", Category::Engine, Layer::Executor, "ezchain_executor/pipeline.py", "The ledger journal could not be written for a confirmed fill.", "The fill could not be journaled.", None, false, Severity::Critical),
    ("BACKTEST_STRATEGY_NOT_GRAPH", Category::Backtest, Layer::Api, "ezchain_api/routers/backtests.py", "Only graph strategies can be backtested.", "Only a graph strategy can be backtested.", Some(422), false, Severity::Warning),
    ("BACKTEST_STRATEGY_MISMATCH", Category::Backtest, Layer::Api, "ezchain_api/routers/backtests.py", "The requested strategy and graph version disagree.", "The backtest strategy does not match.", Some(422), false, Severity::Warning),
    ("BACKTEST_TARGET_REQUIRED", Category::Backtest, Layer::Api, "ezchain_api/routers/backtests.py", "A backtest needs a target token.", "A backtest target is required.", Some(422), false, Severity::Warning),
    ("BACKTEST_NO_CANDLES", Category::Backtest, Layer::Api, "ezchain_api/services/backtests.py", "No candle data covers the requested window.", "No candles for the backtest window.", Some(422), false, Severity::Warning),
    ("BACKTEST_BARS_LIMIT", Category::Backtest, Layer::Api, "ezchain_api/services/backtests.py", "The requested window exceeds the bar limit.", "The backtest window is too large.", Some(422), false, Severity::Warning),
    ("BACKTEST_INVALID", Category::Backtest, Layer::Api, "ezchain_api/services/backtests.py", "The backtest could not be built from its inputs.", "The backtest is invalid.", Some(422), false, Severity::Warning),
    ("BACKTEST_NOT_FOUND", Category::Backtest, Layer::Api, "ezchain_api/routers/backtests.py", "No backtest has that id.", "Backtest not found.", Some(404), false, Severity::Warning),
    ("POLICY_FIELD_UNKNOWN", Category::Strategy, Layer::Core, "ezchain_core/trade_policy.py", "A caller asked for one field of a trade profile by a name the registry does not declare.", "That trade-policy field does not exist.", Some(422), false, Severity::Warning),
    ("POLICY_INVALID", Category::Strategy, Layer::Api, "ezchain_api/services/trade_policy.py", "A stored or requested policy contradicts itself: a bound sits below the size it is supposed to allow.", "This trade policy is internally inconsistent.", Some(422), false, Severity::Warning),
    ("RISK_ACK_REQUIRED", Category::Strategy, Layer::Api, "ezchain_api/services/trade_policy.py", "Changing a strategy to a profile that loosens a bound governing the owner's own funds needs an explicit acknowledgement.", "Confirm the looser risk limit before saving it.", Some(409), false, Severity::Warning),
    ("PROFILE_LOOSENS_STRATEGY", Category::Risk, Layer::Api, "ezchain_api/services/paper.py", "An order asked for a risk profile looser than the one its strategy is pinned to, so the strategy's bound would not apply.", "This order is looser than the strategy's risk profile.", Some(409), false, Severity::Warning),
    ("TRADE_POLICY_UPDATED", Category::Strategy, Layer::Api, "ezchain_api/services/trade_policy.py", "A strategy's risk profile changed; the run log records the previous and the new value.", "Trade policy updated.", None, false, Severity::Info),

];

#[test]
fn registry_is_complete_unique_and_in_declaration_order() {
    assert_eq!(REASON_SPECS.len(), 269, "python oracle records 269 codes");
    assert_eq!(EXPECTED.len(), REASON_SPECS.len());
    for (index, (spec, recorded)) in REASON_SPECS.iter().zip(EXPECTED).enumerate() {
        assert_eq!(spec.code, recorded.0, "declaration order at {index}");
    }
    let mut seen = std::collections::BTreeSet::new();
    for spec in REASON_SPECS {
        assert!(
            seen.insert(spec.code),
            "duplicate reason code {}",
            spec.code
        );
    }
    assert_eq!(seen.len(), REASON_SPECS.len());
}

#[test]
fn reason_returns_the_exact_recorded_spec_for_every_code() {
    for (index, recorded) in EXPECTED.iter().enumerate() {
        let spec =
            reason(recorded.0).unwrap_or_else(|err| panic!("code {} missing: {err}", recorded.0));
        assert_eq!(spec.code, recorded.0, "case {index}");
        assert_eq!(spec.category, recorded.1, "category of {}", recorded.0);
        assert_eq!(spec.layer, recorded.2, "layer of {}", recorded.0);
        assert_eq!(spec.source, recorded.3, "source of {}", recorded.0);
        assert_eq!(spec.summary, recorded.4, "summary of {}", recorded.0);
        assert_eq!(spec.message, recorded.5, "message of {}", recorded.0);
        assert_eq!(
            spec.http_status, recorded.6,
            "http_status of {}",
            recorded.0
        );
        assert_eq!(spec.retryable, recorded.7, "retryable of {}", recorded.0);
        assert_eq!(spec.severity, recorded.8, "severity of {}", recorded.0);
    }
}

#[test]
fn reason_unknown_code_fails_loudly_with_the_python_message() {
    let err = reason("NO_SUCH_CODE").expect_err("unknown code must fail");
    assert_eq!(err.code, "NO_SUCH_CODE");
    assert_eq!(
        err.to_string(),
        "NO_SUCH_CODE is not in the reason-code taxonomy"
    );
}

#[test]
fn message_for_matches_every_recorded_message_and_returns_unknown_input() {
    for recorded in EXPECTED {
        assert_eq!(message_for(recorded.0), recorded.5, "code {}", recorded.0);
    }
    for unknown in ["", "NOPE", "not_a_code", "unknown"] {
        assert_eq!(message_for(unknown), unknown, "unknown code {unknown:?}");
    }
}

#[test]
fn codes_in_matches_recorded_membership_for_every_category() {
    assert_eq!(
        codes_in(Category::Auth),
        &[
            "UNAUTHORIZED",
            "FORBIDDEN",
            "API_KEY_NOT_FOUND",
            "API_KEY_PEPPER_REQUIRED",
            "API_KEY_SCOPES_REQUIRED",
            "API_KEY_SCOPE_UNKNOWN",
            "API_KEY_SUBJECT_INVALID",
            "PROVIDER_KEY_ERROR",
            "PROVIDER_KEY_INVALID",
            "PROVIDER_KEY_PROVIDER_UNKNOWN",
            "PROVIDER_KEY_NOT_FOUND",
            "PROVIDER_KEY_ALREADY_EXISTS",
            "PROVIDER_KEY_ENCRYPTION_UNAVAILABLE",
            "PROVIDER_KEY_FORGET_FAILED",
            "PROVIDER_KEY_PEPPER_REQUIRED",
        ],
    );
    assert_eq!(
        codes_in(Category::Validation),
        &[
            "STRATEGY_NOT_FOUND",
            "PROFILE_NOT_FOUND",
            "ARM_NOT_FOUND",
            "ARM_NOT_EDITABLE",
            "ARM_NOT_CANCELLABLE",
            "CHAIN_NOT_FOUND",
            "INVALID_CURSOR",
            "INVALID_FILTER",
            "INVALID_PRECONDITION",
            "WATCHLIST_CHAIN_UNKNOWN",
            "WATCHLIST_TOKEN_UNKNOWN",
        ],
    );
    assert_eq!(
        codes_in(Category::Risk),
        &[
            "INVALID_SIDE",
            "INVALID_AMOUNT",
            "ORDER_LIMIT",
            "SIZE_BELOW_MIN",
            "SIZE_ABOVE_MAX",
            "EQUITY_UNKNOWN",
            "PAPER_MARK_LIMIT",
            "PRICE_IMPACT_TOO_HIGH",
            "QUOTE_STALE",
            "MARK_TIMESTAMP_FUTURE",
            "PRICE_UNKNOWN",
            "LIQUIDITY_UNKNOWN",
            "LIQUIDITY_TOO_LOW",
            "NO_PAIR",
            "SNAPSHOT_STALE",
            "PAIR_AGE_UNKNOWN",
            "PAIR_TOO_YOUNG",
            "EXIT_NO_ROUTE",
            "NO_SELL_ROUTE",
            "NO_ROUTE",
            "POSITION_LIMIT",
            "MAX_OPEN_POSITIONS",
            "DAILY_LOSS_LIMIT",
            "MISSING_RISK_INPUTS",
            "RISK_DENIED",
            "IDEMPOTENCY_CONFLICT",
            "POSITION_CHANGED",
            "INSUFFICIENT_POSITION",
            "INSUFFICIENT_CASH",
            "PROFILE_UNKNOWN",
            "WALLET_UNKNOWN",
            "SETTLEMENT_ASSET_UNKNOWN",
            "TOPUP_CAP_REACHED",
            "AGENT_BUCKET_EXCEEDED",
            "AGENT_BUCKET_UNREADABLE",
            "AGENT_BUCKET_UNKNOWN",
            "AGENT_BUCKET_REQUIRES_PERSON",
            "AGENT_BUCKET_CAP_INVALID",
            "PROFILE_LOOSENS_STRATEGY",
        ],
    );
    assert_eq!(
        codes_in(Category::Market),
        &[
            "MARKET_DATA_UNAVAILABLE",
            "QUOTE_NOTIONAL_TOO_LARGE",
            "QUOTE_SLIPPAGE_TOO_HIGH",
            "QUOTE_TIMEOUT",
            "NO_QUOTE",
            "QUOTE_SIZE_MISMATCH",
        ],
    );
    assert_eq!(
        codes_in(Category::Custody),
        &[
            "VAULT_UNREACHABLE",
            "VAULT_APPROVAL_DENIED",
            "VAULT_UNKNOWN_HANDLE",
            "VAULT_LOCKED",
            "VAULT_WATCH_ONLY",
            "VAULT_SIGN_FAILED",
            "VAULT_BOUNDARY_REQUIRED",
            "CUSTODY_OWNER_MISMATCH",
            "AUTOMATION_CONSENT_REQUIRED",
        ],
    );
    assert_eq!(
        codes_in(Category::Approval),
        &[
            "AWAITING_APPROVAL",
            "APPROVAL_NOT_FOUND",
            "APPROVAL_EXPIRED",
            "APPROVAL_NOT_PENDING",
            "APPROVAL_REQUIRES_PERSON",
            "APPROVAL_INTENT_INVALID",
            "APPROVAL_STATE_UNREADABLE",
            "APPROVAL_SUPERSEDED",
            "APPROVAL_REJECTED",
            "APPROVAL_DECISION_NOTE_INVALID",
            "AUTOMATION_TIER_UNKNOWN",
            "EXECUTION_REFUSED",
        ],
    );
    assert_eq!(
        codes_in(Category::Signing),
        &[
            "SIGNING_BOUNDARY_REQUIRED",
            "TRANSACTION_NOT_APPROVABLE",
            "TRANSACTION_UNDECODABLE",
            "TRANSACTION_PROGRAM_MISMATCH",
            "TRANSACTION_CLUSTER_MISMATCH",
            "TRANSACTION_MINT_MISMATCH",
            "TRANSACTION_SIDE_MISMATCH",
            "TRANSACTION_AMOUNT_MISMATCH",
            "TRANSACTION_MIN_OUT_MISMATCH",
            "TRANSACTION_RECIPIENT_MISMATCH",
            "TRANSACTION_IDEMPOTENCY_MISMATCH",
            "TRANSACTION_DIGEST_MISMATCH",
            "TRANSACTION_APPROVAL_MISMATCH",
            "TRANSACTION_UNSIGNED",
            "TRANSACTION_BUILD_FAILED",
            "TRANSACTION_SIGN_FAILED",
        ],
    );
    assert_eq!(
        codes_in(Category::Governance),
        &[
            "KILL_SWITCH_ACTIVE",
            "KILL_SWITCH_ARMED",
            "KILL_SWITCH_UNAVAILABLE",
            "GATE_NOT_MET",
        ],
    );
    assert_eq!(
        codes_in(Category::Strategy),
        &[
            "STRATEGY_PARAM_MISSING",
            "STRATEGY_PARAM_TYPE",
            "STRATEGY_PARAM_RANGE",
            "STRATEGY_PARAM_UNKNOWN",
            "STRATEGY_LOOKBACK_UNSUPPORTED",
            "STRATEGY_GRAPH_INVALID",
            "STRATEGY_KIND_UNSUPPORTED",
            "POLICY_FIELD_UNKNOWN",
            "POLICY_INVALID",
            "RISK_ACK_REQUIRED",
            "TRADE_POLICY_UPDATED",
        ],
    );
    assert_eq!(
        codes_in(Category::Graph),
        &[
            "GRAPH_SCHEMA",
            "NODE_TYPE_UNKNOWN",
            "PARAM_INVALID",
            "PORT_UNKNOWN",
            "PIN_TYPE_MISMATCH",
            "INPUT_MULTIPLE",
            "EXEC_FANOUT",
            "INPUT_REQUIRED",
            "CYCLE",
            "NO_EVENT",
            "NO_ACTION",
            "ACTION_UNREACHABLE",
            "NODE_ORPHAN",
            "WARMUP_LONG",
            "NO_EXIT",
            "DUPLICATE_ORDER_PATH",
            "INVALID_GRAPH",
            "UNKNOWN_CONDITION",
            "SUPPRESSED_ONE_ORDER_PER_TICK",
            "GRAPH_RUNTIME_UNAVAILABLE",
            "GRAPH_CONTEXT_UNAVAILABLE",
        ],
    );
    assert_eq!(
        codes_in(Category::Plugin),
        &[
            "PLUGIN_MANIFEST_INVALID",
            "PLUGIN_MANIFEST_UNKNOWN_KEY",
            "PLUGIN_MANIFEST_UNREADABLE",
            "PLUGIN_MANIFEST_TOO_LARGE",
            "PLUGIN_MANIFEST_MISSING",
            "PLUGIN_SDK_INVALID",
            "PLUGIN_SDK_INCOMPATIBLE",
            "PLUGIN_PARAMS_INVALID",
            "PLUGIN_SCHEMA_UNSUPPORTED",
            "PLUGIN_ERROR",
            "PLUGIN_LOAD_FAILED",
            "PLUGIN_CONTRACT_VIOLATION",
            "PLUGIN_NOT_CONFIGURED",
            "PLUGIN_NOT_FOUND",
            "PLUGIN_CHAIN_UNSUPPORTED",
            "PLUGIN_VERSION_MISMATCH",
            "PLUGIN_UNAVAILABLE",
            "PLUGIN_DUPLICATE_ID",
            "PLUGIN_PATH_MISSING",
            "PLUGIN_PATH_UNREADABLE",
            "PLUGIN_INTENT_NOT_PERMITTED",
            "PLUGIN_MULTIPLE_INTENTS",
            "PLUGIN_INTENT_INVALID",
            "PLUGIN_UNSUPPORTED_INTENT",
            "PLUGIN_POSITION_UNKNOWN",
            "PLUGIN_CONTEXT_UNAVAILABLE",
            "PLUGIN_RUNTIME_UNAVAILABLE",
            "PLUGIN_QUARANTINED",
            "PLUGIN_SPAWN_FAILED",
            "PLUGIN_CRASHED",
            "PLUGIN_CONTEXT_INVALID",
            "PLUGIN_TIMEOUT",
            "PLUGIN_PROTOCOL_ERROR",
            "PLUGIN_START_FAILED",
        ],
    );
    assert_eq!(
        codes_in(Category::Backtest),
        &[
            "BACKTEST_STRATEGY_NOT_GRAPH",
            "BACKTEST_STRATEGY_MISMATCH",
            "BACKTEST_TARGET_REQUIRED",
            "BACKTEST_NO_CANDLES",
            "BACKTEST_BARS_LIMIT",
            "BACKTEST_INVALID",
            "BACKTEST_NOT_FOUND",
        ],
    );
    assert_eq!(
        codes_in(Category::Protection),
        &[
            "PROTECTION_PARAM_TYPE",
            "PROTECTION_PARAM_MISSING",
            "PROTECTION_PARAM_RANGE",
            "PROTECTION_PARAM_UNKNOWN",
            "PROTECTION_KIND_UNSUPPORTED",
            "PROTECTION_CONFLICT",
            "PROFILE_PARAM_TYPE",
            "PROFILE_PARAM_MISSING",
            "PROFILE_PARAM_RANGE",
            "PROFILE_KIND_UNSUPPORTED",
            "PROFILE_RULES_REQUIRED",
            "PROFILE_RULES_NOT_ALLOWED",
            "NOT_ARMED",
            "EXPIRED",
            "INVALID_ARM",
        ],
    );
    assert_eq!(
        codes_in(Category::Engine),
        &[
            "STRATEGY_RUN_NOT_LOGGED",
            "INVALID_PARAMS",
            "ORDER_EXECUTOR_UNAVAILABLE",
            "EXECUTION_FAILED",
            "ORDER_REJECTED",
            "ORDER_FAILED",
            "ARM_CHANGED",
            "EVALUATION_FAILED",
            "TIMEOUT",
            "VENUE_ERROR",
            "VENUE_REJECTED",
            "UNBOOKABLE_RECEIPT",
            "RECEIPT_EXCEEDS_ORDER",
            "JOURNAL_FAILED",
        ],
    );
    assert_eq!(
        codes_in(Category::Adapter),
        &[
            "ADAPTER_ERROR",
            "NOT_FOUND",
            "UPSTREAM_ERROR",
            "UPSTREAM_UNREACHABLE",
            "RATE_LIMITED",
            "PROVIDER_BUDGET_EXHAUSTED",
            "PROVIDER_KEY_UNAVAILABLE",
            "CANDLES_UNSUPPORTED_CHAIN",
            "CANDLES_NOT_FOUND",
            "CANDLES_EMPTY",
            "NO_POOL",
            "SAFETY_NOT_IMPLEMENTED",
        ],
    );
    assert_eq!(
        codes_in(Category::Observability),
        &[
            "PROVIDER_STALE",
            "NO_MARKET_ROWS",
            "NO_QUALIFIED_MOVERS",
            "NO_SERIES_DATA",
            "MARKS_UNKNOWN",
            "POSITION_MARK_FUTURE",
            "POSITION_MARK_STALE",
            "POSITION_MARK_UNKNOWN",
            "ORDERS_REJECTED",
            "RECONCILIATION_FAILED",
            "NEGATIVE_CASH",
            "PAPER_TOPUP_CAP_REACHED",
            "NEGATIVE_POSITION",
            "UNKNOWN",
        ],
    );
    assert_eq!(
        codes_in(Category::Outcome),
        &[
            "GRAPH_BUY",
            "GRAPH_SELL",
            "GRAPH_NO_ACTION",
            "PAUSED",
            "GRAPH_NEEDS_CONTEXT",
            "PLUGIN_NEEDS_CONTEXT",
            "MARK_STALE",
            "CHANGE_UNKNOWN",
            "MAX_POSITION",
            "NO_POSITION",
            "UNKNOWN_KIND",
            "DCA_BUY",
            "DCA_INTERVAL",
            "THRESHOLD_BUY",
            "THRESHOLD_SELL",
            "THRESHOLD_WAIT",
            "MOMENTUM_ENTRY",
            "MOMENTUM_EXIT",
            "MOMENTUM_WAIT",
            "DIP_ENTRY",
            "DIP_WAIT",
            "ARMED",
            "TAKE_PROFIT",
            "TAKE_PROFIT_LEVEL",
            "STOP_LOSS",
            "TRAILING_STOP",
            "PLUGIN_NO_ACTION",
            "INTERVAL_NOT_ELAPSED",
            "SHORTFALL_FUNDED",
        ],
    );

    let mut total = 0usize;
    let mut seen = std::collections::BTreeSet::new();
    for category in CATEGORIES {
        let codes = codes_in(*category);
        total += codes.len();
        for code in codes {
            assert!(seen.insert(*code), "code {code} in more than one category");
            assert_eq!(reason(code).expect(code).category, *category);
        }
    }
    assert_eq!(total, 269, "every code belongs to exactly one category");
}

#[test]
fn categories_are_exact_and_in_python_order() {
    assert_eq!(
        CATEGORIES,
        &[
            Category::Auth,
            Category::Validation,
            Category::Risk,
            Category::Market,
            Category::Custody,
            Category::Approval,
            Category::Signing,
            Category::Governance,
            Category::Strategy,
            Category::Graph,
            Category::Plugin,
            Category::Backtest,
            Category::Protection,
            Category::Engine,
            Category::Adapter,
            Category::Observability,
            Category::Outcome,
        ]
    );
    assert_eq!(CATEGORIES.len(), 17);
    for category in CATEGORIES {
        assert_eq!(category.to_string(), category.as_str());
        assert_eq!(Category::from_str(category.as_str()), Ok(*category));
    }
    assert!("not_a_category".parse::<Category>().is_err());
}

#[test]
fn enum_string_forms_match_python_literals() {
    for layer in [
        Layer::Core,
        Layer::Sdk,
        Layer::Adapters,
        Layer::Api,
        Layer::Engine,
        Layer::Executor,
    ] {
        assert_eq!(layer.to_string(), layer.as_str());
    }
    for severity in [Severity::Info, Severity::Warning, Severity::Critical] {
        assert_eq!(severity.to_string(), severity.as_str());
    }
}

#[test]
fn is_denial_is_true_for_every_category_except_outcome() {
    for spec in REASON_SPECS {
        assert_eq!(
            spec.is_denial(),
            spec.category != Category::Outcome,
            "code {}",
            spec.code
        );
    }
}

#[test]
fn derived_registries_match_python_order_and_content() {
    assert_eq!(
        RISK_REASON_CODES,
        &[
            "INVALID_SIDE",
            "INVALID_AMOUNT",
            "ORDER_LIMIT",
            "SIZE_BELOW_MIN",
            "SIZE_ABOVE_MAX",
            "EQUITY_UNKNOWN",
            "PROFILE_UNKNOWN",
            "PAPER_MARK_LIMIT",
            "PRICE_IMPACT_TOO_HIGH",
            "QUOTE_STALE",
            "MARK_TIMESTAMP_FUTURE",
            "PRICE_UNKNOWN",
            "LIQUIDITY_UNKNOWN",
            "LIQUIDITY_TOO_LOW",
            "NO_PAIR",
            "SNAPSHOT_STALE",
            "PAIR_AGE_UNKNOWN",
            "PAIR_TOO_YOUNG",
            "EXIT_NO_ROUTE",
            "NO_SELL_ROUTE",
            "NO_ROUTE",
            "POSITION_LIMIT",
            "MAX_OPEN_POSITIONS",
            "DAILY_LOSS_LIMIT",
            "INSUFFICIENT_CASH",
            "SHORTFALL_FUNDED",
            "TOPUP_CAP_REACHED",
            "INSUFFICIENT_POSITION",
            "POSITION_CHANGED",
            "KILL_SWITCH_ACTIVE",
            "KILL_SWITCH_ARMED",
            "WALLET_UNKNOWN",
            "SETTLEMENT_ASSET_UNKNOWN",
            "AWAITING_APPROVAL",
            "APPROVAL_EXPIRED",
            "APPROVAL_NOT_PENDING",
            "APPROVAL_REQUIRES_PERSON",
            "APPROVAL_INTENT_INVALID",
            "APPROVAL_STATE_UNREADABLE",
            "APPROVAL_SUPERSEDED",
            "APPROVAL_REJECTED",
            "APPROVAL_DECISION_NOTE_INVALID",
            "AUTOMATION_CONSENT_REQUIRED",
            "AUTOMATION_TIER_UNKNOWN",
            "AGENT_BUCKET_EXCEEDED",
            "AGENT_BUCKET_UNREADABLE",
            "AGENT_BUCKET_UNKNOWN",
            "AGENT_BUCKET_REQUIRES_PERSON",
            "AGENT_BUCKET_CAP_INVALID",
        ]
    );
    assert_eq!(RISK_REASON_MESSAGES.len(), RISK_REASON_CODES.len());
    for (index, code) in RISK_REASON_CODES.iter().enumerate() {
        assert_eq!(RISK_REASON_MESSAGES[index].0, *code);
        assert_eq!(risk_reason_message(code), Some(message_for(code)));
    }
    assert_eq!(risk_reason_message("NOT_A_RISK_CODE"), None);
    assert_eq!(
        ezchain_core::reason_codes::VAULT_REASONS,
        &[
            "VAULT_UNREACHABLE",
            "VAULT_APPROVAL_DENIED",
            "VAULT_UNKNOWN_HANDLE",
            "VAULT_LOCKED",
            "VAULT_WATCH_ONLY",
            "VAULT_SIGN_FAILED",
            "VAULT_BOUNDARY_REQUIRED",
        ]
    );
    assert_eq!(
        ezchain_core::reason_codes::BINDING_REASONS,
        &[
            "TRANSACTION_UNDECODABLE",
            "TRANSACTION_PROGRAM_MISMATCH",
            "TRANSACTION_CLUSTER_MISMATCH",
            "TRANSACTION_MINT_MISMATCH",
            "TRANSACTION_SIDE_MISMATCH",
            "TRANSACTION_AMOUNT_MISMATCH",
            "TRANSACTION_MIN_OUT_MISMATCH",
            "TRANSACTION_RECIPIENT_MISMATCH",
            "TRANSACTION_IDEMPOTENCY_MISMATCH",
            "TRANSACTION_DIGEST_MISMATCH",
            "TRANSACTION_APPROVAL_MISMATCH",
        ]
    );
    assert_eq!(
        ezchain_core::reason_codes::EXECUTION_STAGES,
        &[
            "filled",
            "partially_filled",
            "rejected",
            "timeout",
            "denied",
            "journal_error",
        ]
    );
}
