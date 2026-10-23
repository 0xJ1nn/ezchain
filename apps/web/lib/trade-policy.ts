// Typed client for the persisted trade-sizing policy.
//
// The API owns every number and every resolution. This module never computes a PnL,
// equity or fill. The fixture policy is a visibly-labelled sample so Playwright and
// offline UI work can render the form without an API; a live read never falls back to it.

import {
	apiRequest,
	type PaperResult,
	PaperApiError,
	capture,
	webMode,
} from "./paper-api";

export type RiskProfile = "low" | "mid" | "high";

export interface TradeSizeLimits {
	min_amount_usd: string;
	min_pct_equity: string;
	max_amount_usd: string | null;
	max_pct_equity: string | null;
}

export interface TradeProfiles {
	low: TradeSizeLimits;
	mid: TradeSizeLimits;
	high: TradeSizeLimits;
}

export interface TradePolicyInput {
	default_profile: RiskProfile;
	min_amount_usd: string;
	min_pct_equity: string;
	max_amount_usd: string | null;
	max_pct_equity: string | null;
	profiles: TradeProfiles;
}

export interface TradeSizePreview {
	profile: RiskProfile;
	amount_usd: string | null;
	reason: string | null;
	manual: boolean;
	equity_usd: string | null;
	limits: TradeSizeLimits | null;
}

export interface TradeProfilePreviews {
	low: TradeSizePreview;
	mid: TradeSizePreview;
	high: TradeSizePreview;
}

export interface TradePolicyView extends TradePolicyInput {
	source: "default" | "persisted";
	updated_at: string | null;
	updated_by: string | null;
	equity_usd: string | null;
	previews: TradeProfilePreviews;
}

const FIXTURE_LIMITS: TradeProfiles = {
	low: {
		min_amount_usd: "10",
		min_pct_equity: "0.10",
		max_amount_usd: "50",
		max_pct_equity: "1",
	},
	mid: {
		min_amount_usd: "10",
		min_pct_equity: "0.10",
		max_amount_usd: "150",
		max_pct_equity: "2",
	},
	high: {
		min_amount_usd: "10",
		min_pct_equity: "0.10",
		max_amount_usd: "500",
		max_pct_equity: "5",
	},
};

function preview(profile: RiskProfile, amount: string): TradeSizePreview {
	return {
		profile,
		amount_usd: amount,
		reason: null,
		manual: false,
		equity_usd: "10000",
		limits: { ...FIXTURE_LIMITS[profile] },
	};
}

export const fixtureTradePolicy: TradePolicyView = {
	default_profile: "mid",
	min_amount_usd: "10",
	min_pct_equity: "0.10",
	max_amount_usd: "1000",
	max_pct_equity: "5",
	profiles: FIXTURE_LIMITS,
	previews: {
		low: preview("low", "50"),
		mid: preview("mid", "150"),
		high: preview("high", "500"),
	},
	source: "default",
	updated_at: null,
	updated_by: null,
	equity_usd: "10000",
};

function clonePolicy(policy: TradePolicyView): TradePolicyView {
	return {
		...policy,
		profiles: {
			low: { ...policy.profiles.low },
			mid: { ...policy.profiles.mid },
			high: { ...policy.profiles.high },
		},
		previews: {
			low: { ...policy.previews.low },
			mid: { ...policy.previews.mid },
			high: { ...policy.previews.high },
		},
	};
}

export function getTradePolicy(): Promise<PaperResult<TradePolicyView>> {
	return capture(async () => {
		if (webMode() === "fixture") return clonePolicy(fixtureTradePolicy);
		return apiRequest<TradePolicyView>("/settings/trade-policy");
	});
}

/** Mutations always reach the API, including from a fixture build. */
export function saveTradePolicy(input: TradePolicyInput): Promise<TradePolicyView> {
	return apiRequest<TradePolicyView>("/settings/trade-policy", {
		method: "PUT",
		body: JSON.stringify(input),
	});
}

export interface PreviewQuery {
	profile: RiskProfile;
	limits?: Partial<TradeSizeLimits>;
	/** Unsaved workspace bounds, so the preview validates exactly what the form would save. */
	envelope?: Partial<TradeSizeLimits>;
	manualAmount?: string;
}

/** Read-only server preview for an unsaved draft profile. */
export function previewTradeSize(
	query: PreviewQuery,
	mode: "fixture" | "live" = webMode(),
): Promise<PaperResult<TradeSizePreview>> {
	return capture(async () => {
		if (mode === "fixture") {
			const base = fixtureTradePolicy.previews[query.profile];
			if (query.manualAmount) {
				return { ...base, manual: true, amount_usd: query.manualAmount };
			}
			return { ...base };
		}
		const params = new URLSearchParams({ profile: query.profile });
		if (query.limits) {
			for (const [key, value] of Object.entries(query.limits)) {
				if (value !== null && value !== undefined && value !== "") {
					params.set(key, value);
				}
			}
		}
		if (query.envelope) {
			for (const [key, value] of Object.entries(query.envelope)) {
				if (value !== null && value !== undefined && value !== "") {
					params.set(`envelope_${key}`, value);
				}
			}
		}
		if (query.manualAmount) params.set("manual_amount_usd", query.manualAmount);
		return apiRequest<TradeSizePreview>(
			`/settings/trade-policy/preview?${params.toString()}`,
		);
	});
}

export function isTradePolicyError(error: unknown): error is PaperApiError {
	return error instanceof PaperApiError;
}
