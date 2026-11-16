import Link from "next/link";
import { AppShell } from "@/components/shell";
import { getChains, isLiveMode, type Chain } from "@/lib/api";
import { getPaperPositions } from "@/lib/paper-api";
import { watchlistKey } from "@/lib/watchlist";
import { WatchlistView } from "./watchlist-view";

export const dynamic = "force-dynamic";

/**
 * The paper account's open tokens, as `chain:base_token` keys.
 *
 * The account read is one server call, and the keys are computed with the same
 * normalisation the browser-local store uses, so a starred EVM token matches the position
 * it is actually held in. A failed read is an empty list with the reason printed: an
 * unknown position is never rendered as "not held".
 */
async function heldTokenKeys(): Promise<{ keys: string[]; error: string | null }> {
	const result = await getPaperPositions();
	if (!result.ok) {
		return { keys: [], error: result.error.code };
	}
	return {
		keys: [...new Set(result.data.map((position) => watchlistKey(position.chain, position.token_address)))],
		error: null,
	};
}

export default async function WatchlistPage() {
	let chains: Chain[] = [];
	try {
		chains = await getChains();
	} catch {
		// The watchlist lookup reports its own reason codes; a failed registry read must
		// not hide the local list or fail the whole route.
		chains = [];
	}
	const chainNames = Object.fromEntries(chains.map((chain) => [chain.id, chain.name]));
	const held = await heldTokenKeys();

	return (
		<AppShell active="watchlist">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Overview / Watchlist</div>
					<h1>Watchlist</h1>
					<p className="sub">
						Tokens starred in this browser, looked up against the same market feed as
						Markets. The list is local storage, not an account, and nothing here places an
						order.
					</p>
				</div>
				<div className="page-actions">
					<Link className="btn" href="/markets">
						Browse markets
					</Link>
				</div>
			</header>
			<WatchlistView
				chainNames={chainNames}
				live={isLiveMode()}
				heldKeys={held.keys}
				heldError={held.error}
			/>
		</AppShell>
	);
}
