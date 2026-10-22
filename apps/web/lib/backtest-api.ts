// Typed client for backtest runs.
//
// The backtest route is not part of this API build yet. The page therefore renders a
// truthful "not available" state with the reason. The shape below is the contract the
// page is ready to render as soon as the leader wires `GET /api/v1/backtests/runs`.

import { serverAuthorizationHeader } from "./api-auth";
import {
	PaperApiError,
	type PaperResult,
	webMode,
} from "./paper-api";

export interface BacktestRun {
	id: string;
	name: string;
	strategy_id: string | null;
	status: string;
	started_at: string | null;
	finished_at: string | null;
	fill_model: string | null;
	fee_bps: number | null;
	slippage_bps: number | null;
	trades: number | null;
	net_pnl_usd: string | null;
}

function apiBase() {
	if (typeof window !== "undefined") return "";
	return (process.env.EZCHAIN_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

export async function getBacktestRuns(): Promise<PaperResult<BacktestRun[]>> {
	const mode = webMode();
	if (mode === "fixture") {
		return {
			ok: false,
			mode,
			error: {
				code: "BACKTESTS_UNAVAILABLE",
				message:
					"The backtest service is not part of this build. No runs are listed and no results are simulated.",
			},
		};
	}
	try {
		const response = await fetch(`${apiBase()}/api/v1/backtests/runs`, {
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
			if (response.status === 404) {
				return {
					ok: false,
					mode,
					error: {
						code: "BACKTESTS_UNAVAILABLE",
						message:
							"This API build does not expose /api/v1/backtests/runs. No runs are listed and no results are simulated.",
					},
				};
			}
			throw new PaperApiError(code, message, response.status);
		}
		return { ok: true, mode, data: payload as BacktestRun[] };
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
