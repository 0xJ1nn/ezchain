import Link from "next/link";
import { AppShell } from "@/components/shell";
import { ModeBadge } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import { webMode } from "@/lib/runtime";
import { TradingForm } from "./trading-form";

export const dynamic = "force-dynamic";

export default function TradingSettingsPage() {
	const mode = webMode();

	return (
		<AppShell active="settings">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">System / Settings / Trading</div>
					<h1>Trading</h1>
					<p className="sub">
						The quick-buy amounts, slippage and priority fee shown on the trade ticket.
						These are choices in this browser: they shape the controls, never an amount, a
						quote or a risk decision.
					</p>
				</div>
				<div className="page-actions">
					<ModeBadge mode={mode} />
					<Link className="btn" href="/settings">
						Settings
					</Link>
				</div>
			</header>

			<div className="grid cols-2">
				<Panel
					title="Trade ticket"
					sub="stored in this browser"
					action={<Badge tone="ghost">local choice</Badge>}
					footer="The ticket reads these values after the page loads. Changing them never sends an order and never changes the quote returned by the API."
				>
					<TradingForm />
				</Panel>

				<Panel title="Where this applies" sub="honest boundary" className="mt-16">
					<p className="small muted">
						Quick-buy chips set the USD amount in the trade ticket. Slippage and priority
						fee are shown on the ticket and applied by a real venue after the live gate;
						paper fills use the mark and ignore both. The order gate keeps its own limits
						on the server, which are shown on the{" "}
						<Link href="/settings/trade-policy">trade policy page</Link>.
					</p>
					<p className="note mt-12">
						There is no settings write endpoint, so these preferences live in local browser
						storage. A new browser starts from the documented defaults.
					</p>
				</Panel>
			</div>
		</AppShell>
	);
}
