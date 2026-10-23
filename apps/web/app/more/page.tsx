import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Panel } from "@/components/ui";

const groups = [
	{
		title: "Discovery",
		links: [
			["/markets", "Browse markets"],
			["/signals", "Signals"],
		],
	},
	{
		title: "Overview",
		links: [["/performance", "Performance"]],
	},
	{
		title: "Trade",
		links: [
			["/wallets", "Wallets"],
			["/ledger", "Activity / ledger"],
			["/reports", "Reports"],
		],
	},
	{
		title: "Automate",
		links: [
			["/automations", "Automations"],
			["/strategies", "Strategies"],
			["/backtests", "Backtests"],
		],
	},
	{
		title: "Protect",
		links: [
			["/risk", "Risk & kill switch"],
			["/alerts", "Alerts"],
		],
	},
	{
		title: "System",
		links: [
			["/chains", "Chains & RPC"],
			["/integrations", "Integrations"],
			["/security", "Security"],
			["/developer", "Developer"],
			["/operations", "Operations"],
			["/workspace", "Workspace"],
			["/settings", "Settings"],
		],
	},
	{
		title: "Reference",
		links: [
			["/states", "State gallery"],
			["/components", "Components"],
			["/mobile", "Mobile"],
		],
	},
];

export default function MorePage() {
	const fixture = process.env.EZCHAIN_WEB_MODE === "fixture";
	return (
		<AppShell active="more">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">More</div>
					<h1>More</h1>
					<p className="sub">
						Browse and Signals are the primary discovery surfaces and stay one tap away in
						the bottom nav. Performance, Automate, Trade, Protect and System live here under
						More. Reference galleries are design documentation, not production surfaces.
					</p>
				</div>
			</header>

			<div className="grid cols-2">
				{groups.map((group) => (
					<Panel title={group.title} key={group.title}>
						<div className="list">
							{group.links.map(([href, label]) => (
								<Link className="list-row" href={href} key={href}>
									<span className="grow">
										<span className="title">{label}</span>
									</span>
									<span className="muted" aria-hidden="true">
										→
									</span>
								</Link>
							))}
						</div>
					</Panel>
				))}
			</div>

			<Panel title="Chain filter & sync" sub="open the market browser" className="mt-16">
				<div className="row">
					<Link
						className="chain chip"
						href="/markets"
						title="Open the multi-chain market browser"
					>
						<span aria-hidden="true">⬡</span> All chains
					</Link>
					<span className="source chip" title="Market data source">
						{fixture ? "⟳ fixture data" : "⟳ live · dexscreener"}
					</span>
					<span className="badge">PAPER</span>
				</div>
				<p className="note mt-12">
					{fixture
						? "This is the labelled fixture shell. The chain registry is real; open Markets to filter it by chain."
						: "The chain registry and market source are live. Chain filtering lives on the Markets page; open it to change the visible chains."}
				</p>
			</Panel>
		</AppShell>
	);
}
