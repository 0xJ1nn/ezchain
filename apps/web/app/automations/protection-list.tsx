"use client";

import { useEffect, useState } from "react";
import { Badge, Panel } from "@/components/ui";
import {
	armLabel,
	armSummary,
	readFixtureArms,
	type PaperApiMode,
	type ProtectionArm,
} from "@/lib/protection-api";

export function ProtectionList({
	mode,
	initialArms,
}: {
	mode: PaperApiMode;
	initialArms: ProtectionArm[];
}) {
	const [arms, setArms] = useState<ProtectionArm[]>(initialArms);

	useEffect(() => {
		if (mode === "fixture") {
			const stored = readFixtureArms();
			if (stored.length > 0) {
				const ids = new Set(stored.map((arm) => arm.id));
				setArms([...stored, ...initialArms.filter((arm) => !ids.has(arm.id))]);
			}
		}
	}, [mode, initialArms]);

	const armed = arms.filter((arm) => arm.status === "armed");

	return (
		<Panel
			title="Armed protection"
			sub={`${armed.length} armed · PAPER only`}
			action={<Badge tone="ghost">PAPER</Badge>}
			flush
			footer="Each arm evaluates on engine ticks and closes through the same order path as a manual sell. An unknown trigger stays —, never 0."
		>
			{arms.length > 0 ? (
				<div className="table-wrap" tabIndex={0} role="region" aria-label="Armed protection table">
					<table className="table">
						<caption className="sr-only">Armed protection</caption>
						<thead>
							<tr>
								<th scope="col">Position</th>
								<th scope="col">Kind</th>
								<th scope="col">Trigger</th>
								<th scope="col">Status</th>
								<th scope="col">Peak</th>
							</tr>
						</thead>
						<tbody>
							{arms.map((arm) => (
								<tr key={arm.id}>
									<td>
										<span className="strong">{arm.token_symbol}</span>
										<span className="sub" style={{ display: "block" }}>
											{arm.chain}
										</span>
									</td>
									<td>{armLabel(arm.kind)}</td>
									<td>{armSummary(arm)}</td>
									<td>
										<Badge tone={arm.status === "armed" ? "warn" : "ghost"}>{arm.status}</Badge>
									</td>
									<td>{arm.peak_usd ?? "—"}</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			) : (
				<div className="panel-body">
					<p className="muted">No protection arms. Arm one from a position to list it here.</p>
				</div>
			)}
		</Panel>
	);
}
