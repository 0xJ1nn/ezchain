// Typed client for the immutable graph backtest list.
//
// The API stores a backtest as an immutable row and labels every result BACKTEST. The
// page renders only server values; it never simulates a run or computes a return.

import { serverAuthorizationHeader } from "./api-auth";
import { PaperApiError, type PaperResult, webMode } from "./paper-api";

export interface BacktestMetrics {
	net_pnl_usd: string;
	return_pct: string;
	max_drawdown_pct: string;
	win_rate_pct: string | null;
	profit_factor: string | null;
	trades: number;
	closed_trades: number;
	exposure_pct: string;
	sharpe: string | null;
	fees_usd: string;
}

export interface BacktestRow {
	id: string;
	created_at: string;
	label: "BACKTEST";
	request: Record<string, unknown>;
	data_source: string;
	fill_model: string;
	metrics: BacktestMetrics;
	node_hits: Record<string, number>;
}

function apiBase() {
	if (typeof window !== "undefined") return "";
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

export async function getBacktests(strategyId?: string): Promise<PaperResult<BacktestRow[]>> {
	const mode = webMode();
	if (mode === "fixture") {
		return {
			ok: false,
			mode,
			error: {
				code: "BACKTESTS_FIXTURE_UNAVAILABLE",
				message:
					"Backtests are not part of the labelled fixture set. Run the builder against a live API to create a real BACKTEST row.",
			},
		};
	}
	try {
		const query = strategyId ? `?strategy_id=${encodeURIComponent(strategyId)}` : "";
		const response = await fetch(`${apiBase()}/api/v1/backtests${query}`, {
			cache: "no-store",
			headers: { accept: "application/json", ...serverAuthorizationHeader() },
		});
		const text = await response.text();
		let payload: unknown = null;
		if (text) {
			try {
				payload = JSON.parse(text);
			} catch {
				payload = text;
			}
		}
		if (!response.ok) {
			const body = (payload ?? {}) as Record<string, unknown>;
			const detail = body.detail;
			const code =
				(typeof detail === "object" && detail !== null && "code" in detail
					? String((detail as Record<string, unknown>).code)
					: undefined) ?? `HTTP_${response.status}`;
			const message =
				(typeof detail === "object" && detail !== null && "message" in detail
					? String((detail as Record<string, unknown>).message)
					: undefined) ?? `backtest request failed with status ${response.status}`;
			throw new PaperApiError(code, message, response.status);
		}
		return { ok: true, mode, data: payload as BacktestRow[] };
	} catch (error) {
		const apiError = error instanceof PaperApiError ? error : null;
		return {
			ok: false,
			mode,
			error: {
				code: apiError?.code ?? "API_UNREACHABLE",
				message:
					apiError?.message ??
					(error instanceof Error ? error.message : "the API could not be reached"),
			},
		};
	}
}
