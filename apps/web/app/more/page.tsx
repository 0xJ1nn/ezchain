import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Panel } from "@/components/ui";

const groups = [
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
			["/signals", "Signals"],
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
	return (
		<AppShell active="more">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">More</div>
					<h1>More</h1>
					<p className="sub">
						Everything that does not fit in the bottom nav. The mobile shell keeps Home,
						Positions, Markets and Automate one tap away; this page holds the rest.
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

			<Panel title="Chain filter & sync" sub="moved here on mobile" className="mt-16">
				<div className="row">
					<span className="chip">⬡ All chains</span>
					<span className="chip">⟳ fixture data</span>
					<span className="badge">PAPER</span>
				</div>
				<p className="note mt-12">
					These controls are static in the M1 fixture. They move into the shell once the
					chain registry and sync state are real.
				</p>
			</Panel>
		</AppShell>
	);
}
