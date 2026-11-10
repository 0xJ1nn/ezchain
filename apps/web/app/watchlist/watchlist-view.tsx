"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { MarketTable } from "@/components/market-table";
import { Badge, Panel } from "@/components/ui";
import { WatchlistToggle, useWatchlist } from "@/components/watchlist-toggle";
import type { MarketRow } from "@/lib/api";
import { shortAddress } from "@/lib/live-format";
import { watchlistKey } from "@/lib/watchlist";
import styles from "./watchlist-view.module.css";

interface WatchlistRowResult {
	key: string;
	row: MarketRow;
}

interface WatchlistUnavailable {
	key: string;
	code: string;
	message: string;
}

interface LookupResponse {
	rows: WatchlistRowResult[];
	unavailable: WatchlistUnavailable[];
	looked_up_at: string;
}

interface LookupFailure {
	code: string;
	message: string;
}

function isLookupResponse(value: unknown): value is LookupResponse {
	if (!value || typeof value !== "object") return false;
	const candidate = value as Record<string, unknown>;
	return (
		Array.isArray(candidate.rows) &&
		Array.isArray(candidate.unavailable) &&
		typeof candidate.looked_up_at === "string"
	);
}

function utcTime(iso: string): string {
	const date = new Date(iso);
	return Number.isNaN(date.getTime()) ? "unknown time" : `${date.toISOString().slice(11, 19)} UTC`;
}

function countLabel(count: number, singular: string, plural: string): string {
	return `${count} ${count === 1 ? singular : plural}`;
}

// One in-flight lookup per key set. React Strict Mode mounts effects twice in
// development, and aborting the first request left the route handler to start over; a
// shared promise makes both mounts wait on the same read instead of racing.
const lookupRequests = new Map<string, Promise<LookupResponse>>();

function requestLookup(keys: string[]): Promise<LookupResponse> {
	const signature = keys.join("|");
	const existing = lookupRequests.get(signature);
	if (existing) return existing;

	const query = new URLSearchParams();
	for (const key of keys) query.append("key", key);
	const request = fetch(`/watchlist/rows?${query.toString()}`, { cache: "no-store" })
		.then(async (response) => {
			const payload: unknown = await response.json().catch(() => null);
			if (!response.ok) {
				const detail = (payload as { detail?: { code?: unknown; message?: unknown } } | null)
					?.detail;
				throw {
					code: typeof detail?.code === "string" ? detail.code : `HTTP_${response.status}`,
					message:
						typeof detail?.message === "string"
							? detail.message
							: `The watchlist lookup returned ${response.status}.`,
				} satisfies LookupFailure;
			}
			if (!isLookupResponse(payload)) {
				throw {
					code: "WATCHLIST_RESPONSE_INVALID",
					message: "The watchlist lookup returned an unrecognised response.",
				} satisfies LookupFailure;
			}
			return payload;
		})
		.finally(() => {
			lookupRequests.delete(signature);
		});
	lookupRequests.set(signature, request);
	return request;
}

