import Link from "next/link";
import { AppShell } from "@/components/shell";
import { Badge, Panel } from "@/components/ui";
import { getAllStrategyRuns, getStrategies, webMode } from "@/lib/paper-api";
import { StrategiesClient } from "./strategies-client";
import styles from "./strategies.module.css";

export const dynamic = "force-dynamic";

export default async function StrategiesPage() {
	const mode = webMode();
	const [strategies, runs] = await Promise.all([getStrategies(), getAllStrategyRuns()]);

	return (
		<AppShell active="strategies">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Automate / Strategies</div>
					<h1>Strategies</h1>
					<p className="sub">
						User-defined strategies are declarative configuration: a chain, a token, a
						kind and numeric params. There is no strategy code in ezCHAIN and nothing is
						bundled by default. Every decision is evaluated by the engine in{" "}
						<strong>paper</strong> mode and logged with its reason.
					</p>
				</div>
				<div className="page-actions">
					<Badge tone={mode === "fixture" ? "ghost" : "info"} title="Data source">
						{mode === "fixture" ? "FIXTURE" : "LIVE API"}
					</Badge>
					<Link className="btn" href="/backtests">
						Backtests
					</Link>
					<a className="btn primary" href="#new-strategy">
						New strategy
					</a>
				</div>
			</header>

			{!strategies.ok ? (
				<Panel
					className={styles.statePanel}
					title="Strategies unavailable"
					action={<Badge tone="err">{strategies.error.code}</Badge>}
				>
					<div className={styles.empty}>
						<strong>Nothing is being guessed.</strong>
						{strategies.error.message} The list, create form and run log stay hidden until
						the API answers. No demo strategy is ever inserted to fill the gap.
						<div className="row mt-12" style={{ justifyContent: "center" }}>
							<Link className="btn" href="/strategies">
								Retry
							</Link>
							{mode === "fixture" && (
								<span className="small muted">
									Fixture mode reads <span className="mono">lib/paper-api.ts</span>
								</span>
							)}
						</div>
					</div>
				</Panel>
			) : (
				<>
					{!runs.ok && (
						<div className={`${styles.notice} err mb-12`} role="alert">
							Run log unavailable ({runs.error.code}): {runs.error.message}
						</div>
					)}
					<StrategiesClient
						mode={mode}
						initialStrategies={strategies.data}
						initialRuns={runs.ok ? runs.data : []}
					/>
					<div className="annotation mt-16">
						<b>Paper only.</b> A strategy proposes intents; the order service and risk
						policy decide whether they become paper orders. The web never signs, routes or
						prices an order. Fixture mode uses sample decisions in this browser session;
						live mode calls <span className="mono">/api/v1/strategies</span>.
					</div>
				</>
			)}
		</AppShell>
	);
}
