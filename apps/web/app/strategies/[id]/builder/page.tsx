import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge } from "@/components/ui";
import { webMode } from "@/lib/paper-api";
import { GraphBuilder } from "../../builder/graph-builder";

export const dynamic = "force-dynamic";

export default async function EditGraphStrategyPage({
	params,
}: {
	params: Promise<{ id: string }>;
}) {
	const { id } = await params;
	const mode = webMode();

	return (
		<AppShell active="strategies">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/strategies">Strategies</Link> /{" "}
						<Link href={`/strategies/${id}`}>Detail</Link> / Builder
					</div>
					<h1>Edit graph strategy</h1>
					<p className="sub">
						This loads <span className="mono">params.graph</span> from the strategy. Saving
						patches the same row; the run log keeps every decision it already made.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Badge tone="ghost">PAPER</Badge>
					<Link className="btn" href={`/strategies/${id}`}>
						Strategy detail
					</Link>
				</div>
			</header>

			<GraphBuilder mode={mode} strategyId={id} />
		</AppShell>
	);
}
