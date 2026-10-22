import Link from "next/link";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/shell";
import { Panel } from "@/components/ui";

const titles: Record<string, string> = {
	positions: "Positions",
	markets: "Markets",
	performance: "Performance",
	wallets: "Wallets",
	ledger: "Activity / ledger",
	reports: "Reports",
	automations: "Automations",
	strategies: "Strategies",
	signals: "Signals",
	backtests: "Backtests",
	risk: "Risk",
	alerts: "Alerts",
	chains: "Chains & RPC",
	integrations: "Integrations",
	settings: "Settings",
	security: "Security",
	developer: "Developer",
	operations: "Operations",
	workspace: "Workspace",
	states: "State gallery",
	mobile: "Mobile",
	components: "Components",
};

export default async function PlaceholderPage({
	params,
}: {
	params: Promise<{ slug: string[] }>;
}) {
	const { slug } = await params;
	const root = slug[0] ?? "";
	if (!(root in titles)) notFound();
	const title = titles[root];

	return (
		<AppShell active={root}>
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">ezCHAIN / {title}</div>
					<h1>{title}</h1>
					<p className="sub">
						This route is part of the wireframe. The implementation lands in its own scoped
						PR; the shell and dashboard are the first slice.
					</p>
				</div>
				<div className="page-actions">
					<Link className="btn" href="/">
						Back to dashboard
					</Link>
				</div>
			</header>

			<Panel title="Not implemented yet" sub="M1">
				<div className="note">
					<strong>Nothing is pretending to work here.</strong> The route exists so the shell
					can be navigated end to end. The wireframe for this screen lives in{" "}
					<code>design/wireframe/</code> and the work is tracked in the M1 issues.
				</div>
				<div className="row mt-12">
					<Link className="btn" href="/">
						Dashboard
					</Link>
					<Link className="btn" href="/states">
						State gallery
					</Link>
				</div>
			</Panel>
		</AppShell>
	);
}