export function WatchlistView({
	chainNames,
	live,
}: {
	chainNames: Record<string, string>;
	live: boolean;
}) {
	const entries = useWatchlist();
	const [ready, setReady] = useState(false);
	const [lookup, setLookup] = useState<LookupResponse | null>(null);
	const [error, setError] = useState<LookupFailure | null>(null);
	const [loading, setLoading] = useState(false);
	const [attempt, setAttempt] = useState(0);
	const signatureRef = useRef<string | null>(null);

	useEffect(() => {
		setReady(true);
	}, []);

	useEffect(() => {
		const signature = entries
			.map((entry) => watchlistKey(entry.chain, entry.base_token))
			.join("|");
		if (signatureRef.current !== signature) {
			// A different set of keys has no previous answer. Clearing the last lookup
			// prevents a newly added token from being called unavailable while it loads.
			signatureRef.current = signature;
			setLookup(null);
		}
		if (entries.length === 0) {
			setLookup(null);
			setError(null);
			setLoading(false);
			return;
		}
		const keys = entries.map((entry) => watchlistKey(entry.chain, entry.base_token));
		let active = true;
		setLoading(true);
		setError(null);
		requestLookup(keys)
			.then((payload) => {
				if (active) setLookup(payload);
			})
			.catch((caught: unknown) => {
				if (!active) return;
				const failure = caught as Partial<LookupFailure>;
				setError({
					code:
						typeof failure.code === "string" ? failure.code : "WATCHLIST_UNAVAILABLE",
					message:
						typeof failure.message === "string"
							? failure.message
							: "The watchlist lookup failed.",
				});
			})
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, [entries, attempt]);

	const rowByKey = new Map<string, MarketRow>();
	const reasonByKey = new Map<string, WatchlistUnavailable>();
	if (lookup) {
		for (const item of lookup.rows) rowByKey.set(item.key, item.row);
		for (const item of lookup.unavailable) reasonByKey.set(item.key, item);
	}

	const availableRows: MarketRow[] = [];
	const unavailableEntries: Array<{
		key: string;
		entry: (typeof entries)[number];
		reason: WatchlistUnavailable;
	}> = [];
	for (const entry of entries) {
		const key = watchlistKey(entry.chain, entry.base_token);
		const row = rowByKey.get(key);
		if (row) {
			availableRows.push(row);
			continue;
		}
		unavailableEntries.push({
			key,
			entry,
			reason: reasonByKey.get(key) ?? {
				key,
				code: "LOOKUP_INCOMPLETE",
				message: "The lookup did not report a market row for this token.",
			},
		});
	}

	if (!ready) {
		return (
			<Panel
				title="Reading this browser's watchlist"
				action={<Badge tone="ghost">LOCAL</Badge>}
			>
				<div className="note" role="status" aria-live="polite">
					Reading starred tokens from browser storage. No account or server copy is involved.
				</div>
			</Panel>
		);
	}

	if (entries.length === 0) {
		return (
			<Panel
				title="No starred tokens yet"
				sub="stored in this browser only"
				action={<Badge tone="ghost">BROWSER-LOCAL</Badge>}
			>
				<p>
					Star a row on <Link href="/markets">Markets</Link> to keep the token here. The list
					is a key in this browser&apos;s local storage: no account, no server copy and no
					order.
				</p>
				<p className="small muted mt-8">
					Clearing site data removes it. If browser storage is blocked, a star lasts only for
					the current page.
				</p>
				<div className="row mt-12">
					<Link className="btn primary" href="/markets">
						Browse markets →
					</Link>
				</div>
			</Panel>
		);
	}

	return (
		<>
			<div className={styles.summary}>
				<div className="row">
					<Badge tone="ghost">BROWSER-LOCAL</Badge>
					<Badge tone={live ? "ok" : "ghost"}>{live ? "LIVE FETCH" : "FIXTURE DATA"}</Badge>
					<span className="small muted">
						{lookup
							? `${countLabel(entries.length, "starred token", "starred tokens")} · ${availableRows.length} available · ${unavailableEntries.length} unavailable · looked up ${utcTime(lookup.looked_up_at)}`
							: `${countLabel(entries.length, "starred token", "starred tokens")} · looking up…`}
					</span>
				</div>
				<button
					type="button"
					className="btn sm"
					onClick={() => setAttempt((value) => value + 1)}
					disabled={loading}
				>
					{loading ? "Refreshing…" : "Refresh"}
				</button>
			</div>

			{loading && !lookup && (
				<Panel
					title="Looking up starred tokens"
					sub="the list itself never leaves this browser"
					action={<Badge tone="ghost">MARKET FEED</Badge>}
				>
					<div className="note" role="status" aria-live="polite">
						Reading the market feed for{" "}
						{countLabel(entries.length, "starred token", "starred tokens")}.
					</div>
				</Panel>
			)}

			{error && (
				<Panel
					title="Watchlist lookup failed"
					action={<Badge tone="err">{error.code}</Badge>}
					footer={
						<button
							type="button"
							className="btn sm"
							onClick={() => setAttempt((value) => value + 1)}
						>
							Retry
						</button>
					}
				>
					<div className="note err">{error.message}</div>
					<p className="small muted mt-8">
						The stars are still in this browser. No rows are substituted for a failed read.
						{lookup ? " The last successful lookup is shown below." : ""}
					</p>
				</Panel>
			)}

			{lookup && availableRows.length > 0 && (
				<Panel
					flush
					title="Starred markets"
					sub="current market rows for starred tokens"
					action={<Badge tone="ghost">{availableRows.length} AVAILABLE</Badge>}
					footer={
						<span>
							Use the pressed star to remove a token from this browser. Source and age are
							per row; prices are discovery data, not executable quotes.
						</span>
					}
				>
					<MarketTable rows={availableRows} chainNames={chainNames} />
				</Panel>
			)}

			{lookup && availableRows.length === 0 && (
				<Panel
					title="No starred market rows available"
					sub="the tokens stay listed below"
					action={<Badge tone="warn">MARKET DATA</Badge>}
				>
					<p className="small muted">
						None of the {countLabel(entries.length, "starred token", "starred tokens")} appear
						in the current market feed. They are reported below as unavailable rather than
						dropped.
					</p>
				</Panel>
			)}

			{lookup && unavailableEntries.length > 0 && (
				<div className="mt-16">
					<Panel
						title="Unavailable starred tokens"
						sub="reported, not hidden"
						action={<Badge tone="warn">{unavailableEntries.length}</Badge>}
						footer={
							<span>
								A starred token stays listed when the feed has no pair. Search markets to
								see whether a new pair exists.
							</span>
						}
					>
						<div className="table-wrap">
							<table className="table" data-testid="watchlist-unavailable">
								<thead>
									<tr>
										<th scope="col">Token</th>
										<th scope="col">Chain</th>
										<th scope="col">Status</th>
										<th scope="col">Lookup</th>
									</tr>
								</thead>
								<tbody>
									{unavailableEntries.map(({ key, entry, reason }) => {
										const search = new URLSearchParams({
											chain: entry.chain,
											q: entry.base_token,
										});
										return (
											<tr key={key}>
												<td>
													<div className={styles.identity}>
														<WatchlistToggle
															chain={entry.chain}
															baseToken={entry.base_token}
															symbol={entry.symbol}
															name={entry.name}
														/>
														<span>
															<span className="strong">{entry.symbol}</span>
															<span className="sub" style={{ display: "block" }}>
																{entry.name ? `${entry.name} · ` : ""}
																{shortAddress(entry.base_token)}
															</span>
														</span>
													</div>
												</td>
												<td>{chainNames[entry.chain] ?? entry.chain}</td>
												<td>
													<Badge tone="warn">{reason.code}</Badge>
													<span className="small muted" style={{ display: "block" }}>
														{reason.message}
													</span>
												</td>
												<td>
													<Link className="btn sm" href={`/markets?${search.toString()}`}>
														Search markets
													</Link>
												</td>
											</tr>
										);
									})}
								</tbody>
							</table>
						</div>
					</Panel>
				</div>
			)}
		</>
	);
}
