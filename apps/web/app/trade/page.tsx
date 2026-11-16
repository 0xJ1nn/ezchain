"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/shell";

// The entry point for the trading workstation. It opens the last market the
// trade screen saw, or the default market when there is no stored choice, in
// the same screen the token route renders.

const DEFAULT_MARKET = {
	chain: "solana",
	id: "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
};

export default function TradePage() {
	const router = useRouter();
	const [target, setTarget] = useState<string | null>(null);

	useEffect(() => {
		let market = DEFAULT_MARKET;
		try {
			const raw = window.localStorage.getItem("ezchain.lastTradeMarket");
			if (raw) {
				const parsed = JSON.parse(raw) as { chain?: unknown; id?: unknown };
				if (
					typeof parsed.chain === "string" &&
					parsed.chain.length > 0 &&
					typeof parsed.id === "string" &&
					parsed.id.length > 0
				) {
					market = { chain: parsed.chain, id: parsed.id };
				}
			}
		} catch {
			// A blocked or corrupt store falls back to the default market.
		}
		const path = `/tokens/${encodeURIComponent(market.chain)}/${encodeURIComponent(market.id)}`;
		setTarget(path);
		router.replace(path);
	}, [router]);

	return (
		<AppShell active="markets">
			<header className="page-head">
				<div className="titles">
					<h1>Trade</h1>
					<p className="sub">Opening {target ? "your last market" : "the default market"}…</p>
				</div>
			</header>
		</AppShell>
	);
}
