import Link from "next/link";
import { AppShell } from "@/components/shell";
import { getChains, isLiveMode, type Chain } from "@/lib/api";
import { WatchlistView } from "./watchlist-view";

export const dynamic = "force-dynamic";

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
			<WatchlistView chainNames={chainNames} live={isLiveMode()} />
		</AppShell>
	);
}
