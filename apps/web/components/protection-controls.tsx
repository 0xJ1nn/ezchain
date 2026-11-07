"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { Badge } from "@/components/ui";
import { PaperApiError, type PaperApiMode } from "@/lib/paper-api";
import {
	armProtection,
	disarmProtection,
	kindLabel,
	patchProtection,
	paramsSummary,
	protectionStatusMeta,
	trailingDetail,
	type Protection,
	type ProtectionKind,
	type ProtectionProfile,
} from "@/lib/protection-api";
import {
	markProtectionRemoved,
	mergeProtections,
	readProtectionDelta,
	saveProtectionArm,
} from "@/lib/protection-session";

interface Props {
	mode: PaperApiMode;
	chain: string;
	tokenAddress: string;
	/** Arms the server already returned for this position. */
	arms: Protection[];
	profiles: ProtectionProfile[];
}

type FormTarget = { mode: "arm" } | { mode: "edit"; arm: Protection };

interface FormState {
	kind: ProtectionKind;
	loss_pct: string;
	stop_price_usd: string;
	sell_fraction: string;
	activation_pct: string;
	giveback_pct: string;
	giveback_price_usd: string;
	profit_pct: string;
	ladder: string;
	profile_id: string;
}

function defaultProfile(profiles: ProtectionProfile[], chain: string): string {
	const chainProfile = profiles.find((profile) => profile.chain === chain);
	if (chainProfile) return chainProfile.id;
	return profiles.find((profile) => profile.chain === null)?.id ?? "";
}

function emptyForm(profiles: ProtectionProfile[], chain: string): FormState {
	return {
		kind: "stop_loss",
		loss_pct: "",
		stop_price_usd: "",
		sell_fraction: "",
		activation_pct: "",
		giveback_pct: "",
		giveback_price_usd: "",
		profit_pct: "",
		ladder: "",
		profile_id: defaultProfile(profiles, chain),
	};
}

function formFromArm(arm: Protection, profiles: ProtectionProfile[], chain: string): FormState {
	const number_ = (key: string) => {
		const value = arm.params[key];
		return value === undefined || value === null ? "" : String(value);
	};
	const ladder = arm.params.ladder;
	let ladderText = "";
	if (Array.isArray(ladder)) {
		ladderText = ladder
			.map((rung) =>
				typeof rung === "object" && rung !== null
					? `${String((rung as Record<string, unknown>).profit_pct)}:${String(
							(rung as Record<string, unknown>).fraction,
						)}`
					: "",
			)
			.filter(Boolean)
			.join(", ");
	}
	return {
		kind: arm.kind as ProtectionKind,
		loss_pct: number_("loss_pct"),
		stop_price_usd: number_("stop_price_usd"),
		sell_fraction: number_("sell_fraction"),
		activation_pct: number_("activation_pct"),
		giveback_pct: number_("giveback_pct"),
		giveback_price_usd: number_("giveback_price_usd"),
		profit_pct: number_("profit_pct"),
		ladder: ladderText,
		profile_id: arm.profile_id ?? defaultProfile(profiles, chain),
	};
}

/**
 * Params are validated against the same rules the core enforces, then the API is the
 * authority again. Client checks only prevent a request that cannot succeed.
 */
