import Link from "next/link";
import { AppShell } from "@/components/shell";
import { AutomationsView } from "@/app/strategies/automations-view";
import { Badge } from "@/components/ui";
import { webMode } from "@/lib/paper-api";

export const dynamic = "force-dynamic";

// Automations are the scheduler view of the same strategies the engine stores. This route
// renders that view directly instead of forwarding, so the top-level Automations surface
// is a real page with its own URL state rather than a redirect.
export default async function AutomationsPage({
	searchParams,
}: {
	searchParams: Promise<{ state?: string }>;
}) {
	const { state } = await searchParams;
	const mode = webMode();

	return (
		<AppShell active="strategies">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Automations</div>
					<h1>Automations</h1>
					<p className="sub">
						Installed strategies are the automations this build schedules. Each row shows its
						persisted status, interval and last run timestamp from the API, with pause, resume
						and a single manual cycle. The trigger, conditions and actions are declared in the
						visual builder; a dry run simulates one decision and never places an order.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Link className="btn" href="/strategies">
						Strategies
					</Link>
					<Link className="btn" href="/strategies/builder">
						Open builder
					</Link>
				</div>
			</header>

			<AutomationsView demo={state} />
		</AppShell>
	);
}
