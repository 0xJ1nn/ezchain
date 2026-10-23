import { AppShell } from "@/components/shell";
import { FixtureBadge } from "@/components/ops";
import { Badge, Panel } from "@/components/ui";
import { PositionTile } from "@/components/position-tile";
import { positionTiles } from "@/lib/position-fixtures";

const codeStates = [
	{ state: "Entering (intent, not fill)", rendered: true },
	{ state: "Pre-bank", rendered: true },
	{ state: "Riding", rendered: true },
	{ state: "Unsellable · no sell route", rendered: true },
	{ state: "Trail armed", rendered: false },
	{ state: "Banked", rendered: false },
	{ state: "Laddered", rendered: false },
	{ state: "Dust", rendered: false },
	{ state: "Stale mark", rendered: false },
	{ state: "Closed", rendered: false },
	{ state: "Quarantined", rendered: false },
];

export default function StatesPage() {
	return (
		<AppShell active="states">
			<header className="page-head">
				<div className="titles">
					<div className="crumbs">Reference / State gallery</div>
					<h1>State gallery</h1>
					<p className="sub">
						The four position-tile states the shared <span className="mono">PositionTile</span>{" "}
						component currently renders. Every action on a tile is disabled: execution is not
						wired in this build. Values are sample server view-model strings.
					</p>
				</div>
				<div className="page-actions">
					<FixtureBadge />
					<a className="btn" href="https://github.com/0xJ1nn/ezchain/blob/main/design/wireframe/27-states.html" rel="noreferrer">
						Wireframe
					</a>
				</div>
			</header>

			<div className="note mb-12">
				<strong>One model, one tile.</strong> The tile renders a fixture <span className="mono">PositionView</span>{" "}
				exactly as the server would return it. It never computes PnL, executable value or a
				mark. Disabled actions are labelled as such rather than pretending to trade.
			</div>

			<section className="grid cols-2 tiles" aria-label="Position tile states">
				{positionTiles.map((position) => (
					<PositionTile position={position} key={position.id} />
				))}
			</section>

			<div className="split mt-16">
				<Panel title="State coverage" sub="shared tile component" flush>
					<div className="table-wrap">
						<table className="table compact">
							<thead>
								<tr>
									<th>State</th>
									<th>Rendered by PositionTile</th>
								</tr>
							</thead>
							<tbody>
								{codeStates.map((state) => (
									<tr key={state.state}>
										<td>{state.state}</td>
										<td>
											<Badge tone={state.rendered ? "ok" : "warn"}>
												{state.rendered ? "yes" : "not yet"}
											</Badge>
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</Panel>

				<Panel title="What each state means" sub="operator reading">
					<div className="list">
						<div className="list-row">
							<Badge tone="info">ENTERING</Badge>
							<span className="grow">
								<span className="title">Intent placed, no fill yet</span>
								<span className="sub">The tile shows the intent and its last known quote.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ghost">PRE-BANK</Badge>
							<span className="grow">
								<span className="title">Position open, first bank not reached</span>
								<span className="sub">No partial profit has been taken.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="ok">RIDING</Badge>
							<span className="grow">
								<span className="title">Banked and trailing</span>
								<span className="sub">Part of the bag is sold; the remainder follows a trail.</span>
							</span>
						</div>
						<div className="list-row">
							<Badge tone="err">UNSELLABLE</Badge>
							<span className="grow">
								<span className="title">No sell route</span>
								<span className="sub">
									The mark is not executable. The tile never prints the mark as a sell value.
								</span>
							</span>
						</div>
					</div>
				</Panel>
			</div>

			<Panel title="Other wireframe states" sub="outside this slice" className="mt-16">
				<p className="small muted">
					Loading, empty, stale market, quote-expired, risk-denied, paper-gate, quarantined,
					wallet-unsynced, offline and permission-denied states are not all built as reusable
					components. Where a real page needs one today, that page renders a truthful inline
					state with the API reason; this gallery does not stage those as fake tiles.
				</p>
			</Panel>
		</AppShell>
	);
}