function buildParams(
	form: FormState,
): { params: Record<string, unknown> } | { error: string } {
	const params: Record<string, unknown> = {};
	// Returns an error message, or null when the field was empty or parsed fine.
	const numericField = (key: keyof FormState, label: string): string | null => {
		const raw = form[key].trim();
		if (!raw) return null;
		if (!Number.isFinite(Number(raw))) return `${label} must be a number.`;
		params[key] = raw;
		return null;
	};

	let check = numericField("loss_pct", "Drop from avg cost") ?? numericField("stop_price_usd", "Stop price");
	if (check) return { error: check };
	if (form.kind === "stop_loss") {
		if (params.loss_pct === undefined && params.stop_price_usd === undefined) {
			return { error: "A stop loss needs a percent drop or a stop price." };
		}
	}

	check = numericField("activation_pct", "Arm above avg cost");
	if (check) return { error: check };
	if (form.kind === "trailing_stop" && params.activation_pct === undefined) {
		return { error: "A trailing stop needs the percent above cost that arms it." };
	}
	check = numericField("giveback_pct", "Give back from peak") ?? numericField("giveback_price_usd", "Give back in USD");
	if (check) return { error: check };

	check = numericField("profit_pct", "Profit");
	if (check) return { error: check };
	const rawLadder = form.ladder.trim();
	if (form.kind === "take_profit" && !rawLadder && params.profit_pct === undefined) {
		return { error: "A take profit needs a percent or a ladder." };
	}
	if (rawLadder) {
		const rungs: Array<{ profit_pct: number; fraction: number }> = [];
		for (const part of rawLadder.split(",")) {
			const pair = part.trim().split(":");
			if (pair.length !== 2) {
				return { error: "Ladder rungs look like 25:0.5, 60:0.5." };
			}
			const profitPct = Number(pair[0]);
			const fraction = Number(pair[1]);
			if (!Number.isFinite(profitPct) || !Number.isFinite(fraction)) {
				return { error: "Ladder rungs look like 25:0.5, 60:0.5." };
			}
			rungs.push({ profit_pct: profitPct, fraction });
		}
		if (rungs.length === 0) return { error: "A ladder needs at least one rung." };
		params.ladder = rungs;
	}

	check = numericField("sell_fraction", "Sell fraction");
	if (check) return { error: check };
	return { params };
}

