"use client";

import { useEffect, useState } from "react";
import { Badge, Panel } from "@/components/ui";
import type { PaperApiMode } from "@/lib/paper-api";
import {
	kindLabel,
	paramsSummary,
	protectionStatusMeta,
	trailingDetail,
	type Protection,
	type ProtectionProfile,
} from "@/lib/protection-api";
import { mergeProtections, readProtectionDelta } from "@/lib/protection-session";

interface Props {
	mode: PaperApiMode;
	arms: Protection[];
	profiles: ProtectionProfile[];
	/** chain:token_address -> symbol, resolved from the paper positions read model. */
	symbols: Record<string, string>;
	error: { code: string; message: string } | null;
}

function stamp(iso: string | null) {
	// An unknown timestamp prints —; it is never rendered as a zero or a fake date.
	if (!iso) return "—";
	return `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

/**
 * Every protection arm the API returns, in one list. States are shown as persisted:
 * armed, triggered, cancelled, EXPIRED. Fixture-session arms merge in on the client so
 * an arm created on the positions page is visible here after a full page load.
 */
export function ProtectionArmsPanel({ mode, arms, profiles, symbols, error }: Props) {
	const [list, setList] = useState<Protection[]>(arms);

	useEffect(() => {
		if (mode !== "fixture") return;
		setList(mergeProtections(arms, readProtectionDelta()));
	}, [mode, arms]);

	const armed = list.filter((arm) => arm.status === "armed").length;
	const profileName = (id: string | null) =>
		id ? (profiles.find((profile) => profile.id === id)?.name ?? null) : null;
	const symbolOf = (arm: Protection) => symbols[`${arm.chain}:${arm.token_address}`] ?? "—";

	return (
		<Panel
			title="Position protection arms"
			sub={error ? "unavailable" : `${list.length} recorded · ${armed} armed`}
			action={<Badge tone="ghost">PAPER</Badge>}
			flush
			footer="States are the statuses the API persisted: armed, triggered, cancelled, expired. An unknown status prints —, never a guessed label or a zero. Arms are evaluated by the engine on every tick; an unknown mark fails closed."
		>
			{error ? (
				<div className="panel-body">
					<div className="note err">
						{error.code}: {error.message}
					</div>
					<p className="small muted">
						No arm is invented while the list cannot be read. The counts above stay —
						rather than zero.
					</p>
				</div>
			) : list.length === 0 ? (
				<div className="panel-body">
					<p className="small muted">
						No protection arm is recorded. Arm one from a position row on the positions
						page.
					</p>
				</div>
			) : (
				<div className="table-wrap">
					<table className="table">
						<thead>
							<tr>
								<th scope="col">Token</th>
								<th scope="col">Chain</th>
								<th scope="col">Kind</th>
								<th scope="col">Parameters</th>
								<th scope="col">Profile</th>
								<th scope="col">Status</th>
								<th scope="col" className="num">
									Version
								</th>
								<th scope="col">Updated</th>
							</tr>
						</thead>
						<tbody>
							{list.map((arm) => {
								const status = protectionStatusMeta(arm.status);
								const detail = trailingDetail(arm);
								const name = profileName(arm.profile_id);
								return (
									<tr key={arm.id}>
										<td>
											<span className="strong">{symbolOf(arm)}</span>
											<span className="sub" style={{ display: "block" }} title={arm.token_address}>
												{arm.token_address.slice(0, 6)}…{arm.token_address.slice(-4)}
											</span>
										</td>
										<td>{arm.chain}</td>
										<td className="small">{kindLabel(arm.kind)}</td>
										<td className="small">
											{paramsSummary(arm.kind, arm.params)}
											{detail && (
												<span className="sub" style={{ display: "block" }}>
													{detail}
												</span>
											)}
										</td>
										<td className="small">{name ?? "—"}</td>
										<td>
											<Badge tone={status.tone}>{status.label}</Badge>
										</td>
										<td className="num mono small">v{arm.version}</td>
										<td className="small mono">{stamp(arm.updated_at)}</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			)}
		</Panel>
	);
}
