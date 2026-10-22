import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge } from "@/components/ui";
import { webMode } from "@/lib/paper-api";
import { GraphBuilder } from "./graph-builder";

export const dynamic = "force-dynamic";

export default function NewGraphStrategyPage() {
	const mode = webMode();

	return (
		<AppShell active="strategies">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">
						<Link href="/strategies">Strategies</Link> / Builder
					</div>
					<h1>Visual strategy builder</h1>
					<p className="sub">
						Draw the rule set as a typed graph. The document is declarative JSON
						(<span className="mono">ezchain.graph/1</span>) — no code, no eval, no plugin
						loading. Validate with <kbd>F7</kbd>, try a dry run, then save; the engine owns
						evaluation and everything still runs in <strong>paper</strong> mode.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Badge tone="ghost">PAPER</Badge>
					<Link className="btn" href="/strategies">
						All strategies
					</Link>
				</div>
			</header>

			<GraphBuilder mode={mode} />
		</AppShell>
	);
}
