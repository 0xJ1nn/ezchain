"use client";

import { useState, useSyncExternalStore, type MouseEvent } from "react";
import {
	getWatchlistServerSnapshot,
	getWatchlistSnapshot,
	isWatchlisted,
	subscribeWatchlist,
	toggleWatchlist,
	watchlistKey,
	type WatchlistEntry,
} from "@/lib/watchlist";
import styles from "./watchlist-toggle.module.css";

export function useWatchlist(): WatchlistEntry[] {
	return useSyncExternalStore(
		subscribeWatchlist,
		getWatchlistSnapshot,
		getWatchlistServerSnapshot,
	);
}

export function WatchlistToggle({
	chain,
	baseToken,
	symbol,
	name,
	className,
}: {
	chain: string;
	baseToken: string;
	symbol: string;
	name?: string;
	className?: string;
}) {
	const entries = useWatchlist();
	const starred = isWatchlisted(entries, chain, baseToken);
	const [announcement, setAnnouncement] = useState("");
	const label = starred ? `Remove ${symbol} from watchlist` : `Add ${symbol} to watchlist`;

	function onClick(event: MouseEvent<HTMLButtonElement>) {
		// The control may sit inside a row link on another surface. A star click is a
		// watchlist change, never a navigation.
		event.preventDefault();
		event.stopPropagation();
		const result = toggleWatchlist({ chain, base_token: baseToken, symbol, name });
		if (result.full) {
			setAnnouncement("Watchlist is full. Remove a token before adding another.");
			return;
		}
		if (result.added) {
			setAnnouncement(
				result.persisted
					? `${symbol} added to watchlist.`
					: `${symbol} added for this view only; browser storage is unavailable.`,
			);
		} else {
			setAnnouncement(
				result.persisted
					? `${symbol} removed from watchlist.`
					: `${symbol} removed for this view only; browser storage is unavailable.`,
			);
		}
	}

	return (
		<span className={styles.wrap}>
			<button
				type="button"
				className={className ? `${styles.star} ${className}` : styles.star}
				aria-pressed={starred}
				aria-label={label}
				title={label}
				data-watchlist-key={watchlistKey(chain, baseToken)}
				onClick={onClick}
			>
				<span aria-hidden="true">{starred ? "★" : "☆"}</span>
			</button>
			<span className="sr-only" role="status" aria-live="polite">
				{announcement}
			</span>
		</span>
	);
}
