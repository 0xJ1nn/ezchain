"use client";

import { useId, useState } from "react";
import { Badge } from "@/components/ui";
import {
	TradePolicyApiError,
	previewTradePolicy,
	updateStrategyPolicy,
	type StrategyPolicy,
	type TradePolicyDocument,
	type TradePolicyPreview,
} from "@/lib/trade-policy-api";

const PROFILE_RANK: Record<string, number> = { low: 1, mid: 2, high: 3 };

const LOOSER_HINT =
	"Raising a strategy's profile lets more of your own money move. The server refuses the change until you acknowledge the risk here.";

function newEffective(row: StrategyPolicy, draft: string, activeProfile: string | null): string | null {
	return draft === "" ? activeProfile : draft;
}

function isLoosening(
	row: StrategyPolicy,
	draft: string,
	activeProfile: string | null,
): boolean {
	const before = row.effective_profile;
	const after = newEffective(row, draft, activeProfile);
	if (before === after) return false;
	// Choosing a profile where there was none only adds gates.
	if (before === null) return false;
	if (after === null) return true;
	return (PROFILE_RANK[after] ?? 3) > (PROFILE_RANK[before] ?? 3);
}

export function StrategyPolicyForm({
	initial,
	fixture,
}: {
	initial: TradePolicyDocument;
	fixture: boolean;
}) {
	const [strategies, setStrategies] = useState<StrategyPolicy[]>(initial.strategies);
	const [drafts, setDrafts] = useState<Record<string, string>>({});
	const [acknowledged, setAcknowledged] = useState<Record<string, boolean>>({});
	const [pending, setPending] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [notice, setNotice] = useState<string | null>(null);
	const ackId = useId();

	function draftFor(row: StrategyPolicy): string {
		if (drafts[row.id] !== undefined) return drafts[row.id];
		return row.risk_profile ?? "";
	}

	async function save(row: StrategyPolicy) {
		if (pending !== null) return;
		if (fixture) {
			// The server told this page it is rendering fixture data. A profile is a real
			// server write, so a preview says so instead of sending a request that would
			// change another deployment.
			setError(
				"Changing a strategy profile is a real write; it is not simulated in fixture mode.",
			);
			return;
		}
		const draft = draftFor(row);
		const loosening = isLoosening(row, draft, initial.active_profile);
		if (loosening && !acknowledged[row.id]) {
			setError(
				`${row.name}: this loosens a limit that governs your own funds. Tick the acknowledgement below before saving.`,
			);
			return;
		}
		setPending(row.id);
		setError(null);
		setNotice(null);
		try {
			const updated = await updateStrategyPolicy(
				row.id,
				draft === "" ? null : draft,
				loosening && acknowledged[row.id],
			);
			setStrategies((current) => current.map((item) => (item.id === row.id ? updated : item)));
			setDrafts((current) => {
				const next = { ...current };
				delete next[row.id];
				return next;
			});
			setAcknowledged((current) => ({ ...current, [row.id]: false }));
			setNotice(
				updated.source === "strategy"
					? `${row.name} now uses the ${updated.risk_profile} profile.`
					: `${row.name} inherits the deployment default again.`,
			);
		} catch (caught) {
			const apiError = caught instanceof TradePolicyApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: caught instanceof Error
						? caught.message
						: "The trade-policy request failed.",
			);
		} finally {
			setPending(null);
		}
	}

	if (strategies.length === 0) {
		return <p className="small muted">No strategies exist yet, so there is no per-strategy override to set.</p>;
	}

	return (
		<div>
			{fixture && (
				<p className="note mt-8">
					Fixture mode shows the documented defaults. Saving a profile is a real server
					write this preview cannot perform.
				</p>
			)}
			{error && (
				<div className="note err mt-12" role="alert">
					{error}
				</div>
			)}
			{notice && (
				<div className="note ok mt-12" role="status">
					{notice}
				</div>
			)}

			<div className="stack-sm mt-8">
				{strategies.map((row) => {
					const draft = draftFor(row);
					const loosening = isLoosening(row, draft, initial.active_profile);
					const dirty = draft !== (row.risk_profile ?? "");
					const ackKey = `${ackId}-${row.id}`;
					return (
						<div
							className="panel"
							key={row.id}
							data-strategy={row.id}
							style={{ boxShadow: "none" }}
						>
							<div className="panel-body">
								<div className="between" style={{ gap: 16, alignItems: "flex-start" }}>
									<div className="grow">
										<div className="row">
											<span className="strong">{row.name}</span>
											<span className="mono small muted">{row.token_symbol}</span>
											{row.source === "strategy" ? (
												<Badge tone="info">strategy override</Badge>
											) : (
												<Badge tone="ghost">deployment default</Badge>
											)}
										</div>
										<div className="small muted mt-8">
											effective profile{" "}
											<span className="mono">{row.effective_profile ?? "none"}</span>
											{" · "}order{" "}
											<span className="mono">{row.order_usd ?? "unset"}</span>
											{" · "}position cap{" "}
											<span className="mono">{row.max_position_usd ?? "unset"}</span>
										</div>
										{row.warnings.length > 0 && (
											<ul className="small mt-8" style={{ paddingLeft: 18 }}>
												{row.warnings.map((warning, index) => (
													<li key={`${warning.code}-${index}`}>
														<span className="mono">{warning.code}</span>{" "}
														{warning.message}
													</li>
												))}
											</ul>
										)}
									</div>
									<div style={{ width: 210, flex: "0 0 auto" }}>
										<select
											className="select"
											aria-label={`${row.name} risk profile`}
											value={draft}
											onChange={(event) => {
												setDrafts((current) => ({
													...current,
													[row.id]: event.target.value,
												}));
												setNotice(null);
												setError(null);
											}}
										>
											<option value="">inherit deployment default</option>
											{initial.profiles.map((profile) => (
												<option value={profile.name} key={profile.name}>
													{profile.name}
												</option>
											))}
										</select>
										<div className="row mt-8">
											<button
												type="button"
												className="btn sm primary"
												onClick={() => save(row)}
												disabled={!dirty || pending !== null}
											>
												{pending === row.id ? "Saving…" : "Save"}
											</button>
										</div>
									</div>
								</div>

								{loosening && (
									<div className="note warn mt-12">
										<label htmlFor={ackKey} className="row" style={{ gap: 8 }}>
											<input
												id={ackKey}
												type="checkbox"
												checked={acknowledged[row.id] === true}
												onChange={(event) =>
													setAcknowledged((current) => ({
														...current,
														[row.id]: event.target.checked,
													}))
												}
											/>
											<span>
												<strong>You are loosening {row.name}.</strong> {LOOSER_HINT}
											</span>
										</label>
									</div>
								)}
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}

export function SizingPreviewForm({
	activeProfile,
	profiles,
	fixture,
}: {
	activeProfile: string | null;
	profiles: { name: string }[];
	fixture: boolean;
}) {
	const [profile, setProfile] = useState(activeProfile ?? profiles[0]?.name ?? "");
	const [amount, setAmount] = useState("");
	const [equity, setEquity] = useState("");
	const [result, setResult] = useState<TradePolicyPreview | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);

	async function check() {
		if (pending) return;
		setPending(true);
		setError(null);
		setResult(null);
		try {
			setResult(
				await previewTradePolicy({
					profile: profile === "" ? null : profile,
					amount_usd: amount.trim(),
					equity_usd: equity.trim() === "" ? null : equity.trim(),
				}),
			);
		} catch (caught) {
			const apiError = caught instanceof TradePolicyApiError ? caught : null;
			setError(
				apiError
					? `${apiError.code}: ${apiError.message}`
					: caught instanceof Error
						? caught.message
						: "The preview request failed.",
			);
		} finally {
			setPending(false);
		}
	}

	return (
		<div>
			<p className="small muted">
				The API checks the size with the same resolver the order gate runs. A verdict here is
				the verdict an order receives.
			</p>
			{fixture && (
				<p className="note mt-12">
					Sizing is checked by the API. This fixture build cannot answer for it, so the
					preview stays disabled rather than inventing a number.
				</p>
			)}
			<div className="row mt-12" style={{ gap: 12, flexWrap: "wrap" }}>
				<label className="small">
					<span className="muted">profile</span>
					<select
						className="select"
						aria-label="Preview profile"
						value={profile}
						onChange={(event) => setProfile(event.target.value)}
					>
						{profiles.map((item) => (
							<option value={item.name} key={item.name}>
								{item.name}
							</option>
						))}
					</select>
				</label>
				<label className="small">
					<span className="muted">amount (USD)</span>
					<input
						className="input mono"
						inputMode="decimal"
						aria-label="Preview amount"
						value={amount}
						onChange={(event) => setAmount(event.target.value)}
					/>
				</label>
				<label className="small">
					<span className="muted">equity (USD)</span>
					<input
						className="input mono"
						inputMode="decimal"
						aria-label="Preview equity"
						value={equity}
						onChange={(event) => setEquity(event.target.value)}
					/>
				</label>
				<button
					type="button"
					className="btn sm"
					onClick={check}
					disabled={fixture || pending || amount.trim() === ""}
				>
					{pending ? "Checking…" : "Preview"}
				</button>
			</div>

			{error && (
				<div className="note err mt-12" role="alert">
					{error}
				</div>
			)}
			{result && (
				<div className="mt-12" data-preview-verdict={result.allowed ? "allowed" : "rejected"}>
					<div className="row">
						<Badge tone={result.allowed ? "ok" : "warn"}>
							{result.allowed ? "allowed" : "rejected"}
						</Badge>
						<span className="small muted">
							profile <span className="mono">{result.profile}</span>
						</span>
					</div>
					<div className="small muted mt-8">
						effective maximum <span className="mono">{result.effective_max_usd}</span>
						{" · "}percent cap{" "}
						<span className="mono">{result.percent_cap_usd ?? "unknown"}</span>
					</div>
					{result.reasons.length > 0 && (
						<ul className="small mt-8" style={{ paddingLeft: 18 }}>
							{result.reasons.map((reason) => (
								<li key={reason.code}>
									<span className="mono">{reason.code}</span> {reason.message}
								</li>
							))}
						</ul>
					)}
				</div>
			)}
		</div>
	);
}
