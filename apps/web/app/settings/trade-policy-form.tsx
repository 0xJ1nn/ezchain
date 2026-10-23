"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, Panel } from "@/components/ui";
import { formatUsd, formatUsdPrice } from "@/lib/live-format";
import {
	isTradePolicyError,
	previewTradeSize,
	saveTradePolicy,
	type RiskProfile,
	type TradePolicyInput,
	type TradePolicyView,
	type TradeSizeLimits,
	type TradeSizePreview,
} from "@/lib/trade-policy";

type LimitsDraft = {
	min_amount_usd: string;
	min_pct_equity: string;
	max_amount_usd: string;
	max_pct_equity: string;
};

type Draft = {
	default_profile: RiskProfile;
	envelope: LimitsDraft;
	profiles: Record<RiskProfile, LimitsDraft>;
};

const PROFILE_LABELS: Record<RiskProfile, string> = {
	low: "Low · conservative",
	mid: "Mid · default",
	high: "High · aggressive",
};

const DECIMAL = /^\d+(\.\d+)?$/;

/** Exact non-negative decimal comparison; policy bounds never pass through a float. */
function compareDecimal(left: string, right: string): number {
	const [leftInt, leftFrac = ""] = left.split(".");
	const [rightInt, rightFrac = ""] = right.split(".");
	const lint = leftInt.replace(/^0+(?=\d)/, "");
	const rint = rightInt.replace(/^0+(?=\d)/, "");
	if (lint.length !== rint.length) return lint.length < rint.length ? -1 : 1;
	if (lint !== rint) return lint < rint ? -1 : 1;
	const width = Math.max(leftFrac.length, rightFrac.length);
	const lfrac = leftFrac.padEnd(width, "0");
	const rfrac = rightFrac.padEnd(width, "0");
	return lfrac === rfrac ? 0 : lfrac < rfrac ? -1 : 1;
}

function limitsDraft(limits: TradeSizeLimits): LimitsDraft {
	return {
		min_amount_usd: limits.min_amount_usd,
		min_pct_equity: limits.min_pct_equity,
		max_amount_usd: limits.max_amount_usd ?? "",
		max_pct_equity: limits.max_pct_equity ?? "",
	};
}

function draftFromPolicy(policy: TradePolicyView): Draft {
	return {
		default_profile: policy.default_profile,
		envelope: {
			min_amount_usd: policy.min_amount_usd,
			min_pct_equity: policy.min_pct_equity,
			max_amount_usd: policy.max_amount_usd ?? "",
			max_pct_equity: policy.max_pct_equity ?? "",
		},
		profiles: {
			low: limitsDraft(policy.profiles.low),
			mid: limitsDraft(policy.profiles.mid),
			high: limitsDraft(policy.profiles.high),
		},
	};
}

function limitsInput(limits: LimitsDraft): TradeSizeLimits {
	return {
		min_amount_usd: limits.min_amount_usd.trim(),
		min_pct_equity: limits.min_pct_equity.trim(),
		max_amount_usd: limits.max_amount_usd.trim() === "" ? null : limits.max_amount_usd.trim(),
		max_pct_equity: limits.max_pct_equity.trim() === "" ? null : limits.max_pct_equity.trim(),
	};
}

function policyInput(draft: Draft): TradePolicyInput {
	return {
		default_profile: draft.default_profile,
		...limitsInput(draft.envelope),
		profiles: {
			low: limitsInput(draft.profiles.low),
			mid: limitsInput(draft.profiles.mid),
			high: limitsInput(draft.profiles.high),
		},
	};
}

function validateLimits(prefix: string, limits: LimitsDraft): Record<string, string> {
	const errors: Record<string, string> = {};
	const minAmount = limits.min_amount_usd.trim();
	const minPct = limits.min_pct_equity.trim();
	const maxAmount = limits.max_amount_usd.trim();
	const maxPct = limits.max_pct_equity.trim();

	if (!DECIMAL.test(minAmount)) errors[`${prefix}.min_amount_usd`] = "Enter 0 or more.";
	if (!DECIMAL.test(minPct)) errors[`${prefix}.min_pct_equity`] = "Enter a percentage.";
	if (maxAmount !== "" && !DECIMAL.test(maxAmount)) {
		errors[`${prefix}.max_amount_usd`] = "Enter 0 or more, or leave blank.";
	}
	if (maxPct !== "" && !DECIMAL.test(maxPct)) {
		errors[`${prefix}.max_pct_equity`] = "Enter a percentage, or leave blank.";
	}
	if (DECIMAL.test(minPct) && compareDecimal(minPct, "100") > 0) {
		errors[`${prefix}.min_pct_equity`] = "Must be between 0 and 100%.";
	}
	if (maxPct !== "" && DECIMAL.test(maxPct) && compareDecimal(maxPct, "100") > 0) {
		errors[`${prefix}.max_pct_equity`] = "Must be between 0 and 100%.";
	}
	if (DECIMAL.test(minAmount) && maxAmount !== "" && DECIMAL.test(maxAmount)) {
		if (compareDecimal(minAmount, maxAmount) > 0) {
			errors[`${prefix}.max_amount_usd`] = "Max amount must be at least the minimum.";
		}
	}
	if (DECIMAL.test(minPct) && maxPct !== "" && DECIMAL.test(maxPct)) {
		if (compareDecimal(minPct, maxPct) > 0) {
			errors[`${prefix}.max_pct_equity`] = "Max % must be at least the minimum.";
		}
	}
	return errors;
}

