// The trade-policy surface's declaration of what it renders.
//
// This module has no imports so a Python test can evaluate it with Node and compare it
// with the API registry in both directions: a backend-only field or a GUI control with
// no API behind it both fail the build. Order is part of the contract too, because the
// page renders the fields in the order the API returns them.

export const TRADE_POLICY_FIELD_KEYS = [
	"min_order_usd",
	"max_order_usd",
	"max_order_pct_of_equity",
	"per_token_cap_usd",
	"max_slippage_bps",
] as const;

export const TRADE_POLICY_PROFILE_ORDER = ["low", "mid", "high"] as const;