export function ProtectionControls({ mode, chain, tokenAddress, arms, profiles }: Props) {
	const [list, setList] = useState<Protection[]>(arms);
	const [target, setTarget] = useState<FormTarget | null>(null);
	const [form, setForm] = useState<FormState>(() => emptyForm(profiles, chain));
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const rawId = useId();
	const id = (field: string) => `prot-${field}-${rawId.replace(/[^a-zA-Z0-9-]/g, "")}`;

	// Fixture session arms merge in after hydration; a live build only ever shows what
	// the API returned.
	useEffect(() => {
		if (mode !== "fixture") return;
		setList(
			mergeProtections(arms, readProtectionDelta()).filter(
				(arm) => arm.chain === chain && arm.token_address === tokenAddress,
			),
		);
	}, [mode, arms, chain, tokenAddress]);

	const chainProfiles = profiles.filter((profile) => profile.chain === chain);
	const globalProfiles = profiles.filter((profile) => profile.chain === null);
	const profileName = (id_: string | null) =>
		id_ ? (profiles.find((profile) => profile.id === id_)?.name ?? null) : null;

	function openArm() {
		setForm(emptyForm(profiles, chain));
		setError(null);
		setTarget({ mode: "arm" });
	}

	function startEdit(arm: Protection) {
		setForm(formFromArm(arm, profiles, chain));
		setError(null);
		setTarget({ mode: "edit", arm });
	}

	function closeForm() {
		setTarget(null);
		setError(null);
	}

	function update(field: keyof FormState, value: string) {
		setForm((current) => ({ ...current, [field]: value }));
	}

	async function submit(event: FormEvent<HTMLFormElement>) {
		event.preventDefault();
		if (busy || !target) return;
		const built = buildParams(form);
		if ("error" in built) {
			setError(built.error);
			return;
		}
		setBusy(true);
		setError(null);
		const kind = form.kind;
		const profileId = form.profile_id || null;
		try {
			if (mode === "fixture") {
				const now = new Date().toISOString();
				if (target.mode === "arm") {
					const profile = profiles.find((row) => row.id === profileId);
					const arm: Protection = {
						id: `prot-session-${Date.now().toString(36)}-${Math.random()
							.toString(36)
							.slice(2, 6)}`,
						chain,
						token_address: tokenAddress,
						kind,
						params: built.params,
						source: profile?.profile_type === "smart" ? "smart" : "manual",
						status: "armed",
						version: 1,
						profile_id: profileId,
						trailing_activated: false,
						trailing_peak_usd: null,
						trailing_rung_index: 0,
						created_at: now,
						updated_at: now,
					};
					saveProtectionArm(arm);
					setList((current) => [arm, ...current]);
				} else {
					const updated: Protection = {
						...target.arm,
						params: built.params,
						profile_id: profileId,
						version: target.arm.version + 1,
						updated_at: now,
					};
					saveProtectionArm(updated);
					setList((current) =>
						current.map((row) => (row.id === updated.id ? updated : row)),
					);
				}
			} else if (target.mode === "arm") {
				const arm = await armProtection({
					chain,
					token_address: tokenAddress,
					kind,
					params: built.params,
					idempotency_key: `web-manual-${chain}-${kind}-${Date.now().toString(36)}-${Math.random()
						.toString(36)
						.slice(2, 8)}`,
					profile_id: profileId ?? undefined,
				});
				setList((current) => [arm, ...current]);
			} else {
				const arm = await patchProtection(target.arm.id, {
					params: built.params,
					expected_version: target.arm.version,
					profile_id: profileId ?? undefined,
				});
				setList((current) =>
					current.map((row) => (row.id === arm.id ? arm : row)),
				);
			}
			closeForm();
		} catch (caught) {
			const apiError = caught instanceof PaperApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: "The protection arm could not be saved.",
			);
		} finally {
			setBusy(false);
		}
	}

	async function remove(arm: Protection) {
		if (busy) return;
		setBusy(true);
		setError(null);
		try {
			if (mode === "fixture") {
				// A disarm is a state change: the arm stays listed as cancelled.
				markProtectionRemoved(arm.id);
				setList((current) =>
					current.map((row) =>
						row.id === arm.id
							? { ...row, status: "cancelled", updated_at: new Date().toISOString() }
							: row,
					),
				);
			} else {
				const cancelled = await disarmProtection(arm.id);
				setList((current) =>
					current.map((row) => (row.id === cancelled.id ? cancelled : row)),
				);
			}
		} catch (caught) {
			const apiError = caught instanceof PaperApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: "The protection arm could not be removed.",
			);
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="protection-cell">
			{list.length === 0 && target === null && (
				<button type="button" className="btn sm" onClick={openArm}>
					Protect
				</button>
			)}
			<div className="stack-sm">
				{list.map((arm) => {
					const status = protectionStatusMeta(arm.status);
					const detail = trailingDetail(arm);
					const name = profileName(arm.profile_id);
					return (
						<div className="protection-arm" key={arm.id}>
							<div className="row">
								<Badge tone={status.tone}>{status.label}</Badge>
								<span className="strong small">{kindLabel(arm.kind)}</span>
							</div>
							<div className="sub">{paramsSummary(arm.kind, arm.params)}</div>
							{detail && <div className="sub">{detail}</div>}
							<div className="row small muted">
								<span className="mono">v{arm.version}</span>
								{name && <span>{name}</span>}
							</div>
							{arm.status === "armed" && (
								<div className="row">
									<button
										type="button"
										className="btn sm"
										onClick={() => startEdit(arm)}
										disabled={busy}
									>
										Edit
									</button>
									<button
										type="button"
										className="btn sm"
										onClick={() => remove(arm)}
										disabled={busy}
									>
										Remove
									</button>
								</div>
							)}
						</div>
					);
				})}
			</div>
			{target !== null && (
				<form className="stack-sm" onSubmit={submit} style={{ minWidth: 230 }}>
					<div className="field">
						<label htmlFor={id("kind")}>Kind</label>
						<select
							id={id("kind")}
							className="select"
							value={form.kind}
							onChange={(event) => update("kind", event.target.value as ProtectionKind)}
							disabled={target.mode === "edit"}
						>
							<option value="stop_loss">Stop loss</option>
							<option value="trailing_stop">Trailing stop</option>
							<option value="take_profit">Take profit</option>
						</select>
					</div>
					{form.kind === "stop_loss" && (
						<>
							<div className="field">
								<label htmlFor={id("loss_pct")}>Drop from avg cost (%)</label>
								<input
									id={id("loss_pct")}
									className="input mono"
									value={form.loss_pct}
									onChange={(event) => update("loss_pct", event.target.value)}
									inputMode="decimal"
								/>
							</div>
							<div className="field">
								<label htmlFor={id("stop_price_usd")}>Stop price (USD)</label>
								<input
									id={id("stop_price_usd")}
									className="input mono"
									value={form.stop_price_usd}
									onChange={(event) => update("stop_price_usd", event.target.value)}
									inputMode="decimal"
								/>
							</div>
						</>
					)}
					{form.kind === "trailing_stop" && (
						<>
							<div className="field">
								<label htmlFor={id("activation_pct")}>Arm above avg cost (+%)</label>
								<input
									id={id("activation_pct")}
									className="input mono"
									value={form.activation_pct}
									onChange={(event) => update("activation_pct", event.target.value)}
									inputMode="decimal"
								/>
							</div>
							<div className="field">
								<label htmlFor={id("giveback_pct")}>Give back from peak (%)</label>
								<input
									id={id("giveback_pct")}
									className="input mono"
									value={form.giveback_pct}
									onChange={(event) => update("giveback_pct", event.target.value)}
									inputMode="decimal"
								/>
							</div>
							<div className="field">
								<label htmlFor={id("giveback_price_usd")}>Give back from peak ($)</label>
								<input
									id={id("giveback_price_usd")}
									className="input mono"
									value={form.giveback_price_usd}
									onChange={(event) => update("giveback_price_usd", event.target.value)}
									inputMode="decimal"
								/>
							</div>
						</>
					)}
					{form.kind === "take_profit" && (
						<>
							<div className="field">
								<label htmlFor={id("profit_pct")}>Profit above avg cost (%)</label>
								<input
									id={id("profit_pct")}
									className="input mono"
									value={form.profit_pct}
									onChange={(event) => update("profit_pct", event.target.value)}
									inputMode="decimal"
								/>
							</div>
							<div className="field">
								<label htmlFor={id("ladder")}>Ladder</label>
								<input
									id={id("ladder")}
									className="input mono"
									value={form.ladder}
									onChange={(event) => update("ladder", event.target.value)}
									placeholder="25:0.5, 60:0.5"
								/>
								<span className="hint">Rungs as profit%:fraction, first hit sells first.</span>
							</div>
						</>
					)}
					{form.kind !== "trailing_stop" && (
						<div className="field">
							<label htmlFor={id("sell_fraction")}>Sell fraction</label>
							<input
								id={id("sell_fraction")}
								className="input mono"
								value={form.sell_fraction}
								onChange={(event) => update("sell_fraction", event.target.value)}
								placeholder="1"
								inputMode="decimal"
							/>
							<span className="hint">1 = the whole position.</span>
						</div>
					)}
					<div className="field">
						<label htmlFor={id("profile")}>Profile</label>
						<select
							id={id("profile")}
							className="select"
							value={form.profile_id}
							onChange={(event) => update("profile_id", event.target.value)}
						>
							{chainProfiles.map((profile) => (
								<option key={profile.id} value={profile.id}>
									{profile.name} · {profile.chain}
								</option>
							))}
							{globalProfiles.map((profile) => (
								<option key={profile.id} value={profile.id}>
									{profile.name} · global
								</option>
							))}
							<option value="">No profile</option>
						</select>
						<span className="hint">A per-chain profile overrides the global default.</span>
					</div>
					{error && <div className="note err small">{error}</div>}
					<div className="row">
						<button type="submit" className="btn sm primary" disabled={busy}>
							{target.mode === "arm" ? "Arm protection" : "Save changes"}
						</button>
						<button type="button" className="btn sm" onClick={closeForm} disabled={busy}>
							Cancel
						</button>
					</div>
					<span className="hint">
						{mode === "fixture"
							? "Fixture session: the arm is kept in this browser session only."
							: `Armed against the paper account on ${chain}. Unknown marks fail closed.`}
					</span>
				</form>
			)}
			{error && target === null && <div className="note err small">{error}</div>}
			{list.length > 0 && target === null && (
				<button type="button" className="btn sm" onClick={openArm} disabled={busy}>
					Protect
				</button>
			)}
			{mode === "fixture" && list.length === 0 && (
				<span className="hint">No arm yet. Paper mode only.</span>
			)}
		</div>
	);
}
