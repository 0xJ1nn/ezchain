"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Badge, Panel } from "@/components/ui";
import {
	armLabel,
	armSummary,
	cancelProtectionArm,
	createProtectionArm,
	patchProtectionArm,
	readFixtureArms,
	writeFixtureArms,
	type PaperApiMode,
	type ProtectionArm,
	type ProtectionKind,
	type ProtectionProfile,
} from "@/lib/protection-api";
import type { PaperPosition } from "@/lib/paper-api";

interface Props {
	mode: PaperApiMode;
	initialArms: ProtectionArm[];
	initialProfiles: ProtectionProfile[];
	positions: PaperPosition[];
}

interface FormState {
	positionKey: string;
	kind: ProtectionKind;
	price: string;
	pct: string;
	profileId: string;
}

function emptyForm(positions: PaperPosition[]): FormState {
	return {
		positionKey: positions[0] ? `${positions[0].chain}:${positions[0].token_address}` : "",
		kind: "stop_loss",
		price: "",
		pct: "",
		profileId: "",
	};
}

export function ProtectionClient({ mode, initialArms, initialProfiles, positions }: Props) {
	const [arms, setArms] = useState<ProtectionArm[]>(initialArms);
	const [form, setForm] = useState<FormState>(() => emptyForm(positions));
	const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
	const [busy, setBusy] = useState(false);
	const [editing, setEditing] = useState<string | null>(null);
	const [editPrice, setEditPrice] = useState("");

	useEffect(() => {
		if (mode === "fixture") {
			const stored = readFixtureArms();
			if (stored.length > 0) setArms(stored);
		}
	}, [mode]);

	const selected = useMemo(
		() => positions.find((p) => `${p.chain}:${p.token_address}` === form.positionKey),
		[positions, form.positionKey],
	);
	const profile = initialProfiles.find((p) => p.id === form.profileId);

	function persist(next: ProtectionArm[]) {
		setArms(next);
		if (mode === "fixture") writeFixtureArms(next);
	}

	async function submit(event: FormEvent) {
		event.preventDefault();
		if (!selected) {
			setNotice({ tone: "err", text: "Choose an open position first." });
			return;
		}
		const params: Record<string, unknown> = {};
		if (form.kind === "trailing_stop") {
			if (form.pct.trim()) params.trailing_pct = form.pct.trim();
			else if (form.price.trim()) params.trailing_price_usd = form.price.trim();
			else params.trailing_pct = profile?.trailing_pct ?? "5";
		} else if (form.kind === "stop_loss") {
			if (form.price.trim()) params.price_usd = form.price.trim();
			else params.loss_pct = (form.pct.trim() || profile?.stop_loss_pct || "8").toString();
		} else {
			if (form.price.trim()) params.price_usd = form.price.trim();
			else params.gain_pct = (form.pct.trim() || profile?.take_profit_pct || "20").toString();
		}
		setBusy(true);
		try {
			const created = await createProtectionArm(
				{
					chain: selected.chain,
					token_address: selected.token_address,
					token_symbol: selected.symbol,
					kind: form.kind,
					params,
					profile_id: form.profileId || null,
				},
				mode,
			);
			persist([created, ...arms]);
			setNotice({ tone: "ok", text: `Armed ${armLabel(created.kind)} for ${selected.symbol}.` });
			setForm((current) => ({ ...current, price: "", pct: "" }));
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The arm could not be created.",
			});
		} finally {
			setBusy(false);
		}
	}

	async function saveEdit(arm: ProtectionArm) {
		if (!editPrice.trim()) return;
		try {
			const updated = await patchProtectionArm(
				arm.id,
				{
					params: { ...arm.params, price_usd: editPrice.trim() },
				},
				mode,
			);
			persist(arms.map((row) => (row.id === arm.id ? updated : row)));
			setEditing(null);
			setNotice({ tone: "ok", text: `Updated ${armLabel(updated.kind)} to ${editPrice.trim()}.` });
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The arm could not be updated.",
			});
		}
	}

	async function cancel(arm: ProtectionArm) {
		try {
			const updated = await cancelProtectionArm(arm.id, mode);
			persist(arms.map((row) => (row.id === arm.id ? updated : row)));
			setNotice({ tone: "ok", text: `Cancelled ${armLabel(updated.kind)} for ${updated.token_symbol}.` });
		} catch (error) {
			setNotice({
				tone: "err",
				text: error instanceof Error ? error.message : "The arm could not be cancelled.",
			});
		}
	}

	return (
		<Panel
			title="Position protection"
			sub="paper arms · one execution path · unknown stays —"
			action={<Badge tone="ghost">PAPER</Badge>}
			flush
			footer="Arms evaluate on engine ticks and close through the same order path as a manual sell. Profiles are reusable PAPER presets; smart rules are shown verbatim."
		>
			<div className="panel-body">
				{notice && (
					<div className={`note ${notice.tone === "ok" ? "ok" : "err"} mb-12`} role="status">
						{notice.text}
					</div>
				)}
				<form onSubmit={submit} className="stack-sm">
					<div className="between" style={{ flexWrap: "wrap", gap: 8 }}>
						<label>
							Position{" "}
							<select
								aria-label="Position"
								value={form.positionKey}
								onChange={(e) => setForm({ ...form, positionKey: e.target.value })}
							>
								{positions.map((position) => (
									<option
										key={`${position.chain}:${position.token_address}`}
										value={`${position.chain}:${position.token_address}`}
									>
										{position.symbol} ({position.chain})
									</option>
								))}
							</select>
						</label>
						<label>
							Kind{" "}
							<select
								aria-label="Kind"
								value={form.kind}
								onChange={(e) => setForm({ ...form, kind: e.target.value as ProtectionKind })}
							>
								<option value="stop_loss">stop-loss</option>
								<option value="trailing_stop">trailing stop</option>
								<option value="take_profit">take-profit</option>
							</select>
						</label>
						<label>
							Profile{" "}
							<select
								aria-label="Profile"
								value={form.profileId}
								onChange={(e) => setForm({ ...form, profileId: e.target.value })}
							>
								<option value="">—</option>
								{initialProfiles.map((row) => (
									<option key={row.id} value={row.id}>
										{row.name}
										{row.chain ? ` · ${row.chain}` : " · global"} · {row.kind}
									</option>
								))}
							</select>
						</label>
					</div>
					<div className="between" style={{ flexWrap: "wrap", gap: 8 }}>
						{form.kind === "trailing_stop" ? (
							<>
								<label>
									Trailing percent (%){" "}
									<input
										aria-label="Trailing percent (%)"
										value={form.pct}
										onChange={(e) => setForm({ ...form, pct: e.target.value })}
										placeholder="—"
									/>
								</label>
								<label>
									Trailing distance (USD){" "}
									<input
										aria-label="Trailing distance (USD)"
										value={form.price}
										onChange={(e) => setForm({ ...form, price: e.target.value })}
										placeholder="—"
									/>
								</label>
							</>
						) : (
							<>
								<label>
									Trigger price (USD){" "}
									<input
										aria-label="Trigger price (USD)"
										value={form.price}
										onChange={(e) => setForm({ ...form, price: e.target.value })}
										placeholder="—"
									/>
								</label>
								<label>
									{form.kind === "stop_loss" ? "Loss (%)" : "Gain (%)"}{" "}
									<input
										aria-label={form.kind === "stop_loss" ? "Loss (%)" : "Gain (%)"}
										value={form.pct}
										onChange={(e) => setForm({ ...form, pct: e.target.value })}
										placeholder="—"
									/>
								</label>
							</>
						)}
						<button className="btn primary" type="submit" disabled={busy}>
							Arm protection
						</button>
					</div>
					{profile && profile.rules.length > 0 && (
						<div className="note mb-12">
							<strong>{profile.name} rules.</strong>{" "}
							{profile.rules.join(" ")}
						</div>
					)}
					{profile && profile.rules.length === 0 && (
						<div className="note mb-12">
							{profile.name}: stop-loss {profile.stop_loss_pct ?? "—"}% · trailing{" "}
							{profile.trailing_pct ?? "—"}% · take-profit {profile.take_profit_pct ?? "—"}%.
						</div>
					)}
				</form>
			</div>
			<div className="table-wrap">
				<table className="table">
					<thead>
						<tr>
							<th>Position</th>
							<th>Kind</th>
							<th>Trigger</th>
							<th>Status</th>
							<th>Peak</th>
							<th />
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
								<td>
									{editing === arm.id ? (
										<label>
											Trigger price (USD){" "}
											<input
												aria-label="Trigger price (USD)"
												value={editPrice}
												onChange={(e) => setEditPrice(e.target.value)}
											/>
										</label>
									) : (
										armSummary(arm)
									)}
								</td>
								<td>
									<Badge tone={arm.status === "armed" ? "warn" : "ghost"}>{arm.status}</Badge>
								</td>
								<td>{arm.peak_usd ?? "—"}</td>
								<td className="right">
									{editing === arm.id ? (
										<>
											<button className="btn sm primary" type="button" onClick={() => saveEdit(arm)}>
												Save
											</button>{" "}
											<button
												className="btn sm"
												type="button"
												onClick={() => setEditing(null)}
											>
												Keep
											</button>
										</>
									) : arm.status === "armed" ? (
										<>
											<button
												className="btn sm"
												type="button"
												onClick={() => {
													setEditing(arm.id);
													const price =
														typeof (arm.params as Record<string, unknown>).price_usd === "string"
															? ((arm.params as Record<string, unknown>).price_usd as string)
															: "";
													setEditPrice(price);
												}}
											>
												Edit
											</button>{" "}
											<button className="btn sm" type="button" onClick={() => cancel(arm)}>
												Cancel
											</button>
										</>
									) : null}
								</td>
							</tr>
						))}
						{arms.length === 0 && (
							<tr>
								<td colSpan={6} className="muted">
									No protection arms yet. An unknown trigger stays —, never 0.
								</td>
							</tr>
						)}
					</tbody>
				</table>
			</div>
		</Panel>
	);
}