function envelopeError(profile: LimitsDraft, envelope: LimitsDraft): string | null {
	const checks: Array<[string, string, "min" | "max"]> = [
		[profile.min_amount_usd, envelope.min_amount_usd, "min"],
		[profile.min_pct_equity, envelope.min_pct_equity, "min"],
		[profile.max_amount_usd, envelope.max_amount_usd, "max"],
		[profile.max_pct_equity, envelope.max_pct_equity, "max"],
	];
	for (const [inner, outer, kind] of checks) {
		if (!DECIMAL.test(inner) || !DECIMAL.test(outer)) continue;
		if (kind === "min" && compareDecimal(inner, outer) < 0) {
			return "A profile may only tighten the workspace minimum.";
		}
		if (kind === "max" && compareDecimal(inner, outer) > 0) {
			return "A profile may only tighten the workspace maximum.";
		}
	}
	return null;
}

function validateDraft(draft: Draft): Record<string, string> {
	const errors = validateLimits("envelope", draft.envelope);
	for (const profile of Object.keys(draft.profiles) as RiskProfile[]) {
		Object.assign(errors, validateLimits(`profiles.${profile}`, draft.profiles[profile]));
		const outside = envelopeError(draft.profiles[profile], draft.envelope);
		if (outside) errors[`profiles.${profile}.envelope`] = outside;
	}
	return errors;
}

function Preview({ preview, error }: { preview: TradeSizePreview | null; error: string | null }) {
	if (error) return <div className="note err small">{error}</div>;
	if (!preview) return <div className="small muted">Server preview loading…</div>;
	if (preview.reason) {
		return (
			<div className="note warn small">
				<Badge tone="warn">{preview.reason}</Badge> No automatic size can be derived.
				A manual amount is still possible inside the limits.
			</div>
		);
	}
	return (
		<div className="note small">
			<Badge tone="ok">SERVER SIZE</Badge>{" "}
			<span className="mono">{formatUsd(preview.amount_usd)}</span> against{" "}
			<span className="mono">{formatUsd(preview.equity_usd)}</span> equity.
		</div>
	);
}

