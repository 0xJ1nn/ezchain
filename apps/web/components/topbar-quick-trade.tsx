"use client";

import { useState } from "react";
import type { MarketRow, Quote, Reason } from "@/lib/api";
import { formatUsd } from "@/lib/live-format";
import { QuickTradeBar } from "@/components/quick-trade-bar";
import styles from "./topbar-quick-trade.module.css";

export interface QuickTradeOption {
	row: MarketRow;
	quote: Quote | null;
	reason: Reason | null;
}

// A topbar entry point to the same paper ticket the token page uses. The server shell
// supplies the market rows and their quotes; this control only filters and selects them.
// Presets, protection, cost sheet and submit all live in the shared QuickTradeBar, so
// there is no second trade form in the bundle.

export function TopbarQuickTrade({ options }: { options: QuickTradeOption[] }) {
	const [open, setOpen] = useState(false);
	const [query, setQuery] = useState("");
	const [selected, setSelected] = useState<QuickTradeOption | null>(null);
	const [now, setNow] = useState(0);

	const search = query.trim().toLowerCase();
	const rows = options.filter((option) =>
		!search
			? true
			: option.row.symbol.toLowerCase().includes(search) ||
				option.row.name.toLowerCase().includes(search) ||
				option.row.base_token.toLowerCase().includes(search),
	);

	function choose(option: QuickTradeOption) {
		setSelected(option);
		setNow(Date.now());
	}

	return (
		<div className={styles.wrap}>
			<button
				type="button"
				className={`btn sm ${styles.trigger}`}
				aria-expanded={open}
				aria-controls="topbar-quick-trade-panel"
				onClick={() => setOpen((value) => !value)}
			>
				<span aria-hidden="true">⚡</span> Quick trade
			</button>
			{open && (
				<div
					id="topbar-quick-trade-panel"
					className={styles.panel}
					data-testid="topbar-quick-trade-panel"
					role="region"
					aria-label="Quick trade"
				>
					{selected ? (
						<>
							<div className="between mb-8">
								<span className="strong">{selected.row.symbol}</span>
								<button type="button" className="btn sm" onClick={() => setSelected(null)}>
									Change token
								</button>
							</div>
							<p className="small muted mt-0">
								Paper only. Presets, protection and the cost sheet are the same controls as on
								the token page.
							</p>
							<QuickTradeBar
								chain={selected.row.chain}
								tokenAddress={selected.row.base_token}
								symbol={selected.row.symbol}
								side="buy"
								amount="100"
								quote={selected.quote}
								quoteReason={selected.reason}
								now={now}
							/>
						</>
					) : (
						<>
							<input
								className="input"
								type="search"
								value={query}
								maxLength={80}
								placeholder="Filter markets (symbol, name or address)"
								aria-label="Symbol or address"
								onChange={(event) => setQuery(event.target.value)}
							/>
							{rows.length === 0 ? (
								<p className="note mt-8">No market row matched. Nothing was selected.</p>
							) : (
								<div className={`${styles.results} mt-8`}>
									{rows.map((option) => (
										<button
											type="button"
											key={`${option.row.chain}:${option.row.pair_address}`}
											className={`btn sm ${styles.result}`}
											onClick={() => choose(option)}
										>
											{option.row.symbol} · {option.row.chain} ·{" "}
											{formatUsd(option.row.price_usd)}
										</button>
									))}
								</div>
							)}
						</>
					)}
				</div>
			)}
		</div>
	);
}
