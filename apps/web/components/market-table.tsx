"use client";

import Link from "next/link";
import { Badge } from "@/components/ui";
import type { MarketRow } from "@/lib/api";
import {
	ageLabel,
	chainInitial,
	changeText,
	changeTone,
	formatUsdCompact,
	formatUsdPrice,
	initials,
	pairAgeLabel,
	shortAddress,
	sourceLabel,
} from "@/lib/live-format";
import { watchlistKey } from "@/lib/watchlist";
import styles from "./market-table.module.css";
import { useWatchlist, WatchlistToggle } from "./watchlist-toggle";

function TokenCell({ row }: { row: MarketRow }) {
	return (
		<Link className="row" href={`/tokens/${row.chain}/${row.base_token}`}>
			<span className="token" style={{ width: 22, height: 22, fontSize: 8 }} aria-hidden="true">
				{initials(row.symbol)}
			</span>
			<span>
				<span className="strong">{row.symbol}</span>
				<span className="sub" style={{ display: "block" }}>
					{row.name} · {shortAddress(row.base_token)}
				</span>
			</span>
		</Link>
	);
}

function Change({ value }: { value: string | null }) {
	const tone = changeTone(value);
	return <span className={`mono small ${tone === "muted" ? "muted" : tone}`}>{changeText(value)}</span>;
}

function entryLabel(symbol: string, baseToken: string): string {
	return symbol || shortAddress(baseToken);
}

export function MarketTable({
	rows,
	chainNames,
	starredOnly = false,
	showAllHref,
	now,
}: {
	rows: MarketRow[];
	chainNames: Record<string, string>;
	starredOnly?: boolean;
	showAllHref?: string;
	now?: number;
}) {
	const entries = useWatchlist();
	const clock = now ?? Date.now();
	const labelFor = (id: string) => chainNames[id] ?? id;
	const starredKeys = new Set(
		entries.map((entry) => watchlistKey(entry.chain, entry.base_token)),
	);
	const pageKeys = new Set(rows.map((row) => watchlistKey(row.chain, row.base_token)));
	const visibleRows = starredOnly
		? rows.filter((row) => starredKeys.has(watchlistKey(row.chain, row.base_token)))
		: rows;
	const missing = starredOnly
		? entries.filter((entry) => !pageKeys.has(watchlistKey(entry.chain, entry.base_token)))
		: [];
	const missingNames = missing.map((entry) => entryLabel(entry.symbol, entry.base_token)).join(", ");

	return (
		<>
			{starredOnly && (
				<div className={styles.starredNote} data-testid="starred-only-summary">
					<Badge tone="ghost">STARRED ONLY</Badge>
					<span className={`small ${styles.noteText}`}>
						{entries.length === 0
							? "No tokens are starred yet. Use the star at the start of a market row to add one."
							: visibleRows.length > 0
								? `Showing ${visibleRows.length} starred row${
										visibleRows.length === 1 ? "" : "s"
									} on this server page.`
								: "No starred markets on this server page."}
						{missing.length > 0 && (
							<>
								{" "}
								{missing.length} starred token{missing.length === 1 ? "" : "s"} not on this
								page: {missingNames}.{" "}
								<Link href="/watchlist" aria-label="Open watchlist">
									Open watchlist →
								</Link>
							</>
						)}{" "}
						The footer counts describe the server result; the star narrows this page in your
						browser.
					</span>
					{showAllHref && (
						<Link className={`small ${styles.showAll}`} href={showAllHref}>
							Show all markets
						</Link>
					)}
				</div>
			)}

			{visibleRows.length > 0 && (
				<div className="table-wrap">
					<table className="table">
						<thead>
							<tr>
								<th scope="col">Token</th>
								<th scope="col">Chain</th>
								<th scope="col" className="num">
									Price
								</th>
								<th scope="col" className="num col-hide-sm">
									1h
								</th>
								<th scope="col" className="num">
									24h
								</th>
								<th scope="col" className="num">
									Liquidity
								</th>
								<th scope="col" className="num col-hide-sm">
									Vol 24h
								</th>
								<th scope="col">Source · age</th>
								<th scope="col" className="col-hide-sm">
									Pair age
								</th>
								<th scope="col">Safety</th>
							</tr>
						</thead>
						<tbody>
							{visibleRows.map((row) => (
								<tr key={`${row.chain}:${row.pair_address}`}>
									<td>
										<div className={styles.identity}>
											<WatchlistToggle
												chain={row.chain}
												baseToken={row.base_token}
												symbol={row.symbol}
												name={row.name}
											/>
											<TokenCell row={row} />
										</div>
									</td>
									<td>
										<span className="chain">
											<span className="sq">{chainInitial(labelFor(row.chain))}</span>
											{labelFor(row.chain)}
										</span>
									</td>
									<td className="num mono">{formatUsdPrice(row.price_usd)}</td>
									<td className="num col-hide-sm">
										<Change value={row.price_change_h1} />
									</td>
									<td className="num">
										<Change value={row.price_change_h24} />
									</td>
									<td className="num">{formatUsdCompact(row.liquidity_usd)}</td>
									<td className="num col-hide-sm">
										{formatUsdCompact(row.volume_24h_usd)}
									</td>
									<td>
										<span className="small muted">
											{sourceLabel(row.source.name)} · {ageLabel(row.source.observed_at, clock)}
										</span>
									</td>
									<td className="small muted col-hide-sm">
										{pairAgeLabel(row.pair_created_at, clock)}
									</td>
									<td>
										<Badge tone="warn" title="Safety is not implemented in P0">
											unknown
										</Badge>
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			)}
		</>
	);
}