export function TradePolicyForm({
	initial,
	mode,
}: {
	initial: TradePolicyView;
	mode: "fixture" | "live";
}) {
	const router = useRouter();
	const [saved, setSaved] = useState(initial);
	const [draft, setDraft] = useState<Draft>(() => draftFromPolicy(initial));
	const [tab, setTab] = useState<RiskProfile>(initial.default_profile);
	const [saving, setSaving] = useState(false);
	const [notice, setNotice] = useState<string | null>(null);
	const [failure, setFailure] = useState<string | null>(null);
	const [preview, setPreview] = useState<TradeSizePreview | null>(
		initial.previews[initial.default_profile],
	);
	const [previewError, setPreviewError] = useState<string | null>(null);

	const activeLimits = draft.profiles[tab];
	const validation = useMemo(() => validateDraft(draft), [draft]);
	const activeErrors = useMemo(
		() =>
			Object.fromEntries(
				Object.entries(validation).filter(([key]) =>
					key.startsWith(`profiles.${tab}`),
				),
			),
		[validation, tab],
	);

	// Preview the draft against current server equity. In fixture mode the API client
	// returns the labelled sample; it never pretends to be a live resolution. Any error
	// anywhere in the draft blocks the preview, so a stale tab cannot show a size the
	// save would reject.
	useEffect(() => {
		if (Object.keys(validation).length > 0) {
			setPreviewError("Fix the highlighted fields to preview this policy.");
			return;
		}
		setPreviewError(null);
		setPreview(null);
		const timer = setTimeout(() => {
			previewTradeSize(
				{
					profile: tab,
					limits: limitsInput(draft.profiles[tab]),
					envelope: limitsInput(draft.envelope),
				},
				mode,
			)
				.then((result) => {
					if (result.ok) setPreview(result.data);
					else setPreviewError(`${result.error.code}: ${result.error.message}`);
				})
				.catch((caught: unknown) => {
					setPreviewError(caught instanceof Error ? caught.message : "Preview failed.");
				});
		}, 350);
		return () => clearTimeout(timer);
	}, [draft, tab, validation, mode]);

	function update(field: keyof LimitsDraft, value: string) {
		setDraft((current) => ({
			...current,
			profiles: {
				...current.profiles,
				[tab]: { ...current.profiles[tab], [field]: value },
			},
		}));
		setNotice(null);
		setFailure(null);
	}

	function updateEnvelope(field: keyof LimitsDraft, value: string) {
		setDraft((current) => ({
			...current,
			envelope: { ...current.envelope, [field]: value },
		}));
		setNotice(null);
		setFailure(null);
	}

	async function save() {
		if (saving) return;
		const found = validation;
		if (Object.keys(found).length > 0) {
			setFailure("The policy was not sent: fix the highlighted fields.");
			return;
		}
		setSaving(true);
		setFailure(null);
		setNotice(null);
		try {
			const updated = await saveTradePolicy(policyInput(draft));
			setSaved(updated);
			setDraft(draftFromPolicy(updated));
			setPreview(updated.previews[tab]);
			setNotice(
				`Saved · ${updated.updated_at ?? "timestamp returned by the API"} · by ${
					updated.updated_by ?? "unknown principal"
				}`,
			);
			router.refresh();
		} catch (caught) {
			const message = isTradePolicyError(caught)
				? `${caught.code}: ${caught.message}`
				: caught instanceof Error
					? caught.message
					: "The API rejected the policy.";
			setFailure(message);
		} finally {
			setSaving(false);
		}
	}

	function discard() {
		setDraft(draftFromPolicy(saved));
		setFailure(null);
		setNotice(null);
		setTab(saved.default_profile);
	}

	return (
		<Panel
			title="Trade sizing policy"
			sub={saved.source === "persisted" ? "persisted · API" : "config defaults · not yet saved"}
			action={
				<div className="row">
					{saved.source === "persisted" ? (
						<Badge tone="ok">SAVED</Badge>
					) : (
						<Badge tone="ghost">DEFAULTS</Badge>
					)}
					{mode === "fixture" && <Badge tone="warn">FIXTURE SAMPLE</Badge>}
				</div>
			}
			footer={
				<span>
					Every save writes one <span className="mono">app_settings</span> row with the
					API timestamp and principal. It is not a ledger entry and cannot change a paper
					balance. Server equity for the preview is{" "}
					<span className="mono">{formatUsd(saved.equity_usd)}</span>. A resolved size is
					not risk approval: the existing order, position and paper-mark gates still apply.
				</span>
			}
		>
			<div className="stack">
				<div className="grid cols-2">
					<label className="field">
						<span>Minimum amount · USD</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={draft.envelope.min_amount_usd}
							onChange={(event) => updateEnvelope("min_amount_usd", event.target.value)}
							aria-label="Workspace minimum amount USD"
						/>
						{validation["envelope.min_amount_usd"] && (
							<span className="error">{validation["envelope.min_amount_usd"]}</span>
						)}
					</label>
					<label className="field">
						<span>Minimum · % of equity</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={draft.envelope.min_pct_equity}
							onChange={(event) => updateEnvelope("min_pct_equity", event.target.value)}
							aria-label="Workspace minimum percent of equity"
						/>
						{validation["envelope.min_pct_equity"] && (
							<span className="error">{validation["envelope.min_pct_equity"]}</span>
						)}
					</label>
					<label className="field">
						<span>Maximum amount · USD</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={draft.envelope.max_amount_usd}
							onChange={(event) => updateEnvelope("max_amount_usd", event.target.value)}
							aria-label="Workspace maximum amount USD"
						/>
						{validation["envelope.max_amount_usd"] && (
							<span className="error">{validation["envelope.max_amount_usd"]}</span>
						)}
					</label>
					<label className="field">
						<span>Maximum · % of equity</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={draft.envelope.max_pct_equity}
							onChange={(event) => updateEnvelope("max_pct_equity", event.target.value)}
							aria-label="Workspace maximum percent of equity"
						/>
						{validation["envelope.max_pct_equity"] && (
							<span className="error">{validation["envelope.max_pct_equity"]}</span>
						)}
					</label>
				</div>

				<div className="between" style={{ gap: 12 }}>
					<label className="field" style={{ maxWidth: 260 }}>
						<span>Default profile for an order with no profile</span>
						<select
							className="select"
							value={draft.default_profile}
							onChange={(event) => {
								setDraft((current) => ({
									...current,
									default_profile: event.target.value as RiskProfile,
								}));
								setNotice(null);
							}}
							aria-label="Default risk profile"
						>
							<option value="low">low</option>
							<option value="mid">mid</option>
							<option value="high">high</option>
						</select>
					</label>
					<p className="small muted" style={{ maxWidth: 440 }}>
						The workspace envelope is a hard outer bound. Each profile may only tighten
						it; the API rejects a profile outside it.
					</p>
				</div>

				<div className="tabs" role="tablist" aria-label="Risk profile">
					{(Object.keys(draft.profiles) as RiskProfile[]).map((profile) => (
						<button
							key={profile}
							type="button"
							role="tab"
							aria-selected={tab === profile}
							className={`tab${tab === profile ? " on" : ""}`}
							onClick={() => setTab(profile)}
						>
							{profile}
							{profile === draft.default_profile && <span className="count">default</span>}
						</button>
					))}
				</div>

				<div className="small muted">
					{PROFILE_LABELS[tab]} · the same four bounds as the envelope, applied when this
					profile is selected on an order.
				</div>

				<div className="grid cols-2">
					<label className="field">
						<span>Minimum amount · USD</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={activeLimits.min_amount_usd}
							onChange={(event) => update("min_amount_usd", event.target.value)}
							aria-label={`${tab} profile minimum amount USD`}
						/>
						{activeErrors[`profiles.${tab}.min_amount_usd`] && (
							<span className="error">{activeErrors[`profiles.${tab}.min_amount_usd`]}</span>
						)}
					</label>
					<label className="field">
						<span>Minimum · % of equity</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={activeLimits.min_pct_equity}
							onChange={(event) => update("min_pct_equity", event.target.value)}
							aria-label={`${tab} profile minimum percent of equity`}
						/>
						{activeErrors[`profiles.${tab}.min_pct_equity`] && (
							<span className="error">{activeErrors[`profiles.${tab}.min_pct_equity`]}</span>
						)}
					</label>
					<label className="field">
						<span>Maximum amount · USD</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={activeLimits.max_amount_usd}
							onChange={(event) => update("max_amount_usd", event.target.value)}
							aria-label={`${tab} profile maximum amount USD`}
						/>
						{activeErrors[`profiles.${tab}.max_amount_usd`] && (
							<span className="error">{activeErrors[`profiles.${tab}.max_amount_usd`]}</span>
						)}
					</label>
					<label className="field">
						<span>Maximum · % of equity</span>
						<input
							className="input mono"
							inputMode="decimal"
							value={activeLimits.max_pct_equity}
							onChange={(event) => update("max_pct_equity", event.target.value)}
							aria-label={`${tab} profile maximum percent of equity`}
						/>
						{activeErrors[`profiles.${tab}.max_pct_equity`] && (
							<span className="error">{activeErrors[`profiles.${tab}.max_pct_equity`]}</span>
						)}
					</label>
				</div>

				{activeErrors[`profiles.${tab}.envelope`] && (
					<div className="note err small">{activeErrors[`profiles.${tab}.envelope`]}</div>
				)}

				<Preview preview={preview} error={previewError} />

				<div className="row">
					<button className="btn primary" type="button" onClick={save} disabled={saving}>
						{saving ? "Saving…" : "Save trade policy"}
					</button>
					<button className="btn" type="button" onClick={discard} disabled={saving}>
						Discard changes
					</button>
					<span className="small muted">
						Preview amount is resolved by the API from current equity and the profile
						shown, not by this browser.
					</span>
				</div>

				{notice && (
					<div className="note" role="status">
						<Badge tone="ok">SAVED</Badge> {notice}
					</div>
				)}
				{failure && (
					<div className="note err" role="alert">
						<Badge tone="err">NOT SAVED</Badge> {failure}
					</div>
				)}
				{saved.updated_at && (
					<p className="small muted">
						Last change: {saved.updated_at}
						{saved.updated_by ? ` · ${saved.updated_by}` : ""}. Sizing previews use{" "}
						{formatUsdPrice(saved.equity_usd)} equity.
					</p>
				)}
			</div>
		</Panel>
	);
}
